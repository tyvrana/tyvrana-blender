"""Packaged synthetic reference-space construction through the real MCP path."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.types import CallToolResult, TextContent
from tyvrana_protocol import ArtifactDescriptor

from ..construction_fixture import create_fixture
from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, wait_for_project


def test_native_construction(profile: dict[str, str], tmp_path: Path) -> None:
    directory = tmp_path / "blueprints"
    create_fixture(directory)
    profile["TYVRANA_CONSTRUCTION_FIXTURE"] = str(directory)
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/construction_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "construction.log").write_text(log)
    assert result.returncode == 0, log
    assert "CONSTRUCTION_NATIVE_PASSED 9" in log


async def test_construction_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    directory = tmp_path / "blueprints"
    fixture = create_fixture(directory)
    rows: list[dict[str, Any]] = []
    phase = "setup"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def tool(name: str, args: dict[str, Any]) -> CallToolResult:
                started = time.perf_counter()
                response = await client.call_tool(name, args)
                rows.append(
                    {
                        "phase": phase,
                        "tool": name,
                        "operation": args.get("operation"),
                        "request_bytes": len(
                            json.dumps(args, separators=(",", ":")).encode()
                        ),
                        "response_bytes": len(response.model_dump_json().encode()),
                        "seconds": time.perf_counter() - started,
                        "error": response.is_error,
                    }
                )
                (tmp_path / "construction-metrics.json").write_text(
                    json.dumps(rows, indent=2)
                )
                return response

            async def call(operation_name: str, **args: Any) -> dict[str, Any]:
                response = await tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": adapter.instance_id,
                        "operation": "blender." + operation_name,
                        "arguments": args,
                    },
                )
                assert not response.is_error, response.content
                value = cast(dict[str, Any], response.structured_content["result"])
                if operation_name in {"file.save", "file.open"}:
                    await wait_for_project(
                        client, adapter.instance_id, value["filepath"]
                    )
                return value

            for name in ("Front", "Side", "Top"):
                response = await tool(
                    "tyvrana_import_artifact",
                    {
                        "files": [
                            {
                                "path": str(directory / f"{name}.png"),
                                "media_type": "image/png",
                            }
                        ]
                    },
                )
                artifact = ArtifactDescriptor.model_validate(
                    response.structured_content
                )
                response = await tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": adapter.instance_id,
                        "operation": "blender.image.create_from_artifact",
                        "arguments": {
                            "images": [
                                {
                                    "name": name,
                                    "artifact_id": artifact.artifact_id,
                                }
                            ]
                        },
                        "artifact_ids": [artifact.artifact_id],
                    },
                )
                assert not response.is_error, response.content
                await tool(
                    "tyvrana_release_artifact", {"artifact_ids": [artifact.artifact_id]}
                )
            await call(
                "reference.create",
                references=[{"name": n, "image": n} for n in ("Front", "Side", "Top")],
            )
            # Ground-truth blocks; solving never consumes these XYZ values.
            for i, (x, y) in enumerate(
                ((-0.4, -0.4), (-0.4, 0.4), (0.4, -0.4), (0.4, 0.4))
            ):
                await call(
                    "object.create_primitive",
                    primitive="cube",
                    name=f"Block.{i}",
                    location=[x, y, 0],
                    scale=[0.15, 0.1, 0.2],
                )
            phase = "discovery"
            schemas = await tool(
                "tyvrana_list_operations",
                {
                    "adapter_id": adapter.instance_id,
                    "names": [
                        "blender.reference.register",
                        "blender.reference.observation.set",
                        "blender.landmark.derive",
                        "blender.landmark.inspect",
                    ],
                    "schemas": "full",
                },
            )
            assert not schemas.is_error
            phase = "32_new_workflow"
            registered = await call(
                "reference.register", registrations=fixture["registrations"]
            )
            assert registered["page"]["returned_count"] == 3
            assert all(r["frame_id"] is None for r in registered["registrations"])
            written = await call(
                "reference.observation.set", observations=fixture["observations"]
            )
            assert written["count"] == 96
            solved = await call(
                "landmark.derive", landmarks=fixture["landmarks"], tolerance=0.00001
            )
            assert solved["solved"] == 32 and solved["max_residual"] < 1e-6
            compact = await call(
                "landmark.inspect", derived_only=True, detail="summary"
            )
            assert compact["construction"]["solved"] == 32 and not compact["landmarks"]
            phase = "truth_verification"
            points = await call("landmark.inspect", category="mechanical")
            for actual, expected in zip(
                points["landmarks"], fixture["points"], strict=True
            ):
                assert actual["world_point"] == pytest.approx(expected, abs=1e-6)
                assert not actual["stale"] and actual["valid"]
            provenance = await call(
                "landmark.inspect", names=["Corner.00"], detail="provenance"
            )
            assert len(provenance["provenance"][0]["observations"]) == 3
            phase = "32_literal_baseline"
            await call("reference.inspect", names=["Front", "Side", "Top"])
            await call(
                "landmark.set",
                landmarks=[
                    {"name": f"Literal.{i:02}", "point": point}
                    for i, point in enumerate(fixture["points"])
                ],
            )
            phase = "fixtures_B_C_E"
            result = await call(
                "landmark.derive",
                landmarks=[
                    {
                        "name": "Two",
                        "source": {
                            "kind": "observations",
                            "observations": ["Front.00", "Side.00"],
                        },
                    },
                    {
                        "name": "One",
                        "source": {
                            "kind": "observations",
                            "observations": ["Front.00"],
                        },
                    },
                ],
            )
            assert result["solved"] == result["underconstrained"] == 1
            assert not (await call("landmark.inspect", names=["One"]))["landmarks"]
            await call(
                "reference.observation.set",
                observations=[{**fixture["observations"][0], "pixel": [100, 100]}],
            )
            conflict = await call(
                "landmark.derive", landmarks=[fixture["landmarks"][0]]
            )
            assert (
                conflict["inconsistent"] == 1
                and conflict["worst"][0]["worst_observation"] == "Front.00"
            )
            assert (await call("landmark.inspect", names=["Corner.00"]))["landmarks"][
                0
            ]["stale"]
            perspective = await tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": adapter.instance_id,
                    "operation": "blender.reference.register",
                    "arguments": {
                        "registrations": [
                            {**fixture["registrations"][0], "projection": "perspective"}
                        ]
                    },
                },
            )
            assert perspective.is_error
            content = perspective.content[0]
            assert isinstance(content, TextContent)
            assert json.loads(content.text)["code"] == "unsupported_projection"
            phase = "persistence_repeatability"
            await call(
                "reference.observation.set", observations=[fixture["observations"][0]]
            )
            await call("landmark.derive", landmarks=fixture["landmarks"])
            before = await call("landmark.inspect", category="mechanical")
            path = tmp_path / "construction.blend"
            await call("file.save", filepath=str(path))
            await call("file.open", filepath=str(path), discard_current=True)
            assert await call("landmark.inspect", category="mechanical") == before
            await call("landmark.derive", landmarks=fixture["landmarks"])
            assert await call("landmark.inspect", category="mechanical") == before
            phase = "fixture_D"
            await call(
                "reference.calibrate",
                name="Front",
                a=[0, 0],
                b=[200, 0],
                target_distance=3,
            )
            stale = await call(
                "landmark.inspect", category="mechanical", detail="summary"
            )
            assert stale["construction"]["stale"] == 32
            assert (await call("landmark.derive", landmarks=fixture["landmarks"]))[
                "stale"
            ] == 32
            phase = "recovery"
            await call("reference.register", registrations=fixture["registrations"])
            assert (await call("landmark.derive", landmarks=fixture["landmarks"]))[
                "solved"
            ] == 32
    metrics = {
        "rows": rows,
        "initial_solve": solved,
        "fixture": "four mechanical mounting blocks / 32 corners / three blueprints",
    }
    (tmp_path / "construction-metrics.json").write_text(json.dumps(metrics, indent=2))
    print("CONSTRUCTION_METRICS", json.dumps(metrics))
