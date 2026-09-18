"""Editable bounded section lofts for structural and organic components."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32, Vector32
from .organization_models import Memberships, Name, scene_root

type Radius = Annotated[Float32, Field(gt=0, le=10000)]


class LoftSection(Model):
    center: Vector32
    radii: list[Radius] = Field(
        min_length=4,
        max_length=4,
        description="Positive/negative X and positive/negative Z section radii.",
    )
    twist: Float32 = Field(
        default=0, ge=-100, le=100, description="Radians about tangent."
    )


class LoftSpec(Model):
    name: Name
    sections: list[LoftSection] = Field(min_length=2, max_length=64)
    x_reference: Vector32 = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    sides: int = Field(default=12, ge=4, le=64)
    subdivisions: int = Field(default=2, ge=1, le=16)
    interpolation: Literal["linear", "catmull_rom"] = "catmull_rom"
    caps: bool = True
    smooth: bool = True

    @model_validator(mode="after")
    def geometry(self) -> Self:
        if math.hypot(*self.x_reference) < 1e-8:
            raise ValueError("x_reference must be nonzero")
        if any(
            math.dist(a.center, b.center) < 1e-6
            for a, b in zip(self.sections, self.sections[1:], strict=False)
        ):
            raise ValueError("Consecutive section centers must be distinct")
        return self

    def vertex_count(self) -> int:
        return ((len(self.sections) - 1) * self.subdivisions + 1) * self.sides


class LoftBatch(Model):
    components: list[LoftSpec] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({c.name for c in self.components}) != len(self.components):
            raise ValueError("Component names must be unique")
        if sum(c.vertex_count() for c in self.components) > 131072:
            raise ValueError("Loft batch exceeds 131072 generated vertices")
        if sum(len(c.sections) for c in self.components) > 1024:
            raise ValueError("Loft batch exceeds 1024 authored sections")
        return self


class LoftCreateArguments(LoftBatch):
    collections: Memberships = Field(default_factory=scene_root)


class LoftConfigureArguments(LoftBatch):
    """Complete spec replacement; preserves object identity and mesh connectivity."""


class LoftInspectArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=64)
    include_sections: bool = False


class LoftSummary(Model):
    name: str
    component_id: str
    vertex_count: int
    face_count: int
    section_count: int
    bounds_min: Vector32
    bounds_max: Vector32
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=4)
    spec: LoftSpec | None = None


class LoftResult(Model):
    components: list[LoftSummary]
    processing_seconds: float
