"""Native preservation and actual baked PNG bytes through the MCP boundary."""

import asyncio
import base64
import hashlib
import subprocess
from pathlib import Path

from tyvrana_protocol import JsonValue

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_bake_preservation(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/bake_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "bake-native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_BAKE_TESTS_PASSED 8" in result.stdout
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))


async def test_baked_png_reaches_mcp_and_survives_reopen(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            registration = await discover(client)
            assert registration is not None
            identifier = registration.instance_id

            async def call(name: str, /, **arguments: JsonValue) -> JsonValue:
                return await operation(client, identifier, "blender." + name, arguments)

            await call("object.create_primitive", primitive="plane", name="Low")
            await call("uv.create_map", object_name="Low", name="ProductionUV")
            await call(
                "uv.unwrap",
                object_name="Low",
                uv_map="ProductionUV",
                method="angle_based",
                correct_aspect=False,
            )
            await call(
                "uv.pack_islands",
                objects=[{"object_name": "Low", "uv_map": "ProductionUV"}],
                resolution=64,
                padding_pixels=2,
            )
            await call(
                "object.create_primitive",
                primitive="plane",
                name="High",
                location=[0, 0, 0.1],
                scale=[2, 2, 1],
                rotation=[0.1, 0, 0],
            )
            targets: list[JsonValue] = [
                {
                    "target": "Low",
                    "sources": ["High"],
                    "uv_map": "ProductionUV",
                    "cage_extrusion": 0.5,
                    "max_ray_distance": 1,
                }
            ]
            await call(
                "object.create_primitive",
                primitive="plane",
                name="Low2",
                location=[5, 0, 0],
            )
            await call("uv.create_map", object_name="Low2", name="ProductionUV")
            await call(
                "uv.unwrap",
                object_name="Low2",
                uv_map="ProductionUV",
                method="angle_based",
                correct_aspect=False,
            )
            await call(
                "object.create_primitive",
                primitive="plane",
                name="High2",
                location=[5, 0, 0.1],
                scale=[2, 2, 1],
                rotation=[0.1, 0, 0],
            )
            await call(
                "uv.pack_islands",
                objects=[
                    {"object_name": "Low", "uv_map": "ProductionUV"},
                    {"object_name": "Low2", "uv_map": "ProductionUV"},
                ],
                resolution=64,
                padding_pixels=2,
            )
            targets.append(
                {
                    "target": "Low2",
                    "sources": ["High2"],
                    "uv_map": "ProductionUV",
                    "cage_extrusion": 0.5,
                    "max_ray_distance": 1,
                }
            )
            bad = await call(
                "bake.image",
                targets=[
                    {
                        "target": "Missing",
                        "sources": ["High"],
                        "uv_map": "UV",
                        "cage_extrusion": 0.5,
                        "max_ray_distance": 1,
                    }
                ],
                name="Failed",
                resolution=64,
                margin=1,
                device="cpu",
            )
            assert isinstance(bad, dict)
            async with asyncio.timeout(10):
                while True:
                    failed = await call("bake.status", job_id=bad["job_id"])
                    assert isinstance(failed, dict)
                    if failed["state"] == "failed":
                        assert failed["error"]
                        break
                    await asyncio.sleep(0.1)
            images_before = await call("image.inspect")
            assert isinstance(images_before, dict)
            assert "Failed" not in str(images_before)
            queued = await call(
                "bake.image",
                targets=targets,
                name="Normal",
                resolution=64,
                margin=1,
                device="cpu",
            )
            assert isinstance(queued, dict)
            assert queued["state"] == "queued"
            async with asyncio.timeout(30):
                while True:
                    job = await call("bake.status", job_id=queued["job_id"])
                    assert isinstance(job, dict)
                    assert job["state"] != "failed", job
                    if job["state"] == "completed":
                        assert job["completed_targets"] == 2
                        break
                    await asyncio.sleep(0.1)
            destination = tmp_path / "normal.png"
            response = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": identifier,
                    "operation": "blender.image.save",
                    "arguments": {"name": "Normal", "filepath": str(destination)},
                },
            )
            assert not response.is_error, response.content
            images = [c for c in response.content if c.type == "image"]
            assert len(images) == 1
            data = base64.b64decode(images[0].data)
            assert data == destination.read_bytes() and data[24] == 16
            assert response.structured_content is not None
            result = response.structured_content["result"]
            assert (
                isinstance(result, dict)
                and result["sha256"] == hashlib.sha256(data).hexdigest()
            )
            blend = tmp_path / "packed.blend"
            await call("file.save", filepath=str(blend))
            destination.unlink()
            await call("file.open", filepath=str(blend), discard_current=True)
            info = await call("bake.inspect", targets=targets, image="Normal")
            assert isinstance(info, dict)
            qa = info["image_qa"]
            assert isinstance(qa, dict)
            assert (
                qa["invalid_normal_texels"] == 0 and qa["uncovered_alpha_texels"] == 0
            )
            tilt = qa["tilt_degrees"]
            assert isinstance(tilt, dict) and float(str(tilt["median"])) > 1
