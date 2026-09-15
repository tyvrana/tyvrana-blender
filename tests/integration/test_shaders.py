import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tyvrana_protocol import JsonValue

from tyvrana_blender.image_models import ImageInspectResult, ImageSummary
from tyvrana_blender.operations import OPERATIONS
from tyvrana_blender.shader_models import NodeSummary, ShaderGraphSummary

from ..png import mean_pixel_difference, texture_statistics
from .conftest import running_blender
from .test_cameras import error, render
from .test_e2e import catalog_names, core_client, discover, operation


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_image_shader_graph_and_texture_renders_over_mcp(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    profile["TYVRANA_TEST_SHADER"] = "1"
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop = asyncio.get_running_loop()
        async with core_client(tmp_path) as (client, port):
            profile["TYVRANA_TEST_PORT"] = str(port)
            async with running_blender(profile, tmp_path, ui=ui):
                registration = await discover(client)
                assert registration is not None
                identifier = registration.instance_id
                assert await catalog_names(client, registration.instance_id) == set(
                    OPERATIONS
                )

                async def call(name: str, /, **arguments: JsonValue) -> JsonValue:
                    return await operation(
                        client, identifier, "blender." + name, arguments
                    )

                async def connect(
                    source: str,
                    output: str,
                    target: str,
                    input_name: str,
                    *,
                    replace: bool = False,
                ) -> JsonValue:
                    return await call(
                        "shader.connect",
                        material_name="Surface",
                        from_node=source,
                        from_socket=output,
                        to_node=target,
                        to_socket=input_name,
                        replace_existing=replace,
                    )

                scene = await call("scene.inspect")
                cameras = await call("camera.inspect")
                lights = await call("light.inspect")
                ImageInspectResult.model_validate(await call("image.inspect"))
                grid = ImageSummary.model_validate(
                    await call(
                        "image.create_generated",
                        name="Grid",
                        width=128,
                        height=128,
                        generated_type="uv_grid",
                        color_space="sRGB",
                    )
                )
                assert grid.generated_type == "uv_grid" and grid.width == 128
                changed = ImageSummary.model_validate(
                    await call(
                        "image.configure",
                        name="Grid",
                        color_space="Non-Color",
                        alpha_mode="channel_packed",
                    )
                )
                assert (
                    changed.color_space == "Non-Color"
                    and changed.alpha_mode == "channel_packed"
                )
                await call(
                    "image.configure",
                    name="Grid",
                    color_space="sRGB",
                    alpha_mode="straight",
                )
                await call(
                    "material.create_principled",
                    name="Surface",
                    base_color=[0.18, 0.18, 0.18],
                    roughness=1,
                )
                await call(
                    "material.assign",
                    object_name="RenderSubject",
                    material_name="Surface",
                )
                for kind, name in [
                    ("texture_coordinate", "Coordinates"),
                    ("mapping", "Mapping"),
                    ("image_texture", "Texture"),
                ]:
                    created = NodeSummary.model_validate(
                        await call(
                            "shader.node.create",
                            material_name="Surface",
                            node_type=kind,
                            name=name,
                        )
                    )
                    assert created.node_name == name
                await call(
                    "shader.node.configure",
                    material_name="Surface",
                    node_name="Texture",
                    image_name="Grid",
                    interpolation="closest",
                )
                await connect("Coordinates", "UV", "Mapping", "Vector")
                await connect("Mapping", "Vector", "Texture", "Vector")
                flat = await render(client, identifier)
                await connect("Texture", "Color", "Principled BSDF", "Base Color")
                textured = await render(client, identifier)
                flat_stats = await loop.run_in_executor(
                    executor, texture_statistics, flat
                )
                texture_stats = await loop.run_in_executor(
                    executor, texture_statistics, textured
                )
                print("SHADER_UV_GRID", flat_stats, texture_stats)
                assert texture_stats[0] > flat_stats[0] + 100
                assert texture_stats[1] > flat_stats[1] * 3
                assert texture_stats[2] > flat_stats[2] + 10
                graph = ShaderGraphSummary.model_validate(
                    await call("shader.inspect", material_name="Surface")
                )
                assert len(graph.links) == 4 and all(link.valid for link in graph.links)
                await call(
                    "shader.node.configure",
                    material_name="Surface",
                    node_name="Mapping",
                    scale=[4, 4, 1],
                )
                mapped = await render(client, identifier)
                mapped_stats = await loop.run_in_executor(
                    executor, texture_statistics, mapped
                )
                mapping_difference = await loop.run_in_executor(
                    executor, mean_pixel_difference, textured, mapped
                )
                print("SHADER_MAPPING", texture_stats, mapped_stats, mapping_difference)
                assert (
                    mapping_difference > 5 and mapped_stats[1] > texture_stats[1] * 1.2
                )
                await call(
                    "image.create_generated",
                    name="LowResolution",
                    width=16,
                    height=16,
                    generated_type="color_grid",
                )
                await call(
                    "shader.node.configure",
                    material_name="Surface",
                    node_name="Texture",
                    image_name="LowResolution",
                )
                await call(
                    "shader.node.configure",
                    material_name="Surface",
                    node_name="Mapping",
                    scale=[1, 1, 1],
                )
                closest = await render(client, identifier)
                await call(
                    "shader.node.configure",
                    material_name="Surface",
                    node_name="Texture",
                    interpolation="linear",
                )
                linear = await render(client, identifier)
                interpolation_difference = await loop.run_in_executor(
                    executor, mean_pixel_difference, closest, linear
                )
                print("SHADER_INTERPOLATION", interpolation_difference)
                assert interpolation_difference > 0.2
                errors: list[tuple[str, dict[str, JsonValue], str]] = [
                    ("image.configure", {"name": "Missing"}, "image_not_found"),
                    (
                        "image.configure",
                        {"name": "Grid", "color_space": "Unavailable"},
                        "invalid_arguments",
                    ),
                    (
                        "shader.node.delete",
                        {"material_name": "Surface", "node_name": "Principled BSDF"},
                        "node_protected",
                    ),
                    (
                        "shader.node.configure",
                        {"material_name": "Surface", "node_name": "Missing"},
                        "node_not_found",
                    ),
                    (
                        "shader.connect",
                        {
                            "material_name": "Surface",
                            "from_node": "Texture",
                            "from_socket": "Absent",
                            "to_node": "Principled BSDF",
                            "to_socket": "Base Color",
                        },
                        "socket_not_found",
                    ),
                    (
                        "shader.connect",
                        {
                            "material_name": "Surface",
                            "from_node": "Texture",
                            "from_socket": "Color",
                            "to_node": "Principled BSDF",
                            "to_socket": "Base Color",
                        },
                        "socket_already_connected",
                    ),
                ]
                before_errors = await call("shader.inspect", material_name="Surface")
                for name, arguments, code in errors:
                    await error(client, identifier, "blender." + name, arguments, code)
                assert (
                    await call("shader.inspect", material_name="Surface")
                    == before_errors
                )
                await connect("Coordinates", "UV", "Texture", "Vector", replace=True)
                await call(
                    "shader.node.delete", material_name="Surface", node_name="Mapping"
                )
                assert await call(
                    "shader.disconnect",
                    material_name="Surface",
                    to_node="Principled BSDF",
                    to_socket="Base Color",
                ) == {
                    "material_name": "Surface",
                    "to_node": "Principled BSDF",
                    "to_socket": "Base Color",
                    "removed": 1,
                }
                await call(
                    "material.configure_principled", name="Surface", roughness=0.7
                )
                assert await call("scene.inspect") == scene
                assert await call("camera.inspect") == cameras
                assert await call("light.inspect") == lights
            await discover(client, empty=True)
