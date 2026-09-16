"""Bounded native bake jobs with short status calls and deterministic cleanup."""

import logging
from collections.abc import Generator
from typing import Any
from uuid import uuid4

import bpy  # type: ignore[import-not-found]

from . import bake
from .bake_models import BakeImageArguments, BakeImageResult, BakeJobStatus
from .errors import OperationError

log = logging.getLogger(__name__)
_jobs: dict[str, BakeJobStatus] = {}
_active: str | None = None
_steps: Generator[None, None, BakeImageResult] | None = None
_native_done = False
_native_cancelled = False


def busy() -> bool:
    return _active is not None


def completed(obj: Any) -> None:
    global _native_done
    if _active is not None and obj.name.startswith("Bake temporary "):
        _native_done = True


def cancelled(obj: Any) -> None:
    global _native_cancelled, _native_done
    if _active is not None and obj.name.startswith("Bake temporary "):
        _native_cancelled = _native_done = True


def detach() -> None:
    for collection, handler in [
        (bpy.app.handlers.object_bake_complete, completed),
        (bpy.app.handlers.object_bake_cancel, cancelled),
    ]:
        if handler in collection:
            collection.remove(handler)


def tick() -> float | None:
    global _active, _steps, _native_done, _native_cancelled
    if _active is None or _steps is None:
        detach()
        return None
    job = _jobs[_active]
    if job.state == "running":
        if not _native_done or bpy.app.is_job_running("OBJECT_BAKE"):
            return 0.05
        if not _native_cancelled:
            job = job.model_copy(
                update={"completed_targets": job.completed_targets + 1}
            )
    try:
        if _native_cancelled:
            raise OperationError(
                "bake_cancelled", "Native baking failed or was cancelled"
            )
        _native_done = False
        job = job.model_copy(update={"state": "running"})
        next(_steps)
        return 0.05
    except StopIteration as done:
        job = job.model_copy(update={"result": done.value, "state": "completed"})
    except Exception as exc:
        if isinstance(exc, OperationError):
            log.info("Native bake job failed: %s", exc)
        else:
            log.exception("Native bake job failed")
        job = job.model_copy(update={"state": "failed", "error": str(exc)})
    finally:
        _jobs[job.job_id] = job
        if job.state in {"completed", "failed"}:
            try:
                _steps.close()
            finally:
                _steps = None
                _active = None
                _native_done = _native_cancelled = False
                detach()
    return None


def start(arguments: BakeImageArguments) -> BakeJobStatus:
    global _active, _steps
    if (
        busy()
        or bpy.app.is_job_running("OBJECT_BAKE")
        or bpy.app.is_job_running("RENDER")
    ):
        raise OperationError("adapter_busy", "A native render or bake is running")
    if bpy.app.background or bpy.context.window is None:
        raise OperationError(
            "invalid_context",
            "Native asynchronous baking needs the visible application's event loop",
        )
    if bpy.data.images.get(arguments.name) is not None:
        raise OperationError("image_exists", "Choose a new image name")
    while len(_jobs) >= 4:
        del _jobs[next(iter(_jobs))]
    identifier = uuid4().hex
    job = BakeJobStatus(
        job_id=identifier,
        image=arguments.name,
        state="queued",
        completed_targets=0,
        target_count=len(arguments.targets),
    )
    _jobs[identifier] = job
    _active = identifier
    _steps = bake.bake_steps(arguments, asynchronous=True)
    bpy.app.handlers.object_bake_complete.append(completed)
    bpy.app.handlers.object_bake_cancel.append(cancelled)
    bpy.app.timers.register(tick, first_interval=0.1)
    return job.model_copy(deep=True)


def status(identifier: str) -> BakeJobStatus:
    if identifier not in _jobs:
        raise OperationError(
            "bake_job_not_found",
            "Job is absent or expired; the latest four jobs persist until reload",
        )
    return _jobs[identifier].model_copy(deep=True)
