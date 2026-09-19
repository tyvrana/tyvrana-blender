"""Intended interactive host renders and audits its saved delivery dependencies."""

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tyvrana_blender.operations import REGISTRY

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, wait_for_project


def test_native_delivery(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/delivery_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=90,
    )
    log = result.stdout + result.stderr
    (tmp_path / "delivery-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "DELIVERY_NATIVE_PASSED" in log


async def test_interactive_host_render_and_delivery(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_RENDER"] = "1"
    metrics: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            adapter = await discover(client)
            assert adapter is not None

            async def call(name: str, **arguments: Any) -> Any:
                request = dict(
                    adapter_id=adapter.instance_id,
                    operation="blender." + name,
                    arguments=arguments,
                    artifact_delivery="reference",
                )
                started = time.perf_counter()
                response = await client.call_tool("tyvrana_execute_operation", request)
                if response.is_error:
                    print(response.model_dump_json(), flush=True)
                metrics.append(
                    dict(
                        operation=name,
                        request_bytes=len(json.dumps(request).encode()),
                        response_bytes=len(response.model_dump_json().encode()),
                        seconds=time.perf_counter() - started,
                        failed=response.is_error,
                    )
                )
                assert not response.is_error, response
                assert response.structured_content is not None
                return response.structured_content["result"]

            identity = await call("extension.inspect")
            assert not identity["background"]
            document = tmp_path / "delivery.blend"
            await call("file.save", filepath=str(document))
            # A changed native path triggers the existing registration refresh.
            await wait_for_project(client, adapter.instance_id, str(document))
            before = await call("scene.inspect")
            output = tmp_path / "delivered.png"
            status = await call(
                "render.image",
                width=128,
                height=128,
                cycles=dict(samples=8, device="cpu", denoise=False),
                wait_seconds=0,
                output=dict(filepath=str(output)),
            )
            while status["state"] not in {"succeeded", "failed", "cancelled"}:
                status = await call(
                    "render.status",
                    job_id=status["job_id"],
                    after_revision=status["revision"],
                    wait_seconds=20,
                )
            assert status["state"] == "succeeded", status
            assert (
                status["host_pid"] == identity["host_pid"]
                and not status["host_background"]
            )
            assert status["execution"] == "connected_host" and status[
                "document_filepath"
            ] == str(document)
            assert status["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
            worker = identity["worker_pid"]
            assert (
                not Path(f"/proc/{worker}/task/{worker}/children").read_text().strip()  # noqa: ASYNC240 - Owned process observation.
            )
            assert await call("scene.inspect") == before
            audit = await call(
                "file.audit",
                required_files=[dict(filepath=str(output), sha256=status["sha256"])],
            )
            assert audit["ready"], audit
            missing = await call(
                "file.audit",
                required_files=[dict(filepath=str(tmp_path / "absent.exr"))],
            )
            assert not missing["ready"] and missing["counts"]["missing"] == 1
            summary = dict(
                calls=len(metrics),
                request_bytes=sum(r["request_bytes"] for r in metrics),
                response_bytes=sum(r["response_bytes"] for r in metrics),
                seconds=sum(r["seconds"] for r in metrics),
                schema_bytes=sum(
                    len(REGISTRY["blender." + op].contract.model_dump_json().encode())
                    for op in {r["operation"] for r in metrics}
                ),
                host_pid=status["host_pid"],
                native_render_seconds=status["render_seconds"],
                artifact_bytes=status["byte_size"],
                failures=0,
                scope=(
                    "Execute operations only; initial discovery and "
                    "save-registration wait excluded"
                ),
                operations=metrics,
            )
            (tmp_path / "host-delivery-metrics.json").write_text(
                json.dumps(summary, indent=2)
            )
            print("HOST_DELIVERY_METRICS", json.dumps(summary))
