"""Surface-conforming density, flow and gap finishing through actual MCP."""

from pathlib import Path
from typing import Any

import pytest
from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation
from .test_retopo_render import cage_pixels


def edge_selector(values: list[int]) -> dict[str, JsonValue]:
    return {"mode": "indices", "domain": "edge", "indices": [v for v in values]}


@pytest.mark.parametrize("mode", ["loop", "flow", "seam", "gap"])
async def test_finishing_real_render(
    profile: dict[str, str], tmp_path: Path, mode: str
) -> None:
    profile["TYVRANA_TEST_FINISH"] = mode
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None and len(registered.operations) == 87
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            async def edit(op: str, **args: JsonValue) -> Any:
                return await call(
                    "retopo." + op,
                    source_object="Surface",
                    target_object="Cage",
                    surface_offset=0.015,
                    **args,
                )

            async def info() -> Any:
                return await call(
                    "retopo.inspect", source_object="Surface", target_object="Cage"
                )

            async def topology() -> tuple[dict[int, list[float]], list[Any]]:
                summary = await call("mesh.inspect", object_name="Cage")
                domains = {}
                for domain in ["vertex", "edge"]:
                    items = []
                    for start in range(0, summary[domain + "_count"], 256):
                        response = await call(
                            "mesh.query",
                            object_name="Cage",
                            selector={
                                "mode": "indices",
                                "domain": domain,
                                "indices": list(
                                    range(
                                        start,
                                        min(start + 256, summary[domain + "_count"]),
                                    )
                                ),
                            },
                            limit=256,
                        )
                        assert not response["truncated"]
                        items.extend(response["elements"])
                    domains[domain] = items
                return {v["index"]: v["co"] for v in domains["vertex"]}, domains["edge"]

            source = await call("mesh.inspect", object_name="Surface")
            camera = await call("camera.inspect")
            lights = await call("light.inspect")
            materials = await call("material.inspect")
            before = await render(client, identifier)
            initial = await info()
            coords, edges = await topology()
            if mode == "loop":
                edge = next(
                    e
                    for e in edges
                    if abs(coords[e["vertices"][0]][2] - coords[e["vertices"][1]][2])
                    > 0.8
                )
                inserted = await edit(
                    "insert_loop", edge=edge_selector([edge["index"]])
                )
                assert inserted["created"]["vertices"] == 32
                coords, edges = await topology()
                chosen = set(inserted["flow_edges"]["indices"])
                selected = {
                    v for e in edges if e["index"] in chosen for v in e["vertices"]
                }
                anchor = next(
                    v
                    for e in edges
                    if len(set(e["vertices"]) & selected) == 1
                    for v in e["vertices"]
                    if v not in selected and coords[v][2] > 0.3
                )
                moved = await edit(
                    "slide",
                    selector=edge_selector(sorted(chosen)),
                    toward_vertex=anchor,
                    factor=0.15,
                )
                assert not moved["indices_invalidated"]
                coords, edges = await topology()
                ring = [
                    e["index"]
                    for e in edges
                    if abs(coords[e["vertices"][0]][2] - coords[e["vertices"][1]][2])
                    > 0.1
                    and all(coords[v][2] > 0.6 for v in e["vertices"])
                ]
                refined = await edit("subdivide", selector=edge_selector(ring))
                assert refined["created"]["vertices"] == 32
            elif mode == "flow":
                # Select a short transverse edge in the deliberately dense strip.
                edge = min(
                    (
                        e
                        for e in edges
                        if all(-0.65 < coords[v][0] < -0.35 for v in e["vertices"])
                        and abs(
                            coords[e["vertices"][0]][0] - coords[e["vertices"][1]][0]
                        )
                        > 0.005
                    ),
                    key=lambda e: sum(
                        (coords[e["vertices"][0]][i] - coords[e["vertices"][1]][i]) ** 2
                        for i in range(3)
                    ),
                )
                reduced = await edit(
                    "collapse",
                    selector=edge_selector([edge["index"]]),
                    mode="ring",
                    allow_boundary=True,
                )
                assert reduced["removed"]["vertices"] > 0
                # Prepare a convex curved neighborhood through explicit vertex
                # slides before redirecting a new quad pair.
                coords, edges = await topology()
                candidates = [
                    e
                    for e in edges
                    if e["manifold"]
                    and all(
                        coords[v][0] > 0.1 and abs(coords[v][1]) < 0.3
                        for v in e["vertices"]
                    )
                ]
                assert candidates
                diagonal = candidates[0]
                a, b = diagonal["vertices"]
                guides = []
                for current, other in [(a, b), (b, a)]:
                    neighbors = [
                        v
                        for e in edges
                        if current in e["vertices"]
                        for v in e["vertices"]
                        if v not in {current, other}
                    ]
                    toward = max(
                        neighbors,
                        key=lambda v: sum(
                            (coords[v][i] - coords[current][i])
                            * (coords[current][i] - coords[other][i])
                            for i in range(3)
                        ),
                    )
                    guides.append((current, toward))
                for current, toward in guides:
                    await edit(
                        "slide",
                        selector={
                            "mode": "indices",
                            "domain": "vertex",
                            "indices": [current],
                        },
                        toward_vertex=toward,
                        factor=0.2,
                    )
                await edit("rotate_edge", edge=edge_selector([diagonal["index"]]))
                await edit(
                    "relax", selector={"mode": "all", "domain": "vertex"}, iterations=5
                )
            elif mode == "seam":
                a = [
                    e
                    for e in edges
                    if e["boundary"]
                    and all(-0.01 < coords[v][0] < 0 for v in e["vertices"])
                ]
                b = [
                    e
                    for e in edges
                    if e["boundary"]
                    and all(0 < coords[v][0] < 0.01 for v in e["vertices"])
                ]
                av = {v for e in a for v in e["vertices"]}
                bv = {v for e in b for v in e["vertices"]}
                welded = await edit(
                    "stitch",
                    chain_a=edge_selector([e["index"] for e in a]),
                    chain_b=edge_selector([e["index"] for e in b]),
                    start_a=min(av, key=lambda v: coords[v][1]),
                    start_b=min(bv, key=lambda v: coords[v][1]),
                )
                assert welded["removed"]["vertices"] == 5
            else:
                hole = min(
                    initial["target"]["boundaries"], key=lambda b: b["perimeter"]
                )
                filled = await edit(
                    "fill_boundary",
                    boundary=edge_selector(hole["edge_indices"]["indices"]),
                    corner_vertex=min(hole["vertex_indices"]["indices"]),
                    span=2,
                )
                assert filled["created"]["faces"] == 4
            final = await info()
            after = await render(client, identifier)
            (tmp_path / "before.png").write_bytes(before)
            (tmp_path / "after.png").write_bytes(after)
            assert mean_pixel_difference(before, after) > 0.02
            assert cage_pixels(before) > 100 and cage_pixels(after) > 100
            quality = final["target"]
            assert quality["quad_count"] == quality["mesh"]["face_count"]
            assert (
                quality["non_manifold_edge_count"]
                == quality["inconsistent_winding_edge_count"]
                == quality["degenerate_face_count"]
                == 0
            )
            assert final["authored_correspondence"]["vertices"]["max_distance"] < 0.016
            if mode in {"seam", "gap"}:
                assert (
                    quality["boundary_loop_count"]
                    == initial["target"]["boundary_loop_count"] - 1
                )
            if mode == "flow":
                assert any(
                    not p["boundary"] and p["valence"] in {3, 5}
                    for p in quality["poles"]
                )
            assert await call("mesh.inspect", object_name="Surface") == source
            assert await call("camera.inspect") == camera
            assert await call("light.inspect") == lights
            assert await call("material.inspect") == materials
            assert not final["target_modifiers"]
        await discover(client, empty=True)
