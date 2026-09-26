"""Constructive form/reference artifacts and regional diagnostics over real MCP."""

import asyncio
import base64
import io
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from tyvrana_protocol import ArtifactDescriptor

from ..png import assert_image_variation, inspect_png
from .conftest import ROOT, running_blender
from .rendering import complete_render
from .test_e2e import core_client, discover


@pytest.mark.parametrize(
    "script,marker",
    [
        ("form_checks.py", "FORM_NATIVE_PASSED 16"),
        ("visual_checks.py", "VISUAL_NATIVE_PASSED 8"),
    ],
)
def test_native_visual_workflows(
    profile: dict[str, str], tmp_path: Path, script: str, marker: str
) -> None:
    profile["TYVRANA_TEST_OUTPUT"] = str(tmp_path)
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender" / script),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "native.log").write_text(log)
    assert result.returncode == 0 and marker in log, log


async def test_form_reference_and_review_mcp(
    profile: dict[str, str], tmp_path: Path
) -> None:
    image = Image.new("RGBA", (128, 128), (190, 190, 190, 0))
    image.putdata(
        [
            (
                190,
                190,
                190,
                255
                if (((x + 0.5) / 64 - 1) / 0.6) ** 2 + (((y + 0.5) / 64 - 1) / 0.4) ** 2
                <= 1
                else 0,
            )
            for y in range(128)
            for x in range(128)
        ]
    )
    path = tmp_path / "section.png"
    image.save(path)
    records = []
    profile["TYVRANA_TEST_VISUAL"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            identifier = adapter.instance_id

            async def call(
                name: str, args: dict[str, Any], attached: list[str] | None = None
            ) -> Any:
                request = {
                    "adapter_id": identifier,
                    "operation": "blender." + name,
                    "arguments": args,
                }
                if attached:
                    request["artifact_ids"] = attached
                began = time.perf_counter()
                result = await client.call_tool("tyvrana_execute_operation", request)
                records.append(
                    {
                        "operation": name,
                        "request_bytes": len(json.dumps(request).encode()),
                        "response_bytes": len(result.model_dump_json().encode()),
                        "seconds": time.perf_counter() - began,
                        "error": result.is_error,
                    }
                )
                (tmp_path / "form-mcp-metrics.json").write_text(
                    json.dumps(records, indent=2)
                )
                assert not result.is_error, result.content
                return result

            imported = await client.call_tool(
                "tyvrana_import_artifact",
                {"path": str(path), "media_type": "image/png"},
            )
            descriptor = ArtifactDescriptor.model_validate(imported.structured_content)
            await call(
                "image.create_from_artifact",
                {"name": "Section", "artifact_id": descriptor.artifact_id},
                [descriptor.artifact_id],
            )
            await client.call_tool(
                "tyvrana_release_artifact", {"artifact_id": descriptor.artifact_id}
            )
            image_before = await call("image.inspect", {"names": ["Section"]})
            previewed = await call(
                "image.preview", {"names": ["Section"], "tile_size": 128}
            )
            assert len([c for c in previewed.content if c.type == "image"]) == 1
            image_after = await call("image.inspect", {"names": ["Section"]})
            assert image_before.structured_content == image_after.structured_content
            await call(
                "reference.create",
                {
                    "references": [
                        {"name": "Section", "image": "Section"},
                        {"name": "SectionUpper", "image": "Section"},
                    ]
                },
            )
            await call(
                "reference.register",
                {
                    "registrations": [
                        {
                            "reference": "Section",
                            "projection": "plane",
                            "calibration": {
                                "a": [0, 64],
                                "b": [128, 64],
                                "distance": 2,
                            },
                            "origin_pixel": [64, 64],
                            "horizontal": "x",
                            "vertical": "y",
                        },
                        {
                            "reference": "SectionUpper",
                            "projection": "plane",
                            "calibration": {
                                "a": [0, 64],
                                "b": [128, 64],
                                "distance": 2,
                            },
                            "origin_pixel": [64, 64],
                            "origin": [0, 0, 0.1],
                            "horizontal": "x",
                            "vertical": "y",
                        },
                    ]
                },
            )
            created = await call(
                "form.create",
                {
                    "forms": [
                        {
                            "name": "Casting",
                            "voxel_size": 0.02,
                            "surface_fit": {
                                "masks": [
                                    {"reference": "Section"},
                                    {"reference": "SectionUpper"},
                                ],
                                "radius": 0.2,
                                "max_distance": 0.002,
                                "iterations": 1,
                                "adaptive_neighborhood": True,
                            },
                            "parts": [
                                {
                                    "id": "body",
                                    "kind": "ellipsoid",
                                    "center": [0, 0, 0],
                                    "radii": [0.6, 0.4, 0.3],
                                }
                            ],
                        }
                    ]
                },
            )
            job = created.structured_content["result"]
            deadline = time.monotonic() + 45
            while job["state"] in {"queued", "running"}:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.1)
                polled = await call("form.status", {"job_id": job["job_id"]})
                job = polled.structured_content["result"]
            assert job["state"] == "completed", job
            identity = job["result"]["forms"][0]["component_id"]
            fitted = job["result"]["forms"][0]["surface_fit"]
            assert fitted["reference_points"] > 100
            assert fitted["maximum_displacement"] <= 0.002001
            compared = await call(
                "reference.compare",
                {
                    "comparisons": [
                        {
                            "id": "middle",
                            "objects": ["Casting"],
                            "mask": {"reference": "Section"},
                        }
                    ],
                    "resolution": 128,
                },
            )
            assert (
                compared.structured_content["result"]["comparisons"][0]["overlap_iou"]
                > 0.96
            )
            images = [c for c in compared.content if c.type == "image"]
            assert len(images) == 1
            overlay = base64.b64decode(images[0].data)
            assert inspect_png(overlay) == (128, 128)
            with Image.open(io.BytesIO(overlay)) as mask_image:
                assert len(mask_image.getcolors() or []) >= 2
            (tmp_path / "comparison.png").write_bytes(overlay)
            names = [f"Diagnostic{i:02}" for i in range(20)]
            finished = await call(
                "modifier.create_batch",
                {
                    "modifiers": [
                        {
                            "object_name": name,
                            "type": "corrective_smooth",
                            "settings": {"factor": 0.5, "iterations": 6, "scale": 0.01},
                        }
                        for name in names
                    ]
                },
            )
            assert len(finished.structured_content["result"]["created"]) == 20
            placed = await call(
                "object_set.place",
                {
                    "placements": [
                        {
                            "name": names[0],
                            "rule": {
                                "kind": "datums",
                                "origin": {"kind": "world", "point": [0, 0, 0]},
                                "x_axis": {"kind": "world", "point": [1, 0, 0]},
                                "y_axis": {"kind": "world", "point": [0, 1, 0]},
                                "z_axis": {"kind": "world", "point": [0, 0, 1]},
                            },
                        }
                    ]
                },
            )
            assert placed.structured_content["result"]["placements"][0]["valid"]
            checked = await call(
                "geometry.inspect",
                {
                    "objects": [
                        {"object_name": n, "self_intersection": True} for n in names
                    ],
                    "worst_limit": 0,
                },
            )
            assert (
                len(checked.structured_content["result"]["samples"][0]["objects"]) == 20
            )
            await call(
                "layer.inspect",
                {
                    "queries": [
                        {"mode": "current", "source": names[0], "target": names[1]}
                    ],
                    "worst_limit": 0,
                },
            )
            before = await call("scene.inspect", {"limit": 1})
            assert before.structured_content["result"]["object_count"] > 350
            views: list[dict[str, Any]] = [
                {"name": n, "orientation": n}
                for n in ("left", "right", "front", "rear", "top", "bottom")
            ]
            views += [
                {"name": n, "orientation": n, "projection": "perspective"}
                for n in ("oblique", "reverse_oblique")
            ]
            views += [
                {"name": "regional", "orientation": "oblique", "objects": ["Casting"]},
                {
                    "name": "regional_wire",
                    "orientation": "oblique",
                    "objects": names,
                    "wireframe": True,
                },
            ]
            submitted = await call(
                "render.image",
                {
                    "width": 1600,
                    "height": 1600,
                    "inspection": {
                        "objects": ["Casting"],
                        "views": views,
                        "packet": {"directory": str(tmp_path / "review")},
                    },
                    "budget": {"max_total_pixels": 30000000},
                },
            )
            rendered = await complete_render(client, identifier, submitted)
            value = rendered.structured_content["result"]
            assert value["review_directory"] == str(tmp_path / "review")
            assert len(value["inspection_tiles"]) == 10
            images = [c for c in rendered.content if c.type == "image"]
            assert len(images) == 1
            data = base64.b64decode(images[0].data)
            assert inspect_png(data) == (1152, 1536)
            assert_image_variation(data)
            (tmp_path / "review-preview.png").write_bytes(data)
            manifest = json.loads((tmp_path / "review/manifest.json").read_text())
            assert len(manifest["files"]) == 11
            for row in manifest["files"]:
                assert inspect_png(
                    (tmp_path / "review" / row["filename"]).read_bytes()
                ) == (row["width"], row["height"])
            after = await call("scene.inspect", {"limit": 1})
            assert (
                after.structured_content["result"]
                == before.structured_content["result"]
            )
            current = await call("form.inspect", {"names": ["Casting"]})
            assert (
                current.structured_content["result"]["forms"][0]["component_id"]
                == identity
            )
