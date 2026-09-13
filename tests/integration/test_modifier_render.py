import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference, red_bounds
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation


def fixed_scene(state: Any) -> Any:
    # Evaluated dimensions change with a modifier. Generic scene properties must not.
    return [
        {k: v for k, v in obj.items() if k != "dimensions"} for obj in state["objects"]
    ]


@pytest.mark.parametrize(
    "kind", ["mirror", "subdivision_surface", "solidify", "boolean"]
)
async def test_reversible_modifier_form_renders_through_mcp(
    profile: dict[str, str], tmp_path: Path, kind: str
) -> None:
    profile["TYVRANA_TEST_MODIFIER"] = kind
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id

            async def call(operation_name: str, **arguments: JsonValue) -> Any:
                return await operation(
                    client, identifier, "blender." + operation_name, arguments
                )

            materials = await call("material.inspect")
            surface_material = next(
                material["name"]
                for material in materials["materials"]
                if any(
                    a["object"] == "Surface" and a["slot"] == 0
                    for a in material["assignments"]
                )
            )
            await call(
                "material.assign", object_name="Surface", material_name=surface_material
            )
            scene_before = await call("scene.inspect")
            camera_before = await call("camera.inspect")
            light_before = await call("light.inspect")
            material_before = await call("material.inspect")
            authored_before = await call("mesh.inspect", object_name="Surface")
            evaluated_before = await call(
                "mesh.inspect_evaluated", object_name="Surface"
            )
            operand_before = (
                await call("mesh.inspect", object_name="Operand")
                if kind == "boolean"
                else None
            )
            first = await render(client, identifier)
            settings_by_kind: dict[str, dict[str, JsonValue]] = {
                "mirror": {"axes": ["x"], "merge": True},
                "subdivision_surface": {
                    "mode": "catmull_clark",
                    "levels": 2,
                    "render_levels": 2,
                },
                "solidify": {"thickness": 0.8, "offset": -1},
                "boolean": {
                    "operand_object": "Operand",
                    "operation": "difference",
                    "solver": "exact",
                },
            }
            settings = settings_by_kind[kind]
            modifier = await call(
                "modifier.create",
                object_name="Surface",
                type=kind,
                name="Form",
                settings=settings,
            )
            assert modifier["type"] == kind and modifier["index"] == 0
            authored_after = await call("mesh.inspect", object_name="Surface")
            evaluated_after = await call(
                "mesh.inspect_evaluated", object_name="Surface"
            )
            second = await render(client, identifier)
            assert authored_after == authored_before
            assert evaluated_after["vertex_count"] > evaluated_before["vertex_count"]
            assert fixed_scene(await call("scene.inspect")) == fixed_scene(scene_before)
            assert await call("camera.inspect") == camera_before
            assert await call("light.inspect") == light_before
            assert await call("material.inspect") == material_before
            if operand_before is not None:
                assert (
                    await call("mesh.inspect", object_name="Operand") == operand_before
                )
            with ThreadPoolExecutor(max_workers=1) as executor:
                difference = await asyncio.get_running_loop().run_in_executor(
                    executor, mean_pixel_difference, first, second
                )
                first_box = await asyncio.get_running_loop().run_in_executor(
                    executor, red_bounds, first
                )
                second_box = await asyncio.get_running_loop().run_in_executor(
                    executor, red_bounds, second
                )
            assert difference > 1.0
            if kind == "mirror":
                assert second_box[2] - second_box[0] > 1.2 * (
                    first_box[2] - first_box[0]
                )
            elif kind == "subdivision_surface":
                assert second_box[2] - second_box[0] < first_box[2] - first_box[0]
            elif kind == "solidify":
                assert (
                    evaluated_after["bounds_min"][2]
                    < evaluated_before["bounds_min"][2] - 0.7
                )
            print(
                "MODIFIER_RENDER",
                kind,
                "difference",
                difference,
                "bounds",
                first_box,
                second_box,
                "vertices",
                evaluated_before["vertex_count"],
                evaluated_after["vertex_count"],
            )
            (tmp_path / "before.png").write_bytes(first)
            (tmp_path / "after.png").write_bytes(second)
            # Explicit destructive acceptance is separate from reversible rendering.
            if kind in {"subdivision_surface", "mirror"}:
                applied = await call(
                    "modifier.apply", object_name="Surface", modifier_name="Form"
                )
                assert applied["modifiers"] == []
                assert (
                    applied["mesh"]["vertex_count"] == evaluated_after["vertex_count"]
                )
                assert (await call("mesh.inspect", object_name="Surface"))[
                    "vertex_count"
                ] == evaluated_after["vertex_count"]
                third = await render(client, identifier)
                assert mean_pixel_difference(second, third) < 0.2
                assert await call("material.inspect") == material_before
            else:
                removed = await call(
                    "modifier.remove", object_name="Surface", modifier_name="Form"
                )
                assert removed["modifiers"] == []
                assert (
                    await call("mesh.inspect_evaluated", object_name="Surface")
                    == evaluated_before
                )
        await discover(client, empty=True)
