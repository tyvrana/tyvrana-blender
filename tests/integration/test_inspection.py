"""Native bounds, named subsets and custom graph inspection."""

import subprocess
from pathlib import Path

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_bounded_inspection(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/inspection_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "inspection.log").write_text(log)
    assert result.returncode == 0, log
    assert "BOUNDED_INSPECTION_NATIVE_PASSED" in log
    assert "Traceback" not in log


async def test_live_catalog_drives_filtered_inspection(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            registered = await discover(client)
            assert registered is not None
            catalog = await client.call_tool(
                "tyvrana_list_operations",
                {
                    "adapter_id": registered.instance_id,
                    "names": [
                        "blender.scene.inspect",
                        "blender.camera.create",
                        "blender.file.save",
                        "blender.file.open",
                    ],
                    "include_schemas": True,
                },
            )
            assert not catalog.is_error
            contracts = catalog.structured_content
            assert contracts["catalog_sha256"] == registered.catalog_sha256
            for contract in contracts["operations"]:
                if contract["name"].startswith("blender.file."):
                    assert "Wait for registration" in contract["description"]
                    assert "project_path" in contract["description"]
            inspect_contract = next(
                entry
                for entry in contracts["operations"]
                if entry["name"] == "blender.scene.inspect"
            )
            properties = inspect_contract["arguments_schema"]["properties"]
            assert properties["limit"]["default"] == 32
            assert properties["limit"]["maximum"] == 128
            assert "page" in inspect_contract["result_schema"]["properties"]
            for name in ("Part A", "Part B"):
                await operation(
                    client,
                    registered.instance_id,
                    "blender.object.create_primitive",
                    {"primitive": "cube", "name": name},
                )
            first = await operation(
                client,
                registered.instance_id,
                "blender.scene.inspect",
                {"prefix": "Part", "limit": 1},
            )
            assert isinstance(first, dict) and isinstance(first["page"], dict)
            assert first["page"]["matched_count"] == 2
            assert first["page"]["next_offset"] == 1
            focused = await operation(
                client,
                registered.instance_id,
                "blender.scene.inspect",
                {"names": ["Part B"]},
            )
            assert isinstance(focused, dict) and isinstance(focused["objects"], list)
            assert len(focused["objects"]) == 1
            assert isinstance(focused["objects"][0], dict)
            assert focused["objects"][0]["name"] == "Part B"
        await discover(client, empty=True)
