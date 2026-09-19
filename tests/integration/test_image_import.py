"""One canonical image ingestion operation across bounded synthetic inputs."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from mcp.types import TextContent
from tyvrana_protocol import ArtifactDescriptor

from .conftest import running_blender
from .test_e2e import core_client, discover, spooled_count


async def test_image_import_matrix(
    profile: dict[str, str],
    tmp_path: Path,
    input_image_fixtures: Path,
) -> None:
    rows = json.loads((input_image_fixtures / "manifest.json").read_text())
    measurements: list[dict[str, Any]] = []
    async with core_client(tmp_path) as (client, port):
        profile["TYVRANA_TEST_PORT"] = str(port)
        async with running_blender(profile, tmp_path, ui=False):
            adapter = await discover(client)
            assert adapter is not None
            for row in rows:
                request = {
                    "path": str(input_image_fixtures / row["name"]),
                    "media_type": row["media_type"],
                }
                started = time.perf_counter()
                imported = await client.call_tool("tyvrana_import_artifact", request)
                import_seconds = time.perf_counter() - started
                assert not imported.is_error, imported
                descriptor = ArtifactDescriptor.model_validate(
                    imported.structured_content
                )
                assert (
                    descriptor.sha256 == row["sha256"]
                    and descriptor.byte_size == row["bytes"]
                )
                args = dict(
                    adapter_id=adapter.instance_id,
                    operation="blender.image.create_from_artifact",
                    arguments=dict(artifact_id=descriptor.artifact_id, name="Fixture"),
                    artifact_ids=[descriptor.artifact_id],
                )
                started = time.perf_counter()
                result = await client.call_tool("tyvrana_execute_operation", args)
                failure = None
                if result.is_error:
                    content = result.content[0]
                    assert isinstance(content, TextContent)
                    failure = json.loads(content.text)
                measure = dict(
                    fixture=row["name"],
                    artifact_bytes=descriptor.byte_size,
                    import_seconds=import_seconds,
                    operation_seconds=time.perf_counter() - started,
                    request_bytes=len(json.dumps(args).encode()),
                    response_bytes=len(result.model_dump_json().encode()),
                    error=result.is_error,
                    result=failure if result.is_error else result.structured_content,
                    import_request_bytes=len(json.dumps(request).encode()),
                    import_response_bytes=len(imported.model_dump_json().encode()),
                )
                measurements.append(measure)
                print("IMAGE_IMPORT_CASE", json.dumps(measure), flush=True)
                if row["error"]:
                    assert result.is_error, result
                    assert failure is not None
                    assert failure["code"] == row["error"], result
                    state = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id=adapter.instance_id,
                            operation="blender.image.inspect",
                            arguments={"names": ["Fixture"]},
                        ),
                    )
                    assert state.structured_content["result"]["images"] == []
                else:
                    assert not result.is_error, result
                    image = result.structured_content["result"]
                    assert (image["width"], image["height"]) == (
                        row["width"],
                        row["height"],
                    )
                    assert image["packed"] and image["source"] == "file"
                    if row["name"] == "large-rgb.jpg":
                        for name, arguments in [
                            (
                                "reference.create",
                                {"references": [{"name": "Plate", "image": "Fixture"}]},
                            ),
                            ("reference.inspect", {"names": ["Plate"]}),
                            ("reference.remove", {"names": ["Plate"]}),
                        ]:
                            response = await client.call_tool(
                                "tyvrana_execute_operation",
                                dict(
                                    adapter_id=adapter.instance_id,
                                    operation="blender." + name,
                                    arguments=arguments,
                                ),
                            )
                            assert not response.is_error, response
                            if name != "reference.remove":
                                assert response.structured_content["result"][
                                    "references"
                                ][0]["image_size"] == [4672, 3314]
                    deleted = await client.call_tool(
                        "tyvrana_execute_operation",
                        dict(
                            adapter_id=adapter.instance_id,
                            operation="blender.image.remove",
                            arguments={"name": "Fixture"},
                        ),
                    )
                    assert not deleted.is_error, deleted
                async with asyncio.timeout(5):
                    while spooled_count(tmp_path):  # noqa: ASYNC110 - Observe owned worker cleanup.
                        await asyncio.sleep(0.02)
                released = await client.call_tool(
                    "tyvrana_release_artifact", {"artifact_id": descriptor.artifact_id}
                )
                assert not released.is_error
    (tmp_path / "image-import-metrics.json").write_text(
        json.dumps(measurements, indent=2) + "\n"
    )
