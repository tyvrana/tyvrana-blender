"""Packaged production controls and preservation through the actual MCP path."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover


@pytest.mark.parametrize("script", ["constraints_checks.py", "rest_revision_checks.py"])
def test_native_production_rigging(
    profile: dict[str, str], tmp_path: Path, script: str
) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender" / script),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / (script + ".log")).write_text(log)
    assert result.returncode == 0, log


async def test_production_rigging_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            rows = []

            async def call(name: str, /, **args: Any) -> Any:
                start = time.perf_counter()
                response = await client.call_tool(
                    "tyvrana_execute_operation",
                    dict(
                        adapter_id=adapter.instance_id,
                        operation="blender." + name,
                        arguments=args,
                    ),
                )
                assert not response.is_error, response
                data = response.structured_content
                assert data is not None
                rows.append(
                    dict(
                        operation=name,
                        request_bytes=len(json.dumps(args).encode()),
                        response_bytes=len(json.dumps(data).encode()),
                        seconds=time.perf_counter() - start,
                    )
                )
                return data["result"]

            def ref(name: str, bone: str | None = None) -> dict[str, Any]:
                return dict(object_name=name, bone=bone)

            bones = []
            for prefix in ("F", "I", "D"):
                bones += [
                    dict(
                        name=prefix + "0",
                        head=[0, 0, 0],
                        tail=[0, 1, 0],
                        deform=prefix == "D",
                    ),
                    dict(
                        name=prefix + "1",
                        head=[0, 1, 0],
                        tail=[0, 2, 0.1],
                        parent=prefix + "0",
                        connected=True,
                        deform=prefix == "D",
                    ),
                ]
            await call("armature.create", name="Rig", bones=bones)
            for name, location in [
                ("Goal", [0, 1.7, 0.5]),
                ("Pole", [0, 0, 2]),
                ("Skin", [0, 1, 0]),
                ("Obstacle", [4, 0, 0]),
            ]:
                await call(
                    "object.create_primitive",
                    primitive="cube",
                    name=name,
                    location=location,
                )
            await call(
                "armature.bind",
                object_name="Skin",
                armature_object="Rig",
                weights=dict(method="envelopes", bones=["D0", "D1"]),
                allow_unweighted=True,
                preserve_volume=False,
            )
            rows.clear()
            await call(
                "control_rig.configure",
                object_name="Rig",
                name="Chain",
                fk=["F0", "F1"],
                ik=["I0", "I1"],
                deform=["D0", "D1"],
                target=ref("Goal"),
                pole=ref("Pole"),
            )
            await call(
                "armature.pose",
                object_name="Rig",
                bones=[
                    dict(name="F0", rotation=[0.2, 0, 0.15]),
                    dict(name="F1", rotation=[0.7, 0, 0]),
                ],
            )
            for mode in ["IK", "FK"]:
                result = await call(
                    "control_rig.switch", object_name="Rig", name="Chain", mode=mode
                )
                assert result["valid"] and result["maximum_match_error"] < 0.001
            qa = await call(
                "geometry.inspect",
                pairs=[dict(left="Skin", right="Obstacle")],
                frames=[1, 5, 10],
            )
            assert qa["minimum_distance"] > 0
            mechanics = list(rows)
            await call(
                "armature.create",
                name="Revision",
                bones=[dict(name="A", head=[0, 0, 0], tail=[0, 1, 0])],
            )
            await call("object.create_primitive", primitive="cube", name="Bound")
            binding = await call(
                "armature.bind",
                object_name="Bound",
                armature_object="Revision",
                weights=dict(method="envelopes", bones=["A"]),
                allow_unweighted=True,
            )
            rows.clear()
            args = dict(
                object_name="Revision",
                bones=[dict(name="A", head=[0, 0, 0], tail=[0, 1.2, 0])],
                dependency_policy="preserve",
            )
            preview = await call("armature.configure_rest", **args, preview=True)
            result = await call(
                "armature.configure_rest",
                **args,
                expected_rest_sha256=preview["rest_sha256"],
            )
            assert result["rest_revision"]["requires_motion_revalidation"]
            assert result["rest_revision"]["binding_objects"] == ["Bound"]
            await call(
                "object_set.configure", objects=[dict(name="Bound", hide_render=True)]
            )
            checked = await call("armature.inspect", object_name="Revision")
            assert checked["bindings"][0]["weights_sha256"] == binding["weights_sha256"]

            def total(items: list[dict[str, Any]]) -> dict[str, Any]:
                return dict(
                    calls=len(items),
                    operations=len(items),
                    request_bytes=sum(r["request_bytes"] for r in items),
                    response_bytes=sum(r["response_bytes"] for r in items),
                    elapsed_seconds=sum(r["seconds"] for r in items),
                    failures=0,
                    retries=0,
                    renders=0,
                    polls=0,
                    artifact_bytes=0,
                    schema_bytes=sum(
                        len(
                            REGISTRY["blender." + n].contract.model_dump_json().encode()
                        )
                        for n in {r["operation"] for r in items}
                    ),
                )

            metrics = dict(
                controls=total(mechanics),
                revision=total(rows),
                predecessor="No equivalent typed network or bound-rest revision",
            )
            (tmp_path / "rigging-metrics.json").write_text(json.dumps(metrics))
            print("PRODUCTION_RIGGING_METRICS", json.dumps(metrics))
            for name in ("LooseTarget", "Follower"):
                await call("object.create_primitive", primitive="cube", name=name)
            await call(
                "constraint.configure",
                constraints=[
                    dict(
                        name="Follow",
                        owner=ref("Follower"),
                        settings=dict(kind="copy_location", target=ref("LooseTarget")),
                    )
                ],
            )
            await call("object.delete", name="LooseTarget")
            await call(
                "constraint.remove",
                constraints=[dict(owner=ref("Follower"), name="Follow")],
            )
            assert not (await call("constraint.inspect", owners=[ref("Follower")]))[
                "constraints"
            ]
