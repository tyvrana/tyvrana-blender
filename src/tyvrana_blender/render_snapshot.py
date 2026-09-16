"""Main-thread native snapshot preparation for owned background render jobs."""

import json
import sys
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

from . import bake_jobs
from .artifacts import ArtifactSpool
from .errors import OperationError
from .models import RenderArguments
from .render import result_editor, show_result
from .render_models import RenderJobStatus


def prepare(
    arguments: RenderArguments, spool: ArtifactSpool, identifier: str
) -> RenderJobStatus:
    scene = bpy.context.scene
    if (
        bake_jobs.busy()
        or bpy.app.is_job_running("RENDER")
        or bpy.app.is_job_running("OBJECT_BAKE")
    ):
        raise OperationError("adapter_busy", "A native render or bake is running")
    if bpy.context.mode != "OBJECT":
        raise OperationError("invalid_context", "Render snapshots require Object Mode")
    if scene.camera is None:
        raise OperationError("no_camera", "The current scene has no camera")
    from . import growth

    for obj in scene.objects:
        if obj.type == "CURVES" and growth.KEY in obj:
            growth.evaluation_dependencies(obj)
    if arguments.show_result:
        result_editor()
    destination = None
    if arguments.output is not None:
        path = Path(bpy.path.abspath(arguments.output.filepath))
        if not path.is_absolute() or path.suffix.lower() != ".png" or path.is_symlink():
            raise OperationError(
                "file_destination_invalid",
                "Use an absolute or Blender-relative PNG path without symlinks",
            )
        path = path.resolve()
        if not path.parent.is_dir() or (path.exists() and not path.is_file()):
            raise OperationError(
                "file_destination_invalid",
                "Output parent must exist and destination must be a file",
            )
        if path.exists() and not arguments.output.overwrite:
            raise OperationError(
                "file_exists", "Existing render output requires overwrite: true"
            )
        destination = str(path)
    directory = spool.root / "jobs" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    engine = (
        "CYCLES"
        if arguments.cycles
        else (
            "BLENDER_EEVEE"
            if arguments.surface or arguments.uv_checker
            else scene.render.engine
        )
    )
    if engine not in {"CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"}:
        raise OperationError(
            "render_engine_unavailable",
            "Snapshot renders support built-in Cycles, Eevee and Workbench",
        )
    # Native .blend serialization does not retain unsaved painted/baked pixels.
    # Carry their live float buffers privately without packing or changing IDs.
    buffers: list[dict[str, Any]] = []
    total = 0
    for image in bpy.data.images:
        if (
            image.users
            and image.has_data
            and image.source in {"FILE", "GENERATED"}
            and (image.is_dirty or image.source == "GENERATED")
        ):
            size = len(image.pixels) * 4
            total += size
            if total > 512 * 1024 * 1024 or len(buffers) >= 128:
                raise OperationError(
                    "render_snapshot_limit",
                    "Live image buffers exceed 512 MiB or 128 images; "
                    "persist unused images first",
                )
            values = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(values)
            filename = f"pixels-{len(buffers)}.bin"
            values.tofile(directory / filename)
            buffers.append(
                {"name": image.name, "filename": filename, "count": len(values)}
            )
    bpy.context.view_layer.update()
    bpy.data.libraries.write(
        str(directory / "scene.blend"), {scene}, path_remap="ABSOLUTE"
    )
    cycles_preferences = bpy.context.preferences.addons.get("cycles")
    device = None
    if engine == "CYCLES":
        device = (
            arguments.cycles.device.upper() if arguments.cycles else scene.cycles.device
        )
    gpu = None
    if device == "GPU" and cycles_preferences is not None:
        prefs = cycles_preferences.preferences
        gpu = {
            "type": prefs.compute_device_type,
            "devices": {d.id: bool(d.use) for d in prefs.devices},
        }
    config = {
        "binary": bpy.app.binary_path,
        "paths": [p for p in sys.path if p and Path(p).is_absolute()],
        "package": __package__,
        "scene": scene.name,
        "layer": bpy.context.view_layer.name,
        "arguments": arguments.model_dump(mode="json", exclude_unset=True),
        "destination": destination,
        "gpu": gpu,
        "buffers": buffers,
    }
    (directory / "config.json").write_text(json.dumps(config))
    return RenderJobStatus(
        job_id=identifier,
        state="queued",
        submitted_at="",
        width=arguments.width,
        height=arguments.height,
        engine=engine,
        frame=scene.frame_current,
        samples_requested=(
            arguments.cycles.samples if arguments.cycles else scene.cycles.samples
        )
        if engine == "CYCLES"
        else None,
    )


def display_result(spool: ArtifactSpool, identifier: str) -> None:
    editor = result_editor()
    image = bpy.data.images.load(
        str(spool.root / "jobs" / identifier / (identifier + ".png")),
        check_existing=False,
    )
    try:
        image.pack()
        image.name = "Tyvrana Render"
        image["tyvrana_render_preview"] = True
        show_result(editor, image)
        for old in list(bpy.data.images):
            if old != image and old.get("tyvrana_render_preview") and old.users == 0:
                bpy.data.images.remove(old)
    except Exception:
        bpy.data.images.remove(image)
        raise
