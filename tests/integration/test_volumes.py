"""Packaged volume/layer diagnostics and fresh-process native persistence."""

import json
import subprocess
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import OPERATIONS, REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_volumes(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/volume_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "volume-native.log").write_text(log)
    print(log)
    assert result.returncode == 0, log
    assert "VOLUME_NATIVE_PASSED" in log


async def test_volumes_layers_and_guides_persist(
    profile: dict[str, str], tmp_path: Path
) -> None:
    pids = []
    saved: dict[str, Any] = {}
    queries = [
        dict(mode="reference", name="OuterMiddle"),
        dict(mode="current", source="Middle", target="Driver"),
    ]
    volume_queries = [
        dict(object_name="Guide", reference="Rest"),
        *[dict(object_name=n) for n in ["Driver", "Middle", "Outer"]],
    ]
    poses = [
        dict(name=n, bones=[dict(name="B", location=location, rotation=rotation)])
        for n, location, rotation in [
            ("rest", [0, 0, 0], [0, 0, 0]),
            ("mild", [0.1, -0.1, 0], [0.1, 0, 0]),
            ("medium", [0.2, -0.3, 0.1], [0.5, 0, 0]),
            ("near_limit", [0.4, -0.4, 0.3], [1.2, 0, 0]),
            ("combined", [-0.2, 0.3, 0.2], [0.6, 0.3, 0.4]),
        ]
    ]
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
                print("VOLUME_MCP_BUILD", json.dumps(build))
                if host == 0:
                    schema_args: dict[str, Any] = dict(
                        adapter_id=adapter.instance_id,
                        schemas="full",
                        names=[
                            "blender.volume.inspect",
                            "blender.volume.snapshot",
                            "blender.layer.inspect",
                            "blender.layer.capture_reference",
                            "blender.layer.remove_reference",
                            "blender.deformation.sweep",
                        ],
                    )
                    contracts = {}
                    while True:
                        schema = await client.call_tool(
                            "tyvrana_list_operations", schema_args
                        )
                        assert not schema.is_error
                        page = schema.structured_content
                        assert page["catalog_sha256"] == adapter.catalog_sha256
                        contracts.update(
                            {entry["name"]: entry for entry in page["operations"]}
                        )
                        if page["next_offset"] is None:
                            break
                        schema_args["offset"] = page["next_offset"]
                    assert len(contracts) == 6
                    assert (
                        contracts["blender.volume.inspect"]["arguments_schema"][
                            "properties"
                        ]["objects"]["maxItems"]
                        == 16
                    )
                    assert (
                        contracts["blender.layer.inspect"]["arguments_schema"]["$defs"][
                            "ReferenceName"
                        ]["maxLength"]
                        == 128
                    )
                    assert (
                        "volumes"
                        in contracts["blender.deformation.sweep"]["result_schema"][
                            "$defs"
                        ]["PoseEvaluation"]["properties"]
                    )
                    await call(
                        "armature.create",
                        name="Rig",
                        bones=[
                            dict(
                                name="A",
                                head=[0, 0, 0],
                                tail=[0, 1, 0],
                                envelope_distance=2,
                            ),
                            dict(
                                name="B",
                                head=[0, 2, 0],
                                tail=[0, 3, 0],
                                envelope_distance=2,
                            ),
                        ],
                    )
                    await call(
                        "curve.create",
                        curves=[
                            dict(
                                name=name,
                                role="volume",
                                tags=["layer-fixture"],
                                splines=[
                                    dict(
                                        type="BEZIER",
                                        points=[
                                            dict(co=[0, 0, 0], radius=0.6),
                                            dict(co=[0.2, 1, 0], radius=1),
                                            dict(co=[0, 2, 0], radius=0.6),
                                        ],
                                    )
                                ],
                                settings=dict(
                                    resolution=8,
                                    profile=dict(
                                        kind="circle",
                                        radius=r,
                                        resolution=16,
                                        caps=True,
                                    ),
                                ),
                                bindings=[
                                    dict(
                                        spline=0,
                                        point=i,
                                        target=dict(kind="bone", object="Rig", bone=b),
                                    )
                                    for i, b in [(0, "A"), (2, "B")]
                                ],
                            )
                            for name, r in [
                                ("Guide", 0.2),
                                ("MiddleGuide", 0.28),
                                ("OuterGuide", 0.36),
                            ]
                        ],
                    )
                    await call(
                        "volume.snapshot",
                        objects=[
                            dict(source=source, name=name)
                            for source, name in [
                                ("Guide", "Driver"),
                                ("Guide", "Rest"),
                                ("MiddleGuide", "Middle"),
                                ("OuterGuide", "Outer"),
                            ]
                        ],
                        tags=["layer-fixture"],
                    )
                    for name in ["Driver", "Middle"]:
                        await call(
                            "modifier.create",
                            object_name=name,
                            type="triangulate",
                            name="BindTriangles",
                        )
                        await call(
                            "modifier.apply",
                            object_name=name,
                            modifier_name="BindTriangles",
                        )
                    await call(
                        "armature.bind",
                        object_name="Driver",
                        armature_object="Rig",
                        weights=dict(method="envelopes", bones=["A", "B"]),
                        preserve_volume=True,
                    )
                    await call(
                        "surface_deform.bind",
                        driver="Driver",
                        driven=["Middle"],
                        bind_state="current",
                    )
                    await call(
                        "surface_deform.bind",
                        driver="Middle",
                        driven=["Outer"],
                        bind_state="current",
                    )
                    await call(
                        "layer.capture_reference",
                        references=[
                            dict(
                                name="OuterMiddle",
                                source="Outer",
                                target="Middle",
                                sample_count=128,
                            )
                        ],
                    )
                    initial = await call(
                        "volume.inspect", objects=volume_queries, section_samples=4
                    )
                    assert initial["volumes"][0]["path"]["attachment_error_max"] < 1e-5
                    await call(
                        "armature.pose",
                        object_name="Rig",
                        reset=True,
                        bones=poses[2]["bones"],
                    )
                    saved["volume"] = await call(
                        "volume.inspect", objects=volume_queries
                    )
                    saved["layer"] = await call(
                        "layer.inspect", queries=queries, worst_limit=4
                    )
                    assert all(v["valid"] for v in saved["layer"]["layers"])
                    saved["sweep"] = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Driver"],
                        sample_limit=0,
                        poses=poses,
                        volumes=volume_queries,
                        layers=dict(queries=queries, worst_limit=2),
                    )
                    assert saved["sweep"]["restored"]
                    assert all(
                        p["volumes"][0]["path"]["attachment_error_max"] < 1e-5
                        for p in saved["sweep"]["poses"]
                    )
                    print("VOLUME_MCP_SWEEP", json.dumps(saved["sweep"]))
                    # A controlled translation measures signed normal/tangent change.
                    for name, z, size in [("Base", 0, 4), ("Slip", 0.2, 1)]:
                        await call(
                            "object.create_primitive",
                            primitive="plane",
                            name=name,
                            location=[0, 0, z],
                            scale=[size, size, 1],
                        )
                    await call(
                        "layer.capture_reference",
                        references=[dict(name="SlipQA", source="Slip", target="Base")],
                    )
                    await call(
                        "object.set_transform", name="Slip", location=[0.25, -0.1, 0.23]
                    )
                    slip = await call(
                        "layer.inspect",
                        queries=[dict(mode="reference", name="SlipQA")],
                        ray_direction="opposite_source_normal",
                    )
                    assert (
                        abs(
                            slip["layers"][0]["tangential_movement"]["mean"]
                            - (0.25**2 + 0.1**2) ** 0.5
                        )
                        < 1e-5
                    )
                    assert abs(slip["layers"][0]["normal_change"]["mean"] - 0.03) < 1e-5
                    saved["slip"] = slip["layers"]
                    await call("file.save", filepath=str(tmp_path / "layers.blend"))
                else:
                    await call(
                        "file.open",
                        filepath=str(tmp_path / "layers.blend"),
                        discard_current=True,
                    )
                    volumes = await call("volume.inspect", objects=volume_queries)
                    layers = await call("layer.inspect", queries=queries, worst_limit=4)
                    assert volumes["volumes"] == saved["volume"]["volumes"]
                    assert layers["layers"] == saved["layer"]["layers"]
                    bindings = await call(
                        "surface_deform.inspect", objects=["Middle", "Outer"]
                    )
                    assert all(b["bound"] and b["valid"] for b in bindings["bindings"])
                    slip = await call(
                        "layer.inspect",
                        queries=[dict(mode="reference", name="SlipQA")],
                        ray_direction="opposite_source_normal",
                    )
                    assert slip["layers"] == saved["slip"]
                    await call("armature.pose", object_name="Rig", reset=True, bones=[])
                    sweep = await call(
                        "deformation.sweep",
                        armature_object="Rig",
                        objects=["Driver"],
                        sample_limit=0,
                        poses=poses,
                        volumes=volume_queries,
                        layers=dict(queries=queries, worst_limit=2),
                    )
                    assert sweep["poses"] == saved["sweep"]["poses"]
                    # One invalid reference is explicit and repair is removal.
                    await call("object.delete", name="Slip")
                    invalid = await call(
                        "layer.inspect", queries=[dict(mode="reference", name="SlipQA")]
                    )
                    assert not invalid["layers"][0]["valid"]
                    await call(
                        "layer.remove_reference", names=["SlipQA", "OuterMiddle"]
                    )
                    assert (await call("layer.inspect"))["references"] == []
    assert len(set(pids)) == 2
