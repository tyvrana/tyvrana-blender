"""Packaged geometry QA and actual MCP routing on isolated hosts."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


def test_native_geometry_qa(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/geometry_qa_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "geometry-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "GEOMETRY_QA_NATIVE_PASSED 6" in log


async def test_geometry_qa_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            for name, x in [("A", 0), ("B", 3)]:
                await operation(
                    client,
                    adapter.instance_id,
                    "blender.object.create_primitive",
                    dict(primitive="cube", name=name, location=[x, 0, 0]),
                )
            args: dict[str, Any] = dict(
                pairs=[dict(left="A", right="B")],
                objects=[dict(object_name="A", self_intersection=True)],
                frames=[1, 2, 3],
            )
            start = time.perf_counter()
            result: Any = await operation(
                client, adapter.instance_id, "blender.geometry.inspect", args
            )
            elapsed = time.perf_counter() - start
            assert result["minimum_distance"] == 1
            assert len(result["samples"]) == 3
            assert all(
                s["objects"][0]["self_contact_triangle_pairs"] == 0
                for s in result["samples"]
            )
            metrics = dict(
                calls=1,
                operations=1,
                request_bytes=len(json.dumps(args).encode()),
                response_bytes=len(json.dumps(result).encode()),
                schema_bytes=len(
                    REGISTRY["blender.geometry.inspect"]
                    .contract.model_dump_json()
                    .encode()
                ),
                elapsed_seconds=elapsed,
                execution_seconds=result["processing_seconds"],
                triangle_tests=result["triangle_tests"],
                failures=0,
                retries=0,
                renders=0,
                polls=0,
                artifact_bytes=0,
            )
            (tmp_path / "geometry-metrics.json").write_text(json.dumps(metrics))
            print("GEOMETRY_QA_MCP_METRICS", json.dumps(metrics))
