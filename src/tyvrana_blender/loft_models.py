"""Editable bounded section lofts for structural and organic components."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32, Vector32, binary32
from .organization_models import Memberships, Name, scene_root

type Radius = Annotated[Float32, Field(gt=0, le=10000)]


class LoftSection(Model):
    id: Name | None = None
    center: Vector32
    radii: list[Radius] = Field(
        min_length=4,
        max_length=4,
        description="Positive/negative X and positive/negative Z section radii.",
    )
    twist: Float32 = Field(
        default=0, ge=-100, le=100, description="Radians about tangent."
    )


class LoftFeature(Model):
    """Compact radial process, ridge or groove in the transported section frame."""

    id: Name
    position: Float32 = Field(ge=0, le=1)
    angle: Float32 = Field(ge=-binary32(math.pi), le=binary32(math.pi))
    axial_width: Float32 = Field(gt=0, le=1)
    angular_width: Float32 = Field(gt=0, le=binary32(math.pi))
    height: Float32 = Field(ge=-10000, le=10000)


class LoftEnds(Model):
    """Positive depth recesses a capped end; negative depth makes it convex."""

    start_depth: Float32 = 0
    end_depth: Float32 = 0
    rings: int = Field(default=4, ge=2, le=12)


class LoftSpec(Model):
    name: Name
    sections: list[LoftSection] = Field(min_length=2, max_length=64)
    x_reference: Vector32 = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    sides: int = Field(default=12, ge=4, le=64)
    subdivisions: int = Field(default=2, ge=1, le=16)
    interpolation: Literal["linear", "catmull_rom"] = "catmull_rom"
    caps: bool = True
    smooth: bool = True
    features: list[LoftFeature] = Field(default_factory=list, max_length=16)
    ends: LoftEnds | None = None

    @model_validator(mode="after")
    def geometry(self) -> Self:
        ids = [s.id for s in self.sections if s.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Section IDs must be unique")
        if len({f.id for f in self.features}) != len(self.features):
            raise ValueError("Feature IDs must be unique")
        region_ids = ids + [f.id for f in self.features]
        if len(region_ids) != len(set(region_ids)) or set(region_ids) & {
            "start_cap",
            "end_cap",
        }:
            raise ValueError(
                "Section/feature IDs must be distinct and cannot use start_cap/end_cap"
            )
        if self.ends is not None and not self.caps:
            raise ValueError("End shaping requires caps")
        if math.hypot(*self.x_reference) < 1e-8:
            raise ValueError("x_reference must be nonzero")
        if any(
            math.dist(a.center, b.center) < 1e-6
            for a, b in zip(self.sections, self.sections[1:], strict=False)
        ):
            raise ValueError("Consecutive section centers must be distinct")
        return self

    def vertex_count(self) -> int:
        rings = (len(self.sections) - 1) * self.subdivisions + 1
        if self.ends:
            rings += 2 * self.ends.rings
        return rings * self.sides


class LoftSectionEdit(Model):
    id: Name
    center: Vector32 | None = None
    radii: list[Radius] | None = Field(default=None, min_length=4, max_length=4)
    twist: Float32 | None = Field(default=None, ge=-100, le=100)

    @model_validator(mode="after")
    def change(self) -> Self:
        if all(getattr(self, k) is None for k in ("center", "radii", "twist")):
            raise ValueError("Supply at least one non-null section change")
        return self


class LoftRevision(Model):
    name: Name
    sections: list[LoftSection] | None = Field(
        default=None, min_length=2, max_length=64
    )
    section_edits: list[LoftSectionEdit] = Field(default_factory=list, max_length=64)
    x_reference: Vector32 | None = None
    sides: int | None = Field(default=None, ge=4, le=64)
    subdivisions: int | None = Field(default=None, ge=1, le=16)
    interpolation: Literal["linear", "catmull_rom"] | None = None
    caps: bool | None = None
    smooth: bool | None = None
    features: list[LoftFeature] | None = Field(default=None, max_length=16)
    ends: LoftEnds | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def change(self) -> Self:
        if self.sections is not None and self.section_edits:
            raise ValueError("Use sections or section_edits, not both")
        if len({s.id for s in self.section_edits}) != len(self.section_edits):
            raise ValueError("Section edit IDs must be unique")
        if not self.model_fields_set - {"name", "expected_revision"}:
            raise ValueError("Supply a loft change")
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set - {"ends", "expected_revision"}
        ):
            raise ValueError("Loft changes must be non-null except ends")
        return self


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


class LoftConfigureArguments(Model):
    """Selected semantic changes preserve object identity and mesh connectivity."""

    components: list[LoftRevision] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({s.name for s in self.components}) != len(self.components):
            raise ValueError("Component names must be unique")
        return self


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
    revision: int = 1
    region_ids: list[str] = Field(default_factory=list)


class LoftResult(Model):
    components: list[LoftSummary]
    processing_seconds: float
