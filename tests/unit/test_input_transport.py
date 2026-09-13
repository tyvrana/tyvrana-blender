import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from tyvrana_protocol import (
    ArtifactAbort,
    ArtifactAccepted,
    ArtifactBegin,
    ArtifactComplete,
    ArtifactReady,
    CancelRequest,
    Message,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
    encode_artifact_chunk,
    encode_message,
)
from websockets.asyncio.server import ServerConnection

from tyvrana_blender.incoming import input_path

from .test_artifact_transport import receive
from .test_incoming import begin, request
from .test_transport import until, worker


async def send(socket: ServerConnection, message: Message) -> None:
    await socket.send(encode_message(message).decode())


async def transfer(
    socket: ServerConnection, message: ArtifactBegin, data: bytes
) -> None:
    await send(socket, message)
    assert await receive(socket) == ArtifactReady(
        type="artifact.ready", transfer_id=message.transfer_id
    )
    for offset in range(0, len(data), 65536):
        await socket.send(
            encode_artifact_chunk(
                message.transfer_id, offset, data[offset : offset + 65536]
            )
        )
    await send(
        socket,
        ArtifactComplete(type="artifact.complete", transfer_id=message.transfer_id),
    )
    assert await receive(socket) == ArtifactAccepted(
        type="artifact.accepted", transfer_id=message.transfer_id
    )


@pytest.mark.parametrize("size", [0, 1, 65536, 131089])
async def test_worker_input_bytes_before_operation_and_cleanup(size: int) -> None:
    data = (bytes(range(256)) * (size // 256 + 1))[:size]
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        item = begin(data)
        await transfer(socket, item, data)
        assert not any(isinstance(message, OperationRequest) for message in messages)
        path = input_path(
            process.spool.root, item.request_id, item.descriptor.artifact_id
        )
        assert path.read_bytes() == data
        operation = request(item)
        await send(socket, operation)
        await until(lambda: operation in messages)
        assert str(process.spool.root) not in operation.model_dump_json()
        assert all(not isinstance(message, bytes) for message in messages)
        process.send(
            OperationSuccess(
                type="operation.success", request_id=item.request_id, result="packed"
            )
        )
        assert isinstance(await receive(socket), OperationSuccess)
        await until(lambda: not list(process.spool.root.iterdir()))


async def test_concurrent_requests_reuse_and_multi_input_are_independent() -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        first = begin(b"a")
        second = begin(b"b")
        repeated = first.model_copy(
            update={"request_id": "another", "transfer_id": uuid4().hex}
        )
        for item, data in [(first, b"a"), (second, b"b"), (repeated, b"a")]:
            await transfer(socket, item, data)
        one, two = request(first, second), request(repeated, request_id="another")
        await send(socket, one)
        await send(socket, two)
        await until(lambda: one in messages and two in messages)
        process.send(
            OperationFailure(
                type="operation.failure",
                request_id=one.request_id,
                error=ProtocolError(
                    code="artifact_decode_failed", message="Cannot decode"
                ),
            )
        )
        assert isinstance(await receive(socket), OperationFailure)
        await until(lambda: len(list(process.spool.root.iterdir())) == 1)
        assert (
            input_path(
                process.spool.root, "another", first.descriptor.artifact_id
            ).read_bytes()
            == b"a"
        )
        process.send(
            OperationSuccess(
                type="operation.success", request_id=two.request_id, result="packed"
            )
        )
        assert isinstance(await receive(socket), OperationSuccess)
        await until(lambda: not list(process.spool.root.iterdir()))


@pytest.mark.parametrize(
    "failure", ["offset", "size", "hash", "incomplete", "oversize", "unknown"]
)
async def test_invalid_input_aborts_without_dispatch(failure: str) -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        item = begin(b"abc")
        if failure == "oversize":
            item = item.model_copy(
                update={
                    "descriptor": item.descriptor.model_copy(
                        update={"byte_size": 128 * 1024 * 1024 + 1}
                    )
                }
            )
        if failure != "unknown":
            await send(socket, item)
            if failure != "oversize":
                assert isinstance(await receive(socket), ArtifactReady)
        if failure in {"offset", "size", "hash", "unknown"}:
            await socket.send(
                encode_artifact_chunk(
                    item.transfer_id,
                    1 if failure == "offset" else 0,
                    b"abcd" if failure == "size" else b"abd",
                )
            )
        if failure in {"hash", "incomplete"}:
            await send(
                socket,
                ArtifactComplete(
                    type="artifact.complete", transfer_id=item.transfer_id
                ),
            )
        result = await receive(socket)
        assert isinstance(result, ArtifactAbort)
        assert str(process.spool.root) not in result.model_dump_json()
        await until(lambda: not list(process.spool.root.iterdir()))
        assert not any(isinstance(message, OperationRequest) for message in messages)
        following = request(request_id="following")
        await send(socket, following)
        await until(lambda: following in messages)


@pytest.mark.parametrize("stage", ["partial", "accepted", "queued"])
@pytest.mark.parametrize("stop", ["cancel", "abort", "disconnect"])
async def test_input_cancel_abort_disconnect_clean_every_stage(
    stage: str, stop: str
) -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        item = begin(b"abc")
        await send(socket, item)
        assert isinstance(await receive(socket), ArtifactReady)
        await socket.send(
            encode_artifact_chunk(
                item.transfer_id, 0, b"a" if stage == "partial" else b"abc"
            )
        )
        if stage != "partial":
            await send(
                socket,
                ArtifactComplete(
                    type="artifact.complete", transfer_id=item.transfer_id
                ),
            )
            assert isinstance(await receive(socket), ArtifactAccepted)
        if stage == "queued":
            operation = request(item)
            await send(socket, operation)
            await until(lambda: operation in messages)
        if stop == "disconnect":
            await socket.close()
        elif stop == "cancel":
            await send(
                socket,
                CancelRequest(type="operation.cancel", request_id=item.request_id),
            )
        else:
            await send(
                socket,
                ArtifactAbort(
                    type="artifact.abort",
                    transfer_id=item.transfer_id,
                    error=ProtocolError(code="cancelled", message="Cancelled"),
                ),
            )
        await until(lambda: not list(process.spool.root.iterdir()))
        if stop != "disconnect":
            await until(
                lambda: any(
                    isinstance(message, CancelRequest)
                    and message.request_id == item.request_id
                    for message in messages
                )
            )
        if stage == "queued":
            process.send(
                OperationSuccess(
                    type="operation.success", request_id=item.request_id, result="late"
                )
            )
        assert process.process.poll() is None


@pytest.mark.parametrize("failure", ["missing", "mismatch", "partial", "omitted"])
async def test_operation_cannot_reference_unverified_or_mismatched_inputs(
    failure: str,
) -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        item = begin(b"abc")
        if failure == "partial":
            await send(socket, item)
            assert isinstance(await receive(socket), ArtifactReady)
        elif failure != "missing":
            await transfer(socket, item, b"abc")
        if failure == "mismatch":
            item = item.model_copy(
                update={
                    "descriptor": item.descriptor.model_copy(update={"name": "changed"})
                }
            )
        operation = request() if failure == "omitted" else request(item)
        await send(socket, operation)
        result = await receive(socket)
        assert (
            isinstance(result, OperationFailure)
            and result.error.code == "artifact_transfer_failed"
        )
        assert operation not in messages
        await until(lambda: not list(process.spool.root.iterdir()))


async def test_worker_shutdown_cleans_staged_input() -> None:
    root: Path
    async with worker() as (process, connected, _):
        root = process.spool.root
        socket = await asyncio.wait_for(connected.get(), 5)
        item = begin(b"abc")
        await transfer(socket, item, b"abc")
        assert list(root.iterdir())
    assert not root.exists()
