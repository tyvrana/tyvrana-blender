"""Explicit persistent identity establishment for selected native resources."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tyvrana_protocol import ResourceObservation

from .models import Model


class BindingTarget(Model):
    resource_kind: Literal["object", "material", "collection"]
    name: Annotated[str, Field(min_length=1, max_length=256)]


class ProjectBindArguments(Model):
    resources: list[BindingTarget] = Field(default_factory=list, max_length=64)
    fork_project: bool = False
    renew_resource_ids: bool = Field(
        default=False,
        description=(
            "Replace selected IDs to repair duplicated copies; prior bindings "
            "need explicit reconciliation."
        ),
    )

    @model_validator(mode="after")
    def unique(self) -> Self:
        keys = [(r.resource_kind, r.name) for r in self.resources]
        if len(keys) != len(set(keys)):
            raise ValueError("Selected resources must be unique")
        return self


class ProjectBindResult(Model):
    project_id: str
    filepath: str | None
    resources: list[ResourceObservation]
    save_required: bool
