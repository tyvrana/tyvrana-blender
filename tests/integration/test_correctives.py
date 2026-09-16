"""Published-package corrective, transfer and fresh-process persistence workflow."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import OPERATIONS, REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_correctives(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/corrective_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "corrective-native.log").write_text(log)
    print(log)
    assert result.returncode == 0, log
    assert "CORRECTIVE_NATIVE_PASSED" in log


async def test_correctives_and_surface_binding_persist(
    profile: dict[str, str], tmp_path: Path
) -> None:
    pids = []
    saved: dict[str, Any] = {}
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

                build = await call("extension.inspect")
                assert build["build"] == build["implementation_build"]
                print("CORRECTIVE_MCP_BUILD", json.dumps(build))
                if host == 0:
                    schema = await client.call_tool(
                        "tyvrana_list_operations",
                        dict(
                            adapter_id=adapter.instance_id,
                            names=[
                                "blender.shape_keys.edit",
                                "blender.deformation.capture_target",
                                "blender.surface_deform.bind",
                                "blender.weights.transfer",
                            ],
                        ),
                    )
                    assert not schema.is_error
                    for name, n in [
                        ("Source", 2),
                        ("Target", 4),
                        ("Driver", 2),
                        ("Outer", 4),
                    ]:
                        points = [
                            [2 * x / n - 1, 2 * y / n, 0.1 if name == "Outer" else 0]
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
                            "mesh.create", name=name, vertices=points, faces=faces
                        )
                    await call(
                        "armature.create",
                        name="Rig",
                        bones=[
                            dict(
                                name="L",
                                head=[-1, 0, 0],
                                tail=[-1, 2, 0],
                                envelope_distance=5,
                            ),
                            dict(
                                name="R",
                                head=[1, 0, 0],
                                tail=[1, 2, 0],
                                envelope_distance=5,
                            ),
                        ],
                    )
                    for name in ["Source", "Target"]:
                        await call(
                            "armature.bind",
                            object_name=name,
                            armature_object="Rig",
                            weights=dict(method="envelopes", bones=["L", "R"]),
                            preserve_volume=False,
                        )
                    await call(
                        "weights.assign",
                        object_name="Source",
                        layers=[
                            dict(
                                selector=dict(mode="all", domain="vertex"),
                                weights=dict(
                                    mode="gradient",
                                    start=[-1, 0, 0],
                                    end=[1, 0, 0],
                                    start_influences=[dict(bone="L", weight=1)],
                                    end_influences=[dict(bone="R", weight=1)],
                                    interpolation="linear",
                                ),
                            )
                        ],
                    )
                    stamp = time.perf_counter()
                    transfer = await call(
                        "weights.transfer",
                        source="Source",
                        target="Target",
                        groups=[
                            dict(source="L", target="L"),
                            dict(source="R", target="R"),
                        ],
                        max_distance=0.01,
                    )
                    elapsed = time.perf_counter() - stamp
                    weights = await call(
                        "weights.inspect", object_name="Target", sample_limit=32
                    )
                    print(
                        "MCP_NEW_TRANSFER",
                        json.dumps(
                            dict(seconds=elapsed, result=transfer, after=weights)
                        ),
                    )
                    assert weights["non_normalized_vertex_count"] == 0
                    pose = [
                        dict(name="L", rotation=[0.3, 0.2, 0]),
                        dict(name="R", rotation=[0.5, -0.2, 0.1]),
                    ]
                    await call("armature.pose", object_name="Rig", bones=pose)
                    await call(
                        "deformation.capture_target",
                        object_name="Target",
                        name="Desired",
                    )
                    await call(
                        "mesh.transform",
                        object_name="Desired",
                        selector=dict(mode="all", domain="vertex"),
                        translation=[0, 0, 0.1],
                    )
                    before = await call(
                        "deformation.compare",
                        pairs=[dict(object_name="Target", target="Desired")],
                    )
                    await call(
                        "shape_keys.edit",
                        object_name="Target",
                        keys=[
                            dict(
                                name="Correction",
                                create=True,
                                value=1,
                                correction=dict(
                                    mode="captured_target", target="Desired"
                                ),
                            )
                        ],
                    )
                    after = await call(
                        "deformation.compare",
                        pairs=[dict(object_name="Target", target="Desired")],
                    )
                    assert (
                        before["comparisons"][0]["rms"] > 0.09
                        and after["comparisons"][0]["rms"] < 1e-5
                    )
                    print(
                        "MCP_CORRECTION_QUALITY",
                        json.dumps(dict(before=before, after=after)),
                    )
                    await call(
                        "vertex_groups.configure",
                        object_name="Target",
                        groups=[
                            dict(
                                name="Mask",
                                create=True,
                                layers=[
                                    dict(
                                        selector=dict(mode="all", domain="vertex"),
                                        weight=0.5,
                                    )
                                ],
                            )
                        ],
                    )
                    await call(
                        "shape_keys.edit",
                        object_name="Target",
                        keys=[
                            dict(
                                name="Secondary",
                                create=True,
                                value=0.25,
                                vertex_group="Mask",
                                correction=dict(mode="region", delta=[0.02, 0, 0]),
                            ),
                            dict(name="Correction", value=0.75),
                        ],
                    )
                    await call(
                        "surface_deform.bind",
                        driver="Driver",
                        driven=["Outer"],
                        bind_state="current",
                    )
                    await call(
                        "shape_keys.edit",
                        object_name="Driver",
                        keys=[
                            dict(
                                name="Lift",
                                create=True,
                                value=1,
                                correction=dict(mode="region", delta=[0, 0, 0.2]),
                            )
                        ],
                    )
                    outer = await call("mesh.inspect_evaluated", object_name="Outer")
                    assert abs(outer["bounds_min"][2] - 0.3) < 1e-5
                    sweep = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Target"],
                        sample_limit=0,
                        poses=[
                            dict(
                                name="rest",
                                shape_values=[
                                    dict(
                                        object_name="Target", key="Correction", value=0
                                    ),
                                    dict(
                                        object_name="Target", key="Secondary", value=0
                                    ),
                                ],
                            ),
                            dict(
                                name="target",
                                bones=pose,
                                shape_values=[
                                    dict(
                                        object_name="Target", key="Correction", value=1
                                    ),
                                    dict(
                                        object_name="Target", key="Secondary", value=0
                                    ),
                                ],
                                targets=[dict(object_name="Target", target="Desired")],
                            ),
                        ],
                    )
                    assert (
                        sweep["restored"]
                        and sweep["poses"][1]["target_deviations"][0]["rms"] < 1e-5
                    )
                    saved = dict(
                        keys=await call(
                            "shape_keys.inspect",
                            object_name="Target",
                            detail_key="Correction",
                            detail_limit=4,
                        ),
                        outer=outer,
                        bindings=await call(
                            "surface_deform.inspect", objects=["Outer"]
                        ),
                        pose=pose,
                        sweep=sweep,
                    )
                    assert saved["keys"]["keys"][1]["value"] == 0.75
                    await call(
                        "file.save", filepath=str(tmp_path / "correctives.blend")
                    )
                else:
                    await call(
                        "file.open",
                        filepath=str(tmp_path / "correctives.blend"),
                        discard_current=True,
                    )
                    keys = await call(
                        "shape_keys.inspect",
                        object_name="Target",
                        detail_key="Correction",
                        detail_limit=4,
                    )
                    assert keys == saved["keys"]
                    bindings = await call("surface_deform.inspect", objects=["Outer"])
                    assert (
                        bindings["bindings"] == saved["bindings"]["bindings"]
                        and bindings["bindings"][0]["valid"]
                    )
                    outer = await call("mesh.inspect_evaluated", object_name="Outer")
                    assert (
                        outer["evaluated_basis"]["geometry_sha256"]
                        == saved["outer"]["evaluated_basis"]["geometry_sha256"]
                    )
                    await call("armature.pose", object_name="Rig", reset=True, bones=[])
                    sweep = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Target"],
                        sample_limit=0,
                        poses=[
                            dict(
                                name="target",
                                bones=saved["pose"],
                                shape_values=[
                                    dict(
                                        object_name="Target", key="Correction", value=1
                                    ),
                                    dict(
                                        object_name="Target", key="Secondary", value=0
                                    ),
                                ],
                                targets=[dict(object_name="Target", target="Desired")],
                            )
                        ],
                    )
                    assert sweep["poses"][0]["target_deviations"][0]["rms"] < 1e-5
                    await call(
                        "shape_keys.edit",
                        object_name="Driver",
                        keys=[dict(name="Lift", value=0)],
                    )
                    moved = await call("mesh.inspect_evaluated", object_name="Outer")
                    assert abs(moved["bounds_min"][2] - 0.1) < 1e-5
                    await call("surface_deform.unbind", objects=["Outer"])
                    assert not (
                        await call("surface_deform.inspect", objects=["Outer"])
                    )["bindings"][0]["bound"]
    assert len(set(pids)) == 2
