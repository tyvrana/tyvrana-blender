import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation
from .test_modifier_render import fixed_scene


@pytest.mark.parametrize("brush", ["draw", "smooth"])
async def test_render_pick_sculpt_and_verify_through_mcp(
    profile: dict[str, str], tmp_path: Path, brush: str
) -> None:
    profile["TYVRANA_TEST_SCULPT"] = brush
    async with core_client(tmp_path) as (client, port):
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
            assert not (await call("multires.inspect", object_name="Surface"))[
                "present"
            ]
            assert (await call("multires.create", object_name="Surface"))[
                "total_levels"
            ] == 0
            await call("multires.subdivide", object_name="Surface", levels=2)
            await call(
                "multires.configure",
                object_name="Surface",
                viewport_level=2,
                sculpt_level=2,
                render_level=2,
            )
            state = await call("sculpt.inspect", object_name="Surface")
            assert state["view3d_available"] and state["scale_applied"]
            assert state["effective_sculpt_level"] == 2
            before = await render(client, identifier)
            hit = await call(
                "scene.raycast", mode="camera", u=0.55, v=0.47, width=256, height=256
            )
            assert hit["hit"] and hit["object_name"] == "Surface"
            result = await call(
                "sculpt.stroke",
                object_name="Surface",
                brush=brush,
                samples=[{"location": hit["location_object"]}]
                * (40 if brush == "smooth" else 8),
                radius=0.4,
                strength=0.5,
            )
            assert result["changed"]
            after = await render(client, identifier)
            assert await call("mesh.inspect", object_name="Surface") == base
            assert fixed_scene(await call("scene.inspect")) == scene
            assert await call("camera.inspect") == camera
            assert await call("light.inspect") == light
            assert await call("material.inspect") == material
            assert (await call("multires.inspect", object_name="Surface"))[
                "total_levels"
            ] == 2
            with ThreadPoolExecutor(max_workers=1) as pool:
                difference = await asyncio.get_running_loop().run_in_executor(
                    pool, mean_pixel_difference, before, after
                )
            assert difference > 0.15
            (tmp_path / "before.png").write_bytes(before)
            (tmp_path / "after.png").write_bytes(after)
            print(
                "SCULPT_RENDER",
                brush,
                "image_difference",
                difference,
                "hit",
                hit["location_object"],
                "result",
                result,
            )
        await discover(client, empty=True)
