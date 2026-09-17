"""Packaged typed device inspection in a native host, without a render or edit."""

from pathlib import Path

from .conftest import running_blender
from .test_e2e import core_client, discover


async def test_native_render_device_inspection(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            result = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": adapter.instance_id,
                    "operation": "blender.render.devices",
                    "arguments": {},
                },
            )
            assert not result.is_error, result.content
            devices = result.structured_content["result"]
            assert devices["cycles_available"]
            assert devices["device_count"] >= len(devices["devices"])
            assert devices["compute_backend"] in devices["supported_backends"]
