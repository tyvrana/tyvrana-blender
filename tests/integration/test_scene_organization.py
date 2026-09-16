"""Headless object-set workflows through a packaged adapter and real MCP client."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from .conftest import ROOT, running_blender
from .test_e2e import core_client, discover, operation


@pytest.mark.parametrize("batched", [False, True], ids=["individual", "object-set"])
async def test_repeated_parts_workflow(
    profile: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    batched: bool,
) -> None:
    rows: list[dict[str, Any]] = []
    phase = "startup"
    async with core_client(tmp_path) as (client, port):
        original = client.call_tool

        async def observed(
            name: str, arguments: dict[str, Any] | None = None, **kwargs: Any
        ) -> CallToolResult:
            started = time.perf_counter()
            result = await original(name, arguments, **kwargs)
            rows.append(
                dict(
                    id=len(rows) + 1,
                    phase=phase,
                    tool=name,
                    operation=(arguments or {}).get("operation"),
                    request_bytes=len(
                        json.dumps(arguments, separators=(",", ":")).encode()
                    ),
                    response_bytes=len(result.model_dump_json().encode()),
                    seconds=time.perf_counter() - started,
                    error=result.is_error,
                )
            )
            (tmp_path / "workflow-metrics.json").write_text(json.dumps(rows, indent=2))
            return result

        monkeypatch.setattr(client, "call_tool", observed)
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None

            async def call(name: str, /, **args: Any) -> Any:
                return await operation(
                    client, adapter.instance_id, "blender." + name, args
                )

            phase = "discovery"
            names = (
                ["blender.object_set.create"]
                if batched
                else ["blender.object.create_primitive", "blender.material.assign"]
            )
            schemas = await client.call_tool(
                "tyvrana_list_operations",
                dict(adapter_id=adapter.instance_id, names=names, include_schemas=True),
            )
            assert not schemas.is_error
            phase = "setup"
            await call(
                "material.create_principled", name="Metal", base_color=[0.3, 0.35, 0.4]
            )
            phase = "author"
            members = [
                dict(
                    key=f"part{i}",
                    name=f"Part {i:02}",
                    kind="primitive",
                    primitive="cube",
                    location=[i % 6 * 3, i // 6 * 3, 0],
                    scale=[0.5, 0.5, 0.5],
                    material="Metal",
                )
                for i in range(24)
            ]
            if batched:
                await call("object_set.create", objects=members)
            else:
                for m in members:
                    await call(
                        "object.create_primitive",
                        **{
                            k: v
                            for k, v in m.items()
                            if k not in {"key", "kind", "material"}
                        },
                    )
                    await call(
                        "material.assign", object_name=m["name"], material_name="Metal"
                    )
            phase = "verify"
            result = await call("scene.inspect", prefix="Part ")
            assert len(result["objects"]) == 24
            for i, obj in enumerate(result["objects"]):
                assert obj["name"] == f"Part {i:02}"
                assert obj["location"] == [i % 6 * 3, i // 6 * 3, 0]
                assert obj["dimensions"] == [1, 1, 1]
            print(
                "OBJECT_SET_WORKFLOW_METRICS",
                json.dumps(dict(batched=batched, rows=rows)),
            )


def test_native_organization(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/organization_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "organization.log").write_text(log)
    assert result.returncode == 0, log
    assert "ORGANIZATION_NATIVE_PASSED 31" in log


async def test_organized_assembly_room_and_chain_persist(
    profile: dict[str, str],
    tmp_path: Path,
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None and adapter.operation_count == 131

            async def call(name: str, /, **arguments: Any) -> Any:
                return await operation(
                    client, adapter.instance_id, "blender." + name, arguments
                )

            for names in (
                [
                    "blender.collection." + name
                    for name in ["create_hierarchy", "configure", "inspect", "remove"]
                ],
                [
                    "blender.object_set." + name
                    for name in ["create", "configure", "inspect", "remove"]
                ],
            ):
                result = await client.call_tool(
                    "tyvrana_list_operations",
                    dict(
                        adapter_id=adapter.instance_id,
                        names=names,
                        include_schemas=True,
                    ),
                )
                assert not result.is_error
                assert result.structured_content is not None
                assert len(result.structured_content["operations"]) == 4
                assert (
                    result.structured_content["catalog_sha256"]
                    == adapter.catalog_sha256
                )
            await call(
                "collection.create_hierarchy",
                collections=[
                    {"name": "Assembly"},
                    {"name": "Fasteners", "parents": ["Assembly"]},
                    {"name": "Room"},
                    {"name": "Structure", "parents": ["Room"]},
                    {"name": "Furniture", "parents": ["Room"]},
                    {"name": "Lighting", "parents": ["Room"]},
                    {"name": "Chain"},
                    {"name": "Review"},
                ],
            )
            await call("material.create_principled", name="Steel", metallic=0.7)

            def part(key: str, **values: Any) -> dict[str, Any]:
                return dict(
                    key=key, name=key, kind="primitive", primitive="cube", **values
                )

            assembly = [
                part("Base", scale=[2, 2, 0.2], material="Steel", role="base"),
                dict(
                    key="Pivot",
                    name="Pivot",
                    kind="empty",
                    parent={"key": "Base"},
                    role="joint",
                ),
                part(
                    "Arm",
                    parent={"key": "Pivot"},
                    location=[0, 0, 3],
                    scale=[0.2, 0.2, 3],
                    role="arm",
                ),
                part(
                    "Effector",
                    parent={"key": "Arm"},
                    location=[0, 0, 1],
                    role="effector",
                ),
                part(
                    "Bolt",
                    scale=[0.1, 0.1, 0.1],
                    material="Steel",
                    role="fastener",
                    tags=["metal"],
                    collections=["Fasteners", "Review"],
                ),
                *[
                    dict(
                        key=f"Bolt{i}",
                        name=f"Bolt{i}",
                        kind="copy",
                        source={"key": "Bolt"},
                        location=[i * 0.2, 0, 0],
                        scale=[0.1, 0.1, 0.1],
                        collections=["Fasteners"],
                        role="fastener",
                        tags=["metal"],
                    )
                    for i in range(1, 8)
                ],
            ]
            await call("object_set.create", collections=["Assembly"], objects=assembly)
            await call(
                "object_set.create",
                collections=["Structure"],
                objects=[
                    part("Floor", scale=[6, 6, 0.1]),
                    part("WallA", location=[0, 6, 3], scale=[6, 0.1, 3]),
                    part("WallB", location=[-6, 0, 3], scale=[0.1, 6, 3]),
                ],
            )
            await call(
                "object_set.create",
                collections=["Furniture"],
                objects=[
                    part("Table", location=[0, 0, 2], scale=[2, 1, 0.1], role="table"),
                    part(
                        "Leg",
                        location=[-1, -0.7, 1],
                        scale=[0.1, 0.1, 1],
                        material="Steel",
                        role="leg",
                    ),
                    dict(
                        key="Leg2",
                        name="Leg2",
                        kind="copy",
                        source={"key": "Leg"},
                        location=[1, -0.7, 1],
                        scale=[0.1, 0.1, 1],
                        role="leg",
                    ),
                    dict(
                        key="Leg3",
                        name="Leg3",
                        kind="copy",
                        source={"key": "Leg"},
                        location=[-1, 0.7, 1],
                        scale=[0.1, 0.1, 1],
                        role="leg",
                    ),
                    dict(
                        key="Leg4",
                        name="Leg4",
                        kind="copy",
                        source={"key": "Leg"},
                        location=[1, 0.7, 1],
                        scale=[0.1, 0.1, 1],
                        data="independent",
                        materials="independent",
                        role="leg",
                    ),
                ],
            )
            await call(
                "light.create",
                name="Lamp",
                type="point",
                energy=100,
                location=[0, 0, 5],
            )
            await call(
                "object_set.configure",
                objects=[
                    {"name": "Lamp", "collections": ["Lighting"], "role": "lighting"}
                ],
            )
            await call(
                "object_set.create",
                collections=["Lighting"],
                objects=[
                    dict(
                        key="Lamp2",
                        name="Lamp2",
                        kind="copy",
                        source={"name": "Lamp"},
                        data="independent",
                        location=[3, 0, 5],
                        role="lighting",
                    )
                ],
            )
            await call(
                "object_set.create",
                collections=["Chain"],
                objects=[
                    dict(key="Joint0", name="Joint0", kind="empty", role="joint"),
                    dict(
                        key="Joint1",
                        name="Joint1",
                        kind="empty",
                        parent={"key": "Joint0"},
                        location=[0, 0, 2],
                        role="joint",
                    ),
                    dict(
                        key="Joint2",
                        name="Joint2",
                        kind="empty",
                        parent={"key": "Joint1"},
                        location=[0, 0, 2],
                        role="joint",
                    ),
                    part(
                        "Link1",
                        parent={"key": "Joint0"},
                        location=[0, 0, 1],
                        scale=[0.1, 0.1, 1],
                    ),
                    part(
                        "Link2",
                        parent={"key": "Joint1"},
                        location=[0, 0, 1],
                        scale=[0.1, 0.1, 1],
                    ),
                ],
            )
            before = await call(
                "object_set.inspect", collection="Chain", fields=["transforms"]
            )
            await call("object.set_transform", name="Joint1", rotation=[0, 0.5, 0])
            after = await call(
                "object_set.inspect", collection="Chain", fields=["transforms"]
            )
            assert before != after
            bad = await client.call_tool(
                "tyvrana_execute_operation",
                dict(
                    adapter_id=adapter.instance_id,
                    operation="blender.object_set.configure",
                    arguments={"objects": [{"name": "Joint0", "parent": "Joint2"}]},
                ),
            )
            assert bad.is_error
            await call(
                "object_set.configure",
                objects=[{"name": "Joint0", "rename": "Chain Root"}],
            )
            await call(
                "collection.configure",
                collections=[
                    {
                        "name": "Fasteners",
                        "rename": "Hardware",
                        "hide_render": True,
                        "hide_select": True,
                    }
                ],
            )
            focused = await call(
                "object_set.inspect",
                collection="Hardware",
                role="fastener",
                tags=["metal"],
                fields=["metadata", "memberships"],
            )
            assert len(focused["objects"]) == 8
            assert len(json.dumps(focused).encode()) < 4000
            table = await call(
                "object_set.inspect", names=["Leg", "Leg2", "Leg4"], fields=["data"]
            )
            data = [o["data"] for o in table["objects"]]
            assert data[0]["name"] == data[1]["name"] != data[2]["name"]
            assert data[0]["materials"] == data[1]["materials"] != data[2]["materials"]
            hierarchy = await call("collection.inspect")
            saved = await call(
                "object_set.inspect",
                limit=64,
                fields=["hierarchy", "memberships", "metadata", "data", "transforms"],
            )
            filepath = str(tmp_path / "organized-project.blend")
            await call("file.save", filepath=filepath)
            await call("file.open", filepath=filepath, discard_current=True)
            assert await call("collection.inspect") == hierarchy
            assert (
                await call(
                    "object_set.inspect",
                    limit=64,
                    fields=[
                        "hierarchy",
                        "memberships",
                        "metadata",
                        "data",
                        "transforms",
                    ],
                )
                == saved
            )
            await call(
                "collection.remove", name="Review", mode="rehome", target="Hardware"
            )
            removed = await call("object_set.remove", names=["Lamp2"])
            assert removed["deleted"] == ["Lamp2"] and removed["error"] is None
