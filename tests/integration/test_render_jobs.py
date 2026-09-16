"""Real typed MCP render jobs, lifecycle conflicts and fresh-host persistence."""

import asyncio
import base64
import hashlib
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from ..png import inspect_png
from .conftest import running_blender
from .rendering import complete_render
from .test_e2e import core_client, discover, operation


async def call(
    client: Client, identifier: str, name: str, /, **args: Any
) -> CallToolResult:
    return await client.call_tool(
        "tyvrana_execute_operation",
        {
            "adapter_id": identifier,
            "operation": "blender." + name,
            "arguments": args,
        },
    )


async def test_job_failure_recovery_persistence_and_guards(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_RENDER"] = "1"
    profile["TYVRANA_TEST_CYCLES_OVERRIDE"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            identifier = adapter.instance_id
            before = await operation(client, identifier, "blender.scene.inspect", {})
            # Native snapshot succeeds, diagnostic preparation in child fails.
            submitted = await call(
                client,
                identifier,
                "render.image",
                width=64,
                height=64,
                surface={"objects": ["Absent"]},
                wait_seconds=0,
            )
            failed = await complete_render(client, identifier, submitted)
            assert failed.structured_content["result"]["state"] == "failed"
            assert (
                failed.structured_content["result"]["error"]["code"]
                == "object_not_found"
            )
            assert "Traceback" not in failed.model_dump_json()
            output = tmp_path / "persistent.png"
            started = time.monotonic()
            submitted = await call(
                client,
                identifier,
                "render.image",
                width=128,
                height=128,
                cycles={"samples": 4},
                wait_seconds=0,
                output={"filepath": str(output)},
            )
            assert time.monotonic() - started < 2
            assert submitted.structured_content["result"]["state"] == "queued"
            conflicts: list[tuple[str, dict[str, Any]]] = [
                ("render.image", {}),
                (
                    "file.open",
                    {
                        "filepath": str(tmp_path / "absent.blend"),
                        "discard_current": True,
                    },
                ),
                ("file.save", {"filepath": str(tmp_path / "blocked.blend")}),
                ("extension.reload", {}),
                ("bake.image", {}),
                ("object.delete", {"name": "RenderCube"}),
            ]
            for name, args in conflicts:
                rejected = await call(client, identifier, name, **args)
                assert (
                    rejected.is_error and "adapter_busy" in rejected.model_dump_json()
                )
            observed = await call(client, identifier, "render.status")
            assert (
                observed.structured_content["result"]["job_id"]
                == submitted.structured_content["result"]["job_id"]
            )
            assert len(observed.model_dump_json()) < 1800
            assert not any(c.type == "image" for c in observed.content)
            result = await complete_render(client, identifier, submitted)
            status = result.structured_content["result"]
            assert status["state"] == "succeeded"
            pixels = base64.b64decode(
                next(c.data for c in result.content if c.type == "image")
            )
            assert inspect_png(pixels) == (128, 128)
            assert output.read_bytes() == pixels
            assert hashlib.sha256(pixels).hexdigest() == status["sha256"]
            again = await call(
                client, identifier, "render.result", job_id=status["job_id"]
            )
            assert not again.is_error
            assert (
                again.structured_content["artifacts"][0]["artifact_id"]
                != result.structured_content["artifacts"][0]["artifact_id"]
            )
            assert before == await operation(
                client, identifier, "blender.scene.inspect", {}
            )
            await operation(
                client,
                identifier,
                "blender.file.save",
                {"filepath": str(tmp_path / "saved.blend")},
            )
        await discover(client, empty=True)
        for name in ("stop", "stopped", "ready.json"):
            (tmp_path / name).unlink(missing_ok=True)
        async with running_blender(profile, tmp_path, ui=False):
            fresh = await discover(client)
            assert fresh is not None and fresh.instance_id != identifier
            lost = await call(
                client, fresh.instance_id, "render.status", job_id=status["job_id"]
            )
            assert lost.is_error and "render_job_not_found" in lost.model_dump_json()
            await operation(
                client,
                fresh.instance_id,
                "blender.file.open",
                {"filepath": str(tmp_path / "saved.blend"), "discard_current": True},
            )
            assert output.read_bytes() == pixels


@pytest.mark.long_render
async def test_render_exceeds_deadline_and_cancels_running_child(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile.update(TYVRANA_TEST_RENDER="1", TYVRANA_TEST_RENDER_LONG="1")
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            identifier = adapter.instance_id
            started = time.monotonic()
            submitted = await call(
                client,
                identifier,
                "render.image",
                width=1024,
                height=1024,
                cycles={"samples": 512},
                wait_seconds=0,
            )
            assert time.monotonic() - started < 2
            assert not (
                await call(client, identifier, "scene.inspect", limit=1)
            ).is_error
            result = await complete_render(client, identifier, submitted)
            assert result.structured_content["result"]["render_seconds"] > 30
            assert result.structured_content["result"]["state"] == "succeeded"
            assert inspect_png(
                base64.b64decode(
                    next(c.data for c in result.content if c.type == "image")
                )
            ) == (1024, 1024)
            submitted = await call(
                client,
                identifier,
                "render.image",
                width=1024,
                height=1024,
                cycles={"samples": 512},
                wait_seconds=0,
            )
            status = submitted.structured_content["result"]
            while status["state"] == "queued":
                response = await call(
                    client,
                    identifier,
                    "render.status",
                    job_id=status["job_id"],
                    after_revision=status["revision"],
                    wait_seconds=20,
                )
                status = response.structured_content["result"]
            assert status["state"] == "running"
            started = time.monotonic()
            cancelled = await call(
                client, identifier, "render.cancel", job_id=status["job_id"]
            )
            cancelled = await complete_render(client, identifier, cancelled)
            assert cancelled.structured_content["result"]["state"] == "cancelled"
            assert time.monotonic() - started < 5
            unavailable = await call(
                client, identifier, "render.result", job_id=status["job_id"]
            )
            assert unavailable.is_error
            short = await call(
                client,
                identifier,
                "render.image",
                width=64,
                height=64,
                cycles={"samples": 2},
            )
            assert (
                await complete_render(client, identifier, short)
            ).structured_content["result"]["state"] == "succeeded"


async def test_host_loss_disconnects_and_reaps_render_child(
    profile: dict[str, str], tmp_path: Path
) -> None:
    import json
    import os

    from .conftest import ROOT

    profile.update(TYVRANA_TEST_RENDER="1", TYVRANA_TEST_RENDER_LONG="1")
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        with (tmp_path / "crash-host.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                "blender",
                "--background",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "tests/blender/host.py"),
                env=profile,
                stdout=log,
                stderr=log,
            )
            worker_pid = 0
            child_pids: list[int] = []
            try:
                adapter = await discover(client)
                assert adapter is not None
                response = await call(
                    client,
                    adapter.instance_id,
                    "render.image",
                    width=1024,
                    height=1024,
                    cycles={"samples": 512},
                    wait_seconds=0,
                )
                status = response.structured_content["result"]
                while status["state"] == "queued":
                    response = await call(
                        client,
                        adapter.instance_id,
                        "render.status",
                        job_id=status["job_id"],
                        after_revision=status["revision"],
                        wait_seconds=20,
                    )
                    status = response.structured_content["result"]
                assert status["state"] == "running"
                worker_pid = json.loads((tmp_path / "ready.json").read_text())[
                    "worker_pid"
                ]
                children_path = Path(f"/proc/{worker_pid}/task/{worker_pid}/children")
                children = children_path.read_text()  # noqa: ASYNC240 - Small owned process metadata.
                child_pids = [int(x) for x in children.split()]
                assert child_pids
                process.kill()
                await process.wait()
                async with asyncio.timeout(5):
                    response = await client.call_tool(
                        "tyvrana_list_adapters", {"adapter_id": adapter.instance_id}
                    )
                    while response.structured_content["adapters"]:
                        response = await client.call_tool(
                            "tyvrana_list_adapters",
                            {
                                "adapter_id": adapter.instance_id,
                                "after_revision": response.structured_content[
                                    "revision"
                                ],
                                "wait_seconds": 3,
                            },
                        )
                lost = await call(
                    client,
                    adapter.instance_id,
                    "render.status",
                    job_id=status["job_id"],
                )
                assert lost.is_error
                assert (
                    "adapter_not_found" in lost.model_dump_json()
                    or "adapter_disconnected" in lost.model_dump_json()
                )
                async with asyncio.timeout(5):
                    while list(tmp_path.glob("tyvrana-blender-artifacts-*")):  # noqa: ASYNC110, ASYNC240 - Observe owned subprocess cleanup.
                        await asyncio.sleep(0.05)  # noqa: ASYNC110 - Owned process cleanup observation.
                for pid in child_pids:
                    assert not Path(f"/proc/{pid}").exists()  # noqa: ASYNC240 - Small Linux process metadata.
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                for pid in child_pids:
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass
