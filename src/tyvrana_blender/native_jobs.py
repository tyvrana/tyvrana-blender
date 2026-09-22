"""Shared cooperative main-thread scheduler for incremental native operations."""

import logging
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from tyvrana_protocol import ProtocolError

from .errors import OperationError

log = logging.getLogger(__name__)


@dataclass
class NativeJob[S: BaseModel]:
    status: S
    steps: Generator[dict[str, Any], None, Any]
    started: float


class NativeJobs[S: BaseModel]:
    def __init__(self) -> None:
        self.jobs: dict[str, NativeJob[S]] = {}
        self.active: str | None = None

    def busy(self) -> bool:
        return self.active is not None

    def start(self, status: S, steps: Generator[dict[str, Any], None, Any]) -> S:
        if self.busy():
            steps.close()
            raise OperationError("adapter_busy", "An incremental native job is active")
        while len(self.jobs) >= 4:
            del self.jobs[next(iter(self.jobs))]
        identifier = str(status.model_dump()["job_id"])
        self.jobs[identifier] = NativeJob(status, steps, time.perf_counter())
        self.active = identifier
        return self.status(identifier)

    def status(self, identifier: str) -> S:
        if identifier not in self.jobs:
            raise OperationError(
                "native_job_not_found", "Latest four jobs persist until reload"
            )
        return self.jobs[identifier].status.model_copy(deep=True)

    def tick(self) -> None:
        if self.active is None:
            return
        job = self.jobs[self.active]
        updates: dict[str, Any] = {}
        deadline = time.perf_counter() + 0.05
        try:
            while True:
                updates.update(next(job.steps))
                updates["state"] = "running"
                if time.perf_counter() >= deadline:
                    break
        except StopIteration as done:
            updates.update(state="completed", result=done.value)
        except Exception as exc:
            if isinstance(exc, OperationError):
                error = exc.error
            else:
                log.exception("Incremental native job failed")
                error = ProtocolError(
                    code="native_job_failed",
                    message="Native job failed; inspect application log",
                )
            updates.update(state="failed", error=error)
        finally:
            updates.update(
                elapsed_seconds=time.perf_counter() - job.started,
                revision=getattr(job.status, "revision", 0) + 1,
            )
            job.status = job.status.model_copy(update=updates)
            if job.status.model_dump()["state"] in {"completed", "failed"}:
                job.steps.close()
                self.active = None

    def cancel(self, identifier: str) -> S:
        self.status(identifier)
        job = self.jobs[identifier]
        if identifier == self.active:
            job.steps.close()
            job.status = job.status.model_copy(
                update={
                    "state": "cancelled",
                    "elapsed_seconds": time.perf_counter() - job.started,
                    "revision": getattr(job.status, "revision", 0) + 1,
                }
            )
            self.active = None
        return self.status(identifier)

    def shutdown(self) -> None:
        if self.active is not None:
            self.cancel(self.active)
        self.jobs.clear()
