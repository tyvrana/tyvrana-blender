"""Real MCP instructions, region operations and rendered native refinement."""

from pathlib import Path
from typing import Any

import pytest
from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation
from .test_modifier_render import fixed_scene


@pytest.mark.interactive
async def test_regions_through_mcp_core_and_blender(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_SCULPT"] = "smooth"
    async with core_client(tmp_path) as (client, port):
        assert client.instructions and "visual verification" in client.instructions
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            base = await call("mesh.inspect", object_name="Surface")
            scene = fixed_scene(await call("scene.inspect"))
            camera = await call("camera.inspect")
            light = await call("light.inspect")
            material = await call("material.inspect")
            await call("multires.create", object_name="Surface")
            await call("multires.subdivide", object_name="Surface", levels=2)
            await call(
                "multires.configure",
                object_name="Surface",
                viewport_level=2,
                sculpt_level=2,
                render_level=2,
            )
            initial = await call("sculpt.mask.inspect", object_name="Surface")
            assert not initial["effective_values_available"]
            faces = await call("sculpt.face_sets.inspect", object_name="Surface")
            assert not faces["authored"]
            assigned = await call(
                "sculpt.face_sets.assign",
                object_name="Surface",
                selector={
                    "mode": "normal",
                    "domain": "face",
                    "direction": [0, 0, 1],
                    "min_dot": 0.5,
                },
            )
            assert assigned["assigned_id"] == 2
            initialized = await call(
                "sculpt.face_sets.initialize", object_name="Surface", mode="loose_parts"
            )
            assert initialized["authored"] and len(initialized["face_sets"]) == 1
            before = await render(client, identifier)
            hit = await call(
                "scene.raycast", mode="camera", u=0.55, v=0.47, width=256, height=256
            )
            assert hit["hit"] and hit["object_name"] == "Surface"
            stroke = await call(
                "sculpt.mask.stroke",
                object_name="Surface",
                mode="add",
                samples=[{"location": hit["location_object"]}] * 16,
                radius=0.6,
                strength=1,
            )
            assert stroke["sample_count"] == 16
            await call("sculpt.mask.invert", object_name="Surface")
            result = await call(
                "sculpt.filter",
                object_name="Surface",
                type="smooth",
                strength=0.7,
                iterations=30,
            )
            assert result["changed"]
            await call("sculpt.mask.clear", object_name="Surface")
            await call(
                "sculpt.stroke",
                object_name="Surface",
                brush="smooth",
                samples=[{"location": hit["location_object"]}] * 4,
                radius=0.4,
                strength=0.5,
            )
            after = await render(client, identifier)
            assert mean_pixel_difference(before, after) > 0.05
            assert await call("mesh.inspect", object_name="Surface") == base
            assert fixed_scene(await call("scene.inspect")) == scene
            assert await call("camera.inspect") == camera
            assert await call("light.inspect") == light
            assert await call("material.inspect") == material
            assert (
                await call("sculpt.face_sets.inspect", object_name="Surface")
                == initialized
            )
            assert (await call("sculpt.inspect", object_name="Surface"))[
                "effective_sculpt_level"
            ] == 2
        await discover(client, empty=True)
