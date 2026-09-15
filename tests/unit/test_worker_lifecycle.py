import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from tyvrana_protocol import AdapterEvent, OperationSuccess, decode_message
from websockets.asyncio.client import ClientConnection

from tyvrana_blender.operations import registration
from tyvrana_blender.worker import NetworkClient, Output


@pytest.mark.parametrize("failure", [None, TimeoutError, OSError])
async def test_reload_acknowledgement_releases_admission_on_failure(
    tmp_path: Path, failure: type[Exception] | None
) -> None:
    socket = Mock(spec=ClientConnection)
    socket.send = AsyncMock()
    pong = asyncio.get_running_loop().create_future()
    pong.set_result(None)
    socket.ping = AsyncMock(return_value=pong, side_effect=failure)
    output = Mock(spec=Output)
    output.send = AsyncMock()
    client = NetworkClient(
        "ws://127.0.0.1:8765",
        registration("test", "5.2.1", ""),
        output,
        tmp_path,
    )
    client.pending.add("reload")
    client.reload_requests.add("reload")
    response = OperationSuccess(
        type="operation.success", request_id="reload", result={"status": "scheduled"}
    )
    await client.deliver(socket, response)
    assert not client.pending and not client.reload_requests
    assert socket.send.await_count == 1
    assert decode_message(socket.send.call_args.args[0].encode()) == response
    if failure is None:
        assert client.reload_barrier
        output.send.assert_awaited_once_with(
            AdapterEvent(
                type="adapter.event",
                event="blender.response.sent",
                payload={"request_id": "reload"},
            )
        )
    else:
        assert not client.reload_barrier
        output.send.assert_not_awaited()
