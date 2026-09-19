"""Mixed structural families over MCP, diagnostic images and native reload."""

import asyncio
import base64
import json
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

from tyvrana_blender.deployment import stage

from ..assembly_fixtures import master_spec
from ..png import assert_image_variation, inspect_png
from .conftest import ROOT, running_blender
from .rendering import complete_render
from .test_e2e import core_client, discover, wait_for_project
from .test_extension_reload import adapter as wait_adapter


def test_native_assembly(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/assembly_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = result.stdout + result.stderr
    (tmp_path / "assembly-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "ASSEMBLY_NATIVE_PASSED 11" in log


async def test_assembly_mcp_reload_and_diagnostics(
    profile: dict[str, str], tmp_path: Path
) -> None:
    rows = []
    profile["TYVRANA_TEST_LIFECYCLE"] = "1"
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            registered = await discover(client)
            assert registered
            identifier = registered.instance_id

            async def call(operation: str, args: dict[str, Any]) -> dict[str, Any]:
                request = {
                    "adapter_id": identifier,
                    "operation": "blender." + operation,
                    "arguments": args,
                }
                started = time.perf_counter()
                response = await client.call_tool("tyvrana_execute_operation", request)
                rows.append(
                    {
                        "operation": operation,
                        "request": args,
                        "request_bytes": len(
                            json.dumps(request, separators=(",", ":")).encode()
                        ),
                        "response_bytes": len(response.model_dump_json().encode()),
                        "seconds": time.perf_counter() - started,
                        "error": response.is_error,
                    }
                )
                (tmp_path / "assembly-mcp-metrics.json").write_text(
                    json.dumps(rows, indent=2)
                )
                assert not response.is_error, response.content
                if operation == "render.image":
                    response = await complete_render(client, identifier, response)
                    images = [c for c in response.content if c.type == "image"]
                    assert len(images) == 1
                    data = base64.b64decode(images[0].data)
                    assert inspect_png(data) == (768, 768)
                    assert_image_variation(data)
                    (tmp_path / "assembly-mcp.png").write_bytes(data)
                value = dict(response.structured_content["result"])
                if operation in {"file.save", "file.open"}:
                    await wait_for_project(client, identifier, value["filepath"])
                return value

            catalog = await client.call_tool(
                "tyvrana_list_operations",
                {
                    "adapter_id": identifier,
                    "query": "create structural assembly",
                    "limit": 3,
                },
            )
            assert (
                catalog.structured_content["operations"][0]["name"]
                == "blender.assembly.create"
            )
            selected = await client.call_tool(
                "tyvrana_list_operations",
                {
                    "adapter_id": identifier,
                    "names": [
                        "blender.assembly.create",
                        "blender.assembly.configure",
                        "blender.object_set.place",
                        "blender.render.image",
                    ],
                    "schemas": "arguments",
                },
            )
            assert len(selected.structured_content["operations"]) == 4
            assert all(
                "arguments_schema" in op and "result_schema" not in op
                for op in selected.structured_content["operations"]
            )
            (tmp_path / "assembly-discovery.json").write_text(
                selected.model_dump_json()
            )
            created = await call("assembly.create", master_spec())
            assert created["component_count"] == 41 and created["valid"]
            inventory = await call(
                "assembly.inspect", {"name": "StructuralModule", "limit": 64}
            )
            assert len(inventory["components"]) == 41
            await call(
                "object_set.place",
                {
                    "placements": [
                        {
                            "name": "StructuralModule.segments.07",
                            "rule": {"kind": "fit", "dimensions": [None, 0.28, None]},
                        }
                    ]
                },
            )
            placement = await call(
                "object_set.inspect",
                {"names": ["StructuralModule.segments.07"], "fields": ["placement"]},
            )
            assert placement["objects"][0]["placement"]["valid"]
            revised = await call(
                "assembly.configure",
                {
                    "name": "StructuralModule",
                    "expected_revision": 1,
                    "members": [
                        {
                            "family": "segments",
                            "index": 7,
                            "shape": {"features": [{"id": "lug", "height": 0.11}]},
                        }
                    ],
                },
            )
            assert revised["changed_members"] == ["StructuralModule.segments.07"]
            unchanged = [
                r for r in inventory["components"] if r["family"] == "membersL"
            ]
            queries = [
                {
                    "kind": "distance",
                    "name": f"dimension_{i}",
                    "a": {
                        "kind": "geometry",
                        "object": row["name"],
                        "position": [0.5, 0, 0.5],
                        "project": False,
                    },
                    "b": {
                        "kind": "geometry",
                        "object": row["name"],
                        "position": [0.5, 1, 0.5],
                        "project": False,
                    },
                }
                for i, row in enumerate(inventory["components"][:20])
            ]
            assert (
                len(
                    (await call("measurement.inspect", {"queries": queries}))[
                        "measurements"
                    ]
                )
                == 20
            )
            clean = await call(
                "mesh.cleanup",
                {"names": [row["name"] for row in unchanged], "require_closed": True},
            )
            assert not any(row["changed"] for row in clean["objects"])
            rendered = await call(
                "render.image",
                {"width": 256, "height": 256, "inspection": {}, "wait_seconds": 5},
            )
            assert len(rendered["inspection_tiles"]) == 7
            assert (rendered["width"], rendered["height"]) == (768, 768)
            before = await call(
                "assembly.inspect", {"name": "StructuralModule", "limit": 64}
            )
            bound = await call(
                "project.bind",
                {
                    "resources": [
                        {"resource_kind": "object", "name": "StructuralModule"},
                        *[
                            {"resource_kind": "object", "name": r["name"]}
                            for r in before["components"]
                        ],
                    ]
                },
            )
            # Native document binding refreshes the advertised registration.
            query: dict[str, Any] = {"adapter_id": identifier}
            async with asyncio.timeout(15):
                while True:
                    snapshot = await client.call_tool("tyvrana_list_adapters", query)
                    assert not snapshot.is_error, snapshot.content
                    state = snapshot.structured_content
                    if any(
                        a.get("project_id") == bound["project_id"]
                        for a in state["adapters"]
                    ):
                        break
                    query.update(after_revision=state["revision"], wait_seconds=12)
            resource_query = {
                "project_id": bound["project_id"],
                "resources": [
                    {
                        "resource_kind": r["resource_kind"],
                        "resource_id": r["resource_id"],
                    }
                    for r in bound["resources"]
                ],
            }
            resource_before = await call("resource.inspect", resource_query)
            assert all(
                r["state"] == "present" and r["fingerprint"]
                for r in resource_before["resources"]
            )
            filepath = str(tmp_path / "assembly-mcp.blend")
            await call("file.save", {"filepath": filepath})
            await call("file.open", {"filepath": filepath, "discard_current": True})
            assert (
                await call(
                    "assembly.inspect", {"name": "StructuralModule", "limit": 64}
                )
                == before
            )
            assert await call("resource.inspect", resource_query) == resource_before
            installed = (
                Path(profile["BLENDER_USER_RESOURCES"])
                / "extensions/user_default/tyvrana_blender"
            )
            candidate = tmp_path / "reload-fixture.zip"
            with zipfile.ZipFile(candidate, "w") as archive:
                for source in installed.rglob("*"):
                    if source.is_file() and "__pycache__" not in source.parts:
                        data = source.read_bytes()
                        if source.name == "build_info.json":
                            data += b"\n"
                        archive.writestr(source.relative_to(installed).as_posix(), data)
            staged = stage(candidate, installed)
            initial = await call("extension.inspect", {})
            await call("extension.reload", {"expected_build": staged["build"]})
            current = await wait_adapter(client, identifier)
            identifier = current["instance_id"]
            activated = await call("extension.inspect", {})
            assert activated["host_pid"] == initial["host_pid"]
            assert activated["implementation_build"] == staged["build"]
            assert (
                await call(
                    "assembly.inspect", {"name": "StructuralModule", "limit": 64}
                )
                == before
            )
            assert await call("resource.inspect", resource_query) == resource_before
            (tmp_path / "assembly-reload.json").write_text(
                json.dumps(
                    {"initial": initial, "activated": activated, "members": before},
                    indent=2,
                )
            )
