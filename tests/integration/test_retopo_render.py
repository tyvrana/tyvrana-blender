"""All retopology operations and visible quad cages through MCP/core/Blender."""

from pathlib import Path
from typing import Any

from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference, rgb_pixels
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation


def cage_pixels(data: bytes) -> int:
    rgb = rgb_pixels(data)
    return sum(
        g > r * 1.2 and b > r * 1.2 and g > 130
        for r, g, b in zip(rgb[::3], rgb[1::3], rgb[2::3], strict=True)
    )


async def test_build_grow_project_relax_and_render_cage(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_RETOPO"] = "empty"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None and len(registered.operations) == 84
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            source = await call("mesh.inspect", object_name="Surface")
            camera, light = await call("camera.inspect"), await call("light.inspect")
            before = await render(client, identifier)
            (tmp_path / "source.png").write_bytes(before)
            hit = await call("scene.raycast", mode="camera", u=0.5, v=0.5)
            assert hit["hit"] and hit["object_name"] == "Surface"
            made = await call(
                "retopo.create_target", source_object="Surface", name="Cage"
            )
            assert made["mesh"]["vertex_count"] == 0
            pair: dict[str, JsonValue] = {
                "source_object": "Surface",
                "target_object": made["target_object"],
            }
            await call(
                "material.assign",
                object_name=made["target_object"],
                material_name="Cage display",
            )
            seed = await call(
                "retopo.seed_patch",
                **pair,
                center=hit["location_object"],
                tangent_direction=[1, 0, 0],
                width=0.8,
                height=0.7,
                u_segments=4,
                v_segments=3,
                surface_offset=0.015,
            )
            assert seed["after"]["quad_count"] == 12
            query = await call(
                "mesh.query",
                object_name=made["target_object"],
                selector={"mode": "boundary", "domain": "edge"},
                limit=128,
            )
            vertices = await call(
                "mesh.query",
                object_name=made["target_object"],
                selector={"mode": "all", "domain": "vertex"},
                limit=128,
            )
            coordinates = {v["index"]: v["co"] for v in vertices["elements"]}
            chosen = [
                item["index"]
                for item in query["elements"]
                if all(coordinates[i][1] > 0.25 for i in item["vertices"])
            ]
            assert chosen
            grown = await call(
                "retopo.extrude_boundary",
                **pair,
                selector={"mode": "indices", "domain": "edge", "indices": chosen},
                offset=[0, 0.2, 0],
                rotation=[0, 0, 0.12],
                scale=[1.15, 1, 1],
                surface_offset=0.015,
            )
            assert grown["created"]["faces"] == 4
            await call(
                "retopo.project",
                **pair,
                selector={"mode": "all", "domain": "vertex"},
                surface_offset=0.015,
            )
            relaxed = await call(
                "retopo.relax",
                **pair,
                selector={"mode": "all", "domain": "vertex"},
                surface_offset=0.015,
            )
            assert not relaxed["indices_invalidated"]
            info = await call("retopo.inspect", **pair)
            assert info["target"]["quad_count"] == 16
            assert info["target"]["triangle_count"] == info["target"]["ngon_count"] == 0
            assert info["authored_correspondence"]["vertices"]["max_distance"] < 0.016
            assert info["target"]["mesh"]["face_count"] < source["face_count"] / 100
            after = await render(client, identifier)
            (tmp_path / "cage.png").write_bytes(after)
            assert mean_pixel_difference(before, after) > 0.2
            # Thin antialiased cyan lines remain distinct from the gray source.
            # The upper bound rejects an opaque filled patch masquerading as a cage.
            assert cage_pixels(before) + 100 < cage_pixels(after) < 1200
            assert await call("mesh.inspect", object_name="Surface") == source
            assert await call("camera.inspect") == camera
            assert await call("light.inspect") == light
        await discover(client, empty=True)


async def test_bridge_curved_bands_through_mcp(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_RETOPO"] = "tube"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            pair: dict[str, JsonValue] = {
                "source_object": "Surface",
                "target_object": "Cage",
            }
            source = await call("mesh.inspect", object_name="Surface")
            before = await render(client, identifier)
            info = await call("retopo.inspect", **pair)
            loops = sorted(
                info["target"]["boundaries"], key=lambda b: b["bounds_min"][2]
            )
            result = await call(
                "retopo.bridge_loops",
                **pair,
                loop_a={
                    "mode": "indices",
                    "domain": "edge",
                    "indices": loops[1]["edge_indices"]["indices"],
                },
                loop_b={
                    "mode": "indices",
                    "domain": "edge",
                    "indices": loops[2]["edge_indices"]["indices"],
                },
                segments=3,
                surface_offset=0.02,
            )
            assert result["created"]["faces"] == 24
            assert result["after"]["boundary_loop_count"] == 2
            assert result["after"]["quad_count"] == 40
            assert result["correspondence_after"]["vertices"]["max_distance"] < 0.021
            after = await render(client, identifier)
            (tmp_path / "bridge-before.png").write_bytes(before)
            (tmp_path / "bridge-after.png").write_bytes(after)
            assert mean_pixel_difference(before, after) > 0.1
            assert cage_pixels(after) > cage_pixels(before) + 50
            assert await call("mesh.inspect", object_name="Surface") == source
        await discover(client, empty=True)
