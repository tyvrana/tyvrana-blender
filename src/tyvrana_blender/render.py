"""Main-thread scene rendering into extension-owned temporary PNG files."""

import logging
import struct
import threading
from typing import Any

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from .artifacts import ArtifactSpool, ArtifactTooLarge, SpoolFull
from .models import RenderArguments, RenderResult
from .operations import OperationError

logger = logging.getLogger(__name__)


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


def render_image(
    arguments: RenderArguments, spool: ArtifactSpool
) -> tuple[RenderResult, ArtifactDescriptor]:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Rendering requires Blender's main thread")
    scene = bpy.context.scene
    if scene.camera is None:
        raise OperationError("no_camera", "The current scene has no camera")
    if bpy.app.is_job_running("RENDER"):
        raise OperationError("invalid_context", "Blender is already rendering")
    render = scene.render
    image = render.image_settings
    overrides = [
        (render, "resolution_x", arguments.width),
        (render, "resolution_y", arguments.height),
        (render, "resolution_percentage", 100),
        (render, "use_border", False),
        (render, "use_crop_to_border", False),
        (render, "use_multiview", False),
        (render, "use_sequencer", False),
        (image, "media_type", "IMAGE"),
        (image, "file_format", "PNG"),
        (image, "color_mode", "RGBA"),
        (image, "color_depth", "8"),
    ]
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
        with spool.reserve() as (artifact_id, path):
            try:
                for obj, key, value in overrides:
                    setattr(obj, key, value)
                outcome = bpy.ops.render.render("EXEC_DEFAULT", write_still=False)
                result = bpy.data.images.get("Render Result")
                if "FINISHED" not in outcome or result is None:
                    raise OperationError(
                        "render_failed", "Blender did not complete the render"
                    )
                result.save_render(filepath=str(path), scene=scene)
                with path.open("rb") as stream:
                    header = stream.read(24)
                if (
                    header[:8] != b"\x89PNG\r\n\x1a\n"
                    or header[12:16] != b"IHDR"
                    or len(header) != 24
                    or struct.unpack("!II", header[16:24])
                    != (arguments.width, arguments.height)
                ):
                    raise OperationError(
                        "render_failed", "Blender produced unexpected PNG dimensions"
                    )
            finally:
                # Restore format before its dependent settings (mode/depth).
                for obj, key, value in saved:
                    setattr(obj, key, value)
            descriptor = spool.describe(artifact_id)
            return RenderResult(
                width=arguments.width, height=arguments.height
            ), descriptor
    except OperationError:
        raise
    except SpoolFull as exc:
        raise OperationError("adapter_busy", "Too many renders await transfer") from exc
    except ArtifactTooLarge as exc:
        raise OperationError(
            "artifact_too_large", "Rendered PNG exceeds the byte limit"
        ) from exc
    except Exception as exc:
        logger.exception("Blender render failed")
        raise OperationError(
            "render_failed", "Blender render failed; see the application log"
        ) from exc
