"""Native cage display through the ordinary MCP image artifact path."""

import subprocess
from pathlib import Path
from typing import Any

from tyvrana_protocol import JsonValue

from .conftest import ROOT, running_blender
from .test_cameras import error, render
from .test_e2e import core_client, discover, operation
from .test_retopo_render import cage_pixels


def test_native_wireframe_guards(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/wireframe_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=60,
    )
    log = result.stdout + result.stderr
    (tmp_path / "wireframe-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "BLENDER_WIREFRAME_TESTS_PASSED 3" in log, log
    assert "Traceback" not in log, log


async def test_wire_display_preserves_source_target_and_scene(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_RETOPO"] = "empty"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            await call("retopo.create_target", source_object="Surface", name="Cage")
            await call(
                "retopo.seed_patch",
                source_object="Surface",
                target_object="Cage",
                center=[0, 0, 0.7],
                tangent_direction=[1, 0, 0],
                width=0.8,
                height=0.7,
                u_segments=4,
                v_segments=3,
            )
            source = await call("mesh.inspect", object_name="Surface")
            target = await call("mesh.inspect", object_name="Cage")
            scene = await call("scene.inspect")
            stack = await call("modifier.inspect", object_name="Cage")
            materials = await call("material.inspect")
            before = await render(client, identifier)
            after = await render(
                client,
                identifier,
                wireframe={
                    "objects": ["Cage"],
                    "thickness": 0.009,
                    "surface_offset": 0.02,
                },
            )
            (tmp_path / "wire-display.png").write_bytes(after)
            assert cage_pixels(before) + 100 < cage_pixels(after) < 2000
            assert await call("mesh.inspect", object_name="Surface") == source
            assert await call("mesh.inspect", object_name="Cage") == target
            assert await call("scene.inspect") == scene
            assert await call("modifier.inspect", object_name="Cage") == stack
            assert await call("material.inspect") == materials
            await error(
                client,
                identifier,
                "blender.render.image",
                {"wireframe": {"objects": ["Cage", "Missing"]}},
                "object_not_found",
            )
            assert await call("scene.inspect") == scene
            assert await call("material.inspect") == materials
        await discover(client, empty=True)
