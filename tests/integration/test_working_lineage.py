"""One real MCP workflow across reload, reconnect, external edits and new hosts."""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tyvrana_blender.deployment import stage

from ..closed_chain_fixture import commands
from .conftest import ROOT
from .test_e2e import core_client
from .test_extension_reload import candidate_archive, isolated_thread


async def test_working_lineage(profile: dict[str, str], tmp_path: Path) -> None:
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

        async def tool(
            name: str, args: dict[str, Any], error: str | None = None
        ) -> Any:
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
            (tmp_path / "working-calls.json").write_text(json.dumps(calls, indent=2))
            if error:
                assert response.is_error and error in str(response.content), (
                    response.content
                )
                return response.structured_content
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
            if operation == "blender.document.attest" and "job_id" in result:
                while result["state"] in {"queued", "running"}:
                    await asyncio.sleep(result["poll_after_seconds"])
                    result = (
                        await tool(
                            "tyvrana_execute_operation",
                            dict(
                                adapter_id=adapter,
                                operation="blender.document.attest_status",
                                arguments={"job_id": result["job_id"]},
                            ),
                        )
                    )["result"]
                assert result["state"] == "completed", result
                result = result["result"]
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
                adapter,
                "blender.file.save",
                filepath=str(tmp_path / "accepted.blend"),
                overwrite=True,
            )
            await discover()
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
            m2 = dict(
                kind="milestone",
                id="m2",
                label="Working mechanics",
                status="in_progress",
                entity_ids=["harness"],
                document_ids=["doc"],
                prerequisite_ids=["stage"],
            )
            await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=revision,
                upsert=[
                    {**batch[-1], "status": "accepted"},
                    dict(kind="entity", id="harness", label="Harness"),
                    m2,
                ],
                project=dict(stage="m2"),
            )

            async def state(expected: str = "accepted") -> dict[str, Any]:
                packet = await execute("core", "project.continue", project_id=key)
                (tmp_path / "working-state.json").write_text(
                    json.dumps(packet, indent=2)
                )
                stages = {
                    r["record"]["id"]: r
                    for r in packet["records"]
                    if r["record"]["kind"] == "milestone"
                }
                assert stages["stage"]["record"]["status"] == expected, packet
                assert stages["stage"]["historical_status"] == "accepted", packet
                assert stages["m2"]["record"]["status"] == "in_progress", packet
                return dict(packet)

            async def negative(
                operation: str, args: dict[str, Any], error: str
            ) -> None:
                await tool(
                    "tyvrana_execute_operation",
                    dict(adapter_id=adapter, operation=operation, arguments=args),
                    error=error,
                )

            initial = await execute(adapter, "blender.document.attest")
            assert initial["status"] == "complete", initial
            before_failure = await state()
            await negative(
                "blender.object.set_transform",
                dict(name="Missing", location=[1, 0, 0]),
                "object_not_found",
            )
            assert (await state())["project"]["revision"] == before_failure["project"][
                "revision"
            ]
            records["qualification_8"] = "PASS"
            for operation, arguments in commands()[:4]:
                if operation == "armature.create":
                    arguments["bones"].extend(
                        dict(
                            name=f"Joint{i:03}",
                            head=[3, i, 0],
                            tail=[3, i + 1, 0],
                            parent=f"Joint{i - 1:03}" if i else "Rod",
                        )
                        for i in range(126)
                    )
                await execute(adapter, "blender." + operation, **arguments)
                await state()
            await execute(
                adapter,
                "blender.constraint.configure",
                constraints=[
                    dict(
                        name="BaseDatum",
                        owner=dict(object_name="Probe"),
                        settings=dict(
                            kind="copy_location", target=dict(object_name="Fixture")
                        ),
                    )
                ],
            )
            await state()
            working = await execute(adapter, "blender.document.attest")
            assert working["digest"] != initial["digest"]
            first_resource = initial["resources"][0]
            assert first_resource in working["resources"], (first_resource, working)
            records["qualification_1"] = "PASS"
            records["qualification_2"] = "PASS"

            packet = await state()
            validation = dict(
                kind="validation",
                id="harness_check",
                label="Harness check",
                validation_type="inspection",
                entity_ids=["harness"],
                evidence_ids=["harness_proof"],
                status="passed",
                freshness="current",
            )
            await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=packet["project"]["revision"],
                upsert=[
                    dict(
                        kind="evidence",
                        id="harness_proof",
                        label="Harness evidence",
                        storage="external",
                        uri="urn:fixture:harness",
                        summary="Inspected fixture",
                    ),
                    validation,
                ],
            )
            await execute(
                adapter,
                "blender.object.set_transform",
                name="Rig",
                location=[0, 0, 0.1],
            )
            packet = await state()
            harness = next(
                r for r in packet["records"] if r["record"]["id"] == "harness_check"
            )
            assert harness["freshness"] == "stale", harness
            records["qualification_5"] = "PASS"
            await execute(
                adapter,
                "blender.file.save",
                filepath=str(tmp_path / "accepted.blend"),
                overwrite=True,
            )
            packet = await state()
            records["qualification_3"] = "PASS"
            checkpoint = await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=packet["project"]["revision"],
                checkpoint=dict(id="working", label="Mechanics working state"),
            )
            saved = await execute(adapter, "blender.document.attest")
            assert (
                checkpoint["checkpoint"]["document_states"]["doc"]["digest"]
                == saved["digest"]
            )
            records["qualification_4"] = "PASS"
            revision = checkpoint["project"]["revision"]

            await execute(
                adapter,
                "blender.object.set_transform",
                name="Rig",
                location=[0, 0, 0.2],
            )
            await state()
            await execute(
                adapter,
                "blender.file.save",
                filepath=str(tmp_path / "accepted.blend"),
                overwrite=True,
            )
            packet = await state()
            checkpoint = await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=packet["project"]["revision"],
                checkpoint=dict(id="restart", label="Exact restart working state"),
            )
            revision = checkpoint["project"]["revision"]
            saved = await execute(adapter, "blender.document.attest")

            for change, restore in [
                ("geometry_changed", "restore_geometry"),
                ("shader_changed", "restore_shader"),
            ]:
                await external(first, change)
                await negative(
                    "blender.object.set_transform",
                    dict(name="Rig", location=[0, 0, 0.2]),
                    "content_diverged",
                )
                await state("invalidated")
                await external(first, restore)
                assert (await state())["project"]["revision"] == revision
            records["qualification_7"] = "PASS"
            before_incomplete = await execute(adapter, "blender.document.attest")
            await negative(
                "blender.object.create_primitive",
                dict(primitive="cube", name="Unattestable"),
                "mutation_unqualified",
            )
            incomplete = await execute(adapter, "blender.document.attest")
            assert incomplete["status"] != "complete" and incomplete["digest"] is None
            assert (await state("invalidated"))["project"]["revision"] == revision
            await external(first, "remove_unattestable")
            assert (await execute(adapter, "blender.document.attest"))[
                "digest"
            ] == before_incomplete["digest"]
            assert (await state())["project"]["revision"] == revision
            records["incomplete_mutation"] = "REJECTED without trusted advancement"

            archive = candidate_archive(
                installed, tmp_path / "reload.zip", "working_lineage"
            )
            staged = await isolated_thread(stage, archive, installed)
            old = adapter
            await execute(
                adapter, "blender.extension.reload", expected_build=staged["build"]
            )
            adapter = (await discover(previous=old))[0]["instance_id"]
            reloaded = await execute(adapter, "blender.document.attest")
            assert all(
                reloaded[k] == saved[k]
                for k in ["host_session_id", "document_session_id", "digest"]
            )
            packet = await state()
            assert packet["project"]["revision"] == revision
            assert packet["checkpoint"]["id"] == "restart"
            records["qualification_9"] = "PASS"
        async with host("restart"):
            adapter = (await discover())[0]["instance_id"]
            await execute(
                adapter,
                "blender.file.open",
                filepath=str(tmp_path / "accepted.blend"),
                discard_current=True,
            )
            current = await execute(adapter, "blender.document.attest")
            assert current["digest"] == saved["digest"], (current, saved)
            assert current["host_session_id"] != saved["host_session_id"]
            await execute(
                "core",
                "project.attest",
                project_id=key,
                document_id="doc",
                adapter_id=adapter,
                expected_revision=revision,
                mode="reattach",
            )
            packet = await state()
            assert packet["project"]["revision"] == revision
            assert packet["checkpoint"]["id"] == "restart"
            structure = await execute(
                adapter, "blender.armature.inspect", object_name="Rig", sample_limit=0
            )
            assert structure["bone_count"] == 128, structure
            await execute(
                adapter,
                "blender.object.set_transform",
                name="Rig",
                location=[0, 0, 0.25],
            )
            continued = await state()
            assert continued["project"]["revision"] > revision
            records["restored_armature_bones"] = structure["bone_count"]
            records["continued_mutation"] = "PASS"
            records["qualification_10"] = "PASS"
            await execute(
                adapter,
                "blender.mesh.transform",
                object_name="Fixture",
                selector=dict(mode="all", domain="vertex"),
                scale=[1.2, 1, 1],
            )
            await state("invalidated")
            await negative(
                "blender.object.set_transform",
                dict(name="Rig", location=[0, 0, 0.3]),
                "milestone_blocked",
            )
            records["qualification_6"] = "PASS"
        records["calls"] = calls
        records["request_bytes"] = sum(c["request_bytes"] for c in calls)
        records["response_bytes"] = sum(c["response_bytes"] for c in calls)
        (tmp_path / "working-lineage.json").write_text(json.dumps(records, indent=2))
        print(
            "WORKING_LINEAGE_MCP_PASSED",
            json.dumps({k: v for k, v in records.items() if k != "calls"}),
        )
