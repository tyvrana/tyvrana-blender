import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tyvrana_protocol import JsonValue

from tyvrana_blender.light_models import LightInspectResult, LightSummary
from tyvrana_blender.models import SceneSummary

from ..png import channel_means, mean_pixel_difference
from .conftest import running_blender
from .test_cameras import error, render
from .test_e2e import core_client, discover, operation


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_light_controls_and_real_illumination_over_mcp(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    profile["TYVRANA_TEST_LIGHTING"] = "1"
    with ThreadPoolExecutor(max_workers=1) as executor:
        async with core_client(tmp_path) as (client, port):
            profile["TYVRANA_TEST_PORT"] = str(port)
            async with running_blender(profile, tmp_path, ui=ui):
                registered = await discover(client)
                assert registered is not None
                identifier = registered.instance_id
                assert all(
                    "blender.light." + name in registered.operations
                    for name in ("inspect", "create", "configure")
                )
                assert await operation(
                    client, identifier, "blender.light.inspect", {}
                ) == {"lights": []}
                scene_before = SceneSummary.model_validate(
                    await operation(client, identifier, "blender.scene.inspect", {})
                )
                point = LightSummary.model_validate(
                    await operation(
                        client,
                        identifier,
                        "blender.light.create",
                        {
                            "type": "point",
                            "name": "Point",
                            "energy": 25,
                            "location": [-3, -4, 6],
                            "radius": 0.02,
                        },
                    )
                )
                assert point.type == "point" and point.energy == 25
                dark = await render(client, identifier)
                point = LightSummary.model_validate(
                    await operation(
                        client,
                        identifier,
                        "blender.light.configure",
                        {"name": "Point", "energy": 1500},
                    )
                )
                bright = await render(client, identifier)
                dark_mean = (
                    sum(
                        await asyncio.get_running_loop().run_in_executor(
                            executor, channel_means, dark, (108, 112, 148, 172)
                        )
                    )
                    / 3
                )
                bright_mean = (
                    sum(
                        await asyncio.get_running_loop().run_in_executor(
                            executor, channel_means, bright, (108, 112, 148, 172)
                        )
                    )
                    / 3
                )
                assert (
                    dark_mean > 0 and bright_mean > dark_mean * 2 and bright_mean > 30
                )
                print("LIGHT_ENERGY", dark_mean, bright_mean)
                await operation(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Point", "color": [1, 0.015, 0.015]},
                )
                red = await render(client, identifier)
                await operation(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Point", "color": [0.015, 0.015, 1]},
                )
                blue = await render(client, identifier)
                red_rgb, blue_rgb = (
                    await asyncio.get_running_loop().run_in_executor(
                        executor, channel_means, red, (108, 112, 148, 172)
                    ),
                    await asyncio.get_running_loop().run_in_executor(
                        executor, channel_means, blue, (108, 112, 148, 172)
                    ),
                )
                assert red_rgb[0] > red_rgb[2] * 2 and blue_rgb[2] > blue_rgb[0] * 2
                print("LIGHT_COLOR", red_rgb, blue_rgb)
                await operation(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Point", "color": [1, 1, 1]},
                )
                await operation(
                    client,
                    identifier,
                    "blender.object.set_transform",
                    {"name": "Point", "location": [4, 1, 6]},
                )
                moved = await render(client, identifier)
                movement = await asyncio.get_running_loop().run_in_executor(
                    executor, mean_pixel_difference, bright, moved
                )
                assert movement > 3
                print("LIGHT_POSITION_MEAN_DIFFERENCE", movement)
                await operation(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Point", "radius": 2},
                )
                soft = await render(client, identifier)
                softness = await asyncio.get_running_loop().run_in_executor(
                    executor, mean_pixel_difference, moved, soft
                )
                assert softness > 1
                print("LIGHT_RADIUS_MEAN_DIFFERENCE", softness)
                await error(
                    client,
                    identifier,
                    "blender.light.create",
                    {"type": "area", "name": "Point"},
                    "invalid_arguments",
                )
                current = await operation(
                    client, identifier, "blender.light.inspect", {}
                )
                await error(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Point", "energy": 123, "angle": 1},
                    "invalid_arguments",
                )
                assert (
                    await operation(client, identifier, "blender.light.inspect", {})
                    == current
                )
                await operation(
                    client, identifier, "blender.object.delete", {"name": "Point"}
                )
                sun = LightSummary.model_validate(
                    await operation(
                        client,
                        identifier,
                        "blender.light.create",
                        {
                            "type": "sun",
                            "name": "Sun",
                            "energy": 2,
                            "rotation": [0.3, -0.5, 0],
                        },
                    )
                )
                assert sun.type == "sun"
                await operation(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Sun", "angle": 0.05, "exposure": -1, "normalize": True},
                )
                sunlight = await render(client, identifier)
                await operation(
                    client,
                    identifier,
                    "blender.object.set_transform",
                    {"name": "Sun", "rotation": [1.2, 0.6, 0]},
                )
                rotated = await render(client, identifier)
                rotation = await asyncio.get_running_loop().run_in_executor(
                    executor, mean_pixel_difference, sunlight, rotated
                )
                assert rotation > 3
                print("SUN_ROTATION_MEAN_DIFFERENCE", rotation)
                await operation(
                    client, identifier, "blender.object.delete", {"name": "Sun"}
                )
                cases: list[tuple[str, dict[str, JsonValue], dict[str, JsonValue]]] = [
                    (
                        "spot",
                        {"radius": 0.25, "spot_size": 1.5},
                        {"spot_blend": 0.5, "radius": 0.5},
                    ),
                    (
                        "area",
                        {"shape": "rectangle", "size": 2, "size_y": 3},
                        {"shape": "ellipse", "size_y": 4},
                    ),
                ]
                for kind, create_fields, configure_fields in cases:
                    arguments: dict[str, JsonValue] = {
                        "type": kind,
                        "name": kind,
                        **create_fields,
                    }
                    created = LightSummary.model_validate(
                        await operation(
                            client, identifier, "blender.light.create", arguments
                        )
                    )
                    assert created.type == kind
                    changed = LightSummary.model_validate(
                        await operation(
                            client,
                            identifier,
                            "blender.light.configure",
                            {
                                "name": kind,
                                "energy": 300,
                                "use_shadow": False,
                                **configure_fields,
                            },
                        )
                    )
                    assert changed.energy == 300 and not changed.use_shadow
                    await operation(
                        client, identifier, "blender.object.delete", {"name": kind}
                    )
                assert (
                    LightInspectResult.model_validate(
                        await operation(client, identifier, "blender.light.inspect", {})
                    ).lights
                    == []
                )
                await error(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "RenderCube", "energy": 10},
                    "object_not_light",
                )
                await error(
                    client,
                    identifier,
                    "blender.light.configure",
                    {"name": "Missing"},
                    "object_not_found",
                )
                scene_after = SceneSummary.model_validate(
                    await operation(client, identifier, "blender.scene.inspect", {})
                )
                assert scene_before.objects == scene_after.objects
            await discover(client, empty=True)
