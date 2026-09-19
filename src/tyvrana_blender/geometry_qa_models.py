"""Bounded surface clearance and sampled deformation diagnostics."""

from typing import Annotated, Self

from pydantic import Field, FiniteFloat, model_validator

from .models import Model
from .reference_models import Name

Index = Annotated[int, Field(ge=0, le=499999)]
Time = Annotated[FiniteFloat, Field(ge=-1048574, le=1048574)]


class AdaptiveGeometryRange(Model):
    start: Time
    end: Time
    initial_samples: int = Field(default=3, ge=2, le=16)
    max_samples: int = Field(default=32, ge=3, le=64)
    minimum_step: FiniteFloat = Field(default=0.03125, gt=0, le=1048574)
    near_clearance: FiniteFloat = Field(default=0.01, gt=0, le=100)
    change_ratio: FiniteFloat = Field(default=0.25, gt=0, le=10)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.end <= self.start or self.max_samples < 2 * self.initial_samples - 1:
            raise ValueError("Ordered range requires budget for every initial midpoint")
        return self


class InstanceQuery(Model):
    object_name: Name
    self_intersection: bool = True
    obstacles: list[Name] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.object_name in self.obstacles or len(set(self.obstacles)) != len(
            self.obstacles
        ):
            raise ValueError("Obstacles must be distinct external surfaces")
        if not self.self_intersection and not self.obstacles:
            raise ValueError("Specify inter-element contact and/or obstacles")
        return self


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
    pairs: list[ClearancePair] = Field(default_factory=list, max_length=128)
    objects: list[GeometryQuery] = Field(default_factory=list, max_length=128)
    instances: list[InstanceQuery] = Field(default_factory=list, max_length=8)
    adaptive: AdaptiveGeometryRange | None = None
    frames: list[Time] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Explicit scene frames; null uses current frame/subframe. Restored "
        "on exit.",
    )
    tolerance: float = Field(default=0.000001, ge=0.00000001, le=0.1)
    collapsed_area_ratio: float = Field(default=0.01, gt=0, lt=1)
    collapsed_volume_ratio: float = Field(default=0.01, gt=0, lt=1)
    worst_limit: int = Field(default=4, ge=0, le=16)
    max_triangle_tests: int = Field(default=200000, ge=1, le=2000000)
    max_instance_vertices: int = Field(default=250000, ge=1, le=2000000)
    max_instances: int = Field(default=50000, ge=1, le=50000)
    containment: bool = Field(
        default=True,
        description="Test connected-component representative vertices against closed "
        "surfaces; excludes exempted pairs.",
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.adaptive and self.frames:
            raise ValueError("Choose explicit frames or adaptive range")
        count = (
            self.adaptive.max_samples
            if self.adaptive
            else len(self.frames)
            if self.frames
            else 1
        )
        queries = len(self.objects) + len(self.pairs) + len(self.instances)
        if not queries:
            raise ValueError("Specify objects, pairs and/or instances")
        if count * queries > 128:
            raise ValueError("At most 128 query/frame summaries; split the sample set")
        if count * queries * self.worst_limit > 256:
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
        if len({q.object_name for q in self.instances}) != len(self.instances):
            raise ValueError("Inspect each instance source once")
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
    jacobian_samples: int = 0
    jacobian_unavailable: int = 0
    negative_jacobians: int | None = None
    collapsed_jacobians: int | None = None
    minimum_jacobian: float | None = None
    worst_jacobian_vertices: list[int] = Field(default_factory=list)


class ElementContact(Model):
    left_id: str
    right_id: str
    left_prototype: str
    right_prototype: str
    left_layer: int | None
    right_layer: int | None
    surface: SurfaceContact


class InstanceSummary(Model):
    object_name: str
    instance_count: int
    prototype_count: int
    equivalent_vertices: int
    transformed_vertices: int
    tested_instances: int
    candidate_pairs: int
    contact_element_pairs: int
    contact_triangle_pairs: int
    contained_instances: int
    contained_ids: list[str]
    minimum_distance: float | None
    closest: ElementContact | None
    contacts: list[ElementContact]
    degenerate_tested_triangles: int
    path_points: int


class GeometryCoverage(Model):
    mode: str
    sample_count: int
    maximum_gap: float
    risky_intervals_remaining: int = 0
    stopped_by_budget: bool = False
    criteria: list[str] = Field(default_factory=list)


class GeometrySample(Model):
    frame: float
    pairs: list[ClearanceSummary]
    objects: list[GeometrySummary]
    instances: list[InstanceSummary] = Field(default_factory=list)


class GeometryInspectResult(Model):
    samples: list[GeometrySample]
    worst_frame: float | None
    minimum_distance: float | None
    triangle_tests: int
    evaluated_vertex_samples: int
    processing_seconds: float
    restored: bool
    limitations: list[str]
    coverage: GeometryCoverage
