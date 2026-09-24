"""Canonical restore schemas, packaged without replacing runtime dependencies."""

from typing import Any, Literal

from pydantic import field_validator

from .models import Model
from .mutation_models import MutationJobStatus, schema


class RestoreArguments(Model):
    mutation_id: str
    operation: str
    locator: str
    discard_current: Literal[True]
    current: dict[str, str | None]
    target: dict[str, str]

    @field_validator("discard_current", mode="before")
    @classmethod
    def explicit_discard(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Restore requires explicit discard_current: true")
        return value

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return schema("restore-request.schema.json")


class RestoreJobStatus(MutationJobStatus):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return schema("restore-job.schema.json")
