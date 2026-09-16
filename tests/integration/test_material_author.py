"""Typed material authoring survives a complete host shutdown and reopen."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tyvrana_protocol import JsonValue

from tyvrana_blender.material_models import MaterialInspectResult, MaterialSummary

from ..png import mean_pixel_difference
from .conftest import running_blender
from .test_e2e import core_client, discover, operation, wait_for_project


async def test_semantic_material_patch_and_fresh_process_persistence(
    profile: dict[str, str], tmp_path: Path
) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        async with core_client(tmp_path) as (client, port):
            profile["TYVRANA_TEST_PORT"] = str(port)
            identifier = ""

            async def call(name: str, /, **arguments: JsonValue) -> JsonValue:
                return await operation(client, identifier, "blender." + name, arguments)

            async def render() -> bytes:
                result = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": identifier,
                        "operation": "blender.render.image",
                        "arguments": {
                            "width": 128,
                            "height": 128,
                            "cycles": {
                                "samples": 16,
                                "device": "cpu",
                                "denoise": False,
                            },
                        },
                    },
                )
                assert not result.is_error
                return base64.b64decode(
                    next(c.data for c in result.content if c.type == "image")
                )

            async with running_blender(profile, tmp_path, ui=False):
                registered = await discover(client)
                assert registered is not None
                identifier = registered.instance_id
                await call(
                    "object.create_primitive", primitive="uv_sphere", name="Subject"
                )
                await call(
                    "camera.create", name="Camera", location=[0, 0, 5], make_active=True
                )
                await call(
                    "light.create",
                    name="Key",
                    type="area",
                    location=[0, 0, 4],
                    energy=400,
                    size=3,
                )
                await call(
                    "image.create_generated",
                    name="Color",
                    width=32,
                    height=32,
                    generated_type="color_grid",
                    color_space="sRGB",
                )
                created = MaterialSummary.model_validate(
                    await call(
                        "material.author",
                        name="Surface",
                        parameters={
                            "roughness": 0.3,
                            "coat_weight": 0.4,
                            "thin_film_thickness": 400,
                        },
                        textures=[{"channel": "base_color", "image": "Color"}],
                        assignments=[{"object_name": "Subject"}],
                    )
                )
                before_patch = await render()
                updated = MaterialSummary.model_validate(
                    await call(
                        "material.author",
                        name="Surface",
                        mode="update",
                        remove_textures=["base_color"],
                        variation={
                            "channel": "base_color",
                            "low": [0.01, 0.1, 0.01],
                            "high": [0.1, 0.8, 0.1],
                            "scale": 5,
                        },
                    )
                )
                assert created.graph and updated.graph
                assert created.graph.fingerprint != updated.graph.fingerprint
                before_save = await render()
                assert (
                    await asyncio.get_running_loop().run_in_executor(
                        executor, mean_pixel_difference, before_patch, before_save
                    )
                    > 1
                )
                before = MaterialInspectResult.model_validate(
                    await call("material.inspect", names=["Surface"])
                )
                filepath = str(tmp_path / "material.blend")
                await call("file.save", filepath=filepath)
                await wait_for_project(client, identifier, filepath)
            await discover(client, empty=True)
            fresh = tmp_path / "fresh"
            fresh.mkdir()
            profile.update(TYVRANA_TEST_CONTROL=str(fresh), TMPDIR=str(fresh))
            async with running_blender(profile, fresh, ui=False):
                registered = await discover(client)
                assert registered is not None
                identifier = registered.instance_id
                await call("file.open", filepath=filepath, discard_current=True)
                await wait_for_project(client, identifier, filepath)
                after = MaterialInspectResult.model_validate(
                    await call("material.inspect", names=["Surface"])
                )
                assert before.materials == after.materials
                reopened = await render()
                assert (
                    await asyncio.get_running_loop().run_in_executor(
                        executor, mean_pixel_difference, before_save, reopened
                    )
                    < 0.1
                )
