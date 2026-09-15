"""Packaged native rig regressions and the real MCP deformation/render path."""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference
from .conftest import ROOT, running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation


def test_native_armatures(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/rig_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (tmp_path / "rig-native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_RIG_TESTS_PASSED 14" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


async def test_articulated_mesh_render_and_persistence_over_mcp(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            registered = await discover(client)
            assert registered is not None and len(registered.operations) == 101
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            await call(
                "mesh.create",
                name="Surface",
                vertices=[
                    [x, y, 0.0]
                    for y in [0.0, 0.5, 1.0, 1.5, 2.0]
                    for x in [-0.18, 0.18]
                ],
                faces=[[i * 2, i * 2 + 1, i * 2 + 3, i * 2 + 2] for i in range(4)],
            )
            original = await call("mesh.inspect", object_name="Surface")
            created = await call(
                "armature.create",
                name="Rig",
                bones=[
                    {
                        "name": "Base",
                        "head": [0.0, 0.0, 0.0],
                        "tail": [0.0, 1.0, 0.0],
                        "head_radius": 0.3,
                        "tail_radius": 0.3,
                    },
                    {
                        "name": "End",
                        "head": [0.0, 1.0, 0.0],
                        "tail": [0.0, 2.0, 0.0],
                        "parent": "Base",
                        "connected": True,
                        "head_radius": 0.3,
                        "tail_radius": 0.3,
                    },
                ],
            )
            assert created["bone_count"] == 2
            bound = await call(
                "armature.bind",
                object_name="Surface",
                armature_object="Rig",
                weights={"method": "envelopes", "bones": ["Base", "End"]},
            )
            assert bound["unweighted_vertex_count"] == 0
            await call(
                "camera.create",
                name="View",
                projection="orthographic",
                ortho_scale=3.8,
                location=[-0.4, 1.0, 4.0],
                rotation=[0.0, 0.0, 0.0],
                make_active=True,
            )
            await call(
                "light.create",
                name="Key",
                type="area",
                location=[0.0, 0.0, 3.0],
                energy=400.0,
                size=3.0,
            )
            folded = await render(
                client, identifier, cycles={"device": "cpu", "samples": 4}
            )
            before = await call(
                "deformation.inspect", armature_object="Rig", objects=["Surface"]
            )
            assert before["meshes"][0]["displacement_max"] < 1e-6
            await call(
                "armature.pose",
                object_name="Rig",
                bones=[
                    {"name": "Base", "rotation": [0.0, 0.0, 0.15]},
                    {"name": "End", "rotation": [0.0, 0.0, 0.5]},
                ],
            )
            partial = await call(
                "deformation.inspect", armature_object="Rig", objects=["Surface"]
            )
            assert partial["meshes"][0]["displacement_max"] > 0.4
            await call(
                "armature.pose",
                object_name="Rig",
                bones=[
                    {"name": "Base", "rotation": [0.0, 0.0, 0.3]},
                    {"name": "End", "rotation": [0.0, 0.0, 1.2]},
                ],
            )
            extended = await render(
                client, identifier, cycles={"device": "cpu", "samples": 4}
            )
            assert mean_pixel_difference(folded, extended) > 1
            full = await call(
                "deformation.inspect", armature_object="Rig", objects=["Surface"]
            )
            assert (
                full["meshes"][0]["displacement_max"]
                > partial["meshes"][0]["displacement_max"]
            )
            state = await call("armature.inspect", object_name="Rig")
            failure = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": identifier,
                    "operation": "blender.armature.pose",
                    "arguments": {
                        "object_name": "Rig",
                        "reset": True,
                        "bones": [{"name": "Missing"}],
                    },
                },
            )
            assert failure.is_error
            assert (await call("armature.inspect", object_name="Rig"))[
                "pose_sha256"
            ] == state["pose_sha256"]
            path = tmp_path / "articulated.blend"
            await call("file.save", filepath=str(path))
            async with asyncio.timeout(10):
                while True:
                    r = await client.call_tool("tyvrana_list_adapters")
                    if any(
                        a.get("project_path") == str(path)
                        for a in r.structured_content["adapters"]
                    ):
                        break
                    await asyncio.sleep(0.05)
            await call("file.open", filepath=str(path), discard_current=True)
            reopened = await discover(client)
            assert reopened is not None and reopened.instance_id == identifier
            after = await call("armature.inspect", object_name="Rig")
            assert state["rest_sha256"] == after["rest_sha256"]
            assert state["pose_sha256"] == after["pose_sha256"]
            assert (
                state["bindings"][0]["weights_sha256"]
                == after["bindings"][0]["weights_sha256"]
            )
            assert (
                full["meshes"][0]["posed_geometry_sha256"]
                == (
                    await call(
                        "deformation.inspect",
                        armature_object="Rig",
                        objects=["Surface"],
                    )
                )["meshes"][0]["posed_geometry_sha256"]
            )
            await call("armature.pose", object_name="Rig", reset=True)
            assert (
                await call(
                    "deformation.inspect", armature_object="Rig", objects=["Surface"]
                )
            )["meshes"][0]["displacement_max"] < 1e-6
            assert (await call("mesh.inspect", object_name="Surface"))[
                "geometry_sha256"
            ] == original["geometry_sha256"]
        await discover(client, empty=True)
