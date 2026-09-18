"""Bounded surface clearance and sampled deformation diagnostics."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from .models import Model
from .reference_models import Name

Index = Annotated[int, Field(ge=0, le=499999)]


class ContactExemption(Model):
    left_faces: list[Index] = Field(min_length=1, max_length=256)
    right_faces: list[Index] = Field(min_length=1, max_length=256)


class ClearancePair(Model):
    left: Name
    right: Name
    exemptions: list[ContactExemption] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.left == self.right:
            raise ValueError("Use objects.self_intersection for a self query")
        return self


class GeometryQuery(Model):
    object_name: Name
    self_intersection: bool = False
    reference: Name | None = Field(
        default=None,
        description="Same ordered topology; compares normals in each object's local "
        "space.",
    )


class GeometryInspectArguments(Model):
    pairs: list[ClearancePair] = Field(default_factory=list, max_length=16)
    objects: list[GeometryQuery] = Field(default_factory=list, max_length=16)
    frames: list[Annotated[int, Field(ge=-1048574, le=1048574)]] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Explicit scene frames; null uses current frame/subframe. Restored "
        "on exit.",
    )
    tolerance: float = Field(default=0.000001, ge=0.00000001, le=0.1)
    collapsed_area_ratio: float = Field(default=0.01, gt=0, lt=1)
    worst_limit: int = Field(default=4, ge=0, le=16)
    max_triangle_tests: int = Field(default=200000, ge=1, le=2000000)
    containment: bool = Field(
        default=True,
        description="Test connected-component representative vertices against closed "
        "surfaces; excludes exempted pairs.",
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        count = len(self.frames) if self.frames else 1
        if not self.objects and not self.pairs:
            raise ValueError("Specify objects and/or pairs")
        if count * (len(self.objects) + len(self.pairs)) > 128:
            raise ValueError("At most 128 query/frame summaries; split the sample set")
        if count * (len(self.objects) + len(self.pairs)) * self.worst_limit > 256:
            raise ValueError(
                "At most 256 detail findings; reduce worst_limit or frames"
            )
        if len({o.object_name for o in self.objects}) != len(self.objects):
            raise ValueError("Inspect each object once")
        if len({tuple(sorted((p.left, p.right))) for p in self.pairs}) != len(
            self.pairs
        ):
            raise ValueError("Inspect each unordered pair once")
        if self.frames and len(set(self.frames)) != len(self.frames):
            raise ValueError("Frames must be unique")
        return self


class SurfaceContact(Model):
    left_face: int
    right_face: int
    distance: float
    left_point: list[float]
    right_point: list[float]


class ClearanceSummary(Model):
    left: str
    right: str
    minimum_distance: float | None
    closest: SurfaceContact | None
    contact_triangle_pairs: int
    exempt_triangle_pairs: int
    left_representatives_inside_right: int | None
    right_representatives_inside_left: int | None
    contacts: list[SurfaceContact]


class GeometrySummary(Model):
    object_name: str
    triangle_count: int
    topology_sha256: str
    degenerate_triangles: int
    degenerate_faces: list[int]
    closed_consistent: bool
    signed_volume: float | None
    self_contact_triangle_pairs: int | None
    self_contacts: list[SurfaceContact]
    normal_reversed_triangles: int | None
    collapsed_triangles: int | None
    signed_volume_reversed: bool | None


class GeometrySample(Model):
    frame: float
    pairs: list[ClearanceSummary]
    objects: list[GeometrySummary]


class GeometryInspectResult(Model):
    samples: list[GeometrySample]
    worst_frame: float | None
    minimum_distance: float | None
    triangle_tests: int
    evaluated_vertex_samples: int
    processing_seconds: float
    restored: bool
    limitations: list[str]
