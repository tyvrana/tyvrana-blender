"""Packaged typed motion, combined diagnostics and new-host action persistence."""

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import OPERATIONS, REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def transform(
    obj: str = "Rig", bone: str | None = None, prop: str = "rotation", axis: str = "x"
) -> dict[str, Any]:
    return dict(kind="transform", object_name=obj, bone=bone, property=prop, axis=axis)


def test_native_motion(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/motion_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "motion-native.log").write_text(log)
    print(log)
    assert result.returncode == 0, log
    assert "MOTION_NATIVE_PASSED" in log


async def test_motion_corrective_layers_persist(
    profile: dict[str, str], tmp_path: Path
) -> None:
    pids = []
    saved: dict[str, Any] = {}
    qa: dict[str, Any] = dict(
        frames=[1, 6, 11, 16, 21],
        armature_object="Rig",
        bones=["A", "B", "C"],
        objects=["Surface"],
        couplings=["Oppose", "Follow", "Support"],
        volumes=[
            dict(object_name="Surface"),
            dict(object_name="Guide", reference="GuideRest"),
        ],
        layers=dict(queries=[dict(mode="reference", name="Layer")], worst_limit=0),
        detail_frames=[1, 11],
        targets=[dict(object_name="Surface", target="Desired")],
    )
    async with core_client(tmp_path) as (client, port):
        assert client.instructions
        guidance = " ".join(client.instructions.lower().split())
        for concept in ("infer", "workflow", "dependencies", "requested result"):
            assert concept in guidance
        assert "visual" in client.instructions.lower()
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
                print("MOTION_MCP_BUILD", json.dumps(build))
                if host == 0:
                    names = [
                        "blender." + n
                        for n in [
                            "timeline.inspect",
                            "timeline.configure",
                            "motion.set_properties",
                            "coupling.configure",
                            "coupling.inspect",
                            "coupling.remove",
                            "action.edit",
                            "action.assign",
                            "action.inspect",
                            "action.remove",
                            "motion.sample",
                        ]
                    ]
                    request: dict[str, Any] = dict(
                        adapter_id=adapter.instance_id,
                        names=names,
                        include_schemas=True,
                    )
                    contracts = {}
                    while True:
                        result = await client.call_tool(
                            "tyvrana_list_operations", request
                        )
                        assert not result.is_error
                        page = result.structured_content
                        contracts.update(
                            {row["name"]: row for row in page["operations"]}
                        )
                        if page["next_offset"] is None:
                            break
                        request["offset"] = page["next_offset"]
                    assert len(contracts) == 11
                    assert contracts["blender.motion.sample"]["effect"] == "transient"
                    assert (
                        "expression"
                        not in contracts["blender.coupling.configure"][
                            "arguments_schema"
                        ]["properties"]
                    )
                    await call(
                        "armature.create",
                        name="Rig",
                        bones=[
                            dict(
                                name=n,
                                head=[0, 0, 0],
                                tail=[0, 2, 0],
                                envelope_distance=5,
                                limits=dict(x=dict(minimum=-1.6, maximum=1.6)),
                            )
                            for n in ["A", "B", "C"]
                        ],
                    )
                    await call(
                        "object.create_primitive", primitive="cube", name="Surface"
                    )
                    await call(
                        "armature.bind",
                        object_name="Surface",
                        armature_object="Rig",
                        preserve_volume=False,
                        weights=dict(method="envelopes", bones=["A", "B"]),
                    )
                    await call(
                        "armature.pose",
                        object_name="Rig",
                        bones=[
                            dict(name="A", rotation=[1, 0, 0]),
                            dict(name="B", rotation=[-1, 0, 0]),
                        ],
                    )
                    await call(
                        "deformation.capture_target",
                        object_name="Surface",
                        name="Desired",
                    )
                    await call(
                        "mesh.transform",
                        object_name="Desired",
                        selector=dict(mode="all", domain="vertex"),
                        scale=[1, 1 / math.cos(1), 1 / math.cos(1)],
                        pivot="origin",
                    )
                    saved["before_rms"] = (
                        await call(
                            "deformation.compare",
                            pairs=[dict(object_name="Surface", target="Desired")],
                        )
                    )["comparisons"][0]["rms"]
                    await call(
                        "shape_keys.edit",
                        object_name="Surface",
                        keys=[
                            dict(
                                name="Support",
                                create=True,
                                value=1,
                                correction=dict(
                                    mode="captured_target", target="Desired"
                                ),
                            )
                        ],
                    )
                    await call(
                        "shape_keys.edit",
                        object_name="Surface",
                        keys=[dict(name="Support", value=0)],
                    )
                    await call("armature.pose", object_name="Rig", reset=True, bones=[])
                    await call(
                        "volume.snapshot",
                        objects=[dict(source="Surface", name="Outer")],
                    )
                    await call(
                        "mesh.transform",
                        object_name="Outer",
                        selector=dict(mode="all", domain="vertex"),
                        scale=[1.1, 1.1, 1.1],
                        pivot="origin",
                    )
                    await call(
                        "surface_deform.bind",
                        driver="Surface",
                        driven=["Outer"],
                        bind_state="current",
                    )
                    await call(
                        "layer.capture_reference",
                        references=[
                            dict(name="Layer", source="Outer", target="Surface")
                        ],
                    )
                    await call(
                        "curve.create",
                        curves=[
                            dict(
                                name="Guide",
                                splines=[
                                    dict(
                                        type="POLY",
                                        points=[
                                            dict(co=[0, -2, 0], radius=0.5),
                                            dict(co=[0.2, 0, 0], radius=1),
                                            dict(co=[0, 2, 0], radius=0.5),
                                        ],
                                    )
                                ],
                                settings=dict(
                                    profile=dict(
                                        kind="circle",
                                        radius=0.1,
                                        resolution=8,
                                        caps=True,
                                    )
                                ),
                                bindings=[
                                    dict(
                                        spline=0,
                                        point=0,
                                        offset=[0, -2, 0],
                                        target=dict(
                                            kind="bone", object="Rig", bone="A"
                                        ),
                                    ),
                                    dict(
                                        spline=0,
                                        point=2,
                                        offset=[0, 2, 0],
                                        target=dict(
                                            kind="bone", object="Rig", bone="B"
                                        ),
                                    ),
                                ],
                            )
                        ],
                    )
                    await call(
                        "volume.snapshot",
                        objects=[dict(source="Guide", name="GuideRest")],
                    )
                    await call(
                        "coupling.configure",
                        couplings=[
                            dict(
                                name="Oppose",
                                source=transform(bone="A"),
                                target=transform(bone="B"),
                                mapping=dict(
                                    kind="linear",
                                    scale=-1,
                                    clamp=dict(minimum=-1.6, maximum=1.6),
                                ),
                            ),
                            dict(
                                name="Follow",
                                source=transform(bone="A"),
                                target=transform(bone="C"),
                                mapping=dict(
                                    kind="linear",
                                    scale=0.5,
                                    clamp=dict(minimum=-1.6, maximum=1.6),
                                ),
                            ),
                            dict(
                                name="Support",
                                source=transform(bone="A"),
                                target=dict(
                                    kind="shape", object_name="Surface", key="Support"
                                ),
                                mapping=dict(
                                    kind="remap",
                                    input_min=0,
                                    input_max=1,
                                    output_min=0,
                                    output_max=1,
                                ),
                            ),
                        ],
                    )
                    await call(
                        "object.create_primitive",
                        primitive="cube",
                        name="Mover",
                        location=[4, 0, 0],
                    )
                    await call(
                        "action.edit",
                        name="Clip",
                        create=True,
                        channels=[
                            dict(
                                target=transform(bone="A"),
                                keys=[
                                    dict(frame=1, value=0),
                                    dict(frame=11, value=1),
                                    dict(frame=16, value=1.5),
                                    dict(frame=21, value=-0.5),
                                ],
                            ),
                            dict(
                                target=transform("Mover", prop="location"),
                                keys=[
                                    dict(frame=1, value=4, interpolation="CONSTANT"),
                                    dict(frame=21, value=5),
                                ],
                            ),
                            dict(
                                target=transform("Mover", prop="location", axis="z"),
                                keys=[
                                    dict(frame=1, value=0, interpolation="BEZIER"),
                                    dict(frame=21, value=2),
                                ],
                            ),
                        ],
                    )
                    await call("action.assign", name="Clip")
                    bad = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id=adapter.instance_id,
                            operation="blender.coupling.configure",
                            arguments=dict(
                                couplings=[
                                    dict(
                                        name="Cycle",
                                        source=transform(bone="B"),
                                        target=transform(bone="A", axis="z"),
                                        mapping=dict(
                                            kind="linear",
                                            clamp=dict(minimum=-1, maximum=1),
                                        ),
                                    )
                                ]
                            ),
                        ),
                    )
                    assert bad.is_error
                    await call(
                        "timeline.configure",
                        frame=6,
                        subframe=0.25,
                        frame_start=1,
                        frame_end=21,
                        fps=30,
                    )
                else:
                    await call(
                        "file.open",
                        filepath=str(tmp_path / "motion.blend"),
                        discard_current=True,
                    )
                timeline = await call("timeline.inspect")
                assert timeline["frame"] == 6 and timeline["subframe"] == 0.25
                clip = await call("action.inspect", name="Clip", key_limit=8)
                assert (
                    clip["channel_count"] == 3
                    and clip["key_count"] == 8
                    and clip["slot_count"] == 2
                )
                relationships = await call("coupling.inspect")
                assert all(
                    r["valid"] and r["mapping_error"] < 1e-5
                    for r in relationships["couplings"]
                )
                result = await call("motion.sample", **qa)
                assert result["restored"] and result["violation_count"] == 0
                assert await call("timeline.inspect") == timeline
                metrics = {m["name"]: m for m in result["metrics"]}
                assert metrics["coupling.Support.target_value"]["maximum"] == 1
                assert metrics["volume.Guide.attachment_error"]["maximum"] < 1e-5
                assert metrics["layer.0.valid"]["minimum"] == 1
                target_frame = next(
                    row for row in result["details"] if row["frame"] == 11
                )
                assert target_frame["values"]["target.Surface.Desired.rms"] < 1e-5
                assert saved["before_rms"] > 0.1
                if host == 0:
                    saved.update(qa=result, clip=clip, couplings=relationships)
                    await call("file.save", filepath=str(tmp_path / "motion.blend"))
                else:
                    assert clip == saved["clip"]
                    assert relationships == saved["couplings"]
                    assert result["metrics"] == saved["qa"]["metrics"]
                    assert result["details"] == saved["qa"]["details"]
                    assert (await call("surface_deform.inspect", objects=["Outer"]))[
                        "bindings"
                    ][0]["valid"]
                    await call(
                        "action.edit",
                        name="Clip",
                        channels=[
                            dict(
                                target=transform("Mover", prop="location"),
                                keys=[dict(frame=21, value=6)],
                            )
                        ],
                    )
                    assert (await call("action.inspect", name="Clip"))["key_count"] == 8
                    print(
                        "MOTION_MCP_PERSISTENCE",
                        json.dumps(
                            dict(
                                pids=pids,
                                qa_bytes=len(json.dumps(result).encode()),
                                before_rms=saved["before_rms"],
                                target_rms=target_frame["values"][
                                    "target.Surface.Desired.rms"
                                ],
                                metrics=result["metrics"],
                            )
                        ),
                    )
    assert len(set(pids)) == 2
