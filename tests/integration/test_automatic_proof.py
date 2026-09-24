"""Normal public project calls own the entire independent-proof lifecycle."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import running_blender
from .test_e2e import core_client, discover


@pytest.mark.parametrize(
    "case",
    ["success", "wrong_sha", "different_live", "missing_file", "invalid_artifact"],
)
async def test_automatic_bootstrap(
    profile: dict[str, str], tmp_path: Path, case: str
) -> None:
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter

            async def execute(
                operation: str,
                arguments: dict[str, Any],
                target: str = adapter.instance_id,
                error: str | None = None,
            ) -> dict[str, Any]:
                response = await client.call_tool(
                    "tyvrana_execute_operation",
                    dict(adapter_id=target, operation=operation, arguments=arguments),
                )
                (tmp_path / "last-response.json").write_text(response.model_dump_json())
                if error and response.is_error:
                    assert error in str(response.content), response.content
                    return {}
                assert not response.is_error, response.content
                result = response.structured_content["result"]
                while result.get("state") == "running" and "attestation_id" in result:
                    await asyncio.sleep(result["poll_after_seconds"])
                    response = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id="core",
                            operation="project.attest_status",
                            arguments=dict(
                                project_id=project["id"],
                                attestation_id=result["attestation_id"],
                            ),
                        ),
                    )
                    assert not response.is_error, response.content
                    result = response.structured_content["result"]
                if error:
                    assert result.get("state") == "failed" and error in json.dumps(
                        result
                    ), result
                    return {}
                assert result.get("state") != "failed", result
                if operation in {
                    "blender.project.bind",
                    "blender.file.save",
                    "blender.file.open",
                }:
                    async with asyncio.timeout(30):
                        while True:
                            listing = await client.call_tool(
                                "tyvrana_list_adapters",
                                dict(adapter_id=target, wait_seconds=2),
                            )
                            found = listing.structured_content["adapters"]
                            if (
                                found
                                and found[0].get("project_id") == result["project_id"]
                                and found[0].get("project_path") == result["filepath"]
                            ):
                                break
                            await asyncio.sleep(0.1)
                return dict(result)

            await execute(
                "blender.object.create_primitive",
                dict(primitive="cube", name="Fixture"),
            )
            binding = await execute("blender.project.bind", dict(resources=[]))
            artifact = tmp_path / "fixture.blend"
            await execute(
                "blender.file.save", dict(filepath=str(artifact), overwrite=True)
            )
            await execute(
                "blender.file.open", dict(filepath=str(artifact), discard_current=True)
            )
            await discover(client)
            async with asyncio.timeout(30):
                while True:
                    listing = await client.call_tool(
                        "tyvrana_list_adapters",
                        dict(adapter_id=adapter.instance_id, wait_seconds=2),
                    )
                    found = listing.structured_content["adapters"]
                    if (
                        found
                        and found[0].get("project_id") == binding["project_id"]
                        and found[0].get("project_path") == str(artifact)
                    ):
                        break
                    await asyncio.sleep(0.1)
            if case == "different_live":
                await execute(
                    "blender.object.set_transform",
                    dict(name="Fixture", location=[1, 0, 0]),
                )
            project = await execute(
                "project.create",
                dict(title="Proof fixture", goal="Independent saved content"),
                "core",
            )
            await execute(
                "project.apply",
                dict(
                    project_id=project["id"],
                    expected_revision=1,
                    upsert=[
                        dict(
                            kind="document",
                            id="doc",
                            label="Source",
                            application="blender",
                            application_project_id=binding["project_id"],
                            adapter_id=adapter.instance_id,
                        )
                    ],
                ),
                "core",
            )
            with artifact.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            locator = str(artifact)
            error = None
            if case == "wrong_sha":
                sha = "f" * 64
                error = "proof_file_mismatch"
            elif case == "different_live":
                error = "bootstrap_mismatch"
            elif case == "missing_file":
                locator = str(tmp_path / "missing.blend")
                error = "proof_artifact_missing"
            elif case == "invalid_artifact":
                invalid = tmp_path / "invalid.blend"
                invalid.write_bytes(b"Not an application document")
                locator = str(invalid)
                sha = hashlib.sha256(invalid.read_bytes()).hexdigest()
                error = "proof_start_failed"
            result = await execute(
                "project.attest",
                dict(
                    project_id=project["id"],
                    document_id="doc",
                    adapter_id=adapter.instance_id,
                    expected_revision=2,
                    mode="bootstrap",
                    trusted_artifact_sha256=sha,
                    trusted_artifact_locator=locator,
                    provenance="Known saved fixture",
                ),
                "core",
                error=error,
            )
            if error is None:
                assert result.get("digest") or result.get("result", {}).get("digest"), (
                    result
                )
            discovered = await discover(client)
            assert discovered and discovered.instance_id == adapter.instance_id
            assert not list(tmp_path.glob("tyvrana-proof-*"))  # noqa: ASYNC240 - Fixture cleanup check.
