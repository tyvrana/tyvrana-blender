"""One real MCP workflow across reload, reconnect, external edits and new hosts."""

import asyncio
import json
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..closed_chain_fixture import commands
from .conftest import ROOT
from .test_e2e import core_client


async def test_trusted_artifact_restore(
    profile: dict[str, str], tmp_path: Path
) -> None:
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
            started = time.monotonic()
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
                    "seconds": time.monotonic() - started,
                }
            )
            (tmp_path / "restore-calls.json").write_text(json.dumps(calls, indent=2))
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

        async def external(control: Path, action: str, **payload: Any) -> None:
            (control / "ack.json").unlink(missing_ok=True)
            (control / "command.json").write_text(
                json.dumps({"action": action, **payload})
            )
            async with asyncio.timeout(15):
                while not (control / "ack.json").exists():  # noqa: ASYNC110 - Bounded external host acknowledgement.
                    await asyncio.sleep(0.05)
            target = adapter if control.name in {"first", "second"} else proof_adapter
            if action == "different_document":
                await registered(target, None, None)
            elif action == "open_saved":
                await registered(
                    target, native["project_id"], str(tmp_path / "accepted.blend")
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
                status="planned",
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
                project=dict(stage=""),
                checkpoint=dict(id="m1", label="Trusted base"),
            )

            async def state(expected: str = "accepted") -> dict[str, Any]:
                packet = await execute("core", "project.continue", project_id=key)
                stages = {
                    r["record"]["id"]: r
                    for r in packet["records"]
                    if r["record"]["kind"] == "milestone"
                }
                assert stages["stage"]["record"]["status"] == expected, packet
                assert stages["stage"]["historical_status"] == "accepted", packet
                return dict(packet)

            def expected_current(evidence: dict[str, Any]) -> dict[str, Any]:
                return {
                    k: evidence[k]
                    for k in (
                        "host_session_id",
                        "document_session_id",
                        "project_id",
                        "format",
                        "digest",
                    )
                }

            async def restore(
                name: str,
                target: str | None,
                filepath: Path,
                current: dict[str, Any],
                revision: int,
                **overrides: Any,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                request = dict(
                    project_id=key,
                    restore_id=name,
                    expected_revision=revision,
                    document_id="doc",
                    adapter_id=adapter,
                    proof_adapter_id=proof_adapter,
                    checkpoint_id=target,
                    expected_current=expected_current(current),
                    discard_current=True,
                    open_arguments=dict(
                        filepath=str(filepath), discard_current=True, load_ui=False
                    ),
                    provenance="Explicitly discard divergent isolated fixture work",
                )
                request.update(overrides)
                result = await execute("core", "project.restore", **request)
                while result["state"] == "running":
                    await asyncio.sleep(result["poll_after_seconds"])
                    result = await execute(
                        "core",
                        "project.restore_status",
                        project_id=key,
                        restore_id=name,
                    )
                records.setdefault("restores", {})[name] = result
                return dict(result), request

            initial = await execute(adapter, "blender.document.attest")
            revision = (await state())["project"]["revision"]
            async with host("proof_a") as proof_control:
                proof_adapter = next(
                    a["instance_id"]
                    for a in await discover(count=2)
                    if a["instance_id"] != adapter
                )
                await execute(
                    proof_adapter,
                    "blender.file.open",
                    filepath=str(tmp_path / "accepted.blend"),
                    discard_current=True,
                )
                steps = [["blender." + op, args] for op, args in commands()[:1]]
                await external(first, "typed_delta", steps=steps)
                divergent = await execute(adapter, "blender.document.attest")
                await tool(
                    "tyvrana_execute_operation",
                    dict(
                        adapter_id=adapter,
                        operation="blender.object.set_transform",
                        arguments=dict(name="Rig", location=[0, 0, 0.2]),
                    ),
                    error="content_diverged",
                )
                await state("invalidated")
                # Missing discard consent rejects before any load.
                arguments = dict(
                    project_id=key,
                    restore_id="no-consent",
                    expected_revision=revision,
                    document_id="doc",
                    adapter_id=adapter,
                    proof_adapter_id=proof_adapter,
                    checkpoint_id="m1",
                    expected_current=expected_current(divergent),
                    open_arguments=dict(
                        filepath=str(tmp_path / "accepted.blend"), discard_current=True
                    ),
                    provenance="No consent",
                )
                await tool(
                    "tyvrana_execute_operation",
                    dict(
                        adapter_id="core",
                        operation="project.restore",
                        arguments=arguments,
                    ),
                    error="invalid_arguments",
                )
                records["qualification_2"] = "PASS"
                await external(first, "geometry_changed")
                result, _ = await restore(
                    "stale", "m1", tmp_path / "accepted.blend", divergent, revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "restore_current_changed"
                ), result
                records["qualification_3"] = "PASS"
                await external(first, "restore_geometry")
                corrupt = tmp_path / "corrupt.blend"
                corrupt.write_bytes(
                    (tmp_path / "accepted.blend").read_bytes() + b"untrusted change"
                )
                result, _ = await restore("file", "m1", corrupt, divergent, revision)
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "restore_file_mismatch"
                ), result
                records["qualification_4"] = "PASS"
                await external(proof_control, "shader_changed")
                result, _ = await restore(
                    "content", "m1", tmp_path / "accepted.blend", divergent, revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "restore_target_mismatch"
                ), result
                records["qualification_5"] = "PASS"
                await external(proof_control, "restore_shader")
                await external(proof_control, "different_document")
                result, _ = await restore(
                    "lineage", "m1", tmp_path / "accepted.blend", divergent, revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "restore_lineage"
                ), result
                records["qualification_6"] = "PASS"
                await external(proof_control, "open_saved")
                preserved = await execute(adapter, "blender.document.attest")
                assert preserved["digest"] == divergent["digest"]
                assert (await state("invalidated"))["project"]["revision"] == revision
                result, request = await restore(
                    "accepted", "m1", tmp_path / "accepted.blend", divergent, revision
                )
                assert result["state"] == "completed", result
                restored = await execute(adapter, "blender.document.attest")
                assert restored["digest"] == initial["digest"]
                scene = await execute(
                    adapter, "blender.scene.inspect", types=["ARMATURE"], limit=1
                )
                assert scene["page"]["matched_count"] == 0
                await state()
                records["qualification_1"] = records["qualification_7"] = "PASS"
                repeated = await execute("core", "project.restore", **request)
                assert repeated == result
                same, _ = await restore(
                    "already",
                    "m1",
                    tmp_path / "accepted.blend",
                    restored,
                    result["revision"],
                )
                assert (
                    same["state"] == "completed"
                    and same["already_current"]
                    and same["revision"] == result["revision"]
                ), same
                records["qualification_10"] = "PASS"
            await discover()
            packet = await state()
            await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=packet["project"]["revision"],
                project=dict(stage="m2"),
                upsert=[{**m2, "status": "in_progress"}],
            )
            op, args = commands()[0]
            await execute(adapter, "blender." + op, **args)
            await execute(
                adapter,
                "blender.file.save",
                filepath=str(tmp_path / "working.blend"),
                overwrite=True,
            )
            packet = await state()
            checkpoint = await execute(
                "core",
                "project.apply",
                project_id=key,
                expected_revision=packet["project"]["revision"],
                checkpoint=dict(id="m2", label="Saved working mechanics"),
            )
            working = await execute(adapter, "blender.document.attest")
            assert (
                checkpoint["checkpoint"]["document_states"]["doc"]["artifact_sha256"]
                == working["file_sha256"]
            )
            async with host("proof_working"):
                proof_adapter = next(
                    a["instance_id"]
                    for a in await discover(count=2)
                    if a["instance_id"] != adapter
                )
                await execute(
                    proof_adapter,
                    "blender.file.open",
                    filepath=str(tmp_path / "working.blend"),
                    discard_current=True,
                )
                await external(
                    first,
                    "typed_delta",
                    steps=[
                        [
                            "blender.object.set_transform",
                            dict(name="Rig", location=[0, 0, 0.75]),
                        ]
                    ],
                )
                divergent = await execute(adapter, "blender.document.attest")
                packet = await state("invalidated")
                result, _ = await restore(
                    "working",
                    "m2",
                    tmp_path / "working.blend",
                    divergent,
                    packet["project"]["revision"],
                )
                assert (
                    result["state"] == "completed"
                    and result["digest"] == working["digest"]
                ), result
                await state()
                await execute(
                    adapter,
                    "blender.object.set_transform",
                    name="Rig",
                    location=[0, 0, 0.2],
                )
                await execute(
                    adapter,
                    "blender.file.save",
                    filepath=str(tmp_path / "restart.blend"),
                    overwrite=True,
                )
                packet = await state()
                saved = await execute(
                    "core",
                    "project.apply",
                    project_id=key,
                    expected_revision=packet["project"]["revision"],
                    checkpoint=dict(id="restart", label="Durable working checkpoint"),
                )
                saved_evidence = await execute(adapter, "blender.document.attest")
                assert (
                    saved["checkpoint"]["document_states"]["doc"]["digest"]
                    == saved_evidence["digest"]
                )
                records["qualification_8"] = "PASS"
        # Routine restart: old process and both document sessions are gone.
        async with host("second"), host("proof_restart"):
            adapters = await discover(count=2)
            adapter = adapters[0]["instance_id"]
            proof_adapter = adapters[1]["instance_id"]
            await execute(
                proof_adapter,
                "blender.file.open",
                filepath=str(tmp_path / "restart.blend"),
                discard_current=True,
            )
            fresh = await execute(adapter, "blender.document.attest")
            assert fresh["host_session_id"] != saved_evidence["host_session_id"]
            result, _ = await restore(
                "restart",
                "restart",
                tmp_path / "restart.blend",
                fresh,
                saved["project"]["revision"],
            )
            assert (
                result["state"] == "completed"
                and result["digest"] == saved_evidence["digest"]
            ), result
            await state()
            await execute(
                adapter,
                "blender.object.set_transform",
                name="Rig",
                location=[0, 0, 0.3],
            )
            await state()
            records["qualification_9"] = "PASS"
        records.update(
            calls=calls,
            request_bytes=sum(c["request_bytes"] for c in calls),
            response_bytes=sum(c["response_bytes"] for c in calls),
            preflight_before_discard=True,
            manual_attestation_workaround=False,
            manual_milestone_reacceptance=False,
            unknown_content_trusted=False,
        )
        (tmp_path / "restore.json").write_text(json.dumps(records, indent=2))
        print(
            "TRUSTED_RESTORE",
            json.dumps({k: v for k, v in records.items() if k != "calls"}),
        )
