"""Incremental native form batches with atomic commit and cancellation."""

import logging
import time
from collections.abc import Generator
from uuid import uuid4

from tyvrana_protocol import ProtocolError

from . import forms
from .errors import OperationError
from .form_models import (
    FormConfigureArguments,
    FormCreateArguments,
    FormJobStatus,
    FormResult,
)

log = logging.getLogger(__name__)
_jobs: dict[str, FormJobStatus] = {}
_active: str | None = None
_steps: Generator[int, None, FormResult] | None = None
_started = 0.0


def busy() -> bool:
    return _active is not None


def start(arguments: FormCreateArguments | FormConfigureArguments) -> FormJobStatus:
    global _active, _steps, _started
    if busy():
        raise OperationError(
            "adapter_busy", "A form job is active; inspect or cancel it"
        )
    steps = (
        forms.create_steps(arguments)
        if isinstance(arguments, FormCreateArguments)
        else forms.configure_steps(arguments)
    )
    # Validate destinations/ownership before accepting a job. No geometry yet.
    next(steps)
    while len(_jobs) >= 4:
        del _jobs[next(iter(_jobs))]
    identifier = uuid4().hex
    job = FormJobStatus(
        job_id=identifier, state="queued", total_forms=len(arguments.forms)
    )
    _jobs[identifier] = job
    _active, _steps, _started = identifier, steps, time.perf_counter()
    return job.model_copy(deep=True)


def status(identifier: str) -> FormJobStatus:
    if identifier not in _jobs:
        raise OperationError(
            "form_job_not_found", "Latest four jobs persist until host reload"
        )
    return _jobs[identifier].model_copy(deep=True)


def tick() -> None:
    global _active, _steps
    if _active is None or _steps is None:
        return
    job = _jobs[_active]
    deadline = time.perf_counter() + 0.05
    try:
        while True:
            prepared = next(_steps)
            job = job.model_copy(
                update={"state": "running", "prepared_forms": prepared}
            )
            if time.perf_counter() >= deadline:
                break
    except StopIteration as done:
        job = job.model_copy(
            update={
                "state": "completed",
                "prepared_forms": job.total_forms,
                "result": done.value,
            }
        )
    except Exception as exc:
        if isinstance(exc, OperationError):
            error = exc.error
        else:
            log.exception("Native form job failed")
            error = ProtocolError(
                code="form_job_failed",
                message="Native form job failed; inspect application log",
            )
        job = job.model_copy(update={"state": "failed", "error": error})
    finally:
        job = job.model_copy(update={"elapsed_seconds": time.perf_counter() - _started})
        _jobs[job.job_id] = job
        if job.state in {"completed", "failed"}:
            _steps.close()
            _steps, _active = None, None


def cancel(identifier: str) -> FormJobStatus:
    global _active, _steps
    job = status(identifier)
    if identifier == _active:
        assert _steps is not None
        _steps.close()
        _steps, _active = None, None
        job = job.model_copy(
            update={
                "state": "cancelled",
                "elapsed_seconds": time.perf_counter() - _started,
            }
        )
        _jobs[identifier] = job
    return job.model_copy(deep=True)


def shutdown() -> None:
    if _active:
        cancel(_active)
    _jobs.clear()
