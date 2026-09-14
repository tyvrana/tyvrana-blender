"""Render, analyze, rebuild, reinspect and sculpt through the complete MCP path."""

from pathlib import Path
from typing import Any

from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation
from .test_modifier_render import fixed_scene


async def test_remesh_then_sculpt_through_mcp(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_REMESH"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None and len(registered.operations) == 84
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            scene = fixed_scene(await call("scene.inspect"))
            camera = await call("camera.inspect")
            light = await call("light.inspect")
            materials = await call("material.inspect")
            before = await render(client, identifier)
            analysis = await call(
                "sculpt.voxel_remesh.inspect", object_name="Surface", voxel_size=0.05
            )
            assert not analysis["blockers"]
            result = await call(
                "sculpt.voxel_remesh", object_name="Surface", voxel_size=0.05
            )
            assert result["indices_invalidated"]
            assert result["after"]["vertex_count"] > result["before"]["vertex_count"]
            for statistic in ("edge_length", "face_area"):
                assert (
                    result["after"][statistic]["coefficient_of_variation"]
                    < result["before"][statistic]["coefficient_of_variation"] * 0.8
                )
            for a, b in zip(
                result["before"]["bounds_max"],
                result["after"]["bounds_max"],
                strict=True,
            ):
                assert abs(a - b) < 0.08
            rebuilt = await render(client, identifier)
            assert mean_pixel_difference(before, rebuilt) > 0.05
            state = await call("mesh.inspect", object_name="Surface")
            assert state["vertex_count"] == result["after"]["vertex_count"]
            await call(
                "mesh.query",
                object_name="Surface",
                selector={"mode": "all", "domain": "face"},
                limit=4,
            )
            await call("sculpt.inspect", object_name="Surface")
            hit = await call(
                "scene.raycast", mode="camera", u=0.62, v=0.48, width=256, height=256
            )
            assert hit["hit"] and hit["object_name"] == "Surface"
            changed = await call(
                "sculpt.stroke",
                object_name="Surface",
                brush="draw",
                samples=[{"location": hit["location_object"]}] * 6,
                radius=0.4,
                strength=0.7,
            )
            assert changed["changed"]
            after = await render(client, identifier)
            assert mean_pixel_difference(rebuilt, after) > 0.05
            final = await call("mesh.inspect", object_name="Surface")
            assert final["vertex_count"] == state["vertex_count"]
            assert final["face_count"] == state["face_count"]
            assert fixed_scene(await call("scene.inspect")) == scene
            assert await call("camera.inspect") == camera
            assert await call("light.inspect") == light
            assert await call("material.inspect") == materials
        await discover(client, empty=True)
