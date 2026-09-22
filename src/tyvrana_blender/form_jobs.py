"""Incremental form authoring on the shared native job scheduler."""

from collections.abc import Generator
from typing import Any, cast
from uuid import uuid4

from . import forms
from .errors import OperationError
from .form_models import (
    FormConfigureArguments,
    FormCreateArguments,
    FormJobStatus,
    FormResult,
)
from .native_jobs import NativeJobs

_jobs: NativeJobs[FormJobStatus] = NativeJobs()
busy = _jobs.busy
status = _jobs.status
tick = _jobs.tick
cancel = _jobs.cancel
shutdown = _jobs.shutdown


def start(arguments: FormCreateArguments | FormConfigureArguments) -> FormJobStatus:
    if busy():
        raise OperationError(
            "adapter_busy", "A form job is active; inspect or cancel it"
        )
    steps = (
        forms.create_steps(arguments)
        if isinstance(arguments, FormCreateArguments)
        else forms.configure_steps(arguments)
    )
    next(steps)

    def progress() -> Generator[dict[str, Any], None, FormResult]:
        try:
            while True:
                try:
                    prepared = next(steps)
                except StopIteration as done:
                    yield {"prepared_forms": len(arguments.forms)}
                    return cast(FormResult, done.value)
                yield {"prepared_forms": prepared}
        finally:
            steps.close()

    return _jobs.start(
        FormJobStatus(
            job_id=uuid4().hex, state="queued", total_forms=len(arguments.forms)
        ),
        progress(),
    )
