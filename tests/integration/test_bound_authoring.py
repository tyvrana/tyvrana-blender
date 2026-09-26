"""Guarded asynchronous authoring composes with errors, persistence and artifacts."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from mcp import Client

from ..surface_fixtures import fixtures
from .conftest import running_blender
from .test_e2e import core_client, discover


async def test_bound_authoring(
    profile: dict[str, str], tmp_path: Path, input_image_fixtures: Path
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter
            await qualify(client, adapter.instance_id, tmp_path, input_image_fixtures)


async def qualify(client: Client, adapter: str, directory: Path, images: Path) -> None:
    ledger: list[dict[str, Any]] = []

    async def op(name: str, error: str | None = None, **args: Any) -> Any:
        response = await client.call_tool(
            "tyvrana_execute_operation",
            dict(
                adapter_id="core" if name.startswith("project.") else adapter,
                operation=name,
                arguments=args,
            ),
        )
        ledger.append(
            dict(
                operation=name,
                arguments=args,
                response=response.model_dump(mode="json"),
            )
        )
        (directory / "authoring-calls.json").write_text(json.dumps(ledger, indent=2))
        if error:
            assert response.is_error, response
            value = json.loads(
                next(c.text for c in response.content if c.type == "text")
            )
            assert value["code"] == error, value
            assert value["operation"] == name
            assert "Traceback" not in str(value)
            assert len(json.dumps(value)) < 16384
            return value
        assert not response.is_error, response.content
        value = response.structured_content["result"]
        if name in {
            "blender.file.new",
            "blender.file.save",
            "blender.file.open",
            "blender.project.bind",
        }:
            query: dict[str, Any] = dict(adapter_id=adapter, wait_seconds=10)
            async with asyncio.timeout(30):
                while True:
                    listed = await client.call_tool("tyvrana_list_adapters", query)
                    snapshot = listed.structured_content
                    if any(
                        a.get("project_id") == value["project_id"]
                        and a.get("project_path") == value["filepath"]
                        for a in snapshot["adapters"]
                    ):
                        break
                    query["after_revision"] = snapshot["revision"]
        if name in {"blender.form.create", "blender.form.configure"}:
            while value["state"] in {"queued", "running"}:
                await asyncio.sleep(value.get("poll_after_seconds", 0.1))
                value = await op("blender.form.status", job_id=value["job_id"])
            assert value["state"] == "completed", value
        return value

    async def attest() -> Any:
        value = await op("blender.document.attest")
        while value["state"] in {"queued", "running"}:
            await asyncio.sleep(value.get("poll_after_seconds", 0.1))
            value = await op("blender.document.attest_status", job_id=value["job_id"])
        assert value["state"] == "completed", value
        return value["result"]

    assert client.instructions and "semantic intent" in client.instructions
    await op("blender.file.new", discard_current=True)

    for query, families in [
        ("irregular organic geometry", ("form", "loft")),
        ("anatomical hard structure", ("form", "surface", "loft", "assembly")),
        ("refine irregular existing form", ("form", "loft", "surface")),
        ("construct sections profiles", ("loft",)),
        ("assembly related structural components", ("assembly",)),
    ]:
        found = await client.call_tool(
            "tyvrana_list_operations",
            dict(adapter_id=adapter, query=query, schemas="none", limit=5),
        )
        assert not found.is_error
        assert any(
            o["name"].split(".")[1] in families
            for o in found.structured_content["operations"]
        ), found

    for selected in (
        [
            "blender.form.create",
            "blender.form.configure",
            "blender.surface.create",
            "blender.surface.configure",
        ],
        [
            "blender.project.bind",
            "blender.file.save",
            "blender.file.open",
            "blender.render.image",
        ],
    ):
        contracts = await client.call_tool(
            "tyvrana_list_operations",
            dict(adapter_id=adapter, names=selected, schemas="arguments"),
        )
        assert not contracts.is_error

    form = dict(
        voxel_size=0.18,
        parts=[
            dict(id="body", kind="ellipsoid", center=[0, 0, 0], radii=[1, 2, 1]),
            dict(
                id="branch",
                kind="ellipsoid",
                center=[0.6, 1, 0.2],
                radii=[0.7, 1.4, 0.5],
            ),
        ],
    )
    await op("blender.form.create", forms=[dict(name="Unbound", **form)])
    await op("blender.surface.create", surfaces=[fixtures()[0]])
    invalid = dict(
        surfaces=[
            dict(
                name="PerforatedShell",
                expected_revision=1,
                patches=[dict(id="plate", thickness=[2, 2, 2, 2])],
            )
        ]
    )
    unbound = await op(
        "blender.surface.configure", error="surface_degenerate", **invalid
    )
    assert unbound["details"]["patch_id"] == "plate"
    native = await op(
        "blender.project.bind", resources=[dict(resource_kind="object", name="Unbound")]
    )
    project = await op(
        "project.create", title="Bound authoring", goal="Qualify atomic authoring"
    )
    key = project["id"]
    applied = await op(
        "project.apply",
        project_id=key,
        expected_revision=1,
        project=dict(stage="working"),
        upsert=[
            dict(kind="entity", id="shape", label="Shape"),
            dict(
                kind="document",
                id="doc",
                label="Document",
                application="blender",
                application_project_id=native["project_id"],
                adapter_id=adapter,
            ),
            dict(
                kind="binding",
                id="binding",
                label="Shape binding",
                entity_id="shape",
                document_id="doc",
                resource_kind="object",
                resource_id=native["resources"][0]["resource_id"],
            ),
            dict(
                kind="milestone",
                id="working",
                label="Work",
                status="in_progress",
                entity_ids=["shape"],
                document_ids=["doc"],
            ),
        ],
    )
    await op(
        "project.verify",
        project_id=key,
        expected_revision=applied["project"]["revision"],
        binding_ids=["binding"],
    )

    async def revision() -> int:
        return int(
            (await op("project.continue", project_id=key))["project"]["revision"]
        )

    before = await attest()
    rev = await revision()
    bound = await op("blender.surface.configure", error="surface_degenerate", **invalid)
    assert bound == unbound
    assert (await attest())["digest"] == before["digest"]
    assert await revision() == rev
    await op(
        "blender.surface.configure",
        surfaces=[
            dict(
                name="PerforatedShell",
                expected_revision=1,
                patches=[dict(id="plate", thickness=[0.06] * 4)],
            )
        ],
    )
    assert await revision() == rev + 1
    rev = await revision()
    await op("blender.form.create", forms=[dict(name="Bound", **form)])
    assert await revision() == rev + 1
    await op(
        "blender.form.configure",
        forms=[
            dict(
                name="Unbound",
                expected_revision=1,
                parts=[
                    dict(
                        id="body", kind="ellipsoid", center=[0, 0, 0], radii=[1.2, 2, 1]
                    )
                ],
            )
        ],
    )
    assert await revision() == rev + 2
    before = await attest()
    await op(
        "blender.form.configure",
        error="form_revision_conflict",
        forms=[dict(name="Unbound", expected_revision=1, voxel_size=0.2)],
    )
    await op(
        "blender.form.create",
        error="invalid_arguments",
        forms=[dict(name="Invalid", voxel_size=-1)],
    )
    assert (await attest())["digest"] == before["digest"]
    assert await revision() == rev + 2
    # Input transfer must outlive receipt admission until the nested import consumes it.
    image = next(images.glob("*.png"))  # noqa: ASYNC240 - Bounded local fixture inventory.
    imported = await client.call_tool(
        "tyvrana_import_artifact", {"files": [dict(path=str(image))]}
    )
    assert not imported.is_error, imported.content
    identifier = imported.structured_content["artifacts"][0]["artifact_id"]
    response = await client.call_tool(
        "tyvrana_execute_operation",
        dict(
            adapter_id=adapter,
            operation="blender.image.create_from_artifact",
            arguments={"images": [dict(name="Reference", artifact_id=identifier)]},
            artifact_ids=[identifier],
        ),
    )
    assert not response.is_error, response.content
    await client.call_tool("tyvrana_release_artifact", {"artifact_ids": [identifier]})
    path = str(directory / "authoring.blend")
    await op("blender.file.save", filepath=path)
    await op(
        "project.apply",
        project_id=key,
        expected_revision=await revision(),
        checkpoint=dict(id="saved", label="Saved"),
    )
    before = await attest()
    await op("blender.file.open", filepath=path, discard_current=True)
    await op(
        "blender.form.create",
        error="content_diverged",
        forms=[dict(name="Stale", **form)],
    )
    await op(
        "project.attest",
        project_id=key,
        document_id="doc",
        adapter_id=adapter,
        expected_revision=await revision(),
        mode="reattach",
    )
    assert (await attest())["digest"] == before["digest"]
    rev = await revision()
    await op(
        "blender.form.configure",
        forms=[dict(name="Bound", expected_revision=1, voxel_size=0.2)],
    )
    assert await revision() == rev + 1

    await op("blender.form.inspect", names=["Unbound", "Bound"])
    before = await attest()
    rev = await revision()
    assert (await op("blender.viewport.inspect"))["viewports"] == []
    await op("blender.viewport.capture", error="viewport_not_found", viewport_id="1:2")
    assert (await attest())["digest"] == before["digest"]
    assert await revision() == rev
    rendered = await op(
        "blender.render.image",
        width=96,
        height=96,
        inspection=dict(
            objects=["Bound"], views=[dict(name="Shape", orientation="front")]
        ),
    )
    while rendered["state"] in {"queued", "running"}:
        rendered = await op(
            "blender.render.status",
            job_id=rendered["job_id"],
            after_revision=rendered["revision"],
            wait_seconds=20,
        )
    assert rendered["state"] == "succeeded", rendered
    response = await client.call_tool(
        "tyvrana_execute_operation",
        dict(
            adapter_id=adapter,
            operation="blender.render.result",
            arguments=dict(job_id=rendered["job_id"]),
        ),
    )
    assert not response.is_error
    png = base64.b64decode(next(c.data for c in response.content if c.type == "image"))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) == rendered["byte_size"]
    assert hashlib.sha256(png).hexdigest() == rendered["sha256"]
    (directory / "bound-form.png").write_bytes(png)
    assert (await attest())["digest"] == before["digest"]
    assert await revision() == rev
