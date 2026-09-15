"""Full MCP activation, rollback and repeated native generation changes."""

import asyncio
import base64
import json
import os
import shutil
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import ImageContent

from tyvrana_blender.deployment import stage
from tyvrana_blender.operations import OPERATIONS

from ..png import assert_image_variation
from .conftest import ROOT
from .test_e2e import core_client


def candidate_archive(
    installed: Path, path: Path, marker: str, *, broken: bool = False
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for source in installed.rglob("*"):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            data = source.read_bytes()
            if source.name == "operations.py":
                text = data.decode()
                # A deterministic capability proves child operation code changed.
                text += (
                    "\nOPERATIONS = tuple(sorted((*OPERATIONS, "
                    f'"blender.test.{marker}")))\n'
                )
                data = text.encode()
            if source.name == "blender.py" and broken:
                # Fail after registration, exercising cleanup of partial new state.
                data += (
                    b"\n_original_register = register\ndef register():\n"
                    b"    _original_register()\n"
                    b'    raise RuntimeError("deliberate registration failure")\n'
                )
            archive.writestr(source.relative_to(installed).as_posix(), data)
    return path


async def isolated_thread(function: Callable[..., Any], *args: object) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return await asyncio.get_running_loop().run_in_executor(
            executor, partial(function, *args)
        )


async def adapter(client: Client, previous: str | None = None) -> dict[str, Any]:
    async with asyncio.timeout(30):
        while True:
            result = await client.call_tool("tyvrana_list_adapters")
            values = result.structured_content["adapters"]
            assert len(values) <= 1, "Duplicate live adapter connections"
            if values and values[0]["instance_id"] != previous:
                return dict(values[0])
            await asyncio.sleep(0.03)


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_repeated_live_reload_and_registration_rollback(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    installed = (
        Path(profile["BLENDER_USER_RESOURCES"])
        / "extensions/user_default/tyvrana_blender"
    )
    # Targeted development uses current sources in this isolated stopped profile;
    # final validation uses the same sources packaged by the authoritative build.
    shutil.copytree(
        ROOT / "src/tyvrana_blender",
        installed,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    baseline_preferences = list(
        Path(profile["BLENDER_USER_RESOURCES"]).rglob("userpref.blend")  # noqa: ASYNC240 - Isolated fixture.
    )
    preference_bytes = {p: p.read_bytes() for p in baseline_preferences}
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        command = [
            "blender",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/lifecycle_host.py"),
        ]
        command = (
            ["xvfb-run", "-a", *command]
            if ui
            else [command[0], "--background", *command[1:]]
        )
        with (tmp_path / "blender.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *command, env=profile, stdout=log, stderr=log, start_new_session=True
            )
            try:
                current = await adapter(client)

                async def call(
                    name: str, arguments: dict[str, object] | None = None
                ) -> dict[str, Any]:
                    result = await client.call_tool(
                        "tyvrana_execute_operation",
                        {
                            "adapter_id": current["instance_id"],
                            "operation": "blender." + name,
                            "arguments": arguments or {},
                        },
                    )
                    assert not result.is_error, result.content
                    return dict(result.structured_content["result"])

                initial = await call("extension.inspect")
                completed = initial
                rejected = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": current["instance_id"],
                        "operation": "blender.extension.reload",
                        "arguments": {"expected_build": "0" * 64},
                    },
                )
                assert rejected.is_error
                assert (await call("extension.inspect"))["adapter_id"] == current[
                    "instance_id"
                ]
                baseline_scene = await call("scene.inspect")
                baseline_mesh = await call("mesh.inspect", {"object_name": "Cube"})
                destination = tmp_path / "persistent.blend"
                await call("file.save", {"filepath": str(destination)})
                saved = await call("file.inspect")
                seen_workers = {initial["worker_pid"]}
                for index, marker in enumerate(["generation_b", "generation_c"]):
                    before_id = current["instance_id"]
                    archive = candidate_archive(
                        installed, tmp_path / (marker + ".zip"), marker
                    )
                    staged = await isolated_thread(stage, archive, installed)
                    unchanged = await call("extension.inspect")
                    assert unchanged["build"] == completed["build"]
                    response = await call(
                        "extension.reload", {"expected_build": staged["build"]}
                    )
                    assert response["status"] == "scheduled"
                    current = await adapter(client, before_id)
                    assert "blender.test." + marker in current["operations"]
                    assert set(OPERATIONS) <= set(current["operations"])
                    async with asyncio.timeout(10):
                        while True:
                            completed = await call("extension.inspect")
                            if completed["status"] == "completed":
                                break
                            await asyncio.sleep(0.03)
                    assert completed["reload_id"] == response["reload_id"]
                    assert completed["build"] == staged["build"]
                    assert completed["implementation_build"] == staged["build"]
                    assert completed["generation"] == index + 1
                    assert completed["worker_pid"] not in seen_workers
                    seen_workers.add(completed["worker_pid"])
                    assert completed["runtime_timer_count"] == 1
                    assert completed["lifecycle_timer_count"] == 0
                    assert completed["handler_count"] == 5
                    assert completed["registered_class_count"] == 2
                    assert completed["artifact_count"] == 0
                    assert await call("file.inspect") == saved
                    assert (
                        await call("mesh.inspect", {"object_name": "Cube"})
                        == baseline_mesh
                    )
                    scene = await call("scene.inspect")
                    assert scene["objects"] == baseline_scene["objects"]
                    assert all(
                        p.read_bytes() == data for p, data in preference_bytes.items()
                    )

                good_build = completed["build"]
                archive = candidate_archive(
                    installed, tmp_path / "broken.zip", "broken", broken=True
                )
                staged = await isolated_thread(stage, archive, installed)
                before_id = current["instance_id"]
                await call("extension.reload", {"expected_build": staged["build"]})
                current = await adapter(client, before_id)
                restored = await call("extension.inspect")
                assert restored["status"] == "rolled_back"
                assert restored["build"] == good_build
                assert "deliberate registration failure" in restored["error"]
                assert "blender.test.broken" not in current["operations"]
                assert (
                    restored["runtime_timer_count"] == 1
                    and restored["handler_count"] == 5
                )
                assert (
                    await call("mesh.inspect", {"object_name": "Cube"}) == baseline_mesh
                )
                render = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": current["instance_id"],
                        "operation": "blender.render.image",
                        "arguments": {
                            "width": 128,
                            "height": 128,
                            "cycles": {"samples": 4, "device": "cpu", "denoise": False},
                        },
                    },
                )
                assert not render.is_error, render.content
                images = [
                    item for item in render.content if isinstance(item, ImageContent)
                ]
                assert len(images) == 1
                await isolated_thread(
                    assert_image_variation, base64.b64decode(images[0].data)
                )
                await call(
                    "file.save", {"filepath": str(tmp_path / "after_reload.blend")}
                )
                assert (tmp_path / "after_reload.blend").exists()
                ready = json.loads((tmp_path / "ready.json").read_text())
                assert ready["state_unchanged"] and ready["module_count_seen"] >= 3
                (tmp_path / "stop").touch()
                async with asyncio.timeout(10):
                    await process.wait()
                assert process.returncode == 0, (tmp_path / "blender.log").read_text()
                assert (tmp_path / "stopped").read_text() == "clean"
                assert not (tmp_path / "error").exists()
                print("LIVE_RELOAD_NATIVE_MCP_PASSED", "ui" if ui else "background")
            finally:
                if process.returncode is None:
                    (tmp_path / "stop").touch()
                    try:
                        async with asyncio.timeout(5):
                            await process.wait()
                    except TimeoutError:
                        import signal

                        os.killpg(process.pid, signal.SIGTERM)
                        await process.wait()
