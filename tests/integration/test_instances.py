import subprocess
from pathlib import Path
from typing import Any

import pytest
from tyvrana_protocol import JsonValue

from ..png import mean_pixel_difference
from .conftest import ROOT, running_blender
from .test_cameras import render
from .test_e2e import core_client, discover, operation


def test_native_surface_instances(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/instance_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (tmp_path / "instances.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_INSTANCE_TESTS_PASSED" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


async def test_guided_instances_render_through_mcp(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            registered = await discover(client)
            assert registered is not None
            identifier = registered.instance_id

            async def call(op: str, **args: JsonValue) -> Any:
                return await operation(client, identifier, "blender." + op, args)

            await call(
                "material.create_principled", name="Red", base_color=[0.8, 0.01, 0.01]
            )
            await call(
                "mesh.create",
                name="Surface",
                vertices=[[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]],
                faces=[[0, 1, 2, 3]],
                corner_uvs=[[0, 0], [1, 0], [1, 1], [0, 1]],
            )
            await call(
                "mesh.create",
                name="Asset",
                vertices=[[-0.07, 0, 0], [0.07, 0, 0], [0, 0.25, 0.02]],
                faces=[[0, 1, 2]],
                hidden=True,
                materials=["Red"],
            )
            await call(
                "camera.create",
                name="Camera",
                projection="orthographic",
                ortho_scale=2.4,
                location=[0, 0, 4],
                rotation=[0, 0, 0],
                make_active=True,
            )
            await call(
                "light.create",
                name="Key",
                type="area",
                location=[0, 0, 3],
                energy=300,
                size=3,
            )
            before = await render(
                client, identifier, cycles={"samples": 4, "device": "cpu"}
            )
            spec: dict[str, JsonValue] = {
                "surface_object": "Surface",
                "prototype_object": "Asset",
                "uv_map": "UVMap",
                "guides": [
                    [[-0.6, -0.6, 0], [-0.6, 0.6, 0]],
                    [[0.6, -0.6, 0], [0.6, 0.6, 0]],
                ],
                "rows": 5,
                "columns": 6,
                "surface_offset": 0.01,
            }
            made = await call(
                "surface_instances.create", name="Instances", distribution=spec
            )
            assert made["evaluated_instance_count"] == 30
            layout = await call(
                "uv.inspect_layout", objects=["Surface"], uv_map="UVMap", evaluated=True
            )
            assert layout["world_area"] == pytest.approx(4)
            after = await render(
                client, identifier, cycles={"samples": 4, "device": "cpu"}
            )
            assert mean_pixel_difference(before, after) > 1
            changed = await call(
                "surface_instances.configure",
                object_name="Instances",
                distribution={**spec, "rows": 3},
            )
            assert changed["evaluated_instance_count"] == 18
            inspected = await call(
                "surface_instances.inspect", object_name="Instances", sample_limit=2
            )
            assert len(inspected["samples"]) == 2
            assert inspected["binding_sha256"] == changed["binding_sha256"]
            assert inspected["invalid_binding_count"] == 0
            assert inspected["root_position_max_error"] < 1e-5
        await discover(client, empty=True)
