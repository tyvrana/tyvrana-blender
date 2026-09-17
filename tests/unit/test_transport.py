import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from tyvrana_protocol import (
    AdapterRegistration,
    CancelRequest,
    Message,
    OperationRequest,
    OperationSuccess,
    decode_message,
    encode_message,
)
from websockets.asyncio.server import ServerConnection, serve

from tyvrana_blender.models import ConnectionConfig
from tyvrana_blender.operations import registration
from tyvrana_blender.transport import MAX_FRAME, WorkerProcess
from tyvrana_blender.worker import Backoff


@asynccontextmanager
async def worker(
    catalog: AdapterRegistration | None = None,
) -> AsyncIterator[
    tuple[WorkerProcess, asyncio.Queue[ServerConnection], list[Message]]
]:
    connected: asyncio.Queue[ServerConnection] = asyncio.Queue()

    async def accept(socket: ServerConnection) -> None:
        data = await socket.recv()
        message = decode_message(data.encode() if isinstance(data, str) else data)
        assert isinstance(data, str)
        assert isinstance(message, AdapterRegistration)
        assert message.application == "blender"
        connected.put_nowait(socket)
        await socket.wait_closed()

    async with serve(
        accept, "127.0.0.1", 0, close_timeout=0.1, max_size=MAX_FRAME
    ) as server:
        process = WorkerProcess(
            ConnectionConfig(port=server.sockets[0].getsockname()[1]),
            catalog or registration("test-process", "5.2.1 LTS", ""),
        )
        messages: list[Message] = []

        async def pump() -> None:
            while True:
                messages.extend(process.poll())
                await asyncio.sleep(0.01)

        task = asyncio.create_task(pump())
        try:
            yield process, connected, messages
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Stopping synchronously here would block the fake server's close handshake.
            if process.process.stdin:
                process.process.stdin.close()
            async with asyncio.timeout(5):
                while process.process.poll() is None:  # noqa: ASYNC110 - Observe an external process.
                    await asyncio.sleep(0.01)
            process.stop()
            assert process.process.returncode == 0
    assert not process.spool.root.exists()


async def until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110 - Observe messages arriving across process pipes.
            await asyncio.sleep(0.01)


async def test_project_refresh_waits_for_pending_response_and_keeps_worker() -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        pid = process.process.pid
        request = OperationRequest(
            type="operation.request",
            request_id="save",
            operation="blender.file.save",
            arguments={},
        )
        await socket.send(encode_message(request).decode())
        await until(lambda: request in messages)
        process.send(registration("test-process", "5.2.1 LTS", "/project/model.blend"))
        # Hold execution longer than the maintenance tick. Refresh must not drop it.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(connected.get(), 0.7)
        response = OperationSuccess(
            type="operation.success", request_id="save", result={"saved": True}
        )
        process.send(response)
        wire = await asyncio.wait_for(socket.recv(), 5)
        assert isinstance(wire, str)
        assert decode_message(wire.encode()) == response
        following = await asyncio.wait_for(connected.get(), 5)
        assert following is not socket
        assert process.process.pid == pid


async def test_real_worker_round_trip_and_cancellation_suppresses_late_reply() -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        request = OperationRequest(
            type="operation.request",
            request_id="one",
            operation="blender.scene.inspect",
            arguments={"value": [None, "[]"]},
        )
        await socket.send(encode_message(request).decode())
        await until(lambda: request in messages)
        process.send(
            OperationSuccess(
                type="operation.success", request_id="one", result=request.arguments
            )
        )
        reply = await asyncio.wait_for(socket.recv(), 5)
        assert isinstance(reply, str)
        assert decode_message(reply.encode()) == OperationSuccess(
            type="operation.success", request_id="one", result=request.arguments
        )
        cancelled = request.model_copy(update={"request_id": "cancelled"})
        await socket.send(encode_message(cancelled).decode())
        cancel = CancelRequest(type="operation.cancel", request_id="cancelled")
        await socket.send(encode_message(cancel).decode())
        await until(lambda: cancel in messages)
        process.send(
            OperationSuccess(
                type="operation.success", request_id="cancelled", result="late"
            )
        )
        following = request.model_copy(update={"request_id": "following"})
        await socket.send(encode_message(following).decode())
        await until(lambda: following in messages)
        process.send(
            OperationSuccess(
                type="operation.success", request_id="following", result="current"
            )
        )
        wire = await asyncio.wait_for(socket.recv(), 5)
        assert isinstance(wire, str)
        received = decode_message(wire.encode())
        assert isinstance(received, OperationSuccess)
        assert received.request_id == "following"


@pytest.mark.parametrize(
    "wire",
    [
        b"not-json",
        encode_message(
            OperationSuccess(
                type="operation.success", request_id="wrong-direction", result=None
            )
        ),
    ],
)
async def test_malformed_core_messages_close_connection_and_reconnect(
    wire: bytes,
) -> None:
    async with worker() as (_, connected, messages):
        first = await asyncio.wait_for(connected.get(), 5)
        await first.send(wire.decode())
        await asyncio.wait_for(first.wait_closed(), 5)
        assert first.close_code == 1008
        second = await asyncio.wait_for(connected.get(), 5)
        assert second is not first


def test_backoff_is_bounded_and_resets_only_after_healthy_connection() -> None:
    backoff = Backoff()
    assert [backoff.next_delay() for _ in range(8)] == [0.25, 0.5, 1, 2, 4, 8, 8, 8]
    assert backoff.next_delay(healthy=True) == 0.25


def test_stop_before_initial_timer_tick_is_clean() -> None:
    process = WorkerProcess(
        ConnectionConfig(), registration("test-process", "5.2.1 LTS", "")
    )
    process.stop()
    process.stop()
    assert process.process.returncode == 0
    assert not process.spool.root.exists()


def test_stop_discards_an_incomplete_parent_frame() -> None:
    process = WorkerProcess(
        ConnectionConfig(port=1), registration("test-process", "5.2.1 LTS", "")
    )
    try:
        # Isolate EOF on an incomplete frame, independent of catalog/pipe size.
        process._outgoing.clear()
        assert process.process.stdin is not None
        os.write(process.process.stdin.fileno(), b'{"type":')
    finally:
        process.stop()
    assert process.process.returncode == 0
    assert not process.spool.root.exists()


async def test_large_catalog_crosses_parent_worker_and_socket_boundaries() -> None:
    message = registration("large-catalog", "5.2.1 LTS", "")
    template = message.operations[0]
    message = message.model_copy(
        update={
            "resource_inspection": None,
            "operations": tuple(
                template.model_copy(
                    update={
                        "name": f"blender.inspect_{i}",
                        "arguments_schema": {
                            "type": "object",
                            "description": "x" * 100000,
                        },
                    }
                )
                for i in range(20)
            ),
        }
    )
    assert 1024 * 1024 < len(encode_message(message)) < MAX_FRAME
    async with worker(message) as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        request = OperationRequest(
            type="operation.request",
            request_id="after-catalog",
            operation="blender.inspect_0",
            arguments={},
        )
        await socket.send(encode_message(request).decode())
        await until(lambda: request in messages)
        process.send(
            OperationSuccess(
                type="operation.success",
                request_id=request.request_id,
                result={"ok": True},
            )
        )
        response = await asyncio.wait_for(socket.recv(), 5)
        assert isinstance(response, str)
        decoded = decode_message(response.encode())
        assert isinstance(decoded, OperationSuccess)
        assert decoded.request_id == request.request_id


def test_oversized_catalog_is_rejected_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = registration("oversized", "5.2.1 LTS", "")
    template = message.operations[0]
    message = message.model_copy(
        update={
            "resource_inspection": None,
            "operations": tuple(
                template.model_copy(
                    update={
                        "name": f"blender.inspect_{i}",
                        "arguments_schema": {
                            "type": "object",
                            "description": "x" * 100000,
                        },
                    }
                )
                for i in range(43)
            ),
        }
    )

    def unexpected_spawn(*args: object, **kwargs: object) -> None:
        pytest.fail("Oversized registration must not start a worker")

    monkeypatch.setattr("tyvrana_blender.transport.subprocess.Popen", unexpected_spawn)
    with pytest.raises(ValueError, match="Registration exceeds"):
        WorkerProcess(ConnectionConfig(), message)
