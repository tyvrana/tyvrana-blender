import asyncio
import hashlib
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from tyvrana_protocol import (
    ArtifactAccepted,
    ArtifactBegin,
    ArtifactDescriptor,
    JsonValue,
    OperationRequest,
    decode_artifact_chunk,
    decode_message,
)
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from tyvrana_blender.image_models import ImageSummary
from tyvrana_blender.uv_models import UVPackResult

from ..png import checker_png, mean_pixel_difference, texture_statistics
from .conftest import running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation, spooled_count


@asynccontextmanager
async def observed_connection(
    port: int,
) -> AsyncIterator[tuple[int, list[tuple[str, str | bytes]]]]:
    frames: list[tuple[str, str | bytes]] = []

    async def forward(adapter: ServerConnection) -> None:
        async with connect(
            f"ws://127.0.0.1:{port}",
            proxy=None,
            compression=None,
            max_size=4 * 1024 * 1024,
        ) as core:

            async def upstream() -> None:
                async for frame in adapter:
                    frames.append(("to_core", frame))
                    await core.send(frame)

            async def downstream() -> None:
                async for frame in core:
                    frames.append(("to_adapter", frame))
                    await adapter.send(frame)

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async with serve(
        forward,
        "127.0.0.1",
        0,
        compression=None,
        close_timeout=1,
        max_size=4 * 1024 * 1024,
    ) as server:
        yield server.sockets[0].getsockname()[1], frames


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_external_texture_binary_inputs_uvs_and_packed_render(
    profile: dict[str, str],
    tmp_path: Path,
    ui: bool,
) -> None:
    profile["TYVRANA_TEST_CPU_RENDER"] = "1"
    source = tmp_path / "external-checker.png"
    data = checker_png(256, 256)
    source.write_bytes(data)
    async with core_client(tmp_path) as (client, port):
        async with observed_connection(port) as (adapter_port, frames):
            profile["TYVRANA_TEST_PORT"] = str(adapter_port)
            async with running_blender(profile, tmp_path, ui=ui):
                registration = await discover(client)
                assert registration is not None
                identifier = registration.instance_id

                async def call(name: str, /, **arguments: JsonValue) -> JsonValue:
                    return await operation(
                        client, identifier, "blender." + name, arguments
                    )

                imported = await client.call_tool(
                    "tyvrana_import_artifact", {"path": str(source), "name": "Checker"}
                )
                assert not imported.is_error
                descriptor = ArtifactDescriptor.model_validate(
                    imported.structured_content
                )
                assert (
                    descriptor.byte_size == len(data)
                    and descriptor.sha256 == hashlib.sha256(data).hexdigest()
                )
                assert str(source) not in imported.model_dump_json()
                source.unlink()
                for name in ("Grid", "Second"):
                    response = await client.call_tool(
                        "tyvrana_execute_operation",
                        {
                            "adapter_id": identifier,
                            "operation": "blender.image.create_from_artifact",
                            "arguments": {
                                "artifact_id": descriptor.artifact_id,
                                "name": name,
                            },
                            "artifact_ids": [descriptor.artifact_id],
                        },
                    )
                    assert not response.is_error, response.content
                    image = ImageSummary.model_validate(
                        response.structured_content["result"]
                    )
                    assert (
                        image.packed
                        and image.source == "file"
                        and image.generated_type is None
                    )
                    assert (image.width, image.height) == (256, 256)
                    assert str(source) not in response.model_dump_json()
                async with asyncio.timeout(5):
                    while spooled_count(tmp_path):  # noqa: ASYNC110 - Observe the owned Blender process.
                        await asyncio.sleep(0.02)
                released = await client.call_tool(
                    "tyvrana_release_artifact", {"artifact_id": descriptor.artifact_id}
                )
                assert released.structured_content == {"released": True}
                assert not source.exists()
                await call(
                    "object.create_primitive",
                    primitive="plane",
                    name="Surface",
                    scale=[2, 2, 1],
                )
                await call(
                    "camera.create",
                    name="View",
                    projection="orthographic",
                    ortho_scale=5,
                    location=[0, 0, 6],
                    make_active=True,
                )
                await call(
                    "light.create",
                    type="area",
                    name="Lamp",
                    energy=600,
                    size=5,
                    location=[0, 0, 4],
                )
                await call("material.create_principled", name="Finish", roughness=1)
                await call(
                    "material.assign", object_name="Surface", material_name="Finish"
                )
                await call(
                    "shader.node.create",
                    material_name="Finish",
                    node_type="texture_coordinate",
                    name="Coordinates",
                )
                await call(
                    "shader.node.create",
                    material_name="Finish",
                    node_type="image_texture",
                    name="Texture",
                    image_name="Grid",
                    interpolation="closest",
                )
                await call(
                    "shader.connect",
                    material_name="Finish",
                    from_node="Coordinates",
                    from_socket="UV",
                    to_node="Texture",
                    to_socket="Vector",
                )
                flat = await render(client, identifier)
                await call(
                    "shader.connect",
                    material_name="Finish",
                    from_node="Texture",
                    from_socket="Color",
                    to_node="Principled BSDF",
                    to_socket="Base Color",
                )
                await call("uv.create_map", object_name="Surface", name="SurfaceMap")
                await call("uv.set_active", object_name="Surface", name="SurfaceMap")
                await call(
                    "uv.unwrap",
                    object_name="Surface",
                    method="smart_project",
                    island_margin=0.01,
                )
                packed = UVPackResult.model_validate(
                    await call(
                        "uv.pack_islands",
                        objects=[{"object_name": "Surface"}],
                        padding_pixels=16,
                    )
                )
                uv_state = packed.objects[0]
                assert uv_state.active_map == uv_state.active_render_map == "SurfaceMap"
                assert all(item.out_of_unit_square_count == 0 for item in uv_state.maps)
                textured = await render(client, identifier)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    loop = asyncio.get_running_loop()
                    before = await loop.run_in_executor(
                        executor, texture_statistics, flat
                    )
                    after = await loop.run_in_executor(
                        executor, texture_statistics, textured
                    )
                    difference = await loop.run_in_executor(
                        executor, mean_pixel_difference, flat, textured
                    )
                print(
                    "EXTERNAL_TEXTURE",
                    ui,
                    descriptor.byte_size,
                    before,
                    after,
                    difference,
                )
                assert (
                    after[0] > before[0] + 100
                    and after[1] > before[1] + 1
                    and after[2] > before[2] + 10
                )
                assert difference > 5
                async with asyncio.timeout(5):
                    while spooled_count(tmp_path):  # noqa: ASYNC110 - Observe request file cleanup.
                        await asyncio.sleep(0.02)
                assert not source.exists()
                controls = [
                    (direction, decode_message(frame.encode()))
                    for direction, frame in frames
                    if isinstance(frame, str)
                ]
                assert all(
                    str(source) not in frame
                    for _, frame in frames
                    if isinstance(frame, str)
                )
                inputs = [
                    message
                    for direction, message in controls
                    if direction == "to_adapter" and isinstance(message, ArtifactBegin)
                ]
                assert (
                    len(inputs) == 2 and inputs[0].transfer_id != inputs[1].transfer_id
                )
                for item in inputs:
                    assert item.descriptor == descriptor
                    received = bytearray()
                    for direction, frame in frames:
                        if direction == "to_adapter" and isinstance(frame, bytes):
                            chunk = decode_artifact_chunk(frame)
                            if chunk.transfer_id == item.transfer_id:
                                assert chunk.offset == len(received)
                                received.extend(chunk.payload)
                    assert bytes(received) == data
                    accepted = next(
                        i
                        for i, (_, message) in enumerate(controls)
                        if isinstance(message, ArtifactAccepted)
                        and message.transfer_id == item.transfer_id
                    )
                    dispatched = next(
                        i
                        for i, (_, message) in enumerate(controls)
                        if isinstance(message, OperationRequest)
                        and message.request_id == item.request_id
                    )
                    assert accepted < dispatched
            await discover(client, empty=True)
