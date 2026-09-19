"""Spatial coupled chains: closure, intermediate clearances and repeatability."""

import json
import time
from pathlib import Path
from typing import Any

from .conftest import running_blender
from .test_e2e import core_client, discover, operation


async def test_spatial_linkage(profile: dict[str, str], tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(name: str, /, **args: Any) -> Any:
                started = time.perf_counter()
                result = await operation(
                    client, adapter.instance_id, "blender." + name, args
                )
                calls.append(
                    dict(
                        operation=name,
                        argument_bytes=len(json.dumps(args).encode()),
                        result_bytes=len(json.dumps(result).encode()),
                        seconds=time.perf_counter() - started,
                    )
                )
                return result

            bones: list[dict[str, Any]] = [
                dict(name="A0", head=[-0.5, 0, 0], tail=[-0.65, 1, 0.2]),
                dict(
                    name="A1",
                    head=[-0.65, 1, 0.2],
                    tail=[0, 1.8, 0.5],
                    parent="A0",
                    connected=True,
                ),
                dict(name="B0", head=[0.5, 0, 0.4], tail=[0.65, 1, 0.7]),
                dict(
                    name="B1",
                    head=[0.65, 1, 0.7],
                    tail=[0, 1.8, 0.5],
                    parent="B0",
                    connected=True,
                ),
            ]
            await call("armature.create", name="Mechanism", bones=bones)
            await call(
                "object_set.create",
                objects=[
                    dict(kind="empty", key=n, name=n, location=p)
                    for n, p in [
                        ("Control", [0, 0, 0]),
                        ("Goal", [0, 1.5, 0.6]),
                        ("PoleA", [-2, 0, 2]),
                        ("PoleB", [2, 0, -2]),
                    ]
                ],
            )
            await call(
                "constraint.configure",
                constraints=[
                    dict(
                        name="Closure" + side,
                        owner=dict(object_name="Mechanism", bone=side + "1"),
                        settings=dict(
                            kind="ik",
                            target=dict(object_name="Goal"),
                            pole=dict(object_name="Pole" + side),
                            chain_length=2,
                            iterations=256,
                        ),
                    )
                    for side in "AB"
                ],
            )
            await call(
                "motion.set_properties",
                properties=[
                    dict(
                        object_name="Control",
                        name="fold",
                        value=0,
                        minimum=0,
                        maximum=1,
                    )
                ],
            )
            await call(
                "coupling.configure",
                couplings=[
                    dict(
                        name="Goal" + axis,
                        source=dict(
                            kind="property", object_name="Control", property="fold"
                        ),
                        target=dict(
                            kind="transform",
                            object_name="Goal",
                            property="location",
                            axis=axis,
                        ),
                        mapping=dict(
                            kind="piecewise",
                            knots=[
                                dict(input=f, output=v)
                                for f, v in zip([0, 0.5, 1], values, strict=True)
                            ],
                        ),
                    )
                    for axis, values in [
                        ("x", [-0.2, 0.25, 0]),
                        ("y", [1.7, 1.35, 0.9]),
                        ("z", [0.5, 0.8, 1.1]),
                    ]
                ],
            )
            await call(
                "action.edit",
                name="Fold",
                create=True,
                channels=[
                    dict(
                        target=dict(
                            kind="property", object_name="Control", property="fold"
                        ),
                        keys=[
                            dict(frame=1, value=0),
                            dict(frame=9, value=1),
                            dict(frame=17, value=0),
                        ],
                    )
                ],
            )
            await call("action.assign", name="Fold")
            await call(
                "loft.create",
                components=[
                    dict(
                        name=b["name"] + "Link",
                        sides=8,
                        subdivisions=1,
                        interpolation="linear",
                        sections=[
                            dict(
                                center=[
                                    a + (c - a) * t
                                    for a, c in zip(b["head"], b["tail"], strict=True)
                                ],
                                radii=[0.025] * 4,
                            )
                            for t in [0.15, 0.85]
                        ],
                    )
                    for b in bones
                ],
            )
            for bone in bones:
                await call(
                    "armature.bind",
                    object_name=bone["name"] + "Link",
                    armature_object="Mechanism",
                    weights=dict(method="envelopes", bones=[bone["name"]]),
                    preserve_volume=False,
                )

            def point(bone: str, endpoint: str) -> dict[str, Any]:
                return dict(
                    kind="bone", object="Mechanism", bone=bone, endpoint=endpoint
                )

            queries = []
            for side in "AB":
                queries.append(
                    dict(
                        kind="distance",
                        name="Closure" + side,
                        a=point(side + "1", "tail"),
                        b=dict(kind="object", object="Goal", point=[0, 0, 0]),
                        comparison=dict(target=0, tolerance=0.0001),
                    )
                )
                queries.append(
                    dict(
                        kind="distance",
                        name="Joint" + side,
                        a=point(side + "0", "tail"),
                        b=point(side + "1", "head"),
                        comparison=dict(target=0, tolerance=0.0001),
                    )
                )
            baseline = []
            start = len(calls)
            for frame in [1, 5, 9, 13, 17]:
                await call("timeline.configure", frame=frame)
                measured = await call("measurement.inspect", queries=queries)
                baseline.append(measured)
                assert all(m["within_tolerance"] for m in measured["measurements"]), (
                    measured
                )
            old_calls = calls[start:]
            print(
                "SPATIAL_BASELINE",
                json.dumps(dict(calls=old_calls, measurements=baseline)),
            )
            args = dict(range=dict(start=1, end=17, step=1), measurements=queries)
            sweep_start = len(calls)
            sampled = await call("motion.sample", **args)
            repeated = await call("motion.sample", **args)
            assert sampled["restored"] and sampled["violation_count"] == 0
            assert (
                sampled["metrics"] == repeated["metrics"]
                and sampled["details"] == repeated["details"]
            )
            clearance = await call(
                "geometry.inspect",
                pairs=[dict(left="A0Link", right="B0Link")],
                adaptive=dict(
                    start=1,
                    end=17,
                    initial_samples=5,
                    max_samples=33,
                    minimum_step=0.125,
                ),
                worst_limit=2,
            )
            assert clearance["minimum_distance"] > 0.05, clearance
            metrics = dict(
                calls=len(calls),
                operations=calls,
                baseline=old_calls,
                batched=calls[sweep_start : sweep_start + 1],
                residuals=sampled["metrics"],
                max_closure=max(
                    m["value"] for row in baseline for m in row["measurements"]
                ),
                clearance=clearance["minimum_distance"],
                coverage=clearance["coverage"],
                sampled_frames=sampled["sampled_frames"],
                processing_seconds=sampled["processing_seconds"],
                failures=0,
            )
            (tmp_path / "spatial-metrics.json").write_text(
                json.dumps(metrics, indent=2)
            )
            print("SPATIAL_LINKAGE_METRICS", json.dumps(metrics))
