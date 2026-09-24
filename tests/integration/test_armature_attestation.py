"""Independent native processes qualify a production-sized armature graph."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .conftest import ROOT, running_blender
from .test_e2e import core_client
from .test_extension_reload import isolated_thread


def native_qualification(profile: dict[str, str], tmp_path: Path) -> dict[str, Any]:
    installed = (
        Path(profile["BLENDER_USER_RESOURCES"])
        / "extensions/user_default/tyvrana_blender"
    )
    shutil.copytree(
        ROOT / "src/tyvrana_blender",
        installed,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for phase in ("create", "background", "interactive"):
        command = [
            "blender",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/armature_attestation_checks.py"),
        ]
        if phase == "interactive":
            command = ["xvfb-run", "-a", *command]
        else:
            command.insert(1, "--background")
        result = subprocess.run(
            command,
            env={**profile, "TYVRANA_ATTEST_PHASE": phase},
            capture_output=True,
            text=True,
            timeout=240,
        )
        (tmp_path / f"{phase}.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "armature-create.json").read_text())
    assert report["production"]["objects"] > 300
    assert report["production"]["meshes"] > 150
    assert report["production"]["bones"] >= 128
    return dict(report)


async def test_armature_attestation(profile: dict[str, str], tmp_path: Path) -> None:
    report = await isolated_thread(native_qualification, profile, tmp_path)
    async with core_client(tmp_path) as (client, port):
        profile.update(TYVRANA_TEST_PORT=str(port), TYVRANA_TEST_LIFECYCLE="1")
        async with running_blender(profile, tmp_path, ui=False):
            adapters = await client.call_tool(
                "tyvrana_list_adapters", dict(application="blender", wait_seconds=25)
            )
            adapter = adapters.structured_content["adapters"][0]["instance_id"]
            calls: list[dict[str, Any]] = []

            async def execute(operation: str, arguments: dict[str, Any]) -> Any:
                response = await client.call_tool(
                    "tyvrana_execute_operation",
                    dict(adapter_id=adapter, operation=operation, arguments=arguments),
                )
                assert not response.is_error, response.content
                calls.append(
                    dict(
                        operation=operation,
                        response_bytes=len(response.model_dump_json().encode()),
                    )
                )
                return response.structured_content["result"]

            await execute(
                "blender.file.open",
                dict(filepath=str(tmp_path / "production.blend"), discard_current=True),
            )
            query: dict[str, Any] = dict(adapter_id=adapter, wait_seconds=10)
            async with asyncio.timeout(30):
                while True:
                    listed = (
                        await client.call_tool("tyvrana_list_adapters", query)
                    ).structured_content
                    if listed["adapters"] and listed["adapters"][0].get(
                        "project_path"
                    ) == str(tmp_path / "production.blend"):
                        break
                    query["after_revision"] = listed["revision"]
            current = await execute("blender.document.attest", {})
            while current["state"] in {"queued", "running"}:
                await asyncio.sleep(current["poll_after_seconds"])
                current = await execute(
                    "blender.document.attest_status", dict(job_id=current["job_id"])
                )
            assert current["state"] == "completed", current
            assert current["result"]["digest"] == report["production"]["digest"]
            assert max(c["response_bytes"] for c in calls) < 20000, calls
            report["production_mcp"] = dict(
                calls=calls, digest=current["result"]["digest"]
            )
    (tmp_path / "armature-qualification.json").write_text(json.dumps(report, indent=2))
    print(
        "ARMATURE_QUALIFICATION",
        json.dumps(
            {key: report[key] for key in ("sensitivity", "runtime", "production_mcp")}
        ),
    )
