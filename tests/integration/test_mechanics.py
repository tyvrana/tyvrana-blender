"""Packaged native mechanics qualification."""

import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_mechanics(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/mechanics_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "mechanics-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "MECHANICS_NATIVE_PASSED 6" in log


async def test_combined_mechanics_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    """One typed workflow with derived frames and no per-frame client loop."""
    import json
    from typing import Any

    from .conftest import running_blender
    from .test_e2e import core_client

    records: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):

            async def tool(name: str, arguments: dict[str, Any]) -> Any:
                response = await client.call_tool(name, arguments)
                records.append(
                    dict(
                        tool=name,
                        operation=arguments.get("operation"),
                        request_bytes=len(
                            json.dumps(
                                dict(name=name, arguments=arguments),
                                separators=(",", ":"),
                            ).encode()
                        ),
                        response_bytes=len(
                            response.model_dump_json(by_alias=True).encode()
                        ),
                        failed=response.is_error,
                    )
                )
                (tmp_path / "mechanics-mcp-calls.json").write_text(
                    json.dumps(records, indent=2)
                )
                assert not response.is_error, response.content
                return response.structured_content

            adapter = await tool(
                "tyvrana_list_adapters", dict(application="blender", wait_seconds=12)
            )
            assert len(adapter["adapters"]) == 1, adapter
            adapter_id = adapter["adapters"][0]["instance_id"]
            await tool(
                "tyvrana_list_operations",
                dict(
                    adapter_id=adapter_id,
                    names=[
                        "blender.geometry.fit",
                        "blender.contact.inspect",
                        "blender.motion.sample",
                    ],
                    schemas="arguments",
                ),
            )

            async def call(op: str, /, **args: Any) -> Any:
                result = await tool(
                    "tyvrana_execute_operation",
                    dict(
                        adapter_id=adapter_id, operation="blender." + op, arguments=args
                    ),
                )
                assert result["result"].get("error") is None, result
                return result["result"]

            unrelated = [f"Unrelated{i:03}" for i in range(320)]
            for start in range(0, 320, 64):
                await call(
                    "object_set.create",
                    objects=[
                        dict(kind="empty", key=n, name=n)
                        for n in unrelated[start : start + 64]
                    ],
                )
            await call(
                "loft.create",
                components=[
                    dict(
                        name="Tube",
                        sections=[
                            dict(center=[0, 0, -2], radii=[1, 1, 1, 1]),
                            dict(center=[0, 0, 2], radii=[1, 1, 1, 1]),
                        ],
                        sides=48,
                        subdivisions=2,
                        interpolation="linear",
                        caps=False,
                    )
                ],
            )
            await call(
                "object.create_primitive",
                name="Probe",
                primitive="uv_sphere",
                scale=[0.98, 0.98, 0.98],
            )
            fitted = await call(
                "geometry.fit",
                fits=[
                    dict(
                        name="Hinge",
                        method="cylinder",
                        regions=[dict(object_name="Tube")],
                        tolerance=0.0001,
                    )
                ],
            )
            fit = fitted["fits"][0]
            assert fit["status"] == "FITTED" and fit["frame"] is not None, fit
            assert fit["maximum_error"] < 0.0001
            # Native fitted values are consumed directly, without client geometry math.
            await call(
                "armature.create",
                name="Rig",
                space="world",
                bones=[
                    dict(
                        name=n,
                        **fit["frame"],
                        deform=False,
                        limits=dict(y=dict(minimum=-0.7, maximum=0.7)),
                    )
                    for n in ["A", "B"]
                ],
            )
            source = dict(
                kind="transform",
                object_name="Rig",
                bone="A",
                property="rotation",
                axis="y",
            )
            await call(
                "coupling.configure",
                couplings=[
                    dict(
                        name="Follow",
                        source=source,
                        target={**source, "bone": "B"},
                        mapping=dict(
                            kind="linear",
                            scale=-0.5,
                            clamp=dict(minimum=-0.7, maximum=0.7),
                        ),
                    ),
                    dict(
                        name="ProbeFollow",
                        source=source,
                        target=dict(
                            kind="transform",
                            object_name="Probe",
                            property="rotation",
                            axis="z",
                        ),
                        mapping=dict(kind="linear", scale=1),
                    ),
                ],
            )
            await call(
                "action.edit",
                name="Sweep",
                create=True,
                channels=[
                    dict(
                        target=source,
                        keys=[dict(frame=1, value=0), dict(frame=17, value=0.5)],
                    )
                ],
            )
            await call("action.assign", name="Sweep")
            await call("timeline.configure", frame=5, subframe=0.25)
            envelope = dict(
                name="Seat",
                source=dict(
                    object_name="Probe",
                    selector=dict(
                        mode="box", domain="vertex", min=[-2, -2, -0.1], max=[2, 2, 0.1]
                    ),
                ),
                target=dict(object_name="Tube"),
                mode="oriented_patch",
                allowed_side="negative",
                minimum_gap=-0.01,
                maximum_gap=0.04,
            )
            contact = await call(
                "contact.inspect", envelopes=[envelope], max_tests=2000000
            )
            assert contact["envelopes"][0]["classification"] == "PERMITTED_CONTACT", (
                contact
            )
            scene = await call("scene.inspect", limit=1)
            assert scene["object_count"] == 323, scene

            async def snapshot() -> Any:
                return [
                    await call("timeline.inspect"),
                    await call(
                        "object_set.inspect",
                        names=["Rig", "Probe", "Tube"],
                        fields=["transforms"],
                    ),
                    await call(
                        "armature.inspect", object_name="Rig", bone_names=["A", "B"]
                    ),
                ]

            def point(bone: str, endpoint: str) -> dict[str, str]:
                return dict(kind="bone", object="Rig", bone=bone, endpoint=endpoint)

            before = await snapshot()
            sampled = await call(
                "motion.sample",
                range=dict(start=1, end=17),
                armature_object="Rig",
                bones=["A", "B"],
                objects=["Probe"],
                couplings=["Follow", "ProbeFollow"],
                contacts=[envelope],
                contact_max_tests=2000000,
                measurements=[
                    dict(
                        kind="distance",
                        name="Closure",
                        a=point("A", "head"),
                        b=point("B", "head"),
                        comparison=dict(target=0, tolerance=0.0001),
                    ),
                    dict(
                        kind="distance",
                        name="Length",
                        a=point("A", "head"),
                        b=point("A", "tail"),
                        comparison=dict(target=1, tolerance=0.0001),
                    ),
                ],
            )
            assert sampled["restored"] and sampled["scoped_object_count"] == 3, sampled
            assert len(sampled["sampled_frames"]) == 17 and not sampled["details"]
            assert sampled["violation_count"] == 0, sampled
            assert sampled["contacts"][0]["counts"]["PERMITTED_CONTACT"] == 17, sampled
            assert await snapshot() == before
            await call("action.assign", name="Sweep", detach=True)
            await call("coupling.remove", names=["Follow", "ProbeFollow"])
            await call("action.remove", name="Sweep")
            names = unrelated + ["Probe", "Tube", "Rig"]
            for start in range(0, len(names), 256):
                removed = await call(
                    "object_set.remove", names=names[start : start + 256]
                )
                assert removed["error"] is None and not removed["remaining"], removed
            assert (await call("scene.inspect", limit=1))["object_count"] == 0
            evidence = dict(
                calls=len(records),
                request_bytes=sum(r["request_bytes"] for r in records),
                response_bytes=sum(r["response_bytes"] for r in records),
                failures=sum(r["failed"] for r in records),
                retries=0,
                samples=17,
                total_objects=323,
                scoped_objects=3,
                manual_xyz_solving=False,
                per_frame_calls=False,
                cleanup=True,
                fit=fit,
                contact=contact,
                sampled=sampled,
                calls_detail=records,
            )
            (tmp_path / "mechanics-mcp-metrics.json").write_text(
                json.dumps(evidence, indent=2)
            )
            print("MECHANICS_MCP_METRICS", json.dumps(evidence))
