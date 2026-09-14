import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ImageContent
from pydantic import TypeAdapter
from tyvrana_protocol import AdapterRegistration, ArtifactDescriptor, JsonValue

from tyvrana_blender.models import DeleteResult, ObjectSummary, SceneSummary
from tyvrana_blender.operations import OPERATIONS

from ..png import assert_image_variation, inspect_png
from .conftest import running_blender


@asynccontextmanager
async def core_client(
    tmp_path: Path, port: int = 0
) -> AsyncIterator[tuple[Client, int]]:
    executable = os.environ.get("TYVRANA_CORE_EXECUTABLE") or shutil.which(
        "tyvrana-core"
    )
    assert executable, "Set TYVRANA_CORE_EXECUTABLE to an installed core CLI"
    log_path = tmp_path / "core.log"
    with log_path.open("w") as log:
        params = StdioServerParameters(
            command=executable,
            args=["mcp", "--port", str(port)],
            env={"PYTHONASYNCIODEBUG": "1", "TMPDIR": str(tmp_path)},
        )
        async with Client(
            stdio_client(params, errlog=log), read_timeout_seconds=45
        ) as client:
            line = next(
                line
                for line in log_path.read_text().splitlines()
                if "Adapter server listening on" in line
            )
            actual_port = int(line.rsplit(":", 1)[1])
            yield client, actual_port
    assert "Adapter server stopped" in log_path.read_text()
    assert "Traceback" not in log_path.read_text()
    assert not list(tmp_path.glob("tyvrana-artifacts-*"))  # noqa: ASYNC240 - Test directory.


def spooled_count(directory: Path) -> int:
    return int(json.loads((directory / "ready.json").read_text())["spooled_artifacts"])


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
@pytest.mark.parametrize(
    "cycles", [False, True], ids=["scene-engine", "cycles-override"]
)
async def test_real_render_reaches_mcp_image_content(
    profile: dict[str, str],
    tmp_path: Path,
    ui: bool,
    caplog: pytest.LogCaptureFixture,
    cycles: bool,
) -> None:
    profile["TYVRANA_TEST_RENDER"] = "1"
    if ui:
        profile["TYVRANA_TEST_SHOW_RENDER"] = "1"
    if cycles:
        profile["TYVRANA_TEST_CYCLES_OVERRIDE"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=ui):
            registered = await discover(client)
            assert registered is not None
            if not ui:
                failure = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": registered.instance_id,
                        "operation": "blender.render.image",
                        "arguments": {"show_result": True},
                    },
                )
                assert failure.is_error
            result = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": registered.instance_id,
                    "operation": "blender.render.image",
                    "arguments": {
                        "width": 512,
                        "height": 512,
                        "format": "png",
                        "show_result": ui,
                        **(
                            {"cycles": {"samples": 8, "device": "cpu", "denoise": True}}
                            if cycles
                            else {}
                        ),
                    },
                },
            )
            assert not result.is_error, result.content
            images = [
                content
                for content in result.content
                if isinstance(content, ImageContent)
            ]
            assert len(images) == 1 and images[0].mime_type == "image/png"
            data = base64.b64decode(images[0].data, validate=True)
            assert inspect_png(data) == (512, 512)
            with ThreadPoolExecutor(max_workers=1) as executor:
                await asyncio.get_running_loop().run_in_executor(
                    executor, assert_image_variation, data
                )
            descriptor = ArtifactDescriptor.model_validate(
                result.structured_content["artifacts"][0]
            )
            assert descriptor.byte_size == len(data) and len(data) > 1000
            assert descriptor.sha256 == hashlib.sha256(data).hexdigest()
            assert result.structured_content["result"] == {
                "width": 512,
                "height": 512,
                "format": "png",
            }
            assert (
                "/tmp/" not in result.model_dump_json()
                and "file:" not in result.model_dump_json()
            )
            assert "data" not in result.structured_content["result"]
            print("MCP_RENDER_ARTIFACT", descriptor.model_dump_json())
            async with asyncio.timeout(5):
                while spooled_count(tmp_path):  # noqa: ASYNC110 - Observe an external process.
                    await asyncio.sleep(0.02)
        await discover(client, empty=True)
    ready = json.loads((tmp_path / "ready.json").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(ready["worker_pid"], 0)

    assert "Failed to parse" not in caplog.text


async def discover(
    client: Client, *, empty: bool = False
) -> AdapterRegistration | None:
    async with asyncio.timeout(12):
        while True:
            response = await client.call_tool("tyvrana_list_adapters")
            assert not response.is_error
            adapters = response.structured_content["adapters"]
            if empty and not adapters:
                return None
            if not empty and adapters:
                assert len(adapters) == 1
                result = AdapterRegistration.model_validate(
                    {"type": "adapter.register", **adapters[0]}
                )
                assert result.application == "blender"
                assert result.application_version is not None
                assert result.application_version.startswith("5.2.1")
                assert result.operations == OPERATIONS
                return result
            await asyncio.sleep(0.02)


async def operation(
    client: Client, adapter_id: str, name: str, arguments: dict[str, JsonValue]
) -> JsonValue:
    result = await client.call_tool(
        "tyvrana_execute_operation",
        {"adapter_id": adapter_id, "operation": name, "arguments": arguments},
    )
    assert not result.is_error, result.content
    return TypeAdapter(JsonValue).validate_python(result.structured_content["result"])


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_full_mcp_core_blender_vertical_slice(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=ui):
            registered = await discover(client)
            assert registered is not None
            assert registered.project_path is None
            print("MCP_REGISTRATION", registered.model_dump_json(exclude={"type"}))
            identifier = registered.instance_id
            initial = SceneSummary.model_validate(
                await operation(client, identifier, "blender.scene.inspect", {})
            )
            assert initial.object_count == 0
            no_camera = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": identifier,
                    "operation": "blender.render.image",
                    "arguments": {},
                },
            )
            assert no_camera.is_error
            assert '"no_camera"' in no_camera.model_dump_json().replace('\\"', '"')
            created = ObjectSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.object.create_primitive",
                    {"primitive": "cube", "name": "Tyvrana_EndToEnd_Cube"},
                )
            )
            assert created.name == "Tyvrana_EndToEnd_Cube" and created.type == "MESH"
            changed = ObjectSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.object.set_transform",
                    {
                        "name": created.name,
                        "location": [2, 3, 4],
                        "rotation": [0.1, 0.2, 0.3],
                        "scale": [1.5, 2, 0.5],
                    },
                )
            )
            assert changed.location == [2, 3, 4]
            assert changed.rotation == pytest.approx([0.1, 0.2, 0.3], abs=1e-6)
            assert changed.scale == [1.5, 2, 0.5]
            scene = SceneSummary.model_validate(
                await operation(client, identifier, "blender.scene.inspect", {})
            )
            assert scene.object_count == 1 and scene.objects == [changed]
            deleted = DeleteResult.model_validate(
                await operation(
                    client, identifier, "blender.object.delete", {"name": created.name}
                )
            )
            assert deleted.deleted == created.name
            final = SceneSummary.model_validate(
                await operation(client, identifier, "blender.scene.inspect", {})
            )
            assert final.object_count == 0
            error = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": identifier,
                    "operation": "blender.object.delete",
                    "arguments": {"name": "missing"},
                },
            )
            assert error.is_error
        await discover(client, empty=True)
    ready = json.loads((tmp_path / "ready.json").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(ready["worker_pid"], 0)


async def test_ui_remains_responsive_and_reconnects_when_core_restarts(
    profile: dict[str, str], tmp_path: Path
) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    profile["TYVRANA_TEST_PORT"] = str(port)
    async with running_blender(profile, tmp_path, ui=True):
        async with asyncio.timeout(12):
            while not (tmp_path / "ready.json").exists():  # noqa: ASYNC110 - Observe the external UI timer.
                await asyncio.sleep(0.02)
        async with core_client(tmp_path, port) as (first, _):
            registered = await discover(first)
            assert registered is not None
            # Registration comes from the worker; wait for main-thread readiness too.
            await operation(first, registered.instance_id, "blender.scene.inspect", {})
        previous_tick = (tmp_path / "ready.json").stat().st_mtime_ns
        async with asyncio.timeout(5):
            while (tmp_path / "ready.json").stat().st_mtime_ns <= previous_tick:  # noqa: ASYNC110 - Observe the external UI timer.
                await asyncio.sleep(0.02)
        async with core_client(tmp_path, port) as (second, _):
            again = await discover(second)
            assert again is not None and again.instance_id == registered.instance_id
            await operation(second, again.instance_id, "blender.scene.inspect", {})
