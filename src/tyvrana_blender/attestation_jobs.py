"""Read-only attestation using the shared incremental native scheduler."""

from collections.abc import Generator
from typing import Any, cast
from uuid import uuid4

import bpy  # type: ignore[import-not-found]

from . import attestation
from .attestation_model import AttestationJobStatus, DocumentAttestationResult
from .errors import OperationError
from .native_jobs import NativeJobs

_jobs: NativeJobs[AttestationJobStatus] = NativeJobs()
busy = _jobs.busy
status = _jobs.status
tick = _jobs.tick
cancel = _jobs.cancel
shutdown = _jobs.shutdown


def guarded_steps() -> Generator[dict[str, Any], None, DocumentAttestationResult]:
    bpy.context.view_layer.update()
    session = attestation.identity()
    changed = False

    def invalidate(*args: Any) -> None:
        nonlocal changed
        changed = True

    handlers = (
        bpy.app.handlers.depsgraph_update_post,
        bpy.app.handlers.frame_change_post,
        bpy.app.handlers.load_pre,
        bpy.app.handlers.undo_pre,
        bpy.app.handlers.redo_pre,
    )
    for group in handlers:
        group.append(invalidate)
    steps = attestation.inspect_steps(max_seconds=120)
    try:
        while True:
            if changed or session != attestation.identity():
                raise OperationError(
                    "application_changed",
                    "Document changed during attestation; no digest is valid",
                )
            try:
                progress = next(steps)
            except StopIteration as done:
                bpy.context.view_layer.update()
                if changed or session != attestation.identity():
                    raise OperationError(
                        "application_changed",
                        "Document changed during attestation; no digest is valid",
                    ) from None
                return cast(DocumentAttestationResult, done.value)
            yield progress
    finally:
        steps.close()
        for group in handlers:
            if invalidate in group:
                group.remove(invalidate)


def start() -> DocumentAttestationResult:
    if _jobs.active is not None:
        current = _jobs.status(_jobs.active)
    else:
        current = _jobs.start(
            AttestationJobStatus(job_id=uuid4().hex, state="queued"), guarded_steps()
        )
    return DocumentAttestationResult(current.model_dump(mode="json"))
