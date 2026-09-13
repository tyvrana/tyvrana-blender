"""Project saves survive MCP responses, reconnects, and a native file reopen."""

import asyncio
from pathlib import Path

import pytest

from .conftest import running_blender
from .test_e2e import core_client, discover, operation


@pytest.mark.parametrize("ui", [False, True], ids=["background", "ui-timer"])
async def test_project_persistence_over_mcp(
    profile: dict[str, str], tmp_path: Path, ui: bool
) -> None:
    destination = tmp_path / "model.blend"
    milestone = tmp_path / "milestone.blend"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=ui):
            registered = await discover(client)
            assert registered is not None and len(registered.operations) == 83
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

            async def current_project(path: Path) -> None:
                async with asyncio.timeout(10):
                    while True:
                        response = await client.call_tool("tyvrana_list_adapters")
                        adapters = response.structured_content["adapters"]
                        if adapters and adapters[0].get("project_path") == str(path):
                            assert adapters[0]["instance_id"] == identifier
                            return
                        await asyncio.sleep(0.05)

            unsaved = await call("file.inspect", {})
            assert isinstance(unsaved, dict) and unsaved["filepath"] is None
            await operation(
                client,
                identifier,
                "blender.object.create_primitive",
                {"primitive": "cube", "name": "Persistent form", "location": [1, 2, 3]},
            )
            for arguments in [
                {},
                {"filepath": str(tmp_path / "missing" / "model.blend")},
            ]:
                failure = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": identifier,
                        "operation": "blender.file.save",
                        "arguments": arguments,
                    },
                )
                assert failure.is_error
                assert not destination.exists()

            occupied = tmp_path / "directory.blend"
            occupied.mkdir()
            protected = tmp_path / "protected.blend"
            protected.write_bytes(b"keep existing contents")
            alias = tmp_path / "alias.blend"
            alias.symlink_to(protected)
            for path in [occupied, alias]:
                failure = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": identifier,
                        "operation": "blender.file.save",
                        "arguments": {"filepath": str(path), "overwrite": True},
                    },
                )
                assert failure.is_error
                assert protected.read_bytes() == b"keep existing contents"
                assert occupied.is_dir() and alias.is_symlink()

            saved = await call("file.save", {"filepath": str(destination)})
            assert isinstance(saved, dict)
            assert saved["filepath"] == str(destination)
            assert saved["is_saved"] and saved["exists"] and saved["byte_size"]
            await current_project(destination)
            original = destination.read_bytes()
            for arguments in [{}, {"filepath": str(destination)}]:
                failure = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": identifier,
                        "operation": "blender.file.save",
                        "arguments": arguments,
                    },
                )
                assert failure.is_error
                assert destination.read_bytes() == original
            await call(
                "object.set_transform",
                {"name": "Persistent form", "location": [4, 5, 6]},
            )
            await call("file.save", {"overwrite": True})
            assert destination.read_bytes() != original
            await call("file.save", {"filepath": str(milestone)})
            await current_project(milestone)
            state = await call("file.inspect", {})
            assert isinstance(state, dict) and state["filepath"] == str(milestone)
            await call("scene.inspect", {})
        await discover(client, empty=True)

    # Open the saved asset in a separate native process, not the original memory.
    checker = tmp_path / "reopen.py"
    checker.write_text(
        "import bpy, os\n"
        "assert bpy.data.filepath == os.environ['SAVED_PROJECT']\n"
        "obj=bpy.data.objects['Persistent form']\n"
        "assert tuple(obj.location)==(4.0,5.0,6.0)\n"
        "assert len(obj.data.vertices)==8 and len(obj.data.polygons)==6\n"
        "print('NATIVE_SAVED_PROJECT_REOPENED')\n"
    )
    process = await asyncio.create_subprocess_exec(
        "blender",
        "--background",
        str(milestone),
        "--python-exit-code",
        "1",
        "--python",
        str(checker),
        env={**profile, "SAVED_PROJECT": str(milestone)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(30):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, output.decode()
    assert b"NATIVE_SAVED_PROJECT_REOPENED" in output
    assert b"Traceback" not in output
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))  # noqa: ASYNC240 - Isolated test directory.
