"""Existing attached guides and structural response across articulated motion."""

import json
import math
import time
from pathlib import Path
from typing import Any

from .conftest import running_blender
from .test_e2e import core_client, discover, operation


async def test_routed_path_and_volume_response(
    profile: dict[str, str], tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(name: str, /, **args: Any) -> Any:
                start = time.perf_counter()
                result = await operation(
                    client, adapter.instance_id, "blender." + name, args
                )
                calls.append(
                    dict(
                        operation=name,
                        argument_bytes=len(json.dumps(args).encode()),
                        result_bytes=len(json.dumps(result).encode()),
                        seconds=time.perf_counter() - start,
                    )
                )
                return result

            await call(
                "armature.create",
                name="Structure",
                bones=[dict(name="Pivot", head=[1, 0, 0], tail=[1, 1, 0])],
            )
            await call(
                "object_set.create",
                objects=[
                    dict(kind="empty", key="start", name="Anchor", location=[-1, 0, 0])
                ],
            )
            await call(
                "object.create_primitive",
                primitive="uv_sphere",
                name="Joint",
                location=[0, 0.4, 0],
                scale=[0.25, 0.25, 0.25],
            )
            await call(
                "curve.create",
                curves=[
                    dict(
                        name="Route",
                        splines=[
                            dict(
                                points=[
                                    dict(co=p)
                                    for p in [
                                        [-1, 0, 0],
                                        [-0.8, 0.3, 0.8],
                                        [0, 0.5, 1],
                                        [0.8, 0.3, 0.8],
                                        [1, 1, 0],
                                    ]
                                ]
                            )
                        ],
                        settings=dict(
                            resolution=8,
                            profile=dict(kind="circle", radius=0.035, resolution=8),
                        ),
                        bindings=[
                            dict(
                                spline=0,
                                point=0,
                                target=dict(kind="object", object="Anchor"),
                            ),
                            dict(
                                spline=0,
                                point=4,
                                target=dict(
                                    kind="bone", object="Structure", bone="Pivot"
                                ),
                                offset=[0, 1, 0],
                            ),
                        ],
                    )
                ],
            )
            await call(
                "loft.create",
                components=[
                    dict(
                        name="Response",
                        sides=16,
                        subdivisions=3,
                        sections=[
                            dict(center=[3, 0, 0], radii=[0.12] * 4),
                            dict(center=[3, 0.5, 0], radii=[0.25] * 4),
                            dict(center=[3, 1, 0], radii=[0.12] * 4),
                        ],
                    )
                ],
            )
            await call(
                "armature.pose",
                object_name="Structure",
                bones=[dict(name="Pivot", rotation=[0, 0, 0])],
            )
            await call(
                "action.edit",
                name="Articulation",
                create=True,
                channels=[
                    dict(
                        target=dict(
                            kind="transform",
                            object_name="Structure",
                            bone="Pivot",
                            property="rotation",
                            axis="z",
                        ),
                        keys=[
                            dict(frame=1, value=0),
                            dict(frame=9, value=math.pi / 4),
                            dict(frame=17, value=0),
                        ],
                    )
                ],
            )
            await call("action.assign", name="Articulation")
            await call(
                "coupling.configure",
                couplings=[
                    dict(
                        name="Response" + axis,
                        source=dict(
                            kind="transform",
                            object_name="Structure",
                            bone="Pivot",
                            property="rotation",
                            axis="z",
                        ),
                        target=dict(
                            kind="transform",
                            object_name="Response",
                            property="scale",
                            axis=axis,
                        ),
                        mapping=dict(
                            kind="remap",
                            input_min=0,
                            input_max=math.pi / 4,
                            output_min=1,
                            output_max=maximum,
                        ),
                    )
                    for axis, maximum in [
                        ("x", 1 / math.sqrt(1.2)),
                        ("y", 1.2),
                        ("z", 1 / math.sqrt(1.2)),
                    ]
                ],
            )
            await call("timeline.configure", frame=1)
            await call(
                "volume.snapshot",
                objects=[dict(source="Response", name="ResponseRest")],
            )
            await call(
                "object_set.configure",
                objects=[
                    dict(name="ResponseRest", hide_render=True, hide_viewport=True)
                ],
            )
            motion = await call(
                "motion.sample",
                range=dict(start=1, end=17, step=1),
                volumes=[
                    dict(object_name="Route"),
                    dict(object_name="Response", reference="ResponseRest"),
                ],
                couplings=["Responsex", "Responsey", "Responsez"],
                thresholds=[
                    dict(metric="volume.Route.attachment_error", maximum=0.0001),
                    dict(metric="volume.Response.ratio", minimum=0.98, maximum=1.02),
                ],
            )
            assert motion["restored"] and motion["violation_count"] == 0, motion
            clearance = await call(
                "geometry.inspect",
                pairs=[dict(left="Route", right="Joint")],
                adaptive=dict(
                    start=1,
                    end=17,
                    initial_samples=5,
                    max_samples=33,
                    minimum_step=0.125,
                ),
                worst_limit=2,
            )
            assert clearance["minimum_distance"] > 0.03, clearance
            values = {m["name"]: m for m in motion["metrics"]}
            assert (
                values["volume.Route.path_length"]["maximum"]
                > values["volume.Route.path_length"]["minimum"] + 0.01
            )
            assert values["volume.Response.ratio"]["maximum"] > 1.001
            final = await call("curve.inspect", names=["Route"], samples=5)
            assert final["curves"][0]["valid"]
            metrics = dict(
                calls=len(calls),
                operations=calls,
                argument_bytes=sum(r["argument_bytes"] for r in calls),
                result_bytes=sum(r["result_bytes"] for r in calls),
                seconds=sum(r["seconds"] for r in calls),
                minimum_clearance=clearance["minimum_distance"],
                coverage=clearance["coverage"],
                volume_ratio=values["volume.Response.ratio"],
                attachment_error=values["volume.Route.attachment_error"],
                path_length=values["volume.Route.path_length"],
                failures=0,
                limitations=(
                    "Authored route guides over bounded accepted articulation; "
                    "no automatic obstacle wrapping or tissue material simulation"
                ),
            )
            (tmp_path / "routed-response-metrics.json").write_text(
                json.dumps(metrics, indent=2)
            )
            print("ROUTED_RESPONSE_METRICS", json.dumps(metrics))
