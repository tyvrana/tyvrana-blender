"""Real MCP delivery of production formats and bounded frame sequences."""

import hashlib
import json
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

from tests.png import rgb_pixels
from tyvrana_blender.operations import REGISTRY
from tyvrana_blender.render_output import exr_header

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover


def test_native_render_output(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/render_output_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "render-output-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "RENDER_OUTPUT_NATIVE_PASSED" in log


async def test_production_output_sequence_and_cancel(
    profile: dict[str, str], tmp_path: Path
) -> None:
    profile["TYVRANA_TEST_RENDER"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            metrics: list[dict[str, Any]] = []

            async def tool(name: str, args: dict[str, Any]) -> Any:
                start = time.perf_counter()
                response = await client.call_tool(name, args)
                assert not response.is_error, response.content
                metrics.append(
                    dict(
                        tool=name,
                        operation=args.get("operation"),
                        request_bytes=len(json.dumps(args).encode()),
                        response_bytes=len(response.model_dump_json().encode()),
                        elapsed_seconds=time.perf_counter() - start,
                    )
                )
                assert all(c.type != "image" for c in response.content)
                return response.structured_content

            async def op(name: str, /, **args: Any) -> Any:
                return await tool(
                    "tyvrana_execute_operation",
                    dict(
                        adapter_id=adapter.instance_id,
                        operation="blender." + name,
                        arguments=args,
                        artifact_delivery="reference",
                    ),
                )

            async def finish(response: Any) -> Any:
                row = response["result"]
                while row["state"] not in {"succeeded", "failed", "cancelled"}:
                    response = await op(
                        "render.status",
                        job_id=row["job_id"],
                        after_revision=row["revision"],
                        wait_seconds=20,
                    )
                    row = response["result"]
                assert row["state"] == "succeeded", row
                return await op("render.result", job_id=row["job_id"])

            async def render(label: str, **args: Any) -> Any:
                result = await finish(
                    await op(
                        "render.image",
                        width=64,
                        height=64,
                        cycles=dict(samples=1),
                        wait_seconds=0,
                        **args,
                    )
                )
                identifier = result["retained_artifact_ids"][0]
                destination = tmp_path / label
                await tool(
                    "tyvrana_export_artifact",
                    dict(artifact_id=identifier, path=str(destination)),
                )
                return result["result"]

            for frame in (1, 2, 3):
                await op("timeline.configure", frame=frame)
                await render(f"single-{frame}.png")
            before = metrics.copy()
            metrics.clear()
            sequence = await render("sequence.zip", frames=[1, 2, 3])
            after = metrics.copy()
            assert sequence["completed_frames"] == sequence["frame_count"] == 3
            with zipfile.ZipFile(tmp_path / "sequence.zip") as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert [f["frame"] for f in manifest["frames"]] == [1, 2, 3]
                for row in manifest["frames"]:
                    data = archive.read(row["filename"])
                    assert rgb_pixels(data) == rgb_pixels(
                        (tmp_path / f"single-{row['frame']}.png").read_bytes()
                    )
                    assert hashlib.sha256(data).hexdigest() == row["sha256"]
            hdr = await render("output.exr", format="exr", bit_depth=32)
            assert hdr["color_management"]["output_encoding"] == "scene_linear"
            assert exr_header(tmp_path / "output.exr")[:2] == (64, 64)
            status = (
                await op(
                    "render.image",
                    width=128,
                    height=128,
                    frames=list(range(32)),
                    cycles=dict(samples=512),
                    wait_seconds=0,
                )
            )["result"]
            status = (await op("render.cancel", job_id=status["job_id"]))["result"]
            while status["state"] not in {"cancelled", "failed"}:
                status = (
                    await op(
                        "render.status",
                        job_id=status["job_id"],
                        after_revision=status["revision"],
                        wait_seconds=20,
                    )
                )["result"]
            assert status["state"] == "cancelled" and not status["result_available"]

            def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
                return dict(
                    calls=len(rows),
                    operations=sum(r["operation"] is not None for r in rows),
                    request_bytes=sum(r["request_bytes"] for r in rows),
                    response_bytes=sum(r["response_bytes"] for r in rows),
                    elapsed_seconds=sum(r["elapsed_seconds"] for r in rows),
                    polls=sum(r["operation"] == "blender.render.status" for r in rows),
                    failures=0,
                    retries=0,
                    renders=3,
                )

            measured = dict(
                before=totals(before),
                after=totals(after),
                sequence_render_seconds=sequence["render_seconds"],
                artifact_bytes=(tmp_path / "sequence.zip").stat().st_size,
                schema_bytes=len(
                    REGISTRY["blender.render.image"].contract.model_dump_json().encode()
                ),
            )
            (tmp_path / "render-output-metrics.json").write_text(json.dumps(measured))
            print("RENDER_OUTPUT_MCP_METRICS", json.dumps(measured))
