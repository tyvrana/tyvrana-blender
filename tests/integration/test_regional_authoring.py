"""Constructive shell, openings, branches, joint cup and thin web via MCP."""

import base64
import json
import math
import time
from pathlib import Path
from typing import Any

from .conftest import running_blender
from .test_e2e import core_client, discover


async def test_regional_surface_authoring(
    profile: dict[str, str], tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    images: set[int] = set()
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

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
                calls.append(
                    dict(
                        operation=name,
                        argument_bytes=len(json.dumps(args).encode()),
                        result_bytes=len(response.model_dump_json().encode()),
                        seconds=time.perf_counter() - start,
                    )
                )
                assert not response.is_error, response.content
                for item in response.content:
                    if item.type == "image":
                        index = sum(r["operation"] == "render.image" for r in calls)
                        images.add(index)
                        (tmp_path / f"regional-{index}.png").write_bytes(
                            base64.b64decode(item.data)
                        )
                return response.structured_content["result"]

            def component(
                name: str,
                sections: list[tuple[list[float], float | list[float]]],
                **kwargs: Any,
            ) -> dict[str, Any]:
                return dict(
                    name=name,
                    sections=[
                        dict(center=c, radii=r if isinstance(r, list) else [r] * 4)
                        for c, r in sections
                    ],
                    sides=24,
                    subdivisions=3,
                    **kwargs,
                )

            components = [
                component(
                    "Shell",
                    [
                        ([0, -2, 0], 0.5),
                        ([0, -1, 0], [0.85, 0.9, 0.55, 0.6]),
                        ([0, 0.7, 0.05], [1, 1, 0.7, 0.65]),
                        ([0, 2, 0], 0.45),
                    ],
                ),
                component(
                    "Cavity",
                    [
                        ([0, -2.2, 0], 0.32),
                        ([0, -1, 0], [0.66, 0.71, 0.38, 0.4]),
                        ([0, 0.7, 0.05], [0.8, 0.8, 0.5, 0.45]),
                        ([0, 2.2, 0], 0.28),
                    ],
                ),
                component(
                    "PortA",
                    [([-1.5, -0.65, 0], 0.24), ([1.5, -0.65, 0], 0.24)],
                    x_reference=[0, 1, 0],
                ),
                component(
                    "PortB",
                    [([-1.5, 0.85, 0.05], 0.3), ([1.5, 0.85, 0.05], 0.3)],
                    x_reference=[0, 1, 0],
                ),
                component(
                    "Ridge",
                    [
                        ([0, -1.5, -0.45], [0.12, 0.12, 0.2, 0.1]),
                        ([0, 0, -0.8], [0.16, 0.16, 0.45, 0.25]),
                        ([0, 1.5, -0.4], [0.1, 0.1, 0.15, 0.12]),
                    ],
                ),
                component(
                    "ProcessA",
                    [
                        ([-0.55, -0.2, 0.25], 0.24),
                        ([-1.15, -0.1, 0.55], 0.18),
                        ([-1.65, 0.05, 0.85], 0.08),
                    ],
                    x_reference=[0, 1, 0],
                ),
                component(
                    "ProcessB",
                    [
                        ([0.55, -0.2, 0.25], 0.24),
                        ([1.15, -0.1, 0.55], 0.18),
                        ([1.65, 0.05, 0.85], 0.08),
                    ],
                    x_reference=[0, 1, 0],
                ),
                component(
                    "Cup",
                    [
                        ([2.7, -0.5, 0], 0.28),
                        ([2.7, 0, 0], 0.55),
                        ([2.7, 0.5, 0], 0.65),
                    ],
                ),
                component(
                    "CupCavity",
                    [
                        ([2.7, -0.25, 0], 0.08),
                        ([2.7, 0.05, 0], 0.38),
                        ([2.7, 0.7, 0], 0.57),
                    ],
                ),
            ]
            made = await call("loft.create", components=components)
            assert all(c["valid"] for c in made["components"])
            for object_name, operand, operation in [
                ("Shell", "Ridge", "union"),
                ("Shell", "ProcessA", "union"),
                ("Shell", "ProcessB", "union"),
                ("Shell", "Cavity", "difference"),
                ("Shell", "PortA", "difference"),
                ("Shell", "PortB", "difference"),
                ("Cup", "CupCavity", "difference"),
            ]:
                await call(
                    "modifier.create",
                    object_name=object_name,
                    type="boolean",
                    name=operand,
                    settings=dict(
                        operation=operation, solver="exact", operand_object=operand
                    ),
                )
                await call(
                    "modifier.apply", object_name=object_name, modifier_name=operand
                )
            await call(
                "object_set.configure",
                objects=[
                    dict(name=n, hide_render=True, hide_viewport=True)
                    for n in [
                        "Ridge",
                        "ProcessA",
                        "ProcessB",
                        "Cavity",
                        "PortA",
                        "PortB",
                        "CupCavity",
                    ]
                ],
            )
            await call(
                "mesh.create",
                name="Web",
                vertices=[
                    [-1.55, 0.08, 0.78],
                    [-0.6, 0.3, 0.28],
                    [0.6, 0.3, 0.28],
                    [1.55, 0.08, 0.78],
                    [0.6, 1.1, 0.4],
                    [-0.6, 1.1, 0.4],
                ],
                faces=[[0, 1, 5], [1, 2, 4, 5], [2, 3, 4]],
            )
            await call(
                "modifier.create",
                object_name="Web",
                type="solidify",
                settings=dict(thickness=0.025, even_thickness=True),
            )
            await call("sculpt.voxel_remesh", object_name="Shell", voxel_size=0.06)
            await call(
                "modifier.create",
                object_name="Shell",
                type="corrective_smooth",
                settings=dict(iterations=4, factor=0.4),
            )
            summaries = []
            for name in ["Shell", "Cup", "Web"]:
                row = await call("mesh.inspect_evaluated", object_name=name)
                summaries.append(row)
                assert row["manifold_summary"]["non_manifold_edge_count"] == 0, row
                assert row["manifold_summary"]["boundary_edge_count"] == 0, row
            qa = await call(
                "geometry.inspect",
                objects=[
                    dict(object_name=n, self_intersection=True)
                    for n in ["Shell", "Cup", "Web"]
                ],
                max_triangle_tests=1000000,
                worst_limit=4,
            )
            (tmp_path / "regional-qa.json").write_text(json.dumps(qa, indent=2))
            assert all(
                o["closed_consistent"] and o["self_contact_triangle_pairs"] == 0
                for o in qa["samples"][0]["objects"]
            ), qa
            assert all(
                o["degenerate_triangles"] == 0 for o in qa["samples"][0]["objects"]
            ), qa
            await call(
                "camera.create",
                name="Inspection",
                projection="orthographic",
                ortho_scale=7.0,
                location=[7, -8, 6],
                rotation=[math.atan2(math.hypot(6.5, 8), 6), 0, math.atan2(6.5, 8)],
                make_active=True,
            )
            await call(
                "light.create",
                name="Key",
                type="area",
                energy=2000,
                size=5,
                location=[2, -3, 7],
            )
            for index in [1, 2, 3]:
                if index == 2:
                    await call(
                        "object.set_transform",
                        name="Inspection",
                        location=[1, 0, 9],
                        rotation=[0, 0, 0],
                    )
                if index == 3:
                    await call(
                        "object.set_transform",
                        name="Inspection",
                        location=[6, 8, -4],
                        rotation=[
                            math.atan2(math.hypot(5.5, 8), -4),
                            0,
                            math.atan2(5.5, -8),
                        ],
                    )
                    await call(
                        "light.create",
                        name="Underside",
                        type="area",
                        energy=1000,
                        size=5,
                        location=[0, 3, -5],
                        rotation=[math.pi, 0, 0],
                    )
                status = await call(
                    "render.image",
                    width=640,
                    height=640,
                    cycles=dict(samples=16, device="cpu"),
                    budget=dict(max_seconds=30),
                )
                while status["state"] not in ["succeeded", "failed", "cancelled"]:
                    status = await call(
                        "render.status",
                        job_id=status["job_id"],
                        after_revision=status["revision"],
                        wait_seconds=20,
                    )
                assert status["state"] == "succeeded", status
                if index not in images:
                    await call("render.result", job_id=status["job_id"])
            metrics = dict(
                calls=len(calls),
                argument_bytes=sum(r["argument_bytes"] for r in calls),
                response_bytes=sum(r["result_bytes"] for r in calls),
                seconds=sum(r["seconds"] for r in calls),
                operations=calls,
                loft_seconds=made["processing_seconds"],
                generated_vertices=sum(c["vertex_count"] for c in made["components"]),
                authored_sections=sum(len(c["sections"]) for c in components),
                evaluated=[
                    dict(
                        name=s["object_name"],
                        vertices=s["vertex_count"],
                        faces=s["face_count"],
                        manifold=s["manifold_summary"],
                    )
                    for s in summaries
                ],
                failures=0,
                scope=(
                    "Argument bytes; full MCP response bytes including images; "
                    "excludes discovery"
                ),
            )
            (tmp_path / "regional-metrics.json").write_text(
                json.dumps(metrics, indent=2)
            )
            print("REGIONAL_AUTHORING_METRICS", json.dumps(metrics))
