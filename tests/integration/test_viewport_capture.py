"""Real interactive framebuffer evidence and multiview call/payload comparison."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from mcp.types import ImageContent

from tyvrana_blender.operations import REGISTRY

from ..png import channel_means, inspect_png, rgb_pixels
from .conftest import running_blender
from .test_e2e import core_client, discover


@pytest.mark.interactive
async def test_viewport_contact_sheet_restores_and_matches_single_views(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=True):
            adapter = await discover(client)
            assert adapter is not None
            measurements: list[dict[str, Any]] = []

            async def tool(name: str, args: dict[str, Any]) -> Any:
                start = time.perf_counter()
                response = await client.call_tool(name, args)
                assert not response.is_error, response.content
                measurements.append(
                    dict(
                        tool=name,
                        request_bytes=len(json.dumps(args).encode()),
                        response_bytes=len(response.model_dump_json().encode()),
                        elapsed_seconds=time.perf_counter() - start,
                    )
                )
                assert not any(isinstance(c, ImageContent) for c in response.content)
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

            async def export(response: Any, name: str) -> bytes:
                identifier = response["retained_artifact_ids"][0]
                path = tmp_path / name
                await tool(
                    "tyvrana_export_artifact",
                    dict(artifact_id=identifier, path=str(path)),
                )
                return path.read_bytes()

            await op(
                "object.create_primitive",
                primitive="cube",
                name="Asymmetric",
                scale=[1, 2, 0.4],
            )
            views = (await op("viewport.inspect"))["result"]["viewports"]
            identifier = views[0]["viewport_id"]
            await op(
                "viewport.frame", viewport_id=identifier, object_names=["Asymmetric"]
            )
            await op(
                "viewport.configure", viewport_id=identifier, orientation="oblique"
            )
            original = (await op("viewport.inspect", viewport_id=identifier))["result"][
                "viewports"
            ][0]
            measurements.clear()
            individual = []
            for i, view in enumerate(["front", "right", "top"]):
                await op("viewport.configure", viewport_id=identifier, orientation=view)
                image = await op("viewport.capture", viewport_id=identifier)
                individual.append(await export(image, f"single-{i}.png"))
            await op(
                "viewport.configure", viewport_id=identifier, orientation="oblique"
            )
            before = measurements.copy()
            measurements.clear()
            combined = await op(
                "viewport.capture",
                viewport_id=identifier,
                views=[dict(orientation=v) for v in ["front", "right", "top"]],
            )
            sheet = await export(combined, "sheet.png")
            after = measurements.copy()
            state = (await op("viewport.inspect", viewport_id=identifier))["result"][
                "viewports"
            ][0]
            assert state == original
            width, height = inspect_png(sheet)
            sw, sh = inspect_png(individual[0])
            assert (width, height) == (3 * sw, sh)
            pixels = rgb_pixels(sheet)
            errors = []
            for i, single in enumerate(individual):
                crop = b"".join(
                    pixels[(y * width + i * sw) * 3 : (y * width + (i + 1) * sw) * 3]
                    for y in range(height)
                )
                source = rgb_pixels(single)
                errors.append(
                    sum(abs(a - b) for a, b in zip(crop, source, strict=True))
                    / len(crop)
                )
            # The central solid must be shaded, not a black hole in an overlay buffer.
            for single in individual:
                means = channel_means(
                    single, (sw // 2 - 10, sh // 2 - 10, sw // 2 + 10, sh // 2 + 10)
                )
                assert min(means) > 65, means
            # Allow tiny UI timer/highlight differences, not stale or wrong views.
            assert max(errors) < 1, errors
            assert individual[0] != individual[1] != individual[2]
            metadata = combined["result"]
            assert metadata["restored"] and len(metadata["captures"]) == 3
            assert all(
                c["state"]["viewport_id"] == identifier for c in metadata["captures"]
            )
            assert (
                len({tuple(c["state"]["view_rotation"]) for c in metadata["captures"]})
                == 3
            )

            def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
                return dict(
                    calls=len(rows),
                    operations=sum(
                        r["tool"] == "tyvrana_execute_operation" for r in rows
                    ),
                    request_bytes=sum(r["request_bytes"] for r in rows),
                    response_bytes=sum(r["response_bytes"] for r in rows),
                    elapsed_seconds=sum(r["elapsed_seconds"] for r in rows),
                    failures=0,
                    retries=0,
                    renders=0,
                    polls=0,
                )

            metrics = dict(
                before=totals(before),
                after=totals(after),
                schema_bytes=len(
                    REGISTRY["blender.viewport.capture"]
                    .contract.model_dump_json()
                    .encode()
                ),
                before_artifact_bytes=sum(map(len, individual)),
                after_artifact_bytes=len(sheet),
                mean_pixel_errors=errors,
            )
            (tmp_path / "viewport-metrics.json").write_text(json.dumps(metrics))
            print("VIEWPORT_MCP_METRICS", json.dumps(metrics))
