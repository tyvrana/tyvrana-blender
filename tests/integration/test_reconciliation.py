"""One real MCP workflow across reload, reconnect, external edits and new hosts."""

import asyncio
import json
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tyvrana_blender.deployment import stage

from ..closed_chain_fixture import commands
from .conftest import ROOT
from .test_e2e import core_client
from .test_extension_reload import candidate_archive, isolated_thread


async def test_pre_receipt_reconciliation(
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
            (tmp_path / "reconciliation-calls.json").write_text(
                json.dumps(calls, indent=2)
            )
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
            target = adapter if control.name == "first" else proof_adapter
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

            initial = await execute(adapter, "blender.document.attest")
            accepted_revision = (await state())["project"]["revision"]
            all_steps = [["blender." + op, args] for op, args in commands()[:4]]
            async with host("proof") as proof_control:
                proof_adapter = next(
                    a["instance_id"]
                    for a in await discover(count=2)
                    if a["instance_id"] != adapter
                )
                await execute(
                    proof_adapter,
                    "blender.file.open",
                    discard_current=True,
                    filepath=str(tmp_path / "accepted.blend"),
                )
                await external(first, "typed_delta", steps=all_steps[:1])
                current = await execute(adapter, "blender.document.attest")
                await state("invalidated")

                async def recover(
                    name: str,
                    prior: dict[str, Any],
                    live: dict[str, Any],
                    steps: list[Any],
                    revision: int,
                ) -> tuple[dict[str, Any], dict[str, Any]]:
                    request = dict(
                        project_id=key,
                        reconciliation_id=name,
                        expected_revision=revision,
                        document_id="doc",
                        adapter_id=adapter,
                        proof_adapter_id=proof_adapter,
                        stage_id="m2",
                        prior_digest=prior["digest"],
                        expected_digest=live["digest"],
                        provenance="Controlled known authoring before receipts",
                        delta=[
                            dict(
                                operation=op, arguments=args, owner_entity_id="harness"
                            )
                            for op, args in steps
                        ],
                    )
                    result = await execute("core", "project.reconcile", **request)
                    while result["state"] == "running":
                        await asyncio.sleep(result["poll_after_seconds"])
                        result = await execute(
                            "core",
                            "project.reconcile_status",
                            project_id=key,
                            reconciliation_id=name,
                        )
                    records.setdefault("reconciliations", {})[name] = result
                    return dict(result), request

                extra = [
                    [
                        "blender.object.create_primitive",
                        dict(primitive="cube", name="Undeclared"),
                    ]
                ]
                await external(first, "typed_delta", steps=extra)
                unknown = await execute(adapter, "blender.document.attest")
                result, _ = await recover(
                    "extra", initial, unknown, all_steps[:1], accepted_revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "reconciliation_delta_mismatch"
                ), result
                assert (await state("invalidated"))["project"][
                    "revision"
                ] == accepted_revision
                records["qualification_3"] = "PASS"
                await external(
                    first,
                    "typed_delta",
                    steps=[["blender.object.delete", dict(name="Undeclared")]],
                )
                await external(proof_control, "open_saved")
                await external(first, "geometry_changed")
                changed = await execute(adapter, "blender.document.attest")
                result, _ = await recover(
                    "upstream", initial, changed, all_steps[:1], accepted_revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "reconciliation_upstream_changed"
                ), result
                records["qualification_4"] = "PASS"
                await external(first, "restore_geometry")
                changed_steps = json.loads(json.dumps(all_steps[:1]))
                changed_steps[0][1]["bones"][1]["tail"][0] = 3.125
                result, _ = await recover(
                    "hash", initial, current, changed_steps, accepted_revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "reconciliation_delta_mismatch"
                ), result
                records["qualification_5"] = "PASS"
                await external(proof_control, "different_document")
                result, _ = await recover(
                    "lineage", initial, current, all_steps[:1], accepted_revision
                )
                assert (
                    result["state"] == "failed"
                    and result["error_code"] == "reconciliation_lineage"
                ), result
                records["qualification_6"] = "PASS"
                await external(proof_control, "open_saved")
                result, request = await recover(
                    "known", initial, current, all_steps[:1], accepted_revision
                )
                assert result["state"] == "completed", result
                records["qualification_1"] = "PASS"
                packet = await state()
                assert packet["project"]["stage"] == "m2"
                repeated = await execute("core", "project.reconcile", **request)
                assert (
                    repeated["already_reconciled"]
                    and repeated["revision"] == result["revision"]
                )
                assert (await state())["project"]["revision"] == result["revision"]
                records["qualification_10"] = "PASS"
                # Extend the recovered head with several missing resource receipts.
                await execute(
                    adapter,
                    "blender.file.save",
                    filepath=str(tmp_path / "accepted.blend"),
                    overwrite=True,
                )
                prior = await execute(adapter, "blender.document.attest")
                await external(proof_control, "open_saved")
                multiple_steps = json.loads(
                    json.dumps(all_steps).replace('"Rig"', '"SecondRig"')
                )
                await external(first, "typed_delta", steps=multiple_steps)
                current = await execute(adapter, "blender.document.attest")
                packet = await state("invalidated")
                result, _ = await recover(
                    "multiple",
                    prior,
                    current,
                    multiple_steps,
                    packet["project"]["revision"],
                )
                assert result["state"] == "completed", result
                records["qualification_2"] = "PASS"
            await discover()
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
            packet = await state()
            records["qualification_7"] = "PASS"
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
                checkpoint=dict(id="working", label="Recovered working state"),
            )
            saved = await execute(adapter, "blender.document.attest")
            assert (
                checkpoint["checkpoint"]["document_states"]["doc"]["digest"]
                == saved["digest"]
            )
            records["qualification_8"] = "PASS"
            archive = candidate_archive(
                installed, tmp_path / "reload.zip", "reconciliation"
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
            await state()
            discovery = await tool("tyvrana_list_adapters", dict(adapter_id=adapter))
            await external(first, "reconnect")
            await tool(
                "tyvrana_list_adapters",
                dict(
                    adapter_id=adapter,
                    after_revision=discovery["revision"],
                    wait_seconds=10,
                ),
            )
            await registered(
                adapter, native["project_id"], str(tmp_path / "working.blend")
            )
            await execute(
                adapter,
                "blender.object.set_transform",
                name="Rig",
                location=[0, 0, 0.2],
            )
            await state()
            records["qualification_9"] = "PASS"
        records.update(
            calls=calls,
            request_bytes=sum(c["request_bytes"] for c in calls),
            response_bytes=sum(c["response_bytes"] for c in calls),
            manual_attestation_workaround=False,
            false_milestone_block=False,
            unexplained_content_accepted=False,
            force_flag=False,
        )
        (tmp_path / "reconciliation.json").write_text(json.dumps(records, indent=2))
        print(
            "RECONCILIATION",
            json.dumps({k: v for k, v in records.items() if k != "calls"}),
        )
