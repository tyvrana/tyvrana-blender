"""Packaged headless structural articulation and interaction comparisons."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


@pytest.mark.parametrize("compact", [False, True], ids=["existing", "compact"])
async def test_chain_interaction(
    profile: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compact: bool,
) -> None:
    rows: list[dict[str, Any]] = []
    phase = "startup"
    async with core_client(tmp_path) as (client, port):
        original = client.call_tool

        async def observed(
            name: str, arguments: dict[str, Any] | None = None, **kwargs: Any
        ) -> CallToolResult:
            started = time.perf_counter()
            result = await original(name, arguments, **kwargs)
            rows.append(
                dict(
                    phase=phase,
                    tool=name,
                    operation=(arguments or {}).get("operation"),
                    request_bytes=len(
                        json.dumps(arguments, separators=(",", ":")).encode()
                    ),
                    response_bytes=len(result.model_dump_json().encode()),
                    seconds=time.perf_counter() - started,
                    error=result.is_error,
                )
            )
            (tmp_path / "workflow-metrics.json").write_text(json.dumps(rows, indent=2))
            return result

        monkeypatch.setattr(client, "call_tool", observed)
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(name: str, /, **arguments: Any) -> Any:
                return await operation(
                    client, adapter.instance_id, "blender." + name, arguments
                )

            phase = "discovery"
            result = await client.call_tool(
                "tyvrana_list_operations",
                dict(
                    adapter_id=adapter.instance_id,
                    names=[
                        "blender.armature.create",
                        "blender.armature.pose",
                        "blender.armature.inspect_structure"
                        if compact
                        else "blender.armature.inspect",
                    ],
                    include_schemas=True,
                ),
            )
            assert not result.is_error
            phase = "author"
            bones = [
                dict(
                    name=f"S{i:02}",
                    head=[0, i, 0],
                    tail=[0, i + 1, 0],
                    **({"parent": f"S{i - 1:02}"} if i else {}),
                    connected=bool(i),
                )
                for i in range(20)
            ]
            created = await call(
                "armature.create",
                name="Structure",
                bones=bones,
                **({"sample_limit": 0} if compact else {}),
            )
            assert created["bone_count"] == 20
            phase = "pose"
            posed = await call(
                "armature.pose",
                object_name="Structure",
                bones=[dict(name="S00", rotation=[0, 0, 0.3])],
                **({"sample_limit": 0} if compact else {}),
            )
            assert posed["pose_sha256"] != created["pose_sha256"]
            phase = "inspect"
            if compact:
                inspected = await call(
                    "armature.inspect_structure",
                    object_name="Structure",
                    names=["S19"],
                    fields=["pose"],
                )
                tail = inspected["segments"][0]["pose"]["tail"]
            else:
                inspected = await call(
                    "armature.inspect", object_name="Structure", bone_names=["S19"]
                )
                tail = inspected["bones"][0]["posed_tail_world"]
            import math

            assert tail == pytest.approx(
                [-20 * math.sin(0.3), 20 * math.cos(0.3), 0], abs=1e-4
            )
            print(
                "CHAIN_WORKFLOW_METRICS", json.dumps(dict(compact=compact, rows=rows))
            )


def test_native_joints(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/joint_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "joint-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "JOINT_NATIVE_PASSED 21" in log


async def test_structural_fixtures_persist_across_hosts(
    profile: dict[str, str],
    tmp_path: Path,
) -> None:
    import math

    def span(low: float, high: float) -> dict[str, float]:
        return dict(minimum=low, maximum=high)

    def hinge(axis: str) -> dict[str, Any]:
        return {a: span(-0.4, 0.6) if a == axis else span(0, 0) for a in "xyz"}

    fixtures: dict[str, list[dict[str, Any]]] = {
        "Hinge": [
            dict(name="Base", head=[0, -1, 0], tail=[0, 0, 0], deform=False),
            dict(
                name="Arm",
                head={"kind": "landmark", "name": "Pivot"},
                tail=[0, 2, 0],
                parent="Base",
                connected=True,
                x_reference=[1, 0, 0],
                limits=hinge("x"),
            ),
        ],
        "Limb": [
            dict(
                name="A",
                head=[3, 0, 0],
                tail=[3, 1, 0],
                x_reference=[0, 0, 1],
                limits=hinge("x"),
            ),
            dict(
                name="B",
                head=[3, 1, 0],
                tail=[4, 1, 0],
                parent="A",
                connected=True,
                x_reference=[0, 1, 0],
                limits=hinge("y"),
            ),
            dict(
                name="C",
                head=[4, 1, 0],
                tail=[4, 1, 1],
                parent="B",
                connected=True,
                x_reference=[1, 0, 0],
                limits=hinge("z"),
            ),
        ],
        "MultiAxis": [
            dict(
                name="Joint",
                head=[-3, 0, 0],
                tail=[-3, 1, 0],
                roll=0.4,
                limits=dict(x=span(-0.2, 0.3), y=span(-0.3, 0.4), z=span(-0.1, 0.2)),
            )
        ],
        "OrganicChain": [
            dict(
                name="Proximal",
                head=[0, 0, 3],
                tail=[0.2, 0.8, 3.2],
                x_reference=[1, 0, 0],
                limits=dict(x=span(-0.3, 0.5), y=span(-0.2, 0.2), z=span(-0.2, 0.2)),
            ),
            dict(
                name="Intermediate",
                head=[0.2, 0.8, 3.2],
                tail=[0.4, 1.5, 3.1],
                parent="Proximal",
                connected=True,
                x_reference=[1, 0, 0],
                limits=hinge("z"),
            ),
        ],
    }
    poses = {
        "Hinge": [dict(name="Arm", rotation=[0.9, 0, 0])],
        "Limb": [
            dict(name="A", rotation=[0.8, 0, 0]),
            dict(name="B", rotation=[0, 0.3, 0]),
            dict(name="C", rotation=[0, 0, -0.8]),
        ],
        "MultiAxis": [dict(name="Joint", rotation=[0.7, 0.8, 0.5])],
        "OrganicChain": [
            dict(name="Proximal", rotation=[0.3, 0.1, -0.1]),
            dict(name="Intermediate", rotation=[0, 0, 0.7]),
            dict(name="Distal", rotation=[0.1, 0, 0]),
        ],
    }
    expected: dict[str, dict[str, list[float]]] = {
        "Hinge": {"Base": [0, 0, 0], "Arm": [0.6, 0, 0]},
        "Limb": {"A": [0.6, 0, 0], "B": [0, 0.3, 0], "C": [0, 0, -0.4]},
        "MultiAxis": {"Joint": [0.3, 0.4, 0.2]},
        "OrganicChain": {
            "Proximal": [0.3, 0.1, -0.1],
            "Middle": [0, 0, 0.6],
            "Distal": [0.1, 0, 0],
        },
    }
    saved: dict[str, Any] = {}
    rest: dict[str, Any] = {}
    path = str(tmp_path / "structures.blend")
    async with core_client(tmp_path) as (client, port):
        assert client.instructions and "infer the professional workflow" in " ".join(
            client.instructions.split()
        )
        profile["TYVRANA_TEST_PORT"] = str(port)
        for host_index in range(2):
            host_path = tmp_path / f"host-{host_index}"
            host_path.mkdir()
            host_profile = {
                **profile,
                "TYVRANA_TEST_CONTROL": str(host_path),
                "TMPDIR": str(host_path),
            }
            async with running_blender(host_profile, host_path, ui=False):
                adapter = await discover(client)
                assert adapter is not None and adapter.operation_count == 141

                async def call(
                    name: str,
                    /,
                    *,
                    identifier: str = adapter.instance_id,
                    **arguments: Any,
                ) -> Any:
                    return await operation(
                        client, identifier, "blender." + name, arguments
                    )

                async def inspect(name: str) -> Any:
                    return await call(
                        "armature.inspect_structure",
                        object_name=name,
                        fields=["rest", "frame", "limits", "pose"],
                    )

                async def measurements() -> Any:
                    def point(
                        bone: str, endpoint: str, state: str = "evaluated"
                    ) -> dict[str, str]:
                        return dict(
                            kind="bone",
                            object="Hinge",
                            bone=bone,
                            endpoint=endpoint,
                            state=state,
                        )

                    return await call(
                        "measurement.inspect",
                        queries=[
                            dict(
                                kind="distance",
                                name="Arm length",
                                a=point("Arm", "head"),
                                b=point("Arm", "tail"),
                                comparison=dict(target=2, tolerance=1e-5),
                            ),
                            dict(
                                kind="distance",
                                name="Reach",
                                a=point("Base", "head"),
                                b=point("Arm", "tail"),
                                comparison=dict(
                                    target=math.sqrt(5 + 4 * math.cos(0.6)),
                                    tolerance=1e-5,
                                ),
                            ),
                            dict(
                                kind="angle",
                                name="Hinge angle",
                                a=point("Arm", "tail", "rest"),
                                vertex=point("Arm", "head"),
                                b=point("Arm", "tail"),
                                comparison=dict(
                                    target=math.degrees(0.6), tolerance=0.001
                                ),
                            ),
                        ],
                    )

                if host_index == 0:
                    schemas = await client.call_tool(
                        "tyvrana_list_operations",
                        dict(
                            adapter_id=adapter.instance_id,
                            names=[
                                "blender.armature." + n
                                for n in [
                                    "create",
                                    "configure_rest",
                                    "configure_joints",
                                    "inspect_structure",
                                    "pose",
                                ]
                            ],
                            include_schemas=True,
                        ),
                    )
                    assert not schemas.is_error
                    await call(
                        "collection.create_hierarchy",
                        collections=[dict(name="Structures"), dict(name="References")],
                    )
                    await call(
                        "object_set.create",
                        collections=["References"],
                        objects=[
                            dict(
                                key="Origin",
                                name="Origin",
                                kind="empty",
                                role="structural-reference",
                                tags=["fixture"],
                            )
                        ],
                    )
                    await call(
                        "landmark.set",
                        landmarks=[
                            dict(
                                name="Pivot",
                                point=[0, 0, 0],
                                object="Origin",
                                category="joint center",
                            )
                        ],
                    )
                    for name, bones in fixtures.items():
                        created = await call(
                            "armature.create", name=name, bones=bones, sample_limit=0
                        )
                        assert created["bones"] == []
                    await call(
                        "object_set.configure",
                        objects=[
                            dict(
                                name=name,
                                collections=["Structures"],
                                role="rest-structure",
                                tags=["fixture"],
                            )
                            for name in fixtures
                        ],
                    )
                    await call(
                        "armature.configure_rest",
                        object_name="OrganicChain",
                        renames=[dict(name="Intermediate", rename="Middle")],
                        bones=[
                            dict(
                                name="Distal",
                                head=[0.4, 1.5, 3.1],
                                tail=[0.3, 2, 3],
                                parent="Middle",
                                connected=True,
                                roll=0.3,
                                limits=hinge("x"),
                            )
                        ],
                    )
                    poses["OrganicChain"][1]["name"] = "Middle"
                    for name in fixtures:
                        rest[name] = await inspect(name)
                        assert rest[name]["valid"]
                        await call(
                            "armature.pose",
                            object_name=name,
                            bones=poses[name],
                            sample_limit=0,
                        )
                        saved[name] = await inspect(name)
                        assert saved[name]["valid"]
                        for segment in saved[name]["segments"]:
                            angles = expected[name][segment["name"]]
                            assert segment["pose"][
                                "evaluated_rotation"
                            ] == pytest.approx(angles, abs=1e-5)
                    # The posed edit must fail atomically; reset is the explicit repair.
                    failed = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id=adapter.instance_id,
                            operation="blender.armature.configure_rest",
                            arguments=dict(
                                object_name="Hinge",
                                renames=[dict(name="Arm", rename="MovingArm")],
                            ),
                        ),
                    )
                    assert failed.is_error
                    assert await inspect("Hinge") == saved["Hinge"]
                    await call(
                        "armature.pose", object_name="Hinge", reset=True, sample_limit=0
                    )
                    await call(
                        "armature.configure_rest",
                        object_name="Hinge",
                        renames=[dict(name="Arm", rename="MovingArm")],
                        sample_limit=0,
                    )
                    await call(
                        "armature.configure_rest",
                        object_name="Hinge",
                        renames=[dict(name="MovingArm", rename="Arm")],
                        sample_limit=0,
                    )
                    await call(
                        "armature.pose",
                        object_name="Hinge",
                        bones=poses["Hinge"],
                        sample_limit=0,
                    )
                    # Updating limits while posed is safe: rest geometry is unchanged.
                    await call(
                        "armature.configure_joints",
                        object_name="MultiAxis",
                        joints=[
                            dict(
                                name="Joint", limits=fixtures["MultiAxis"][0]["limits"]
                            )
                        ],
                        sample_limit=0,
                    )
                    measured = await measurements()
                    assert all(m["within_tolerance"] for m in measured["measurements"])
                    await call("file.save", filepath=path)
                else:
                    await call("file.open", filepath=path, discard_current=True)
                    for name in fixtures:
                        reopened = await inspect(name)
                        assert reopened == saved[name]
                        await call(
                            "armature.pose",
                            object_name=name,
                            reset=True,
                            sample_limit=0,
                        )
                        neutral = await inspect(name)
                        for segment in neutral["segments"]:
                            assert segment["pose"]["head"] == pytest.approx(
                                segment["rest"]["head"], abs=1e-5
                            )
                            assert segment["pose"]["tail"] == pytest.approx(
                                segment["rest"]["tail"], abs=1e-5
                            )
                        await call(
                            "armature.pose",
                            object_name=name,
                            bones=poses[name],
                            sample_limit=0,
                        )
                        assert await inspect(name) == saved[name]
                    assert await measurements() == measured
                    organized = await call(
                        "object_set.inspect",
                        collection="Structures",
                        fields=["metadata"],
                    )
                    assert organized["page"]["matched_count"] == 4
