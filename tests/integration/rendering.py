"""Canonical bounded job waiting for image-oriented integration assertions."""

from mcp import Client
from mcp.types import CallToolResult


async def complete_render(
    client: Client, identifier: str, response: CallToolResult
) -> CallToolResult:
    assert not response.is_error, response.content
    status = response.structured_content["result"]
    while status["state"] not in {"succeeded", "failed", "cancelled"}:
        response = await client.call_tool(
            "tyvrana_execute_operation",
            {
                "adapter_id": identifier,
                "operation": "blender.render.status",
                "arguments": {
                    "job_id": status["job_id"],
                    "after_revision": status["revision"],
                    "wait_seconds": 20,
                },
            },
        )
        assert not response.is_error, response.content
        status = response.structured_content["result"]
    if status["state"] == "succeeded" and not any(
        c.type == "image" for c in response.content
    ):
        response = await client.call_tool(
            "tyvrana_execute_operation",
            {
                "adapter_id": identifier,
                "operation": "blender.render.result",
                "arguments": {"job_id": status["job_id"]},
            },
        )
        assert not response.is_error, response.content
    return response
