"""Packaged Hair Curves, articulated attachment and fresh-host persistence."""

import json
import subprocess
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import OPERATIONS

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_growth(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/growth_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "growth-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "BLENDER_GROWTH_TESTS_PASSED" in log


async def fixture(call: Any) -> dict[str, Any]:
    n = 8
    vertices = [
        [-2 + 4 * x / n, -2 + 4 * y / n, 0.06 * (-2 + 4 * x / n) ** 2]
        for y in range(n + 1)
        for x in range(n + 1)
    ]
    faces = [
        [
            y * (n + 1) + x,
            y * (n + 1) + x + 1,
            (y + 1) * (n + 1) + x + 1,
            (y + 1) * (n + 1) + x,
        ]
        for y in range(n)
        for x in range(n)
    ]
    await call(
        "mesh.create",
        name="Carrier",
        vertices=vertices,
        faces=faces,
        corner_uvs=[
            [(vertices[i][0] + 2) / 4, (vertices[i][1] + 2) / 4]
            for face in faces
            for i in face
        ],
    )
    await call(
        "armature.create",
        name="Rig",
        bones=[
            dict(name="Left", head=[0, 0, 0], tail=[0, 1, 0]),
            dict(name="Right", head=[0, 0, 0], tail=[0, 1, 0]),
        ],
    )
    await call(
        "armature.bind",
        object_name="Carrier",
        armature_object="Rig",
        weights=dict(
            method="explicit",
            vertices=[
                dict(
                    vertex=i,
                    influences=[dict(bone="Left" if v[0] < 0 else "Right", weight=1)],
                )
                for i, v in enumerate(vertices)
            ],
        ),
    )
    await call(
        "shape_keys.edit",
        object_name="Carrier",
        keys=[
            dict(
                name="Lift",
                create=True,
                value=0,
                correction=dict(mode="region", delta=[0, 0, 0.5]),
            )
        ],
    )
    await call(
        "mesh.create",
        name="Ribbon",
        hidden=True,
        vertices=[
            [-0.06, 0, 0],
            [0.1, 0, 0],
            [-0.04, 0, 0.5],
            [0.08, 0, 0.5],
            [-0.01, 0, 1],
            [0.02, 0, 1],
        ],
        faces=[[0, 1, 3, 2], [2, 3, 5, 4]],
    )
    for name, color in [("Blue", [0.025, 0.19, 0.42]), ("Gold", [0.7, 0.27, 0.025])]:
        await call(
            "material.create_principled", name=name, base_color=color, roughness=0.45
        )
    families = [
        dict(name="Fine", length=0.45, radius=0.008, material="Blue"),
        dict(
            name="Broad",
            length=0.65,
            material="Gold",
            shape=[[0, 0, 0], [0.1, 0, 0.55], [0.7, 0, 0.8]],
            template=dict(object_name="Ribbon", mode="deform"),
        ),
    ]
    regions = [
        dict(
            name="West",
            family="Fine",
            guides=32,
            children=256,
            selector=dict(domain="face", mode="box", min=[-3, -3, -1], max=[0, 3, 1]),
        ),
        dict(
            name="East",
            family="Broad",
            guides=32,
            children=256,
            flow=[-1, 0, 0],
            selector=dict(domain="face", mode="box", min=[0, -3, -1], max=[3, 3, 1]),
        ),
    ]
    created = await call(
        "growth.create",
        name="Field",
        surface="Carrier",
        families=families,
        regions=regions,
    )
    assert created["summary"]["evaluated_curves"] == 512
    return dict(families=families, regions=regions)


def poses() -> list[dict[str, Any]]:
    return [
        dict(name="Rest"),
        dict(
            name="Partial",
            bones=[
                dict(name="Left", rotation=[0, 0.35, 0]),
                dict(name="Right", rotation=[0, -0.35, 0]),
            ],
        ),
        dict(
            name="Extended",
            bones=[
                dict(name="Left", rotation=[0.2, 0.65, 0]),
                dict(name="Right", rotation=[-0.2, -0.65, 0]),
            ],
            shape_values=[dict(object_name="Carrier", key="Lift", value=0.6)],
        ),
    ]


async def test_growth_persistence_and_restored_qa(
    profile: dict[str, str], tmp_path: Path
) -> None:
    saved: dict[str, Any] = {}
    pids = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        for host in range(2):
            folder = tmp_path / f"host-{host}"
            folder.mkdir()
            env = dict(profile, TYVRANA_TEST_CONTROL=str(folder), TMPDIR=str(folder))
            async with running_blender(env, folder, ui=False) as process:
                pids.append(process.pid)
                adapter = await discover(client)
                assert adapter is not None and adapter.operation_count == len(
                    OPERATIONS
                )

                async def call(
                    name: str, /, identifier: str = adapter.instance_id, **args: Any
                ) -> Any:
                    return await operation(client, identifier, "blender." + name, args)

                identity = await call("extension.inspect")
                assert identity["build"] == identity["implementation_build"]
                if host == 0:
                    await fixture(call)
                    saved = await call(
                        "growth.inspect",
                        object_name="Field",
                        guide_limit=4,
                        include_points=True,
                    )
                    await call("file.save", filepath=str(tmp_path / "growth.blend"))
                else:
                    await call(
                        "file.open",
                        filepath=str(tmp_path / "growth.blend"),
                        discard_current=True,
                    )
                    reopened = await call(
                        "growth.inspect",
                        object_name="Field",
                        guide_limit=4,
                        include_points=True,
                    )
                    assert reopened["guides"] == saved["guides"]
                    assert (
                        reopened["summary"]["system_id"]
                        == saved["summary"]["system_id"]
                    )
                before = await call("armature.inspect", object_name="Rig")
                result = await call(
                    "growth.sample",
                    object_name="Field",
                    armature_object="Rig",
                    poses=poses(),
                    template_samples=64,
                )
                assert result["restored"]
                assert all(
                    r["valid"] and r["qa"]["maximum_root_error"] < 1e-4
                    for r in result["samples"]
                )
                assert all(r["qa"]["frame_flips"] == 0 for r in result["samples"])
                assert before == await call("armature.inspect", object_name="Rig")
                compact = await call("growth.inspect", object_name="Field")
                assert compact["guides"] == [] and compact["recipe"] is None
                assert len(json.dumps(compact)) < 2200
                if host == 1:
                    bad = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id=adapter.instance_id,
                            operation="blender.growth.configure",
                            arguments=dict(
                                object_name="Field",
                                guides=[dict(root_id=999999, length_scale=2)],
                            ),
                        ),
                    )
                    assert bad.is_error
                    assert (
                        saved["guides"]
                        == (
                            await call(
                                "growth.inspect",
                                object_name="Field",
                                guide_limit=4,
                                include_points=True,
                            )
                        )["guides"]
                    )
                    await call("uv.unwrap", object_name="Carrier", method="conformal")
                    invalid = await call(
                        "growth.inspect", object_name="Field", qa_samples=0
                    )
                    assert not invalid["summary"]["valid"]
                    rebound = await call(
                        "growth.configure", object_name="Field", rebind=True
                    )
                    assert rebound["roots_rebound"] and rebound["summary"]["valid"]
                    await call("growth.remove", object_name="Field")
            await discover(client, empty=True)
    assert pids[0] != pids[1]
