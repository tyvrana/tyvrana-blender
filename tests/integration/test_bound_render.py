"""Packaged render jobs preserve a normally bound semantic working document."""

import asyncio
import base64
import hashlib
import os
from pathlib import Path
from typing import Any

from mcp import Client

from .conftest import ROOT
from .test_e2e import core_client, discover

HOST = """import importlib, os, time
from pathlib import Path
import addon_utils
import bpy
module = "bl_ext.user_default.tyvrana_blender"
addon_utils.enable(module, default_set=True)
lifecycle = importlib.import_module(module + ".lifecycle")
assert lifecycle._backend._runtime is None
bpy.context.preferences.addons[module].preferences.port = int(os.environ["TEST_PORT"])
control = Path(os.environ["TEST_CONTROL"])
try:
    deadline = time.monotonic() + 240
    while not (control / "stop").exists():
        assert time.monotonic() < deadline
        lifecycle._backend.pump()
        if bpy.app.timers.is_registered(lifecycle._poll):
            if lifecycle._poll() is None:
                bpy.app.timers.unregister(lifecycle._poll)
        time.sleep(.02)
finally:
    lifecycle.disable()
    print("BOUND_RENDER_HOST_CLEAN", flush=True)
"""


async def test_bound_render_preserves_working_head(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "BLENDER_USER_RESOURCES": str(tmp_path / "profile"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "TMPDIR": str(tmp_path),
        "TEST_CONTROL": str(tmp_path),
    }
    installed = await asyncio.create_subprocess_exec(
        "blender",
        "--factory-startup",
        "--command",
        "extension",
        "install-file",
        "--repo",
        "user_default",
        str(ROOT / "dist/tyvrana_blender-0.1.0.zip"),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(installed.communicate(), 45)
    assert installed.returncode == 0, output.decode()
    script = tmp_path / "host.py"
    script.write_text(HOST)
    async with core_client(tmp_path) as (client, port):
        with (tmp_path / "host.log").open("wb") as log:
            host = await asyncio.create_subprocess_exec(
                "blender",
                "--background",
                "--threads",
                "2",
                "--python-exit-code",
                "1",
                "--python",
                str(script),
                env={**env, "TEST_PORT": str(port)},
                stdout=log,
                stderr=log,
            )
            try:
                adapter = await discover(client)
                assert adapter is not None
                await qualify(client, adapter.instance_id, tmp_path)
            finally:
                (tmp_path / "stop").touch()
                try:
                    await asyncio.wait_for(host.wait(), 20)
                except TimeoutError:
                    host.kill()
                    await host.wait()
                assert host.returncode == 0, (tmp_path / "host.log").read_text()
        assert "BOUND_RENDER_HOST_CLEAN" in (tmp_path / "host.log").read_text()


async def qualify(client: Client, adapter: str, directory: Path) -> None:
    async def op(name: str, /, **args: Any) -> Any:
        result = await client.call_tool(
            "tyvrana_execute_operation",
            dict(
                adapter_id="core" if name.startswith("project.") else adapter,
                operation=name,
                arguments=args,
            ),
        )
        assert not result.is_error, result.content
        value = result.structured_content["result"]
        if name in {
            "blender.file.new",
            "blender.file.save",
            "blender.file.open",
            "blender.project.bind",
        }:
            query: dict[str, Any] = dict(adapter_id=adapter, wait_seconds=10)
            async with asyncio.timeout(30):
                while True:
                    observed = await client.call_tool("tyvrana_list_adapters", query)
                    assert not observed.is_error, observed.content
                    found = observed.structured_content
                    if any(
                        a.get("project_path") == value["filepath"]
                        and a.get("project_id") == value["project_id"]
                        for a in found["adapters"]
                    ):
                        break
                    query["after_revision"] = found["revision"]
        return result

    async def attest() -> Any:
        response = await op("blender.document.attest")
        value = response.structured_content["result"]
        while value["state"] in {"queued", "running"}:
            await asyncio.sleep(value["poll_after_seconds"])
            response = await op(
                "blender.document.attest_status", job_id=value["job_id"]
            )
            value = response.structured_content["result"]
        assert value["state"] == "completed", value
        assert value["result"]["status"] == "complete"
        return value["result"]

    async def render(*, failure: bool = False, cancel: bool = False) -> None:
        response = await op(
            "blender.render.image",
            width=64,
            height=64,
            wait_seconds=0,
            **(
                dict(frames=list(range(1, 33)), cycles=dict(device="cpu", samples=1))
                if cancel
                else dict(
                    inspection=dict(
                        objects=["Absent" if failure else "Fixture"],
                        views=[dict(name="Side", orientation="right")],
                    )
                )
            ),
        )
        value = response.structured_content["result"]
        if cancel:
            response = await op("blender.render.cancel", job_id=value["job_id"])
            value = response.structured_content["result"]
        while value["state"] in {"queued", "running", "cancel_requested"}:
            response = await op(
                "blender.render.status",
                job_id=value["job_id"],
                after_revision=value["revision"],
                wait_seconds=20,
            )
            value = response.structured_content["result"]
        assert value["state"] == (
            "failed" if failure else "cancelled" if cancel else "succeeded"
        ), value
        if failure:
            assert value["error"]["code"] == "organization_invalid"
            assert "Traceback" not in str(value)
        elif not cancel:
            response = await op("blender.render.result", job_id=value["job_id"])
            data = base64.b64decode(
                next(c.data for c in response.content if c.type == "image")
            )
            assert data.startswith(b"\x89PNG\r\n\x1a\n")
            assert hashlib.sha256(data).hexdigest() == value["sha256"]
            assert len(data) == value["byte_size"]

    await op("blender.file.new", discard_current=True)
    await op("blender.object.create_primitive", primitive="cube", name="Fixture")
    await render()  # Unbound document.
    await op(
        "blender.camera.create", name="Camera", location=[0, 0, 8], make_active=True
    )
    native = (
        await op(
            "blender.project.bind",
            resources=[dict(resource_kind="object", name="Fixture")],
        )
    ).structured_content["result"]
    project = (
        await op(
            "project.create",
            title="Render fixture",
            goal="Inspect provisional geometry",
        )
    ).structured_content["result"]
    key = project["id"]
    applied = await op(
        "project.apply",
        project_id=key,
        expected_revision=1,
        project=dict(stage="working"),
        upsert=[
            dict(kind="entity", id="object", label="Fixture"),
            dict(
                kind="document",
                id="doc",
                label="Fixture document",
                application="blender",
                application_project_id=native["project_id"],
                adapter_id=adapter,
            ),
            dict(
                kind="binding",
                id="binding",
                label="Fixture binding",
                entity_id="object",
                document_id="doc",
                resource_kind="object",
                resource_id=native["resources"][0]["resource_id"],
            ),
            dict(
                kind="milestone",
                id="working",
                label="Provisional",
                status="in_progress",
                entity_ids=["object"],
                document_ids=["doc"],
            ),
        ],
    )
    revision = applied.structured_content["result"]["project"]["revision"]
    await render()  # Unverified bindings permit observation, not authoring/acceptance.
    await op(
        "project.verify",
        project_id=key,
        expected_revision=revision,
        binding_ids=["binding"],
    )
    await op("blender.object.set_transform", name="Fixture", location=[0, 0, 0.5])
    path = str(directory / "fixture.blend")
    await op("blender.file.save", filepath=path)
    project = (await op("project.continue", project_id=key)).structured_content[
        "result"
    ]["project"]
    checkpoint = await op(
        "project.apply",
        project_id=key,
        expected_revision=project["revision"],
        checkpoint=dict(id="working", label="Working fixture"),
    )
    revision = checkpoint.structured_content["result"]["project"]["revision"]
    baseline = await attest()
    for options in ({}, {"failure": True}, {"cancel": True}):
        await render(**options)
        after = await attest()
        for field in (
            "digest",
            "format",
            "project_id",
            "host_session_id",
            "document_session_id",
        ):
            assert after[field] == baseline[field], field
        project = (await op("project.continue", project_id=key)).structured_content[
            "result"
        ]["project"]
        assert project["revision"] == revision
    await op("blender.file.save", filepath=path, overwrite=True)
    await op("blender.file.open", filepath=path, discard_current=True)
    project = (await op("project.continue", project_id=key)).structured_content[
        "result"
    ]["project"]
    # Observation remains available after reopen, but stale working identity cannot
    # authorize an edit or silently replace the trusted head.
    await render()
    rejected = await client.call_tool(
        "tyvrana_execute_operation",
        dict(
            adapter_id=adapter,
            operation="blender.object.set_transform",
            arguments=dict(name="Fixture", location=[1, 0, 0.5]),
        ),
    )
    assert rejected.is_error and "content_diverged" in str(rejected.content)
    await op(
        "project.attest",
        project_id=key,
        document_id="doc",
        adapter_id=adapter,
        expected_revision=project["revision"],
        mode="reattach",
    )
    await render()
    assert (await attest())["digest"] == baseline["digest"]
    await op("blender.file.save", filepath=path, overwrite=True)

    # Clearing a bound document is also lifecycle, not a receipt advancing its head.
    project = (await op("project.continue", project_id=key)).structured_content[
        "result"
    ]["project"]
    await op("blender.file.new", discard_current=True)
    current = (await op("project.continue", project_id=key)).structured_content[
        "result"
    ]["project"]
    assert current["revision"] == project["revision"]
    await op("blender.file.open", filepath=path, discard_current=True)
    await op(
        "project.attest",
        project_id=key,
        document_id="doc",
        adapter_id=adapter,
        expected_revision=current["revision"],
        mode="reattach",
    )
    assert (await attest())["digest"] == baseline["digest"]
