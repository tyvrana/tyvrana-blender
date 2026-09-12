import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tyvrana_protocol import JsonValue

from tyvrana_blender.material_models import (
    MaterialAssignResult,
    MaterialInspectResult,
    MaterialSummary,
)
from tyvrana_blender.models import SceneSummary

from ..png import channel_means, highlight_statistics, mean_pixel_difference
from .conftest import running_blender
from .test_cameras import error, render
from .test_e2e import core_client, discover, operation


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_material_controls_and_rendered_surface_over_mcp(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    profile["TYVRANA_TEST_MATERIAL"] = "1"
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop = asyncio.get_running_loop()
        async with core_client(tmp_path) as (client, port):
            profile["TYVRANA_TEST_PORT"] = str(port)
            async with running_blender(profile, tmp_path, ui=ui):
                registered = await discover(client)
                assert registered is not None
                identifier = registered.instance_id

                async def material(
                    name: str, arguments: dict[str, JsonValue]
                ) -> dict[str, JsonValue]:
                    result = await operation(
                        client, identifier, "blender.material." + name, arguments
                    )
                    assert isinstance(result, dict)
                    return result

                initial = MaterialInspectResult.model_validate(
                    await material("inspect", {})
                )
                assert (
                    next(
                        m for m in initial.materials if m.name == "CustomGraph"
                    ).surface
                    == "custom"
                )
                scene = SceneSummary.model_validate(
                    await operation(client, identifier, "blender.scene.inspect", {})
                )
                camera = await operation(
                    client, identifier, "blender.camera.inspect", {}
                )
                lights = await operation(
                    client, identifier, "blender.light.inspect", {}
                )
                created = MaterialSummary.model_validate(
                    await material(
                        "create_principled",
                        {
                            "name": "SurfaceA",
                            "base_color": [0.65, 0.015, 0.015],
                            "roughness": 0.8,
                        },
                    )
                )
                assert created.surface == "principled" and created.assignments == []
                assigned = MaterialAssignResult.model_validate(
                    await material(
                        "assign",
                        {"object_name": "RenderSubject", "material_name": "SurfaceA"},
                    )
                )
                assert assigned.slots == ["SurfaceA"] and assigned.assigned_slot == 0
                red = await render(client, identifier)
                await material(
                    "configure_principled",
                    {"name": "SurfaceA", "base_color": [0.015, 0.015, 0.65]},
                )
                blue = await render(client, identifier)
                box = (100, 110, 156, 156)
                red_rgb = await loop.run_in_executor(executor, channel_means, red, box)
                blue_rgb = await loop.run_in_executor(
                    executor, channel_means, blue, box
                )
                assert red_rgb[0] > red_rgb[2] * 2 and blue_rgb[2] > blue_rgb[0] * 2
                print("MATERIAL_BASE_COLOR", red_rgb, blue_rgb)
                await material(
                    "configure_principled",
                    {
                        "name": "SurfaceA",
                        "base_color": [0.18, 0.18, 0.18],
                        "roughness": 0.02,
                    },
                )
                glossy = await render(client, identifier)
                await material(
                    "configure_principled", {"name": "SurfaceA", "roughness": 0.65}
                )
                matte = await render(client, identifier)
                glossy_stats = await loop.run_in_executor(
                    executor, highlight_statistics, glossy
                )
                matte_stats = await loop.run_in_executor(
                    executor, highlight_statistics, matte
                )
                print("MATERIAL_ROUGHNESS", glossy_stats, matte_stats)
                assert (
                    glossy_stats[1] - glossy_stats[0]
                    > (matte_stats[1] - matte_stats[0]) * 1.2
                )
                assert glossy_stats[2] > matte_stats[2]
                await material(
                    "configure_principled", {"name": "SurfaceA", "metallic": 1}
                )
                metal = await render(client, identifier)
                metallic_difference = await loop.run_in_executor(
                    executor, mean_pixel_difference, matte, metal
                )
                assert metallic_difference > 1
                print("MATERIAL_METALLIC_DIFFERENCE", metallic_difference)
                await material(
                    "configure_principled",
                    {
                        "name": "SurfaceA",
                        "emission_color": [0.05, 1, 0.05],
                        "emission_strength": 3,
                    },
                )
                emissive = await render(client, identifier)
                emission_difference = await loop.run_in_executor(
                    executor, mean_pixel_difference, metal, emissive
                )
                emission_rgb = await loop.run_in_executor(
                    executor, channel_means, emissive, box
                )
                assert (
                    emission_difference > 5 and emission_rgb[1] > emission_rgb[0] * 1.5
                )
                print("MATERIAL_EMISSION", emission_difference, emission_rgb)
                await material(
                    "create_principled",
                    {
                        "name": "SurfaceB",
                        "base_color": [0.65, 0.015, 0.015],
                        "roughness": 0.8,
                    },
                )
                replaced = MaterialAssignResult.model_validate(
                    await material(
                        "assign",
                        {"object_name": "RenderSubject", "material_name": "SurfaceB"},
                    )
                )
                reassigned = await render(client, identifier)
                assignment_difference = await loop.run_in_executor(
                    executor, mean_pixel_difference, emissive, reassigned
                )
                reassigned_rgb = await loop.run_in_executor(
                    executor, channel_means, reassigned, box
                )
                assert (
                    assignment_difference > 5
                    and reassigned_rgb[0] > reassigned_rgb[1] * 2
                )
                print("MATERIAL_ASSIGNMENT", assignment_difference, reassigned_rgb)
                assert replaced.slots == ["SurfaceB"]
                appended = MaterialAssignResult.model_validate(
                    await material(
                        "assign",
                        {
                            "object_name": "RenderSubject",
                            "material_name": "SurfaceA",
                            "slot_index": 1,
                        },
                    )
                )
                assert appended.slots == ["SurfaceB", "SurfaceA"]
                errors: list[tuple[str, dict[str, JsonValue], str]] = [
                    (
                        "configure_principled",
                        {"name": "CustomGraph", "roughness": 0.5},
                        "unsupported_material_graph",
                    ),
                    ("configure_principled", {"name": "Missing"}, "material_not_found"),
                    (
                        "assign",
                        {"object_name": "Camera", "material_name": "SurfaceA"},
                        "object_not_material_capable",
                    ),
                    (
                        "assign",
                        {
                            "object_name": "RenderSubject",
                            "material_name": "SurfaceA",
                            "slot_index": 3,
                        },
                        "invalid_arguments",
                    ),
                    ("create_principled", {"name": "SurfaceA"}, "invalid_arguments"),
                ]
                before_errors = await material("inspect", {})
                for name, arguments, code in errors:
                    await error(
                        client, identifier, "blender.material." + name, arguments, code
                    )
                assert await material("inspect", {}) == before_errors
                assert (
                    SceneSummary.model_validate(
                        await operation(client, identifier, "blender.scene.inspect", {})
                    ).objects
                    == scene.objects
                )
                assert (
                    await operation(client, identifier, "blender.camera.inspect", {})
                    == camera
                )
                assert (
                    await operation(client, identifier, "blender.light.inspect", {})
                    == lights
                )
            await discover(client, empty=True)
