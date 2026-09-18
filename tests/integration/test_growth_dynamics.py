"""Packaged native dynamics and observable MCP cache lifecycle."""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover


def test_native_dynamics(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/dynamics_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "native-dynamics.log").write_text(log)
    assert result.returncode == 0, log
    assert "DYNAMICS_NATIVE_PASSED" in log


async def test_dynamics_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            rows: list[dict[str, Any]] = []

            async def call(name: str, /, **args: Any) -> Any:
                request = dict(
                    adapter_id=adapter.instance_id,
                    operation="blender." + name,
                    arguments=args,
                )
                start = time.perf_counter()
                response = await client.call_tool("tyvrana_execute_operation", request)
                assert not response.is_error, response
                rows.append(
                    dict(
                        operation=name,
                        request_bytes=len(json.dumps(request).encode()),
                        response_bytes=len(response.model_dump_json().encode()),
                        seconds=time.perf_counter() - start,
                    )
                )
                return response.structured_content["result"]

            for name, z in [("Surface", 0), ("Floor", -1)]:
                await call(
                    "object.create_primitive",
                    primitive="plane",
                    name=name,
                    location=[0, 0, z],
                )
            await call(
                "growth.create",
                name="Field",
                surface="Surface",
                families=[dict(name="Fiber", length=0.5)],
                regions=[dict(name="Area", family="Fiber", guides=8, children=16)],
            )
            rows.clear()
            args = dict(
                object_name="Field",
                frame_start=1,
                frame_end=16,
                settings=dict(colliders=["Floor"]),
            )
            job = await call("growth.dynamics.bake", **args)
            deadline = time.monotonic() + 30
            while job["state"] in {"queued", "running"}:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
                job = await call("growth.dynamics.status", job_id=job["job_id"])
            assert job["state"] == "completed", job
            assert job["result"]["maximum_displacement"] > 0.001
            qa = await call("growth.sample", object_name="Field", frames=[1, 8, 16])
            assert qa["restored"] and all(r["valid"] for r in qa["samples"])
            assert max(r["qa"]["maximum_root_error"] for r in qa["samples"]) < 1e-5
            metrics = dict(
                calls=len(rows),
                operations=len(rows),
                request_bytes=sum(r["request_bytes"] for r in rows),
                response_bytes=sum(r["response_bytes"] for r in rows),
                elapsed_seconds=sum(r["seconds"] for r in rows),
                execution_seconds=job["result"]["processing_seconds"],
                polls=sum(r["operation"].endswith(".status") for r in rows),
                failures=0,
                retries=0,
                renders=0,
                cache_bytes=job["result"]["cache_bytes"],
                schema_bytes=sum(
                    len(REGISTRY["blender." + n].contract.model_dump_json().encode())
                    for n in {r["operation"] for r in rows}
                ),
                predecessor="No equivalent typed secondary simulation",
            )
            old = job["result"]["cache_sha256"]
            replacement = await call(
                "growth.dynamics.bake", **(args | dict(replace=True, frame_end=128))
            )
            cancelled = await call(
                "growth.dynamics.cancel", job_id=replacement["job_id"]
            )
            assert cancelled["state"] == "cancelled"
            check = await call("growth.dynamics.inspect", object_name="Field")
            assert check["cache_sha256"] == old and check["valid"]
            await call("object.set_transform", name="Surface", location=[0.1, 0, 0])
            assert not (await call("growth.dynamics.inspect", object_name="Field"))[
                "valid"
            ]
            await call("growth.dynamics.clear", object_name="Field")
            assert (await call("growth.inspect", object_name="Field"))["summary"][
                "valid"
            ]
            (tmp_path / "dynamics-metrics.json").write_text(json.dumps(metrics))
            print("DYNAMICS_MCP_METRICS", json.dumps(metrics))
