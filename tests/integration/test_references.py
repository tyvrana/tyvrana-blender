"""Packaged headless reference/measurement and persistence validation."""

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.types import CallToolResult
from tyvrana_protocol import ArtifactDescriptor

from tyvrana_blender.operations import OPERATIONS

from ..png import checker_png
from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_references(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/reference_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "references.log").write_text(log)
    assert result.returncode == 0, log
    assert "REFERENCE_NATIVE_PASSED 19" in log
    assert "Traceback" not in log


async def test_reference_measurement_mcp_workflow(
    profile: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "reference.png"
    source.write_bytes(checker_png(200, 100))
    rows: list[dict[str, Any]] = []
    phase = "startup"
    async with core_client(tmp_path) as (client, port):
        assert client.instructions
        guidance = " ".join(client.instructions.lower().split())
        for concept in ("infer", "workflow", "dependencies", "requested result"):
            assert concept in guidance
        original = client.call_tool

        async def observed(
            name: str, arguments: dict[str, Any] | None = None, **kwargs: Any
        ) -> CallToolResult:
            start = time.perf_counter()
            result = await original(name, arguments, **kwargs)
            rows.append(
                {
                    "id": len(rows) + 1,
                    "tool": name,
                    "operation": (arguments or {}).get("operation"),
                    "phase": phase,
                    "seconds": time.perf_counter() - start,
                    "request_bytes": len(
                        json.dumps(arguments, separators=(",", ":")).encode()
                    ),
                    "response_bytes": len(result.model_dump_json().encode()),
                    "structured_bytes": len(
                        json.dumps(
                            result.structured_content, separators=(",", ":")
                        ).encode()
                    ),
                    "error": result.is_error,
                }
            )
            (tmp_path / "workflow-metrics.json").write_text(json.dumps(rows, indent=2))
            return result

        monkeypatch.setattr(client, "call_tool", observed)
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            registered = await discover(client)
            assert registered is not None and registered.operation_count == len(
                OPERATIONS
            )
            identifier = registered.instance_id
            phase = "contracts"
            schemas = await client.call_tool(
                "tyvrana_list_operations",
                {
                    "adapter_id": identifier,
                    "names": [
                        "blender.reference.create",
                        "blender.landmark.set",
                        "blender.measurement.inspect",
                        "blender.reference.calibrate",
                    ],
                    "schemas": "full",
                },
            )
            assert not schemas.is_error
            contracts = cast(dict[str, Any], schemas.structured_content)
            assert len(contracts["operations"]) == 4
            assert contracts["catalog_sha256"] == registered.catalog_sha256
            assert all(
                c["arguments_schema"] and c["result_schema"]
                for c in contracts["operations"]
            )

            async def call(name: str, /, **arguments: Any) -> Any:
                return await operation(client, identifier, "blender." + name, arguments)

            phase = "image_ingestion"
            imported = await client.call_tool(
                "tyvrana_import_artifact",
                {"path": str(source), "name": "Synthetic reference"},
            )
            assert not imported.is_error
            descriptor = ArtifactDescriptor.model_validate(imported.structured_content)
            source.unlink()
            image = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": identifier,
                    "operation": "blender.image.create_from_artifact",
                    "arguments": {
                        "artifact_id": descriptor.artifact_id,
                        "name": "Chart",
                    },
                    "artifact_ids": [descriptor.artifact_id],
                },
            )
            assert not image.is_error
            released = await client.call_tool(
                "tyvrana_release_artifact", {"artifact_id": descriptor.artifact_id}
            )
            assert not released.is_error
            phase = "create_references"
            references = await call(
                "reference.create",
                references=[
                    {
                        "name": "Front",
                        "image": "Chart",
                        "size": 2,
                        "category": "orthographic",
                        "source_label": "Synthetic 200x100 chart",
                    },
                    {
                        "name": "Side",
                        "image": "Chart",
                        "size": 2,
                        "rotation": [0, math.pi / 2, 0],
                    },
                    {
                        "name": "Top",
                        "image": "Chart",
                        "size": 2,
                        "rotation": [math.pi / 2, 0, 0],
                    },
                ],
            )
            assert len(references["references"]) == 3
            assert all(r["packed"] and r["valid"] for r in references["references"])
            phase = "filtered_reference"
            focused = await call("reference.inspect", names=["Front"])
            assert len(focused["references"]) == 1
            assert len(json.dumps(focused)) < 1800
            phase = "configure_calibrate"
            await call(
                "reference.configure",
                references=[
                    {"name": "Side", "opacity": 0.2},
                    {"name": "Top", "perspective": True},
                ],
            )
            calibration = await call(
                "reference.calibrate",
                name="Front",
                a=[0, 0],
                b=[200, 0],
                target_distance=4,
            )
            assert calibration["after_distance"] == 4 and calibration["factor"] == 2
            phase = "landmarks"
            await call(
                "object.create_primitive",
                primitive="cube",
                name="Part",
                scale=[2, 1, 1],
                location=[10, 0, 0],
            )
            landmarks = await call(
                "landmark.set",
                landmarks=[
                    {"name": "Origin", "point": [0, 0, 0]},
                    {
                        "name": "Mount",
                        "point": [1, 0, 0],
                        "object": "Part",
                        "category": "assembly",
                    },
                    {"name": "Tip", "point": [3, 4, 0]},
                    {"name": "Reference datum", "point": [0, 0, 0], "object": "Front"},
                ],
            )
            assert len(landmarks["landmarks"]) == 4
            assert landmarks["landmarks"][1]["world_point"] == [12, 0, 0]
            phase = "filtered_landmark"
            selected = await call(
                "landmark.inspect", object="Part", category="assembly"
            )
            assert len(selected["landmarks"]) == 1
            assert len(json.dumps(selected)) < 650
            phase = "measurements"
            queries = [
                {
                    "kind": "distance",
                    "name": "Diagonal",
                    "a": {"kind": "landmark", "name": "Origin"},
                    "b": {"kind": "landmark", "name": "Tip"},
                    "comparison": {"target": 5, "tolerance": 0.00001},
                },
                {
                    "kind": "angle",
                    "name": "Right angle",
                    "a": {"kind": "world", "point": [1, 0, 0]},
                    "vertex": {"kind": "world", "point": [0, 0, 0]},
                    "b": {"kind": "world", "point": [0, 1, 0]},
                    "comparison": {"target": 90, "tolerance": 0.001},
                },
                {"kind": "bounds", "name": "Dimensions", "object": "Part"},
                {
                    "kind": "distance",
                    "name": "Reference width",
                    "a": {
                        "kind": "reference_pixel",
                        "reference": "Front",
                        "pixel": [0, 0],
                    },
                    "b": {
                        "kind": "reference_pixel",
                        "reference": "Front",
                        "pixel": [200, 0],
                    },
                    "comparison": {"target": 4, "tolerance": 0.00001},
                },
            ]
            measurements = await call("measurement.inspect", queries=queries)
            assert measurements["measurements"][0]["within_tolerance"]
            assert measurements["measurements"][1]["within_tolerance"]
            assert measurements["measurements"][2]["dimensions"] == [4, 2, 2]
            assert measurements["measurements"][3]["within_tolerance"]
            await call("scene.configure_units", system="metric", meters_per_unit=0.01)
            meters = await call(
                "measurement.inspect", queries=[queries[0]], unit="meters"
            )
            assert meters["measurements"][0]["value"] == pytest.approx(0.05)
            phase = "expected_error"
            bad = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": identifier,
                    "operation": "blender.reference.configure",
                    "arguments": {"references": [{"name": "Front", "opacity": 2}]},
                },
            )
            assert bad.is_error and len(bad.model_dump_json()) < 5000
            failed_id = rows[-1]["id"]
            phase = "linked_repair"
            await call(
                "reference.configure", references=[{"name": "Front", "opacity": 0.75}]
            )
            rows[-1]["retry_of"] = failed_id
            phase = "persistence"
            path = tmp_path / "reference-project.blend"
            await call("file.save", filepath=str(path))
            await call("file.open", filepath=str(path), discard_current=True)
            persisted = await call("reference.inspect", names=["Front"])
            assert (
                persisted["references"][0]["packed"]
                and persisted["references"][0]["valid"]
            )
            assert (
                persisted["references"][0]["source_label"] == "Synthetic 200x100 chart"
            )
            assert persisted["references"][0]["opacity"] == pytest.approx(0.75)
            assert (
                await call("landmark.inspect", object="Part", category="assembly")
                == selected
            )
            reopened = await call("measurement.inspect", queries=queries)
            assert reopened["measurements"] == measurements["measurements"]
            assert reopened["units"]["meters_per_unit"] == pytest.approx(0.01)
            phase = "cleanup"
            await call(
                "landmark.remove", names=["Origin", "Mount", "Tip", "Reference datum"]
            )
            await call("reference.remove", names=["Front", "Side", "Top"])
            await call("image.remove", name="Chart")
            assert (await call("reference.inspect"))["page"]["total_count"] == 0
            assert (await call("landmark.inspect"))["page"]["total_count"] == 0
        phase = "shutdown"
        await discover(client, empty=True)
    (tmp_path / "workflow-metrics.json").write_text(json.dumps(rows, indent=2))
    print("REFERENCE_WORKFLOW_METRICS " + json.dumps(rows))
    assert sum(bool(r["error"]) for r in rows) == 1
