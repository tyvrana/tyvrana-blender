"""One real MCP workflow across reload, reconnect, external edits and new hosts."""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tyvrana_blender.deployment import stage

from .conftest import ROOT
from .test_e2e import core_client
from .test_extension_reload import candidate_archive, isolated_thread


async def test_document_continuity(profile: dict[str, str], tmp_path: Path) -> None:
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
    records: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):

        async def tool(name: str, args: dict[str, Any]) -> Any:
            response = await client.call_tool(name, args)
            calls.append(
                {
                    "tool": name,
                    "operation": args.get("operation"),
                    "request_bytes": len(
                        json.dumps(args, separators=(",", ":")).encode()
                    ),
                    "response_bytes": len(response.model_dump_json().encode()),
                    "error": response.is_error,
                }
            )
            assert not response.is_error, response.content
            return response.structured_content

        async def registered(
            target: str, project_id: str | None, filepath: str | None
        ) -> None:
            query: dict[str, Any] = dict(adapter_id=target, wait_seconds=10)
            async with asyncio.timeout(30):
                while True:
                    response = await tool("tyvrana_list_adapters", query)
                    values = response["adapters"]
                    if (
                        len(values) == 1
                        and values[0].get("project_id") == project_id
                        and values[0].get("project_path") == filepath
                    ):
                        return
                    query["after_revision"] = response["revision"]

        async def execute(adapter: str, operation: str, **args: Any) -> Any:
            result = (
                await tool(
                    "tyvrana_execute_operation",
                    dict(adapter_id=adapter, operation=operation, arguments=args),
                )
            )["result"]
            if operation in {
                "blender.project.bind",
                "blender.file.save",
                "blender.file.open",
            }:
                await registered(adapter, result["project_id"], result["filepath"])
            return result

        async def discover(
            previous: str | None = None, count: int = 1
        ) -> list[dict[str, Any]]:
            query: dict[str, Any] = dict(application="blender", wait_seconds=10)
            async with asyncio.timeout(30):
                while True:
                    result = await tool("tyvrana_list_adapters", query)
                    found = result["adapters"]
                    if len(found) == count and (
                        previous is None
                        or all(a["instance_id"] != previous for a in found)
                    ):
                        return list(found)
                    query["after_revision"] = result["revision"]

        @asynccontextmanager
        async def host(name: str) -> AsyncIterator[Path]:
            control = tmp_path / name
            control.mkdir()
            env = {
                **profile,
                "TYVRANA_TEST_CONTROL": str(control),
                "TYVRANA_TEST_PORT": str(port),
            }
            with (control / "host.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    "blender",
                    "--background",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(ROOT / "tests/blender/continuity_host.py"),
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                try:
                    yield control
                finally:
                    (control / "stop").touch()
                    async with asyncio.timeout(15):
                        await process.wait()
                    assert process.returncode == 0, (control / "host.log").read_text()

        async def external(control: Path, action: str) -> None:
            (control / "ack.json").unlink(missing_ok=True)
            (control / "command.json").write_text(json.dumps({"action": action}))
            async with asyncio.timeout(15):
                while not (control / "ack.json").exists():  # noqa: ASYNC110 - Bounded external host acknowledgement.
                    await asyncio.sleep(0.05)
            if action == "different_document":
                await registered(adapter, None, None)
            elif action == "open_saved":
                await registered(
                    adapter, native["project_id"], str(tmp_path / "accepted.blend")
                )

        async with host("first") as first:
            adapter = (await discover())[0]["instance_id"]
            await execute(
                adapter,
                "blender.object.create_primitive",
                primitive="cube",
                name="Fixture",
            )
            await execute(
                adapter,
                "blender.material.create_principled",
                name="Surface",
                roughness=0.5,
            )
            await execute(
                adapter,
                "blender.material.assign",
                object_name="Fixture",
                material_name="Surface",
            )
            native = await execute(
                adapter,
                "blender.project.bind",
                resources=[dict(resource_kind="object", name="Fixture")],
            )
            await execute(
                adapter, "blender.file.save", filepath=str(tmp_path / "accepted.blend")
            )
            await discover()
            initial = await execute(adapter, "blender.document.attest")
            (tmp_path / "initial-attestation.json").write_text(
                json.dumps(initial, indent=2)
            )
            assert initial["status"] == "complete", json.dumps(initial)
            records["before"] = dict(adapter=adapter, attestation=initial)
            project = await execute(
                "core",
                "project.create",
                title="Continuity fixture",
                goal="Preserve accepted material content",
            )
            key = project["id"]
            batch = [
                dict(kind="entity", id="part", label="Part"),
                dict(
                    kind="document",
                    id="doc",
                    label="Source",
                    application="blender",
                    application_project_id=native["project_id"],
                    adapter_id=adapter,
                ),
                dict(
                    kind="binding",
                    id="binding",
                    label="Part binding",
                    entity_id="part",
                    document_id="doc",
                    resource_kind="object",
                    resource_id=native["resources"][0]["resource_id"],
                ),
                dict(
                    kind="evidence",
                    id="proof",
                    label="Measured result",
                    storage="application",
                    binding_id="binding",
                    summary="Fixture geometry and shading were inspected.",
                ),
                dict(
                    kind="validation",
                    id="check",
                    label="Fixture check",
                    validation_type="inspection",
                    entity_ids=["part"],
                    evidence_ids=["proof"],
                    summary="Fixture measurements meet declared tolerances.",
                    status="passed",
                    freshness="current",
                ),
                dict(
                    kind="milestone",
                    id="stage",
                    label="Fixture accepted",
                    status="in_progress",
                    entity_ids=["part"],
                    document_ids=["doc"],
                    validation_ids=["check"],
                    acceptance="Known geometry and shading",
                ),
            ]
            await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=1,
                upsert=batch,
                project=dict(stage="stage"),
            )
            verified = await execute(
                "core",
                "project.verify",
                project_id=key,
                expected_revision=2,
                binding_ids=["binding"],
            )
            revision = verified["project"]["revision"]
            await execute(
                "core",
                "project.attest",
                project_id=key,
                expected_revision=revision,
                document_id="doc",
                adapter_id=adapter,
            )
            accepted = await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=revision,
                upsert=[{**batch[-1], "status": "accepted"}],
                project=dict(stage=""),
            )
            revision = accepted["project"]["revision"]
            records["revision"] = revision

            async def state(expected: str) -> None:
                packet = await execute("core", "project.continue", project_id=key)
                assert packet["project"]["revision"] == revision
                stage_record = next(
                    x["record"]
                    for x in packet["records"]
                    if x["record"]["id"] == "stage"
                )
                if stage_record["status"] != expected:
                    (tmp_path / "failed-state.json").write_text(
                        json.dumps(packet, indent=2)
                    )
                    records["failed_attestation"] = await execute(
                        adapter, "blender.document.attest"
                    )
                    (tmp_path / "partial-continuity.json").write_text(
                        json.dumps(records, indent=2)
                    )
                assert stage_record["status"] == expected, packet

            await state("accepted")
            for index in range(2):
                archive = candidate_archive(
                    installed, tmp_path / f"reload-{index}.zip", f"continuity_{index}"
                )
                staged = await isolated_thread(stage, archive, installed)
                old = adapter
                await execute(
                    adapter, "blender.extension.reload", expected_build=staged["build"]
                )
                adapter = (await discover(previous=old))[0]["instance_id"]
                current = await execute(adapter, "blender.document.attest")
                assert all(
                    current[k] == initial[k]
                    for k in ["host_session_id", "document_session_id", "digest"]
                )
                await state("accepted")
                records[f"reload_{index}"] = dict(
                    before=old, after=adapter, attestation=current
                )
            await external(first, "reconnect")
            await discover()
            await state("accepted")
            records["reconnect"] = "PASS"
            for change, restore in [
                ("geometry_changed", "restore_geometry"),
                ("shader_changed", "restore_shader"),
            ]:
                await external(first, change)
                changed = await execute(adapter, "blender.document.attest")
                assert changed["digest"] != initial["digest"]
                await state("invalidated")
                await external(first, restore)
                await state("accepted")
                records[change] = "PASS"
            await external(first, "different_document")
            changed = await execute(adapter, "blender.document.attest")
            assert changed["document_session_id"] != initial["document_session_id"]
            await state("invalidated")
            records["different_document"] = "PASS"
            await external(first, "open_saved")
            reloaded = await execute(adapter, "blender.document.attest")
            assert reloaded["digest"] == initial["digest"]
            await execute(
                "core",
                "project.attest",
                project_id=key,
                document_id="doc",
                adapter_id=adapter,
                expected_revision=revision,
                mode="reattach",
            )
            await state("accepted")
            async with host("second"):
                adapters = await discover(count=2)
                other = next(
                    x["instance_id"] for x in adapters if x["instance_id"] != adapter
                )
                await execute(
                    other,
                    "blender.file.open",
                    filepath=str(tmp_path / "accepted.blend"),
                    discard_current=True,
                )
                proof = await execute(other, "blender.document.attest")
                assert (
                    proof["host_session_id"] != initial["host_session_id"]
                    and proof["digest"] == initial["digest"]
                )
                await state("accepted")
                records["second_process"] = proof
        # The first process has ended. A new process must explicitly prove equivalence.
        async with host("restart"):
            adapter = (await discover())[0]["instance_id"]
            await execute(
                adapter,
                "blender.file.open",
                filepath=str(tmp_path / "accepted.blend"),
                discard_current=True,
            )
            await state("invalidated")
            await execute(
                "core",
                "project.attest",
                project_id=key,
                document_id="doc",
                adapter_id=adapter,
                expected_revision=revision,
                mode="reattach",
            )
            await state("accepted")
            records["restart"] = "PASS"
        records["calls"] = calls
        records["request_bytes"] = sum(c["request_bytes"] for c in calls)
        records["response_bytes"] = sum(c["response_bytes"] for c in calls)
        (tmp_path / "continuity.json").write_text(json.dumps(records, indent=2))
        print(
            "DOCUMENT_CONTINUITY_MCP_PASSED",
            json.dumps({k: v for k, v in records.items() if k != "calls"}),
        )
