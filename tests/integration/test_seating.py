"""Real Blender qualification of the public rigid seating placement mode."""

import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_seating(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/seating_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "seating-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "SEATING_NATIVE_PASSED 9" in log


async def test_seating_public_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    import asyncio
    import json
    import time
    from typing import Any

    from .conftest import running_blender
    from .test_e2e import core_client

    calls: list[dict[str, Any]] = []
    started = time.perf_counter()
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):

            async def tool(name: str, args: dict[str, Any]) -> Any:
                response = await client.call_tool(name, args)
                calls.append(
                    dict(
                        tool=name,
                        operation=args.get("operation"),
                        request_bytes=len(
                            json.dumps(args, separators=(",", ":")).encode()
                        ),
                        response_bytes=len(response.model_dump_json().encode()),
                        failed=response.is_error,
                    )
                )
                (tmp_path / "seating-calls.json").write_text(
                    json.dumps(calls, indent=2)
                )
                assert not response.is_error, response.content
                return response.structured_content

            listing = await tool(
                "tyvrana_list_adapters", dict(application="blender", wait_seconds=12)
            )
            assert len(listing["adapters"]) == 1
            adapter = listing["adapters"][0]["instance_id"]

            async def call(operation: str, target: str = adapter, **args: Any) -> Any:
                r = await tool(
                    "tyvrana_execute_operation",
                    dict(adapter_id=target, operation=operation, arguments=args),
                )
                value = r["result"]
                if operation in {
                    "blender.project.bind",
                    "blender.file.save",
                    "blender.file.open",
                }:
                    async with asyncio.timeout(20):
                        while True:
                            observed = await tool(
                                "tyvrana_list_adapters",
                                dict(adapter_id=target, wait_seconds=2),
                            )
                            if (
                                observed["adapters"]
                                and observed["adapters"][0].get("project_id")
                                == value["project_id"]
                                and observed["adapters"][0].get("project_path")
                                == value["filepath"]
                            ):
                                break
                            await asyncio.sleep(0.1)
                return value

            await tool(
                "tyvrana_list_operations",
                dict(
                    adapter_id=adapter,
                    names=["blender.object_set.place"],
                    schemas="arguments",
                ),
            )
            objects = [
                dict(
                    kind="empty",
                    key="root",
                    name="Root",
                    location=[0.2, -0.15, 0.3],
                    rotation=[0.12, -0.08, 0.2],
                ),
                *[
                    dict(
                        kind="primitive",
                        primitive="plane",
                        key=f"s{i}",
                        name=f"Source{i}",
                        parent=dict(key="root"),
                        location=[x, 0, 0.05],
                        scale=[0.2, 0.2, 0.2],
                    )
                    for i, x in enumerate((-0.5, 0.5))
                ],
                *[
                    dict(
                        kind="primitive",
                        primitive="plane",
                        key=f"t{i}",
                        name=f"Target{i}",
                        location=[x, 0, 0],
                        scale=[0.4, 0.4, 0.4],
                    )
                    for i, x in enumerate((-0.5, 0.5))
                ],
                *[
                    dict(
                        kind="empty",
                        key=f"c{i}",
                        name=f"Chain{i}",
                        parent=dict(key="root" if i == 0 else f"c{i - 1}"),
                        location=[0.05, 0.02, 0.01],
                    )
                    for i in range(9)
                ],
                dict(
                    kind="empty",
                    key="free",
                    name="Independent",
                    location=[0.1, 0.2, 0.4],
                ),
                dict(
                    kind="primitive",
                    primitive="cube",
                    key="obstacle",
                    name="Obstacle",
                    location=[0, 0, -0.3],
                    scale=[2, 2, 0.2],
                ),
            ]
            await call("blender.object_set.create", objects=objects)
            moving = [
                "Root",
                "Source0",
                "Source1",
                "Independent",
                *[f"Chain{i}" for i in range(9)],
            ]
            before = await call(
                "blender.object_set.inspect",
                names=moving,
                fields=["transforms", "hierarchy"],
            )
            solved = await call(
                "blender.object_set.place",
                seating=dict(
                    members=["Root", "Independent"],
                    interfaces=[
                        dict(
                            kind="surface",
                            name=f"seat{i}",
                            source=dict(object_name=f"Source{i}"),
                            target=dict(object_name=f"Target{i}"),
                            gap=0.05,
                            normals="parallel",
                            align_centers=True,
                            tolerance=0.0001,
                            angular_tolerance=0.002,
                        )
                        for i in range(2)
                    ],
                    guards=[
                        dict(
                            name=f"guard{i}",
                            source=dict(object_name=f"Source{i}"),
                            target=dict(object_name="Obstacle"),
                            minimum_clearance=0.02,
                        )
                        for i in range(2)
                    ],
                    translation_limit=[1, 1, 1],
                    rotation_limit=[0.6, 0.6, 0.6],
                ),
            )
            result = solved["seating"]
            assert result["status"] == "SOLVED", result
            assert result["applied"] and result["moved_member_count"] == 13
            contact = await call(
                "blender.contact.inspect",
                envelopes=[
                    dict(
                        name=f"mate{i}",
                        source=dict(object_name=f"Source{i}"),
                        target=dict(object_name=f"Target{i}"),
                        mode="oriented_patch",
                        maximum_gap=0.0501,
                    )
                    for i in range(2)
                ]
                + [
                    dict(
                        name=f"guard{i}",
                        source=dict(object_name=f"Source{i}"),
                        target=dict(object_name="Obstacle"),
                        maximum_gap=1,
                    )
                    for i in range(2)
                ],
            )
            assert all(
                e["classification"] == "PERMITTED_CONTACT" for e in contact["envelopes"]
            ), contact
            after = await call(
                "blender.object_set.inspect",
                names=moving,
                fields=["transforms", "hierarchy"],
            )
            old = {o["name"]: o for o in before["objects"]}
            delta = result["world_delta"]
            for obj in after["objects"]:
                previous = old[obj["name"]]
                matrix = previous["transforms"]["world_matrix"]
                expected = [
                    [
                        sum(delta[i][k] * matrix[k][j] for k in range(4))
                        for j in range(4)
                    ]
                    for i in range(4)
                ]
                assert (
                    max(
                        abs(expected[i][j] - obj["transforms"]["world_matrix"][i][j])
                        for i in range(4)
                        for j in range(4)
                    )
                    < 2e-6
                )
                assert obj["hierarchy"] == previous["hierarchy"]
            native = await call("blender.project.bind", resources=[])
            saved = tmp_path / "seated.blend"
            await call("blender.file.save", filepath=str(saved), overwrite=True)
            await call("blender.file.open", filepath=str(saved), discard_current=True)
            async with asyncio.timeout(20):
                while True:
                    listing = await tool(
                        "tyvrana_list_adapters",
                        dict(application="blender", wait_seconds=2),
                    )
                    if (
                        listing["adapters"]
                        and listing["adapters"][0].get("project_id")
                        == native["project_id"]
                    ):
                        break
                    await asyncio.sleep(0.1)
            attested = await call("blender.document.attest")
            while attested["state"] in {"queued", "running"}:
                await asyncio.sleep(0.5)
                attested = await call(
                    "blender.document.attest_status", job_id=attested["job_id"]
                )
            assert attested["state"] == "completed", attested
            project = await call(
                "project.create",
                target="core",
                title="Rigid assembly fixture",
                goal="Simultaneous qualified seating",
            )
            batch = await call(
                "project.apply",
                target="core",
                project_id=project["id"],
                expected_revision=1,
                upsert=[
                    dict(
                        kind="document",
                        id="doc",
                        label="Seated fixture",
                        application="blender",
                        application_project_id=native["project_id"],
                        adapter_id=adapter,
                    )
                ],
            )
            revision = batch["project"]["revision"]
            proof = await call(
                "project.attest",
                target="core",
                project_id=project["id"],
                expected_revision=revision,
                document_id="doc",
                adapter_id=adapter,
                mode="bootstrap",
                trusted_artifact_sha256=attested["result"]["file_sha256"],
                trusted_artifact_locator=str(saved),
                provenance="Isolated public rigid seating qualification",
            )
            while proof.get("state") == "running":
                await asyncio.sleep(proof["poll_after_seconds"])
                proof = await call(
                    "project.attest_status",
                    target="core",
                    project_id=project["id"],
                    attestation_id=proof["attestation_id"],
                )
            assert proof.get("state") != "failed", proof
            checkpoint = await call(
                "project.apply",
                target="core",
                project_id=project["id"],
                expected_revision=revision,
                checkpoint=dict(id="seated", label="Qualified rigid assembly"),
            )
            assert (
                checkpoint["checkpoint"]["document_states"]["doc"]["digest"]
                == attested["result"]["digest"]
            )
            report = dict(
                calls=len(calls),
                request_bytes=sum(c["request_bytes"] for c in calls),
                response_bytes=sum(c["response_bytes"] for c in calls),
                failures=sum(c["failed"] for c in calls),
                retries=0,
                seating=result,
                contacts=contact["envelopes"],
                relative_transforms_preserved=True,
                saved_checkpoint=True,
                manual_xyz_search=False,
                raw_mesh_payload=False,
                elapsed_seconds=time.perf_counter() - started,
            )
            (tmp_path / "seating-mcp.json").write_text(json.dumps(report, indent=2))
