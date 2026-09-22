"""Canonical guarded-mutation schemas distributed independently of transport."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, RootModel
from tyvrana_protocol import JsonValue, ProtocolError, ResourceReference

from .models import Model


def schema(name: str) -> dict[str, Any]:
    return dict(json.loads(Path(__file__).with_name(name).read_text()))


class MutationArguments(Model):
    mutation_id: str
    operation: str
    arguments: JsonValue
    host_session_id: str
    document_session_id: str
    project_id: str
    format: str
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    resources: list[ResourceReference] = Field(default_factory=list, max_length=4096)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return schema("mutation-request.schema.json")


class MutationResult(RootModel[dict[str, JsonValue]]):
    pass


class MutationJobStatus(Model):
    job_id: str
    state: Literal["queued", "running", "completed", "failed", "cancelled"]
    revision: int = 0
    elapsed_seconds: float = 0
    poll_after_seconds: float = 0.5
    progress: dict[str, JsonValue] | None = None
    result: MutationResult | None = None
    error: ProtocolError | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return schema("mutation-job.schema.json")
