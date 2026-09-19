"""Render-specific async service in the networking process; never imports bpy."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import uuid4

from tyvrana_protocol import (
    ArtifactDescriptor,
    Message,
    OperationFailure,
    OperationRequest,
)

from . import render_output
from .artifacts import ArtifactSpool
from .errors import OperationError
from .models import RenderArguments, RenderResult
from .operations import Response, SceneBackend, execute
from .render_models import (
    TERMINAL,
    RenderJobArguments,
    RenderJobError,
    RenderJobStatus,
    RenderStatusArguments,
)

log = logging.getLogger(__name__)
OPERATIONS = frozenset(
    {
        "blender.render.image",
        "blender.render.status",
        "blender.render.cancel",
        "blender.render.result",
    }
)
MAX_HISTORY = 16
MAX_RESULTS = 4


def utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    status: RenderJobStatus
    arguments: RenderArguments
    directory: Path
    prepared: asyncio.Future[Response]
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    created: float = field(default_factory=time.monotonic)
    task: asyncio.Task[None] | None = None


class RenderJobs:
    def __init__(self, spool: Path, send: Callable[[Message], Awaitable[None]]) -> None:
        self.spool = spool
        self.send = send
        self.jobs: dict[str, Job] = {}
        self.active: str | None = None
        self.closed = False

    def operation_allowed(self, operation: str) -> bool:
        return True

    def lookup(self, identifier: str) -> Job:
        try:
            return self.jobs[identifier]
        except KeyError:
            raise OperationError(
                "render_job_not_found",
                "Job absent or evicted; latest 16 records last until host/reload",
            ) from None

    def observe(self, job: Job) -> RenderJobStatus:
        elapsed = job.status.elapsed_seconds
        if job.status.state not in TERMINAL:
            elapsed = time.monotonic() - job.created
        return job.status.model_copy(
            update={"elapsed_seconds": round(elapsed, 3)}, deep=True
        )

    def change(self, job: Job, **updates: object) -> None:
        old = job.changed
        job.status = job.status.model_copy(
            update={**updates, "revision": job.status.revision + 1}
        )
        job.changed = asyncio.Event()
        old.set()

    def render(self, arguments: RenderArguments, request_id: str) -> RenderJobStatus:
        if self.active is not None or self.closed:
            raise OperationError(
                "adapter_busy", "One render job is already active; wait or cancel it"
            )
        while len(self.jobs) >= MAX_HISTORY:
            self.evict(next(iter(self.jobs)))
        identifier = uuid4().hex
        directory = self.spool / "jobs" / identifier
        directory.mkdir(parents=True)
        job = Job(
            RenderJobStatus(
                job_id=identifier,
                state="queued",
                submitted_at=utc(),
                width=arguments.width,
                height=arguments.height,
                format=arguments.format,
                bit_depth=arguments.bit_depth,
                color_mode=arguments.color_mode,
                frame_count=len(arguments.frames or [0]),
            ),
            arguments,
            directory,
            asyncio.get_running_loop().create_future(),
        )
        self.jobs[identifier] = job
        self.active = identifier
        job.task = asyncio.create_task(self.run(job))
        return self.observe(job)

    def evict(self, identifier: str) -> None:
        job = self.jobs.pop(identifier)
        shutil.rmtree(job.directory, ignore_errors=True)

    def accept_prepared(self, response: Response) -> bool:
        job = self.jobs.get(response.request_id)
        if job is None:
            return False
        if not job.prepared.done():
            job.prepared.set_result(response)
        return True

    def render_status(self, arguments: RenderStatusArguments) -> RenderJobStatus:
        identifier = arguments.job_id or self.active or next(reversed(self.jobs), None)
        if identifier is None:
            raise OperationError(
                "render_job_not_found",
                "No retained render jobs in this host generation",
            )
        return self.observe(self.lookup(identifier))

    def render_cancel(self, arguments: RenderJobArguments) -> RenderJobStatus:
        job = self.lookup(arguments.job_id)
        if job.status.state not in TERMINAL and job.status.state != "cancel_requested":
            self.change(job, state="cancel_requested")
            (job.directory / "cancel.request").touch()
        return self.observe(job)

    def render_result(
        self, arguments: RenderJobArguments
    ) -> tuple[RenderJobStatus, ArtifactDescriptor]:
        job = self.lookup(arguments.job_id)
        if job.status.state != "succeeded" or not job.status.result_available:
            raise OperationError(
                "render_result_unavailable",
                "Successful retained output is unavailable; inspect job status",
            )
        # Fresh transfer copy: networking deletes it on delivery/failure; the
        # bounded retained source is independent and remains retryable.
        spool = ArtifactSpool(self.spool)
        try:
            with spool.reserve(render_output.media_type(job.arguments)) as (
                identifier,
                path,
            ):
                shutil.copyfile(
                    job.directory
                    / (job.status.job_id + render_output.suffix(job.arguments)),
                    path,
                )
                descriptor = spool.describe(
                    identifier,
                    render_output.media_type(job.arguments),
                    maximum=job.arguments.budget.max_artifact_bytes,
                )
                return self.observe(job), descriptor
        except Exception as exc:
            raise OperationError(
                "artifact_unavailable",
                "Could not stage render artifact; retry retrieval",
            ) from exc

    async def wait(self, job: Job, revision: int, seconds: float) -> None:
        if (
            job.status.state in TERMINAL
            or job.status.revision != revision
            or seconds == 0
        ):
            return
        event = job.changed
        try:
            async with asyncio.timeout(seconds):
                await event.wait()
        except TimeoutError:
            pass

    def begin(self, request: OperationRequest) -> Response:
        return execute(cast(SceneBackend, self), request)

    async def handle(
        self, request: OperationRequest, response: Response | None = None
    ) -> Response:
        response = self.begin(request) if response is None else response
        if isinstance(response, OperationFailure):
            return response
        status = RenderJobStatus.model_validate(response.result)
        job = self.lookup(status.job_id)
        if request.operation == "blender.render.image":
            deadline = time.monotonic() + job.arguments.wait_seconds
            while job.status.state not in TERMINAL:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await self.wait(job, job.status.revision, remaining)
            if job.status.state == "succeeded":
                # Reuse normal typed result dispatch, without submitting again.
                return execute(
                    cast(SceneBackend, self),
                    request.model_copy(
                        update={
                            "operation": "blender.render.result",
                            "arguments": {"job_id": status.job_id},
                        }
                    ),
                )
        elif request.operation == "blender.render.status":
            args = RenderStatusArguments.model_validate(request.arguments)
            await self.wait(
                job,
                args.after_revision
                if args.after_revision is not None
                else status.revision,
                args.wait_seconds,
            )
        return response.model_copy(
            update={"result": self.observe(job).model_dump(mode="json")}
        )

    @staticmethod
    def is_cancelled(job: Job) -> bool:
        return job.status.state == "cancel_requested"

    def persist(self, job: Job, destination: str) -> None:
        target = Path(destination)
        if target.is_symlink() or not target.parent.is_dir():
            raise OperationError(
                "file_destination_invalid",
                "Output destination changed or is unavailable",
            )
        fd, temporary = tempfile.mkstemp(
            prefix=".tyvrana-render-",
            suffix=render_output.suffix(job.arguments),
            dir=target.parent,
        )
        os.close(fd)
        path = Path(temporary)
        try:
            shutil.copyfile(
                job.directory
                / (job.status.job_id + render_output.suffix(job.arguments)),
                path,
            )
            assert job.arguments.output is not None
            if job.arguments.output.overwrite:
                os.replace(path, target)
            else:
                os.link(path, target)
            self.change(job, saved_filepath=str(target))
        finally:
            path.unlink(missing_ok=True)

    async def run(self, job: Job) -> None:
        identifier = job.status.job_id
        try:
            await self.send(
                OperationRequest(
                    type="operation.request",
                    request_id=identifier,
                    operation="blender.render.image",
                    arguments=job.arguments.model_dump(mode="json", exclude_unset=True),
                )
            )
            response = await job.prepared
            if isinstance(response, OperationFailure):
                raise OperationError(response.error.code, response.error.message)
            prepared = RenderJobStatus.model_validate(response.result)
            self.change(
                job,
                engine=prepared.engine,
                frame=prepared.frame,
                samples_requested=prepared.samples_requested,
                host_pid=prepared.host_pid,
                host_background=prepared.host_background,
                document_filepath=prepared.document_filepath,
                scene_name=prepared.scene_name,
            )
            config = json.loads((job.directory / "config.json").read_text())
            last_progress = None
            while not (job.directory / "completed.json").is_file():
                progress = job.directory / "progress.json"
                if progress.is_file():
                    current = json.loads(progress.read_text())
                    if current != last_progress:
                        updates: dict[str, object] = {
                            "completed_frames": current["completed_frames"]
                        }
                        if not self.is_cancelled(job):
                            updates.update(
                                state="running",
                                started_at=current.get("started_at")
                                or job.status.started_at
                                or utc(),
                            )
                        self.change(job, **updates)
                        last_progress = current
                # The connected host owns native work. Never signal or kill it.
                await asyncio.sleep(0.05)
            if self.is_cancelled(job):
                return
            report = json.loads((job.directory / "completed.json").read_text())
            if "error" in report:
                raise OperationError(
                    report["error"]["code"], report["error"]["message"]
                )
            # Validate bytes again in the transfer-owning process.
            spool = ArtifactSpool(job.directory)
            descriptor = spool.describe(
                identifier,
                render_output.media_type(job.arguments),
                maximum=job.arguments.budget.max_artifact_bytes,
            )
            if config.get("destination"):
                self.persist(job, config["destination"])
            retained = [j for j in self.jobs.values() if j.status.result_available]
            for older in retained[: max(0, len(retained) - MAX_RESULTS + 1)]:
                (
                    older.directory
                    / (older.status.job_id + render_output.suffix(older.arguments))
                ).unlink(missing_ok=True)
                self.change(older, result_available=False)
            self.change(
                job,
                state="succeeded",
                result_available=True,
                byte_size=descriptor.byte_size,
                sha256=descriptor.sha256,
                render_seconds=report["render_seconds"],
                started_at=report.get("started_at") or job.status.started_at,
                completed_frames=report.get("completed_frames", 1),
                color_management=RenderResult.model_validate(
                    report["output"]
                ).color_management
                if "output" in report
                else None,
                output_channels=report.get("output", {}).get("output_channels", []),
                width=report.get("output", {}).get("width", job.arguments.width),
                height=report.get("output", {}).get("height", job.arguments.height),
                inspection_tiles=RenderResult.model_validate(
                    report["output"]
                ).inspection_tiles
                if "output" in report
                else [],
            )
        except asyncio.CancelledError:
            (job.directory / "cancel.request").touch()
            raise
        except Exception as exc:
            error = exc.error if isinstance(exc, OperationError) else None
            if error is None:
                log.exception("Render job failed")
            if job.status.state != "cancel_requested":
                self.change(
                    job,
                    state="failed",
                    error=RenderJobError(
                        code=error.code[:80] if error else "render_failed",
                        message=error.message[:500]
                        if error
                        else "Render job failed; see application log",
                    ),
                )
        finally:
            if self.is_cancelled(job):
                self.change(job, state="cancelled")
            self.change(
                job, completed_at=utc(), elapsed_seconds=time.monotonic() - job.created
            )
            for path in job.directory.iterdir():
                if (
                    path.name != identifier + render_output.suffix(job.arguments)
                    or job.status.state != "succeeded"
                ):
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
            self.active = None

    async def close(self) -> None:
        self.closed = True
        tasks = [j.task for j in self.jobs.values() if j.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        shutil.rmtree(self.spool / "jobs", ignore_errors=True)
        self.jobs.clear()
        self.active = None
