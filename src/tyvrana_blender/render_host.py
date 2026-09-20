"""Bounded render jobs in the connected host, with frame-boundary cancellation."""

import json
import logging
import os
import shutil
import time
import zipfile
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import bake_jobs, render_output
from .artifacts import ArtifactSpool, artifact_path
from .errors import OperationError
from .models import RenderArguments
from .render import native_pending, render_steps, result_editor
from .render_models import RenderJobStatus

log = logging.getLogger(__name__)


@dataclass
class HostJob:
    directory: Path
    steps: Generator[None, None, dict[str, Any]]


_active: HostJob | None = None


def busy() -> bool:
    return _active is not None


def write(directory: Path, name: str, value: dict[str, Any]) -> None:
    temporary = directory / (name + ".pending")
    temporary.write_text(json.dumps(value))
    temporary.replace(directory / name)


def prepare(
    arguments: RenderArguments, spool: ArtifactSpool, identifier: str
) -> RenderJobStatus:
    global _active
    scene = bpy.context.scene
    if (
        busy()
        or bake_jobs.busy()
        or bpy.app.is_job_running("RENDER")
        or bpy.app.is_job_running("OBJECT_BAKE")
    ):
        raise OperationError("adapter_busy", "A native render or bake is running")
    if bpy.context.mode != "OBJECT":
        raise OperationError("invalid_context", "Rendering requires Object Mode")
    if scene.camera is None and arguments.inspection is None:
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
        if (
            not path.is_absolute()
            or path.suffix.lower() != render_output.suffix(arguments)
            or path.is_symlink()
        ):
            raise OperationError(
                "file_destination_invalid",
                "Use an absolute or Blender-relative output path "
                "with the matching suffix and no symlink",
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
    engine = (
        "BLENDER_WORKBENCH"
        if arguments.inspection
        else "CYCLES"
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
            "Host rendering supports built-in Cycles, Eevee and Workbench",
        )
    device = None
    if engine == "CYCLES":
        import _cycles  # type: ignore[import-not-found]

        from .render_devices import requested_device

        device = requested_device(
            bpy.context, arguments.cycles, _cycles.available_devices
        )
    directory = spool.root / "jobs" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    write(directory, "config.json", {"destination": destination})
    _active = HostJob(directory, frames(arguments, directory))
    return RenderJobStatus(
        job_id=identifier,
        state="queued",
        submitted_at="",
        width=arguments.width,
        height=arguments.height,
        format=arguments.format,
        bit_depth=arguments.bit_depth,
        color_mode=arguments.color_mode,
        frame_count=len(arguments.frames or [0]),
        engine=engine,
        frame=scene.frame_current,
        samples_requested=(
            arguments.cycles.samples if arguments.cycles else scene.cycles.samples
        )
        if engine == "CYCLES"
        else None,
        host_pid=os.getpid(),
        host_background=bpy.app.background,
        scene_name=scene.name,
        document_filepath=bpy.data.filepath or None,
        device=device,
    )


def frames(
    arguments: RenderArguments, directory: Path
) -> Generator[None, None, dict[str, Any]]:
    scene = bpy.context.scene
    frame, subframe = scene.frame_current, scene.frame_subframe
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    files: list[Path] = []
    total_bytes = 0
    report: dict[str, Any] = {"render_seconds": 0, "completed_frames": 0}
    buffer = directory / "frame-buffer"
    buffer.mkdir()
    spool = ArtifactSpool(buffer)

    def check() -> None:
        if (directory / "cancel.request").exists():
            raise OperationError(
                "render_cancelled", "Cancelled at a native frame boundary"
            )
        if time.monotonic() - started >= arguments.budget.max_seconds:
            raise OperationError(
                "render_budget_exceeded",
                "Render deadline exceeded; native frame drained and host restored",
            )

    def observe(event: str, seconds: float) -> None:
        if event == "completed":
            report["render_seconds"] += seconds
        else:
            report.setdefault("started_at", datetime.now(UTC).isoformat())
        write(
            directory,
            "progress.json",
            {
                "completed_frames": len(results),
                "running": True,
                "started_at": report["started_at"],
            },
        )

    try:
        for index, target in enumerate(arguments.frames or [frame]):
            check()
            scene.frame_set(
                target, subframe=subframe if arguments.frames is None else 0
            )
            remaining = max(
                1, arguments.budget.max_seconds - (time.monotonic() - started)
            )
            options = arguments.model_copy(
                update={
                    "frames": None,
                    "budget": arguments.budget.model_copy(
                        update={"max_seconds": remaining}
                    ),
                }
            )
            result, descriptor = yield from render_steps(
                options,
                spool,
                observe=observe,
                interactive=not bpy.app.background,
                checkpoint=check,
            )
            check()
            filename = f"frame-{target:07d}" + (
                ".png" if arguments.format == "png" else ".exr"
            )
            destination = directory / (
                filename
                if arguments.frames
                else directory.name + render_output.suffix(arguments)
            )
            total_bytes += descriptor.byte_size
            if total_bytes > arguments.budget.max_artifact_bytes:
                raise OperationError(
                    "artifact_too_large", "Sequence exceeds max_artifact_bytes"
                )
            shutil.move(artifact_path(spool.root, descriptor), destination)
            files.append(destination)
            results.append(
                dict(
                    frame=target,
                    filename=filename,
                    byte_size=descriptor.byte_size,
                    sha256=descriptor.sha256,
                )
            )
            report["output"] = result.model_dump(mode="json")
            report["completed_frames"] = index + 1
            write(
                directory,
                "progress.json",
                {"completed_frames": len(results), "running": True},
            )
            # Yield between frames so cancellation/connection events can be serviced.
            yield
        check()
        if arguments.frames:
            with zipfile.ZipFile(
                directory / (directory.name + ".zip"),
                "w",
                compression=zipfile.ZIP_STORED,
            ) as archive:
                for path in files:
                    archive.write(path, path.name)
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        dict(output=report["output"], frames=results), sort_keys=True
                    ),
                )
            for path in files:
                path.unlink()
        return report
    finally:
        scene.frame_set(frame, subframe=subframe)
        shutil.rmtree(buffer)


def tick() -> None:
    global _active
    if _active is None:
        return
    job = _active
    try:
        next(job.steps)
        return
    except StopIteration as done:
        report = done.value
    except Exception as exc:
        if not isinstance(exc, OperationError):
            log.exception("Connected-host render failed")
        report = {
            "error": {
                "code": exc.error.code
                if isinstance(exc, OperationError)
                else "render_failed",
                "message": exc.error.message
                if isinstance(exc, OperationError)
                else "Native host render failed; see application log",
            }
        }
    try:
        job.steps.close()
        write(job.directory, "completed.json", report)
    finally:
        _active = None


def shutdown() -> None:
    global _active
    if _active is not None:
        if native_pending() or bpy.app.is_job_running("RENDER"):
            raise OperationError(
                "adapter_busy", "Native render is still draining; wait before unloading"
            )
        _active.steps.close()
        _active = None
