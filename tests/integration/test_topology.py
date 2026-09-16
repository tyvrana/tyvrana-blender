"""Packaged background topology and deformation validation."""

import subprocess
from pathlib import Path

from tyvrana_blender.operations import OPERATIONS

from .conftest import ROOT


def test_native_topology(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/topology_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "topology-native.log").write_text(log)
    print(log)
    assert result.returncode == 0, log
    assert "TOPOLOGY_NATIVE_PASSED" in log


async def test_topology_workflow_persists_across_hosts(
    profile: dict[str, str],
    tmp_path: Path,
) -> None:
    import json
    import math
    import time
    from typing import Any

    from tyvrana_blender.operations import REGISTRY

    from .conftest import running_blender
    from .test_e2e import core_client, discover, operation

    saved: dict[str, Any] = {}
    pids = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        for host in range(2):
            directory = tmp_path / f"host-{host}"
            directory.mkdir()
            env = dict(
                profile, TYVRANA_TEST_CONTROL=str(directory), TMPDIR=str(directory)
            )
            async with running_blender(env, directory, ui=False) as process:
                pids.append(process.pid)
                adapter = await discover(client)
                assert adapter is not None and adapter.operation_count == len(
                    OPERATIONS
                )

                async def call(
                    name: str, /, identifier: str = adapter.instance_id, **args: Any
                ) -> Any:
                    REGISTRY["blender." + name].parse(args)
                    return await operation(client, identifier, "blender." + name, args)

                lifecycle = await call("extension.inspect")
                assert lifecycle["build"] == lifecycle["implementation_build"]
                print("TOPOLOGY_MCP_BUILD", json.dumps(lifecycle))
                if host == 0:
                    schemas = await client.call_tool(
                        "tyvrana_list_operations",
                        dict(
                            adapter_id=adapter.instance_id,
                            names=[
                                "blender.mesh.inspect_topology",
                                "blender.mesh.insert_loops",
                                "blender.deformation.sweep",
                                "blender.retopo.insert_loop",
                            ],
                            include_schemas=True,
                        ),
                    )
                    assert not schemas.is_error
                    await call(
                        "mesh.create",
                        name="Source",
                        vertices=[[-2, -2, 0], [2, -2, 0], [2, 2, 0], [-2, 2, 0]],
                        faces=[[0, 1, 2, 3]],
                    )
                    await call(
                        "retopo.create_target", source_object="Source", name="Target"
                    )
                    await call(
                        "retopo.seed_patch",
                        source_object="Source",
                        target_object="Target",
                        center=[0, 0, 0],
                        tangent_direction=[1, 0, 0],
                        width=2,
                        height=2,
                        u_segments=4,
                        v_segments=1,
                    )
                    stamp = time.perf_counter()
                    vs = await call(
                        "mesh.query",
                        object_name="Target",
                        selector=dict(mode="all", domain="vertex"),
                        limit=256,
                    )
                    es = await call(
                        "mesh.query",
                        object_name="Target",
                        selector=dict(mode="all", domain="edge"),
                        limit=256,
                    )
                    points = {v["index"]: v["co"] for v in vs["elements"]}
                    edge = next(
                        e
                        for e in es["elements"]
                        if abs(
                            points[e["vertices"][0]][1] - points[e["vertices"][1]][1]
                        )
                        > 1.99
                    )
                    start = min(edge["vertices"], key=lambda v: points[v][1])
                    refined = await call(
                        "retopo.insert_loop",
                        source_object="Source",
                        target_object="Target",
                        edge=dict(
                            mode="indices", domain="edge", indices=[edge["index"]]
                        ),
                        from_vertex=start,
                        factors=[0.25, 0.5, 0.75],
                    )
                    print(
                        "MCP_NEW_LOOPS",
                        json.dumps(
                            dict(
                                seconds=time.perf_counter() - stamp,
                                after=refined["after"],
                            )
                        ),
                    )
                    assert refined["after"]["quad_count"] == 16
                    n = 12
                    ys = [-2, -1, 0, 1, 2]
                    vertices = [
                        [
                            0.3 * math.cos(i * 2 * math.pi / n),
                            y,
                            0.3 * math.sin(i * 2 * math.pi / n),
                        ]
                        for y in ys
                        for i in range(n)
                    ]
                    faces = [
                        [
                            j * n + i,
                            j * n + (i + 1) % n,
                            (j + 1) * n + (i + 1) % n,
                            (j + 1) * n + i,
                        ]
                        for j in range(len(ys) - 1)
                        for i in range(n)
                    ]
                    await call(
                        "mesh.create", name="Hinge", vertices=vertices, faces=faces
                    )
                    await call(
                        "armature.create",
                        name="Rig",
                        bones=[
                            dict(name="A", head=[0, -2, 0], tail=[0, 0, 0]),
                            dict(
                                name="B",
                                head=[0, 0, 0],
                                tail=[0, 2, 0],
                                parent="A",
                                connected=True,
                            ),
                        ],
                    )
                    await call(
                        "armature.bind",
                        object_name="Hinge",
                        armature_object="Rig",
                        weights=dict(method="envelopes", bones=["A", "B"]),
                    )
                    poses = [
                        dict(
                            name=str(i), bones=[dict(name="B", rotation=[angle, 0, 0])]
                        )
                        for i, angle in enumerate([0, 0.25, 0.6, 1.1, 1.5])
                    ]
                    stamp = time.perf_counter()
                    sweep = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Hinge"],
                        poses=poses,
                        bone_names=["B"],
                        sample_limit=0,
                    )
                    print(
                        "MCP_NEW_SWEEP",
                        json.dumps(
                            dict(seconds=time.perf_counter() - stamp, result=sweep)
                        ),
                    )
                    assert len(sweep["poses"]) == 5 and sweep["restored"]
                    region = dict(
                        frame=dict(kind="bone", object="Rig", bone="B"),
                        min=[-1, -1.01, -1],
                        max=[1, 1.01, 1],
                    )
                    query = await call(
                        "mesh.query",
                        object_name="Hinge",
                        selector=dict(
                            mode="region",
                            domain="edge",
                            region=region,
                            inclusion="all_vertices",
                        ),
                        limit=128,
                    )
                    rail = next(
                        e
                        for e in query["elements"]
                        if max(e["vertices"]) - min(e["vertices"]) == 12
                    )
                    await call(
                        "mesh.insert_loops",
                        object_name="Hinge",
                        cuts=[
                            dict(
                                edge=rail["index"],
                                from_vertex=min(rail["vertices"]),
                                factors=[0.25, 0.5, 0.75],
                            )
                        ],
                    )
                    summary = await call(
                        "mesh.inspect_topology",
                        object_name="Hinge",
                        selector=dict(mode="region", domain="vertex", region=region),
                        sample_limit=2,
                    )
                    assert summary["selected"]["vertices"] > 36
                    regional = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Hinge"],
                        poses=poses,
                        regions=[dict(name="joint", **region)],
                        sample_limit=1,
                    )
                    assert (
                        regional["poses"][3]["meshes"][0]["regions"][0]["vertex_count"]
                        > 0
                    )
                    saved = await call(
                        "mesh.inspect_topology", object_name="Hinge", sample_limit=0
                    )
                    await call("file.save", filepath=str(tmp_path / "topology.blend"))
                else:
                    await call(
                        "file.open",
                        filepath=str(tmp_path / "topology.blend"),
                        discard_current=True,
                    )
                    reopened = await call(
                        "mesh.inspect_topology", object_name="Hinge", sample_limit=0
                    )
                    assert reopened["topology_sha256"] == saved["topology_sha256"]
                    assert reopened["selected"] == saved["selected"]
                    result = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Hinge"],
                        poses=[
                            dict(
                                name="bend",
                                bones=[dict(name="B", rotation=[0.7, 0, 0])],
                            )
                        ],
                    )
                    assert result["restored"]
    assert len(set(pids)) == 2
