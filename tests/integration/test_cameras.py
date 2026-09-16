import base64
import hashlib
import json
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import ImageContent, TextContent
from tyvrana_protocol import ArtifactDescriptor, JsonValue

from tyvrana_blender.camera_models import CameraInspectResult, CameraSummary
from tyvrana_blender.models import ObjectSummary

from ..png import inspect_png, red_bounds, rgb_pixels
from .conftest import running_blender
from .rendering import complete_render
from .test_e2e import catalog_names, core_client, discover, operation


async def render(client: Client, identifier: str, **options: JsonValue) -> bytes:
    response = await client.call_tool(
        "tyvrana_execute_operation",
        {
            "adapter_id": identifier,
            "operation": "blender.render.image",
            "arguments": {"width": 256, "height": 256, **options},
        },
    )
    response = await complete_render(client, identifier, response)
    assert not response.is_error, response.content
    images = [item for item in response.content if isinstance(item, ImageContent)]
    assert len(images) == 1 and images[0].mime_type == "image/png"
    data = base64.b64decode(images[0].data, validate=True)
    descriptor = ArtifactDescriptor.model_validate(
        response.structured_content["artifacts"][0]
    )
    assert descriptor.byte_size == len(data)
    assert descriptor.sha256 == hashlib.sha256(data).hexdigest()
    assert inspect_png(data) == (256, 256)
    return data


async def error(
    client: Client,
    identifier: str,
    name: str,
    arguments: dict[str, JsonValue],
    code: str,
) -> None:
    response = await client.call_tool(
        "tyvrana_execute_operation",
        {
            "adapter_id": identifier,
            "operation": name,
            "arguments": arguments,
        },
    )
    if name == "blender.render.image" and not response.is_error:
        response = await complete_render(client, identifier, response)
        assert response.structured_content["result"]["state"] == "failed"
        assert response.structured_content["result"]["error"]["code"] == code
        return
    assert response.is_error
    block = response.content[0]
    assert isinstance(block, TextContent)
    assert json.loads(block.text)["code"] == code
    assert "Traceback" not in block.text


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_camera_controls_and_render_framing_over_mcp(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    profile["TYVRANA_TEST_RENDER"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=ui):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id
            names = await catalog_names(client, identifier, "blender.camera.")
            assert all(
                "blender.camera." + name in names
                for name in ("inspect", "create", "configure", "set_active")
            )
            initial = CameraInspectResult.model_validate(
                await operation(client, identifier, "blender.camera.inspect", {})
            )
            for camera in initial.cameras:
                await operation(
                    client, identifier, "blender.object.delete", {"name": camera.name}
                )
            empty = await operation(client, identifier, "blender.camera.inspect", {})
            assert isinstance(empty, dict)
            assert empty["active_camera"] is None and empty["cameras"] == []
            pose: dict[str, JsonValue] = {
                "location": [0, -8, 4.5],
                "rotation": [1.1583858728408813, 0, 0],
            }
            perspective = CameraSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.camera.create",
                    {"name": "CameraA", "lens_mm": 32, **pose},
                )
            )
            assert perspective.active and perspective.lens_mm == 32
            orthographic = CameraSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.camera.create",
                    {
                        "name": "CameraB",
                        "projection": "orthographic",
                        "ortho_scale": 6,
                        **pose,
                    },
                )
            )
            assert not orthographic.active and orthographic.ortho_scale == 6
            await operation(
                client,
                identifier,
                "blender.camera.set_active",
                {"name": perspective.name},
            )
            wide = await render(client, identifier)
            longer = CameraSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.camera.configure",
                    {
                        "name": perspective.name,
                        "lens_mm": 64,
                        "clip_start": 0.25,
                        "clip_end": 300,
                    },
                )
            )
            assert (
                longer.lens_mm == 64
                and longer.location == perspective.location
                and longer.rotation == perspective.rotation
            )
            tight = await render(client, identifier)
            wide_box, tight_box = red_bounds(wide), red_bounds(tight)
            assert tight_box[2] - tight_box[0] > (wide_box[2] - wide_box[0]) * 1.5
            assert rgb_pixels(wide) != rgb_pixels(tight)
            print("PERSPECTIVE_LENS_FRAMING", wide_box, tight_box)
            moved = ObjectSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.object.set_transform",
                    {"name": orthographic.name, "location": [2, -8, 4.5]},
                )
            )
            assert moved.location == [2, -8, 4.5]
            active = CameraSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.camera.set_active",
                    {"name": orthographic.name},
                )
            )
            assert active.active and active.location == moved.location
            ortho_image = await render(client, identifier)
            ortho_box = red_bounds(ortho_image)
            assert sum(ortho_box[::2]) / 2 < sum(tight_box[::2]) / 2 - 40
            assert rgb_pixels(tight) != rgb_pixels(ortho_image)
            print("ACTIVE_CAMERA_FRAMING", tight_box, ortho_box)
            configured = CameraSummary.model_validate(
                await operation(
                    client,
                    identifier,
                    "blender.camera.configure",
                    {"name": orthographic.name, "ortho_scale": 8, "shift_y": 0.125},
                )
            )
            assert configured.ortho_scale == 8 and configured.shift_y == 0.125
            wider_ortho = await render(client, identifier)
            assert rgb_pixels(ortho_image) != rgb_pixels(wider_ortho)
            inspected = CameraInspectResult.model_validate(
                await operation(client, identifier, "blender.camera.inspect", {})
            )
            assert inspected.active_camera == orthographic.name
            assert (
                next(c for c in inspected.cameras if c.name == orthographic.name)
                == configured
            )
            await error(
                client,
                identifier,
                "blender.camera.configure",
                {"name": perspective.name, "lens_mm": 80, "clip_end": 0.1},
                "invalid_arguments",
            )
            following = CameraInspectResult.model_validate(
                await operation(client, identifier, "blender.camera.inspect", {})
            )
            assert following == inspected
            await error(
                client,
                identifier,
                "blender.camera.set_active",
                {"name": "RenderCube"},
                "object_not_camera",
            )
            await error(
                client,
                identifier,
                "blender.camera.configure",
                {"name": "Missing"},
                "object_not_found",
            )
            await operation(
                client, identifier, "blender.object.delete", {"name": orthographic.name}
            )
            final = CameraInspectResult.model_validate(
                await operation(client, identifier, "blender.camera.inspect", {})
            )
            assert final.active_camera is None and [c.name for c in final.cameras] == [
                perspective.name
            ]
            await error(client, identifier, "blender.render.image", {}, "no_camera")
        await discover(client, empty=True)
