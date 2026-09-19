"""Main-thread scene rendering into extension-owned temporary PNG files."""

import logging
import struct
import threading
import time
from collections.abc import Callable, Generator
from typing import Any

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from . import render_output
from .artifacts import ArtifactSpool, ArtifactTooLarge, SpoolFull
from .errors import OperationError
from .models import RenderArguments, RenderResult
from .uv_checker import display as checker_display
from .wireframe import display

logger = logging.getLogger(__name__)
_native_async = False


def native_pending() -> bool:
    """Includes the native job's delay before is_job_running becomes true."""
    return _native_async


def output_nodes(tree: Any, visited: set[int]) -> list[Any]:
    if tree is None or tree.as_pointer() in visited:
        return []
    visited.add(tree.as_pointer())
    nodes = []
    for node in tree.nodes:
        if node.type == "OUTPUT_FILE":
            nodes.append(node)
        elif node.type == "GROUP":
            nodes.extend(output_nodes(node.node_tree, visited))
    return nodes


def result_editor() -> tuple[Any, Any]:
    if bpy.app.background:
        raise OperationError("invalid_context", "Showing a render requires a window")
    window = bpy.context.window
    if window is None:
        raise OperationError("invalid_context", "No active window for render display")
    areas = [
        area for area in window.screen.areas if area.type in {"VIEW_3D", "IMAGE_EDITOR"}
    ]
    if not areas:
        raise OperationError(
            "invalid_context", "No 3D View or Image Editor for render display"
        )
    return window, max(areas, key=lambda area: area.width * area.height)


def show_result(editor: tuple[Any, Any], result: Any) -> None:
    window, area = editor
    area.type = "IMAGE_EDITOR"
    area.spaces.active.ui_mode = "VIEW"
    area.spaces.active.image = result
    region = next(region for region in area.regions if region.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        outcome = bpy.ops.image.view_all(fit_view=True)
        if "FINISHED" not in outcome:
            raise OperationError(
                "render_display_failed", "Could not fit the render view"
            )
    area.tag_redraw()


def render_image(
    arguments: RenderArguments,
    spool: ArtifactSpool,
    *,
    observe: Callable[[str, float], None] | None = None,
) -> tuple[RenderResult, ArtifactDescriptor]:
    steps = render_steps(arguments, spool, observe=observe)
    try:
        next(steps)
    except StopIteration as done:
        return done.value  # type: ignore[no-any-return]
    raise RuntimeError("Synchronous render unexpectedly yielded")


def render_steps(
    arguments: RenderArguments,
    spool: ArtifactSpool,
    *,
    observe: Callable[[str, float], None] | None = None,
    interactive: bool = False,
    checkpoint: Callable[[], None] | None = None,
) -> Generator[None, None, tuple[RenderResult, ArtifactDescriptor]]:
    global _native_async
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Rendering requires Blender's main thread")
    if arguments.inspection is not None:
        from .inspection_render import steps

        return (
            yield from steps(
                arguments,
                spool,
                observe=observe,
                interactive=interactive,
                checkpoint=checkpoint,
            )
        )
    scene = bpy.context.scene
    if scene.camera is None:
        raise OperationError("no_camera", "The current scene has no camera")
    if bpy.app.is_job_running("RENDER"):
        raise OperationError("invalid_context", "Blender is already rendering")
    editor = result_editor() if arguments.show_result else None
    render = scene.render
    overrides: list[tuple[Any, str, Any]] = [
        (render, "resolution_x", arguments.width),
        (render, "resolution_y", arguments.height),
        (render, "resolution_percentage", 100),
        (render, "use_border", False),
        (render, "use_crop_to_border", False),
        (render, "use_multiview", False),
        (render, "use_sequencer", False),
        (render, "use_single_layer", True),
    ]
    if arguments.uv_checker is not None or (
        arguments.surface is not None and arguments.cycles is None
    ):
        overrides.append((render, "engine", "BLENDER_EEVEE"))
    if arguments.cycles is not None:
        options = arguments.cycles
        cycles = getattr(scene, "cycles", None)
        if cycles is None:
            raise OperationError("render_engine_unavailable", "Cycles is unavailable")
        overrides.extend(
            [
                (render, "engine", "CYCLES"),
                (cycles, "device", options.device.upper()),
                (cycles, "samples", options.samples),
                (cycles, "use_denoising", options.denoise),
                (cycles, "use_layer_samples", "IGNORE"),
                (cycles, "use_sample_subset", False),
                (
                    cycles,
                    "time_limit",
                    min(
                        cycles.time_limit or arguments.budget.max_seconds,
                        arguments.budget.max_seconds,
                    ),
                ),
            ]
        )
    # File Output nodes are side effects, not part of the requested PNG. Preserve
    # compositor image processing while preventing writes to user output paths.
    overrides.extend(
        (node, "mute", True)
        for node in output_nodes(scene.compositing_node_group, set())
    )
    saved = [(obj, key, getattr(obj, key)) for obj, key, _ in overrides]
    try:
        output_media = render_output.media_type(arguments, sequence=False)
        with (
            spool.reserve(output_media) as (artifact_id, path),
            render_output.output_settings(scene, bpy.context.view_layer, arguments),
        ):
            try:
                for obj, key, value in overrides:
                    setattr(obj, key, value)
                with (
                    display(arguments.wireframe),
                    checker_display(arguments.uv_checker or arguments.surface),
                ):
                    if observe is not None:
                        observe("running", 0)
                    started = time.monotonic()
                    if interactive:
                        completed = []

                        def finish(*_args: Any) -> None:
                            completed.append(True)

                        def cancel(*_args: Any) -> None:
                            completed.append(False)

                        bpy.app.handlers.render_complete.append(finish)
                        bpy.app.handlers.render_cancel.append(cancel)
                        _native_async = True
                        try:
                            outcome = bpy.ops.render.render(
                                "INVOKE_DEFAULT",
                                write_still=False,
                                layer=bpy.context.view_layer.name,
                            )
                            if "RUNNING_MODAL" in outcome:
                                while not completed or bpy.app.is_job_running("RENDER"):
                                    yield
                                outcome = (
                                    {"FINISHED"} if completed[-1] else {"CANCELLED"}
                                )
                        finally:
                            _native_async = False
                            if finish in bpy.app.handlers.render_complete:
                                bpy.app.handlers.render_complete.remove(finish)
                            if cancel in bpy.app.handlers.render_cancel:
                                bpy.app.handlers.render_cancel.remove(cancel)
                    else:
                        outcome = bpy.ops.render.render(
                            "EXEC_DEFAULT",
                            write_still=False,
                            layer=bpy.context.view_layer.name,
                        )
                    if observe is not None:
                        observe("completed", time.monotonic() - started)
                result = bpy.data.images.get("Render Result")
                if "FINISHED" not in outcome or result is None:
                    raise OperationError(
                        "render_failed", "Blender did not complete the render"
                    )
                result.save_render(filepath=str(path), scene=scene)
                channels = []
                if arguments.format == "png":
                    with path.open("rb") as stream:
                        header = stream.read(29)
                    if (
                        header[:8] != b"\x89PNG\r\n\x1a\n"
                        or len(header) != 29
                        or struct.unpack("!II", header[16:24])
                        != (arguments.width, arguments.height)
                        or header[24] != arguments.bit_depth
                    ):
                        raise OperationError(
                            "render_failed", "Unexpected PNG dimensions/depth"
                        )
                else:
                    width, height, types = render_output.exr_header(path)
                    if (width, height) != (arguments.width, arguments.height):
                        raise OperationError(
                            "render_failed", "Unexpected EXR dimensions"
                        )
                    channels = sorted(types)
                    # Depth/index passes remain float even in half-float color EXR.
                    requested = [
                        render_output.PASS_NAMES[p] for p in arguments.passes
                    ] + [a.name for a in arguments.aovs]
                    missing = [
                        n
                        for n in requested
                        if not any("." + n + "." in c for c in channels)
                    ]
                    if missing:
                        raise OperationError(
                            "render_pass_unavailable",
                            "Engine omitted requested output passes: "
                            + ", ".join(missing),
                        )
                metadata = render_output.color_metadata(scene, arguments)
            finally:
                # Restore format before its dependent settings (mode/depth).
                for obj, key, value in saved:
                    setattr(obj, key, value)
            descriptor = spool.describe(
                artifact_id, output_media, maximum=arguments.budget.max_artifact_bytes
            )
            if editor is not None:
                show_result(editor, result)
            return RenderResult(
                width=arguments.width,
                height=arguments.height,
                format=arguments.format,
                bit_depth=arguments.bit_depth,
                color_mode=arguments.color_mode,
                color_management=metadata,
                output_channels=channels,
            ), descriptor
    except OperationError:
        raise
    except SpoolFull as exc:
        raise OperationError("adapter_busy", "Too many renders await transfer") from exc
    except ArtifactTooLarge as exc:
        raise OperationError(
            "artifact_too_large", "Render artifact exceeds max_artifact_bytes"
        ) from exc
    except Exception as exc:
        logger.exception("Blender render failed")
        raise OperationError(
            "render_failed", "Blender render failed; see the application log"
        ) from exc
