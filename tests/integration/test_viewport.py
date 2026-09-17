"""Real process identity, viewport checkpoints and empty-document lifecycle."""

import asyncio
import json
from pathlib import Path

import pytest
from tyvrana_protocol import JsonValue

from tyvrana_blender.extension_models import ExtensionState
from tyvrana_blender.file_models import FileState
from tyvrana_blender.viewport_models import ViewportInspection, ViewportState

from .conftest import running_blender
from .test_e2e import core_client, discover, operation, wait_for_project


@pytest.mark.parametrize("ui", [False, True], ids=["background", "interactive-xvfb"])
async def test_interactive_checkpoint_and_new_project(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        profile["TYVRANA_TEST_NEW"] = "1"
        async with running_blender(profile, tmp_path, ui=ui):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id

            async def call(name: str, arguments: dict[str, object]) -> object:
                result = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": identifier,
                        "operation": "blender." + name,
                        "arguments": arguments,
                    },
                )
                assert not result.is_error, result.content
                return result.structured_content["result"]

            before = ExtensionState.model_validate(await call("extension.inspect", {}))
            native = json.loads((tmp_path / "ready.json").read_text())
            assert before.host_pid == native["host_pid"]
            assert before.background == native["background"]
            assert before.window_count == native["window_count"]
            assert before.host_pid > 0 and before.host_pid != before.worker_pid
            assert before.background is not ui
            assert before.adapter_id == identifier
            assert before.application_version == registered.application_version
            assert before.project_path is None
            if ui:
                assert before.window_count > 0
            for name, x in [("First", 12), ("Second", 16)]:
                await operation(
                    client,
                    identifier,
                    "blender.object.create_primitive",
                    {
                        "primitive": "cube",
                        "name": name,
                        "location": [x, 4, 2],
                    },
                )
            views = ViewportInspection.model_validate(
                await call("viewport.inspect", {})
            )
            assert bool(views.viewports) is ui
            target = views.viewports[0].viewport_id if ui else "1:2"
            if ui:
                frame = ViewportState.model_validate(
                    await call(
                        "viewport.frame",
                        {
                            "viewport_id": target,
                            "object_names": ["Second", "First"],
                        },
                    )
                )
                assert frame.active_object == "Second"
                assert frame.selected_objects == ["First", "Second"]
                assert frame.selected_count == 2
                assert frame.view_location == pytest.approx([14, 4, 2])
                assert frame.view_distance > 0 and frame.width > 0 and frame.height > 0
                inspection = ViewportInspection.model_validate(
                    await call("viewport.inspect", {"viewport_id": target})
                )
                assert inspection.viewports == [frame]
            for viewport_id, names in [("1:2", ["First"]), (target, ["Missing"])]:
                failure = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": identifier,
                        "operation": "blender.viewport.frame",
                        "arguments": {
                            "viewport_id": viewport_id,
                            "object_names": names,
                        },
                    },
                )
                assert failure.is_error
            if ui:
                current = ViewportInspection.model_validate(
                    await call("viewport.inspect", {"viewport_id": target})
                )
                assert current.viewports == [
                    frame
                ]  # Failed preflight cannot disturb selection/framing.
            bound = await call("project.bind", {})
            assert isinstance(bound, dict)
            query: dict[str, JsonValue] = {"adapter_id": identifier}
            async with asyncio.timeout(15):
                while True:
                    snapshot = await client.call_tool("tyvrana_list_adapters", query)
                    state = snapshot.structured_content
                    if any(
                        a.get("project_id") == bound["project_id"]
                        for a in state["adapters"]
                    ):
                        break
                    query.update(after_revision=state["revision"], wait_seconds=12)
            destination = tmp_path / "saved.blend"
            await call("file.save", {"filepath": str(destination)})
            await wait_for_project(client, identifier, str(destination))
            saved = ExtensionState.model_validate(await call("extension.inspect", {}))
            assert saved.project_id and saved.project_path == str(destination)
            reset = FileState.model_validate(
                await call("file.new", {"discard_current": True})
            )
            assert (
                reset.filepath is None
                and reset.project_id is None
                and not reset.is_saved
            )
            await wait_for_project(client, identifier, None)
            scene = await call("scene.inspect", {})
            assert isinstance(scene, dict)
            assert scene["objects"] == []
            after = ExtensionState.model_validate(await call("extension.inspect", {}))
            assert after.host_pid == before.host_pid
            assert after.worker_pid == before.worker_pid
            assert after.adapter_id == before.adapter_id
            assert after.background == before.background
            assert after.window_count == before.window_count
            assert after.project_path is None and after.project_id is None
            assert destination.is_file()  # New does not delete the prior saved file.
            assert (
                bool(
                    ViewportInspection.model_validate(
                        await call("viewport.inspect", {})
                    ).viewports
                )
                is ui
            )
        await discover(client, empty=True)
