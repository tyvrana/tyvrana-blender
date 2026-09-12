import asyncio
from pathlib import Path

from tyvrana_protocol import (
    AdapterRegistration,
    ArtifactBegin,
    ArtifactReady,
    CancelRequest,
    OperationRequest,
    OperationSuccess,
    decode_artifact_chunk,
    decode_message,
    encode_message,
)
from websockets.asyncio.server import ServerConnection, serve

from .conftest import running_blender
from .test_e2e import spooled_count


async def test_cancellation_after_real_render_during_binary_transfer(
    profile: dict[str, str], tmp_path: Path
) -> None:
    connected: asyncio.Queue[ServerConnection] = asyncio.Queue()

    async def accept(socket: ServerConnection) -> None:
        data = await socket.recv()
        assert isinstance(data, str)
        assert isinstance(decode_message(data.encode()), AdapterRegistration)
        connected.put_nowait(socket)
        await socket.wait_closed()

    async with serve(accept, "127.0.0.1", 0, close_timeout=0.1) as server:
        profile["TYVRANA_TEST_RENDER"] = "1"
        profile["TYVRANA_TEST_PORT"] = str(server.sockets[0].getsockname()[1])
        with running_blender(profile, tmp_path, ui=False):
            socket = await asyncio.wait_for(connected.get(), 15)
            await socket.send(
                encode_message(
                    OperationRequest(
                        type="operation.request",
                        request_id="cancel-render",
                        operation="blender.render.image",
                        arguments={"width": 512, "height": 512},
                    )
                ).decode()
            )
            data = await asyncio.wait_for(socket.recv(), 30)
            assert isinstance(data, str)
            begin = decode_message(data.encode())
            assert isinstance(begin, ArtifactBegin)
            assert begin.descriptor.byte_size > 1000
            await socket.send(
                encode_message(
                    ArtifactReady(type="artifact.ready", transfer_id=begin.transfer_id)
                ).decode()
            )
            data = await asyncio.wait_for(socket.recv(), 5)
            assert isinstance(data, bytes)
            assert decode_artifact_chunk(data).payload.startswith(b"\x89PNG\r\n\x1a\n")
            await socket.send(
                encode_message(
                    CancelRequest(type="operation.cancel", request_id="cancel-render")
                ).decode()
            )
            async with asyncio.timeout(5):
                while spooled_count(tmp_path):  # noqa: ASYNC110 - Observe the external Blender process.
                    await asyncio.sleep(0.02)
            await socket.send(
                encode_message(
                    OperationRequest(
                        type="operation.request",
                        request_id="still-alive",
                        operation="blender.scene.inspect",
                        arguments={},
                    )
                ).decode()
            )
            async with asyncio.timeout(5):
                while True:
                    data = await socket.recv()
                    if isinstance(data, str):
                        message = decode_message(data.encode())
                        if isinstance(message, OperationSuccess):
                            assert message.request_id == "still-alive"
                            break
