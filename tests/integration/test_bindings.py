"""Saved identity survives typed rename, save-as and reopen; missing is explicit."""

import asyncio
from pathlib import Path
from typing import Any

from tyvrana_protocol import JsonValue

from .conftest import running_blender
from .test_e2e import core_client, discover, wait_for_project


async def test_native_resource_binding_lifecycle(
    profile: dict[str, str], tmp_path: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(
                name: str, arguments: dict[str, JsonValue]
            ) -> dict[str, Any]:
                response = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": adapter.instance_id,
                        "operation": "blender." + name,
                        "arguments": arguments,
                    },
                )
                assert not response.is_error, response.content
                result = response.structured_content["result"]
                assert isinstance(result, dict)
                return result

            await call("object.create_primitive", {"primitive": "cube", "name": "Link"})
            await call("material.create_principled", {"name": "Surface"})
            bound = await call(
                "project.bind",
                {
                    "resources": [
                        {"resource_kind": "object", "name": "Link"},
                        {"resource_kind": "material", "name": "Surface"},
                    ]
                },
            )
            identifier = bound["project_id"]
            assert isinstance(identifier, str) and len(identifier) == 32
            resources = bound["resources"]
            assert isinstance(resources, list)
            refs: list[JsonValue] = [
                {"resource_kind": r["resource_kind"], "resource_id": r["resource_id"]}
                for r in resources
                if isinstance(r, dict)
            ]
            query: dict[str, JsonValue] = {"project_id": identifier, "resources": refs}
            discovery: dict[str, JsonValue] = {"adapter_id": adapter.instance_id}
            async with asyncio.timeout(15):
                while True:
                    snapshot = await client.call_tool(
                        "tyvrana_list_adapters", discovery
                    )
                    if any(
                        a.get("project_id") == identifier
                        for a in snapshot.structured_content["adapters"]
                    ):
                        break
                    discovery.update(
                        after_revision=snapshot.structured_content["revision"],
                        wait_seconds=12,
                    )
            observed = await call("resource.inspect", query)
            assert all(r["state"] == "present" for r in observed["resources"])
            original = tmp_path / "original.blend"
            await call("file.save", {"filepath": str(original)})
            await wait_for_project(client, adapter.instance_id, str(original))
            await call(
                "object_set.configure",
                {"objects": [{"name": "Link", "rename": "Renamed link"}]},
            )
            observed = await call("resource.inspect", query)
            assert observed["resources"][0]["name"] == "Renamed link"
            destination = tmp_path / "renamed.blend"
            saved = await call("file.save", {"filepath": str(destination)})
            assert saved["project_id"] == identifier
            await wait_for_project(client, adapter.instance_id, str(destination))
            await call("object.delete", {"name": "Renamed link"})
            observed = await call("resource.inspect", query)
            assert observed["resources"][0]["state"] == "missing"
            await call(
                "file.open", {"filepath": str(destination), "discard_current": True}
            )
            await wait_for_project(client, adapter.instance_id, str(destination))
            observed = await call("resource.inspect", query)
            assert observed["resources"][0]["state"] == "present"
            assert observed["resources"][0]["name"] == "Renamed link"
            await call(
                "object_set.create",
                {
                    "objects": [
                        {
                            "kind": "copy",
                            "key": "copy",
                            "name": "Copied link",
                            "source": {"name": "Renamed link"},
                        }
                    ]
                },
            )
            observed = await call("resource.inspect", query)
            assert observed["resources"][0]["state"] == "ambiguous"
            repaired = await call(
                "project.bind",
                {
                    "resources": [{"resource_kind": "object", "name": "Copied link"}],
                    "renew_resource_ids": True,
                },
            )
            assert (
                repaired["resources"][0]["resource_id"] != resources[0]["resource_id"]
            )
            observed = await call("resource.inspect", query)
            assert observed["resources"][0]["state"] == "present"
            wrong = await client.call_tool(
                "tyvrana_execute_operation",
                {
                    "adapter_id": adapter.instance_id,
                    "operation": "blender.resource.inspect",
                    "arguments": {**query, "project_id": "wrong-file"},
                },
            )
            assert wrong.is_error
            forked = await call("project.bind", {"fork_project": True})
            assert forked["project_id"] != identifier


async def test_native_project_prerequisites_guard_mutation(
    profile: dict[str, str], tmp_path: Path
) -> None:
    """Evidence-backed progression and blocked native writes on one isolated host."""
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def execute(
                name: str, arguments: dict[str, Any], *, error: bool = False
            ) -> dict[str, Any]:
                response = await client.call_tool(
                    "tyvrana_execute_operation",
                    {
                        "adapter_id": "core"
                        if name.startswith("project.")
                        else adapter.instance_id,
                        "operation": name,
                        "arguments": arguments,
                    },
                )
                assert response.is_error == error, response.content
                if error:
                    return {}
                result = response.structured_content["result"]
                assert isinstance(result, dict)
                return result

            await execute(
                "blender.object.create_primitive", {"primitive": "cube", "name": "Base"}
            )
            identity = await execute(
                "blender.project.bind",
                {"resources": [{"resource_kind": "object", "name": "Base"}]},
            )
            discovery: dict[str, JsonValue] = {"adapter_id": adapter.instance_id}
            async with asyncio.timeout(15):
                while True:
                    snapshot = await client.call_tool(
                        "tyvrana_list_adapters", discovery
                    )
                    if any(
                        a.get("project_id") == identity["project_id"]
                        for a in snapshot.structured_content["adapters"]
                    ):
                        break
                    discovery.update(
                        after_revision=snapshot.structured_content["revision"],
                        wait_seconds=12,
                    )
            project = await execute(
                "project.create",
                {"title": "Verified assembly", "goal": "Build on verified geometry"},
            )
            key = project["id"]
            foundation = {
                "kind": "milestone",
                "id": "foundation",
                "label": "Foundation",
                "entity_ids": ["base"],
                "document_ids": ["doc"],
                "validation_ids": ["dimensions"],
                "acceptance": "Cube dimensions 2 by 2 by 2",
                "status": "in_progress",
            }
            downstream = {
                "kind": "milestone",
                "id": "finish",
                "label": "Finish",
                "entity_ids": ["cap"],
                "document_ids": ["doc"],
                "prerequisite_ids": ["foundation"],
            }
            check = {
                "kind": "validation",
                "id": "dimensions",
                "label": "Dimensions",
                "entity_ids": ["base"],
                "validation_type": "measurement",
            }
            await execute(
                "project.apply",
                {
                    "project_id": key,
                    "expected_revision": 1,
                    "project": {"stage": "foundation"},
                    "upsert": [
                        {"kind": "entity", "id": "base", "label": "Base"},
                        {"kind": "entity", "id": "cap", "label": "Cap"},
                        {
                            "kind": "document",
                            "id": "doc",
                            "label": "Native document",
                            "application": "blender",
                            "application_project_id": identity["project_id"],
                            "adapter_id": adapter.instance_id,
                        },
                        {
                            "kind": "binding",
                            "id": "base-resource",
                            "label": "Native base",
                            "entity_id": "base",
                            "document_id": "doc",
                            "resource_kind": "object",
                            "resource_id": identity["resources"][0]["resource_id"],
                        },
                        foundation,
                        downstream,
                        check,
                    ],
                },
            )
            await execute(
                "project.apply",
                {
                    "project_id": key,
                    "expected_revision": 2,
                    "upsert": [{**downstream, "status": "in_progress"}],
                    "project": {"stage": "finish"},
                },
                error=True,
            )
            observed = await execute("blender.scene.inspect", {"names": ["Base"]})
            assert observed["objects"][0]["name"] == "Base"
            assert observed["objects"][0]["dimensions"] == [2.0, 2.0, 2.0]
            verified = await execute(
                "project.verify",
                {
                    "project_id": key,
                    "expected_revision": 2,
                    "binding_ids": ["base-resource"],
                },
            )
            accepted = await execute(
                "project.apply",
                {
                    "project_id": key,
                    "expected_revision": verified["project"]["revision"],
                    "project": {"stage": "finish"},
                    "upsert": [
                        {
                            "kind": "evidence",
                            "id": "inspection",
                            "label": "Native geometry inspection",
                            "storage": "application",
                            "binding_id": "base-resource",
                            "summary": "Measured dimensions [2, 2, 2].",
                        },
                        {
                            **check,
                            "status": "passed",
                            "freshness": "current",
                            "evidence_ids": ["inspection"],
                            "summary": "Dimensions meet the fixture criteria.",
                        },
                        {**foundation, "status": "accepted"},
                        {**downstream, "status": "in_progress"},
                    ],
                },
            )
            await execute(
                "blender.object.create_primitive", {"primitive": "cube", "name": "Cap"}
            )
            state = await execute("project.continue", {"project_id": key})
            assert state["project"]["revision"] == accepted["project"]["revision"]
            assert (
                next(
                    v["record"]
                    for v in state["records"]
                    if v["record"]["id"] == "foundation"
                )["status"]
                == "accepted"
            )
            await execute(
                "project.apply",
                {
                    "project_id": key,
                    "expected_revision": state["project"]["revision"],
                    "upsert": [
                        {
                            "kind": "entity",
                            "id": "base",
                            "label": "Revised foundation specification",
                        }
                    ],
                },
            )
            await execute(
                "blender.object.create_primitive",
                {"primitive": "cube", "name": "Forbidden"},
                error=True,
            )
            # Read-only inspection remains available during prerequisite repair.
            await execute("blender.scene.inspect", {"names": ["Cap"]})
            inspected = await execute("blender.scene.inspect", {})
            assert "Forbidden" not in {o["name"] for o in inspected["objects"]}
            state = await execute("project.continue", {"project_id": key})
            assert any(
                b["record_id"] == "foundation" for b in state["stage_state"]["blockers"]
            )
