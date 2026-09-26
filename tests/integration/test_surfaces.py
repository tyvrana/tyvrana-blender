"""Packaged native and real MCP qualification of independent complex surfaces."""

import json
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from ..surface_fixtures import fixtures
from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, wait_for_project


def test_native_surfaces(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/surface_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "surface-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "SURFACE_NATIVE_PASSED 6" in log


async def test_surfaces_mcp(profile: dict[str, str], tmp_path: Path) -> None:
    rows = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter

            async def call(
                name: str,
                arguments: dict[str, Any],
                error: str | tuple[str, ...] | None = None,
            ) -> dict[str, Any]:
                args = {
                    "adapter_id": "core"
                    if name.startswith("core:")
                    else adapter.instance_id,
                    "operation": name[5:]
                    if name.startswith("core:")
                    else "blender." + name,
                    "arguments": arguments,
                }
                start = time.perf_counter()
                response = await client.call_tool("tyvrana_execute_operation", args)
                rows.append(
                    {
                        "operation": name,
                        "request_bytes": len(
                            json.dumps(args, separators=(",", ":")).encode()
                        ),
                        "response_bytes": len(response.model_dump_json().encode()),
                        "seconds": time.perf_counter() - start,
                        "error": response.is_error,
                    }
                )
                (tmp_path / "surface-metrics.json").write_text(
                    json.dumps(rows, indent=2)
                )
                body = cast(dict[str, Any], response.structured_content)
                if body is None:
                    body = json.loads(cast(Any, response.content[0]).text)
                if error:
                    assert response.is_error and body.get("error", body).get(
                        "code"
                    ) in ((error,) if isinstance(error, str) else error), body
                    return body
                assert not response.is_error, body
                return cast(dict[str, Any], body["result"])

            specs = fixtures()
            result = await call("surface.create", {"surfaces": specs})
            assert len(result["surfaces"]) == 4
            for s in result["surfaces"]:
                assert (
                    s["valid"] and s["components"] == 1 and s["non_manifold_edges"] == 0
                )
                assert (
                    s["vertex_count"] - s["edge_count"] + s["face_count"]
                    == 2 - 2 * s["opening_count"]
                )
            spec = specs[1]
            name = spec["name"]
            identity = await call(
                "project.bind",
                {"resources": [{"resource_kind": "object", "name": name}]},
            )
            args: dict[str, Any] = {"adapter_id": adapter.instance_id}
            for _ in range(10):
                snapshot = await client.call_tool("tyvrana_list_adapters", args)
                body = snapshot.structured_content
                assert body is not None
                if (
                    body["adapters"]
                    and body["adapters"][0].get("project_id") == identity["project_id"]
                ):
                    break
                args.update(after_revision=body["revision"], wait_seconds=10)
            else:
                raise AssertionError("Binding identity did not register")
            project = await call(
                "core:project.create",
                {"title": "Revisable surface", "goal": "Keep evidence fresh"},
            )
            key = project["id"]
            check = {
                "kind": "validation",
                "id": "qa",
                "label": "Topology",
                "entity_ids": ["shell"],
                "validation_type": "geometry",
            }
            await call(
                "core:project.apply",
                {
                    "project_id": key,
                    "expected_revision": 1,
                    "project": {"stage": "construction"},
                    "upsert": [
                        {
                            "kind": "milestone",
                            "id": "construction",
                            "label": "Construction",
                            "entity_ids": ["shell"],
                            "document_ids": ["doc"],
                            "status": "in_progress",
                        },
                        {"kind": "entity", "id": "shell", "label": "Shell"},
                        {
                            "kind": "document",
                            "id": "doc",
                            "label": "Document",
                            "application": "blender",
                            "application_project_id": identity["project_id"],
                            "adapter_id": adapter.instance_id,
                        },
                        {
                            "kind": "binding",
                            "id": "native",
                            "label": "Surface",
                            "entity_id": "shell",
                            "document_id": "doc",
                            "resource_kind": "object",
                            "resource_id": identity["resources"][0]["resource_id"],
                        },
                        check,
                    ],
                },
            )
            verified = await call(
                "core:project.verify",
                {"project_id": key, "expected_revision": 2, "binding_ids": ["native"]},
            )
            await call(
                "core:project.apply",
                {
                    "project_id": key,
                    "expected_revision": verified["project"]["revision"],
                    "upsert": [
                        {
                            "kind": "evidence",
                            "id": "proof",
                            "label": "Inspection",
                            "storage": "application",
                            "binding_id": "native",
                            "summary": "Native manifold shell with through-opening",
                        },
                        {
                            **check,
                            "status": "passed",
                            "freshness": "current",
                            "evidence_ids": ["proof"],
                        },
                    ],
                },
            )
            updated = await call(
                "surface.configure",
                {
                    "surfaces": [
                        {
                            "name": name,
                            "expected_revision": 1,
                            "nodes": [
                                {"id": "e", "point": [-0.8, -2.9, 1.0]},
                                {"id": "f", "point": [0.8, -2.9, 1.0]},
                            ],
                            "curves": [{"id": "e-f", "through": [[0, -2.95, 1.05]]}],
                            "features": [{**spec["features"][1], "height": 0.14}],
                            "thickness": 0.04,
                        }
                    ]
                },
            )
            assert not updated["surfaces"][0]["topology_changed"]
            continuation = await call("core:project.continue", {"project_id": key})
            assert (
                next(v for v in continuation["records"] if v["record"]["id"] == "qa")[
                    "freshness"
                ]
                == "stale"
            )

            change = {
                "name": name,
                "expected_revision": 2,
                "openings": [{**spec["openings"][0], "radii": [0.22, 0.17]}],
            }
            await call(
                "surface.configure",
                {"surfaces": [change]},
                "surface_topology_change_required",
            )
            updated = await call(
                "surface.configure",
                {"surfaces": [{**change, "topology_policy": "rebuild"}]},
            )
            assert updated["surfaces"][0]["topology_changed"]
            inspected = await call(
                "surface.inspect",
                {"names": [s["name"] for s in specs], "region_limit": 64},
            )
            assert all(s["valid"] for s in inspected["surfaces"])
            code: str | tuple[str, ...]
            for fault in ("contour", "junction", "budget", "conflict"):
                bad = deepcopy(specs[0])
                bad["name"] = "Invalid"
                if fault == "contour":
                    bad["curves"][0]["end"] = "c"
                    code = "surface_junction_invalid"
                elif fault == "junction":
                    bad = deepcopy(specs[1])
                    bad["name"] = "Invalid"
                    bad["patches"].append({**bad["patches"][0], "id": "extra"})
                    code = "surface_junction_invalid"
                elif fault == "budget":
                    bad["patches"][0]["resolution"] = 100
                    code = "invalid_arguments"
                else:
                    bad["nodes"][2]["point"] = bad["nodes"][0]["point"]
                    code = ("surface_self_intersection", "surface_degenerate")
                await call("surface.create", {"surfaces": [bad]}, code)
            path = str(tmp_path / "surfaces.blend")
            await call("file.save", {"filepath": path})
            await wait_for_project(client, adapter.instance_id, path)
            await call("file.open", {"filepath": path, "discard_current": True})
            reopened = await call(
                "surface.inspect",
                {"names": [s["name"] for s in specs], "region_limit": 64},
            )
            assert reopened["surfaces"] == inspected["surfaces"]
