import asyncio

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
    decode_artifact_chunk,
    decode_message,
    encode_message,
)
from websockets.asyncio.server import ServerConnection

from tyvrana_blender.transport import WorkerProcess

from .test_transport import until, worker


async def receive(socket: ServerConnection) -> Message:
    data = await asyncio.wait_for(socket.recv(), 5)
    assert isinstance(data, str)
    return decode_message(data.encode())


def prepare(process: WorkerProcess, data: bytes) -> OperationSuccess:
    with process.spool.reserve() as (identifier, path):
        path.write_bytes(data)
        descriptor = process.spool.describe(identifier)
    return OperationSuccess(
        type="operation.success",
        request_id="render",
        result={"width": 512, "height": 512, "format": "png"},
        artifacts=(descriptor,),
    )


async def request_render(socket: ServerConnection, messages: list[Message]) -> None:
    request = OperationRequest(
        type="operation.request",
        request_id="render",
        operation="blender.render.image",
        arguments={},
    )
    await socket.send(encode_message(request).decode())
    await until(lambda: request in messages)


async def test_worker_streams_binary_chunks_and_waits_for_acceptance() -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        await request_render(socket, messages)
        data = bytes(range(256)) * 1024
        response = prepare(process, data)
        process.send(response)
        begin = await receive(socket)
        assert isinstance(begin, ArtifactBegin)
        assert begin.descriptor == response.artifacts[0]
        assert "/" not in begin.descriptor.artifact_id
        await socket.send(
            encode_message(
                ArtifactReady(type="artifact.ready", transfer_id=begin.transfer_id)
            ).decode()
        )
        received = bytearray()
        while len(received) < len(data):
            wire = await asyncio.wait_for(socket.recv(), 5)
            assert isinstance(wire, bytes)
            chunk = decode_artifact_chunk(wire)
            assert chunk.transfer_id == begin.transfer_id and chunk.offset == len(
                received
            )
            received.extend(chunk.payload)
        assert bytes(received) == data
        assert await receive(socket) == ArtifactComplete(
            type="artifact.complete", transfer_id=begin.transfer_id
        )
        # No success may appear until core verifies and acknowledges completion.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.recv(), 0.05)
        await socket.send(
            encode_message(
                ArtifactAccepted(
                    type="artifact.accepted", transfer_id=begin.transfer_id
                )
            ).decode()
        )
        assert await receive(socket) == response
        await until(lambda: not list(process.spool.root.iterdir()))


@pytest.mark.parametrize("stage", ["admission", "chunks", "acceptance"])
async def test_cancel_after_render_during_transfer_cleans_file(stage: str) -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        await request_render(socket, messages)
        process.send(prepare(process, b"x" * (2 * 1024 * 1024)))
        begin = await receive(socket)
        assert isinstance(begin, ArtifactBegin)
        if stage != "admission":
            await socket.send(
                encode_message(
                    ArtifactReady(type="artifact.ready", transfer_id=begin.transfer_id)
                ).decode()
            )
            assert isinstance(await asyncio.wait_for(socket.recv(), 5), bytes)
            if stage == "acceptance":
                while isinstance(await asyncio.wait_for(socket.recv(), 5), bytes):
                    pass
        cancel = CancelRequest(type="operation.cancel", request_id="render")
        await socket.send(encode_message(cancel).decode())
        await until(
            lambda: cancel in messages and not list(process.spool.root.iterdir())
        )
        following = OperationRequest(
            type="operation.request",
            request_id="following",
            operation="blender.scene.inspect",
            arguments={},
        )
        await socket.send(encode_message(following).decode())
        await until(lambda: following in messages)
        process.send(
            OperationSuccess(
                type="operation.success", request_id="following", result="alive"
            )
        )
        async with asyncio.timeout(5):
            while True:
                wire = await socket.recv()
                if isinstance(wire, str):
                    message = decode_message(wire.encode())
                    if isinstance(message, OperationSuccess):
                        assert message.request_id == "following"
                        break


@pytest.mark.parametrize("failure", ["reject", "disconnect"])
async def test_rejected_or_disconnected_transfer_releases_file(failure: str) -> None:
    async with worker() as (process, connected, messages):
        socket = await asyncio.wait_for(connected.get(), 5)
        await request_render(socket, messages)
        process.send(prepare(process, b"rendered bytes"))
        begin = await receive(socket)
        assert isinstance(begin, ArtifactBegin)
        if failure == "disconnect":
            await socket.close()
        else:
            await socket.send(
                encode_message(
                    ArtifactAbort(
                        type="artifact.abort",
                        transfer_id=begin.transfer_id,
                        error=ProtocolError(
                            code="artifact_transfer_failed", message="Store full"
                        ),
                    )
                ).decode()
            )
            assert isinstance(await receive(socket), ArtifactAbort)
            failed = await receive(socket)
            assert isinstance(failed, OperationFailure)
            assert failed.error.code == "artifact_transfer_failed"
            assert str(process.spool.root) not in failed.model_dump_json()
        await until(lambda: not list(process.spool.root.iterdir()))
