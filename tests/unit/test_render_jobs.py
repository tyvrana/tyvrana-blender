"""Render lifecycle, event waits, ownership and races without Blender."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tyvrana_protocol import (
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
)

from tyvrana_blender import render_jobs
from tyvrana_blender.artifacts import ArtifactSpool
from tyvrana_blender.models import RenderArguments
from tyvrana_blender.render_jobs import RenderJobs
from tyvrana_blender.render_models import RenderJobArguments, RenderStatusArguments


def request(name: str, **arguments: Any) -> OperationRequest:
    return OperationRequest(
        type="operation.request",
        request_id="client",
        operation="blender.render." + name,
        arguments=arguments,
    )


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[RenderJobs]:
    tasks: list[asyncio.Task[None]] = []

    async def complete(directory: Path) -> None:
        (directory / "progress.json").write_text(
            json.dumps({"running": True, "completed_frames": 0})
        )
        await asyncio.sleep(0.1)
        (directory / (directory.name + ".png")).write_bytes(b"png bytes")
        (directory / "completed.json").write_text(json.dumps({"render_seconds": 0.05}))

    async def send(message: Any) -> None:
        job = jobs.jobs[message.request_id]
        (job.directory / "config.json").write_text(json.dumps({"destination": None}))
        jobs.accept_prepared(
            OperationSuccess(
                type="operation.success",
                request_id=message.request_id,
                result=job.status.model_copy(update={"host_pid": 123}).model_dump(
                    mode="json"
                ),
            )
        )
        tasks.append(asyncio.create_task(complete(job.directory)))

    jobs = RenderJobs(tmp_path, send)
    try:
        yield jobs
    finally:
        await asyncio.gather(*tasks)
        await jobs.close()


async def finish(service: RenderJobs, identifier: str) -> None:
    task = service.jobs[identifier].task
    assert task is not None
    async with asyncio.timeout(5):
        await task


async def test_success_wait_compact_status_and_retryable_result(
    service: RenderJobs,
) -> None:
    try:
        response = await service.handle(request("image", wait_seconds=0))
        assert isinstance(response, OperationSuccess) and not response.artifacts
        assert isinstance(response.result, dict)
        identifier = response.result["job_id"]
        job = service.lookup(str(identifier))
        await service.wait(job, job.status.revision, 1)
        await finish(service, job.status.job_id)
        status = service.render_status(RenderStatusArguments(job_id=job.status.job_id))
        assert status.state == "succeeded" and status.started_at and status.completed_at
        assert len(status.model_dump_json()) < 1500
        assert status.host_pid == 123 and status.execution == "connected_host"
        first = service.render_result(RenderJobArguments(job_id=status.job_id))[1]
        ArtifactSpool(service.spool).release((first,))
        second = service.render_result(RenderJobArguments(job_id=status.job_id))[1]
        assert first.sha256 == second.sha256 and first.artifact_id != second.artifact_id
        assert (
            service.render_cancel(RenderJobArguments(job_id=status.job_id)).state
            == "succeeded"
        )
    finally:
        await service.close()


async def test_short_submit_returns_inline_and_invalid_bounds(
    service: RenderJobs,
) -> None:
    try:
        result = await service.handle(request("image", wait_seconds=1))
        assert isinstance(result, OperationSuccess) and len(result.artifacts) == 1
        cases: list[tuple[str, dict[str, Any]]] = [
            ("image", {"wait_seconds": 6}),
            ("status", {"job_id": "a" * 32, "wait_seconds": 21}),
            ("cancel", {"job_id": "b" * 32}),
            ("result", {"job_id": "../"}),
        ]
        for op, args in cases:
            result = await service.handle(request(op, **args))
            assert isinstance(result, OperationFailure)
    finally:
        await service.close()


async def test_queued_cancel_and_admission_guard(service: RenderJobs) -> None:
    entered = asyncio.Event()
    resume = asyncio.Event()
    original = service.send

    async def held(message: Any) -> None:
        entered.set()
        await resume.wait()
        await original(message)

    service.send = held
    try:
        status = service.render(RenderArguments(wait_seconds=0), "ignored")
        await entered.wait()
        with pytest.raises(Exception, match="already active"):
            service.render(RenderArguments(), "other")
        args = RenderJobArguments(job_id=status.job_id)
        assert service.render_cancel(args).state == "cancel_requested"
        assert service.render_cancel(args).state == "cancel_requested"
        resume.set()
        await finish(service, status.job_id)
        assert service.render_cancel(args).state == "cancelled"
        assert list(service.jobs[status.job_id].directory.iterdir()) == []
        assert service.active is None
    finally:
        resume.set()
        await service.close()


async def test_running_cancel_recovery_and_no_false_artifact(
    service: RenderJobs,
) -> None:
    try:
        status = service.render(RenderArguments(wait_seconds=0), "unused")
        job = service.jobs[status.job_id]
        while job.status.state == "queued":
            await service.wait(job, job.status.revision, 1)
        assert job.status.state == "running"
        service.render_cancel(RenderJobArguments(job_id=status.job_id))
        assert service.observe(job).state == "cancel_requested"
        assert job.task is not None
        assert service.active == status.job_id and not job.task.done()
        await finish(service, status.job_id)
        assert (
            service.observe(job).state == "cancelled"
            and not job.status.result_available
        )
        result = await service.handle(request("result", job_id=status.job_id))
        assert isinstance(result, OperationFailure)
        result = await service.handle(request("image", wait_seconds=1))
        assert isinstance(result, OperationSuccess) and result.artifacts
    finally:
        await service.close()


async def test_wait_timeout_and_cancellation_do_not_cancel_job(
    service: RenderJobs,
) -> None:
    try:
        service.send = AsyncMock()
        status = service.render(RenderArguments(wait_seconds=0), "unused")
        job = service.jobs[status.job_id]
        await service.wait(job, status.revision, 0.001)
        waiter = asyncio.create_task(service.wait(job, status.revision, 20))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert job.status.state == "queued"
        await service.wait(job, 999, 20)  # stale revision returns immediately
    finally:
        await service.close()


async def test_preparation_failure_and_artifact_store_failure(
    service: RenderJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.send

    async def fail(message: Any) -> None:
        service.accept_prepared(
            OperationFailure(
                type="operation.failure",
                request_id=message.request_id,
                error=ProtocolError(code="no_camera", message="Choose a camera"),
            )
        )

    try:
        service.send = fail
        status = service.render(RenderArguments(), "unused")
        await finish(service, status.job_id)
        assert service.jobs[status.job_id].status.error.code == "no_camera"  # type: ignore[union-attr]
        service.send = original
        with monkeypatch.context() as scope:

            def broken(*args: Any) -> Any:
                raise OSError("injected storage failure")

            scope.setattr(ArtifactSpool, "describe", broken)
            status = service.render(RenderArguments(), "unused")
            await finish(service, status.job_id)
            assert service.jobs[status.job_id].status.state == "failed"
            assert not list(service.jobs[status.job_id].directory.iterdir())
        result = await service.handle(request("image", wait_seconds=1))
        assert isinstance(result, OperationSuccess) and result.artifacts
    finally:
        await service.close()


async def test_history_and_result_retention_are_bounded(
    service: RenderJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_jobs, "MAX_HISTORY", 5)
    monkeypatch.setattr(render_jobs, "MAX_RESULTS", 2)
    identifiers = []
    try:
        for _ in range(7):
            status = service.render(RenderArguments(wait_seconds=0), "unused")
            identifiers.append(status.job_id)
            await finish(service, status.job_id)
        assert len(service.jobs) == 5
        assert sum(j.status.result_available for j in service.jobs.values()) == 2
        assert service.jobs[identifiers[-3]].status.state == "succeeded"
        assert not service.jobs[identifiers[-3]].status.result_available
        result = await service.handle(request("status", job_id=identifiers[0]))
        assert (
            isinstance(result, OperationFailure)
            and result.error.code == "render_job_not_found"
        )
    finally:
        await service.close()


async def test_cancel_before_result_response_task_starts_releases_copy(
    service: RenderJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tyvrana_blender.operations import registration
    from tyvrana_blender.worker import NetworkClient

    try:
        result = await service.handle(request("image", wait_seconds=1))
        assert isinstance(result, OperationSuccess)
        assert isinstance(result.result, dict)
        ArtifactSpool(service.spool).release(result.artifacts)
        output = AsyncMock()
        client = NetworkClient(
            "ws://unused", registration("test", "5.2.1", ""), output, service.spool
        )
        client.renders = service
        req = request("result", job_id=result.result["job_id"])
        client.pending.add(req.request_id)
        client.start_render_response(AsyncMock(), req)
        assert len(list(service.spool.glob("*.png"))) == 1
        client.cancel_request(req.request_id)
        await client.cleanup()
        assert not list(service.spool.glob("*.png"))
        assert not client._requests
        assert service.render_status(RenderStatusArguments()).result_available
    finally:
        await service.close()
