"""Owned native surface-motion bindings with explicit current-state capture."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .mesh_models import Arguments
from .models import Model
from .modifier_models import Name
from .numeric import Float32


class SurfaceBindArguments(Arguments):
    driver: Name
    driven: list[Name] = Field(min_length=1, max_length=8)
    bind_state: Literal["current"]
    falloff: Float32 = Field(default=4, ge=2, le=16)
    strength: Float32 = Field(default=1, ge=0, le=1)
    vertex_group: str = Field(default="", max_length=63)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.driven)) != len(self.driven) or self.driver in self.driven:
            raise ValueError("Driver and driven objects must be distinct")
        return self


class SurfaceInspectArguments(Arguments):
    objects: list[Name] = Field(min_length=1, max_length=16)


class SurfaceBinding(Model):
    driven: str
    driver: str | None
    modifier: str | None
    bound: bool
    valid: bool
    reason: str | None
    modifier_index: int | None
    falloff: float | None
    strength: float | None
    vertex_group: str | None
    driver_topology: str | None
    driven_topology: str | None


class SurfaceBindings(Model):
    bindings: list[SurfaceBinding]
    processing_seconds: float
