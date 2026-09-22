"""Large real native content observed through bounded MCP job calls."""

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from .conftest import ROOT, running_blender
from .test_e2e import core_client


async def test_dense_attestation_job(profile: dict[str, str], tmp_path: Path) -> None:
    shutil.copytree(
        ROOT / "src/tyvrana_blender",
        Path(profile["BLENDER_USER_RESOURCES"])
        / "extensions/user_default/tyvrana_blender",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    records: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile.update(TYVRANA_TEST_PORT=str(port), TYVRANA_TEST_DENSE_ATTEST="1")
        async with running_blender(profile, tmp_path, ui=False):
            adapters = await client.call_tool(
                "tyvrana_list_adapters", {"application": "blender", "wait_seconds": 25}
            )
            adapter = adapters.structured_content["adapters"][0]["instance_id"]

            async def execute(operation: str, arguments: dict[str, Any]) -> Any:
                started = time.perf_counter()
                response = await client.call_tool(
                    "tyvrana_execute_operation",
                    dict(adapter_id=adapter, operation=operation, arguments=arguments),
                )
                elapsed = time.perf_counter() - started
                records.append(
                    dict(
                        operation=operation,
                        elapsed=elapsed,
                        bytes=len(response.model_dump_json().encode()),
                        result=response.structured_content,
                    )
                )
                assert not response.is_error, response.content
                assert elapsed < 25
                return response.structured_content["result"]

            initial = await execute("blender.document.attest", {})
            assert records[-1]["elapsed"] < 2 and initial["state"] == "queued"
            current = initial
            delay = 0.5
            while current["state"] in {"queued", "running"}:
                await asyncio.sleep(delay)
                current = await execute(
                    "blender.document.attest_status", {"job_id": initial["job_id"]}
                )
                delay = min(2.0, delay * 1.5)
            assert current["state"] == "completed", current
            assert current["result"]["status"] == "complete", current
            assert current["result"]["work"]["bulk_elements"] > 100000000
            assert records[-1]["bytes"] < 20000
            assert len(records) < 20
            cancel = await execute("blender.document.attest", {})
            cancelled = await execute(
                "blender.document.attest_cancel", {"job_id": cancel["job_id"]}
            )
            assert cancelled["state"] == "cancelled", cancelled
    (tmp_path / "async-attestation.json").write_text(json.dumps(records, indent=2))
