"""Separate source/target retopology, bounded construction and spatial quality."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .mesh_models import (
    Arguments,
    BoundedIndices,
    ElementCounts,
    MeshElementSelector,
    MeshSummary,
    MeshVector,
)
from .models import Model, Vector
from .modifier_models import ModifierSummary, Name
from .numeric import Float32, binary32
from .remesh_models import Distribution

MAX_TARGET_ELEMENTS = 100_000
MAX_SELECTED_VERTICES = 4096
MAX_BOUNDARY_EDGES = 128
MAX_BOUNDARIES = 64
MAX_RELAX_WORK = 100_000
MAX_COORDINATE = 1_000_000
EXTREME_ASPECT_RATIO = 10.0
GUIDANCE = (
    "Retopology reads the current evaluated high-resolution source and edits a "
    "separate authored low-poly target. Pass source_object and target_object "
    "explicitly. Create an empty target, orient a seed patch, grow boundaries or "
    "bridge loops, then project/relax and inspect/render. Seed/extrude/bridge "
    "invalidate snapshot indices; reinspect before reuse. Project/relax preserve "
    "topology. Correspondence is spatial QA, not a source-vertex map. Quads alone "
    "do not establish good deformation flow; voxel remesh is not retopology. "
    "create_target takes source_object and optional name. seed_patch requires "
    "source-local center and tangent_direction, world width/height; use "
    "u_segments and v_segments (1..16, default 1) for grid resolution. "
    "project/relax require a vertex selector such as "
    "{mode: all, domain: vertex}. relax additionally takes iterations (1..50, "
    "default 5), factor (0..1, positive, default 0.5), preserve_boundary "
    "(default true). extrude_boundary requires an edge selector and target-local "
    "offset. bridge_loops requires loop_a/loop_b edge selectors for disjoint "
    "equal closed boundaries, optional segments (1..16) and native twist. "
    "Index selectors use {mode: indices, domain: edge, indices: [...]}. "
    "All edits accept world surface_offset (-1..1, default 0) and "
    "max_projection_distance (positive, at most 1000, default 1). "
    "Inspect face-center distances as well as vertex distances: coarse faces "
    "can cut through curvature even when their vertices lie on the source."
)


class RetopoCreateArguments(Arguments):
    source_object: Name
    name: Name = "Retopology"


class RetopoInspectArguments(Arguments):
    source_object: Name
    target_object: Name

    @model_validator(mode="after")
    def separate_objects(self) -> Self:
        if self.source_object == self.target_object:
            raise ValueError("Source and target must be separate objects")
        return self


class ProjectionSettings(RetopoInspectArguments):
    surface_offset: Annotated[Float32, Field(ge=-1, le=1)] = 0.0
    max_projection_distance: Annotated[Float32, Field(gt=0, le=1000)] = 1.0


class RetopoSeedArguments(ProjectionSettings):
    center: MeshVector
    tangent_direction: MeshVector
    width: Annotated[Float32, Field(ge=binary32(0.0001), le=1000)]
    height: Annotated[Float32, Field(ge=binary32(0.0001), le=1000)]
    u_segments: Annotated[int, Field(ge=1, le=16)] = 1
    v_segments: Annotated[int, Field(ge=1, le=16)] = 1

    @field_validator("tangent_direction")
    @classmethod
    def nonzero(cls, value: list[float]) -> list[float]:
        if math.hypot(*value) == 0:
            raise ValueError("Provide a nonzero tangent orientation")
        return value


class RetopoProjectArguments(ProjectionSettings):
    selector: MeshElementSelector
    mode: Literal["nearest_surface"] = "nearest_surface"

    @field_validator("selector")
    @classmethod
    def vertex_domain(cls, value: MeshElementSelector) -> MeshElementSelector:
        if value.domain != "vertex":
            raise ValueError("Projection and relaxation require a vertex selector")
        return value


class RetopoRelaxArguments(RetopoProjectArguments):
    iterations: Annotated[int, Field(ge=1, le=50)] = 5
    factor: Annotated[Float32, Field(gt=0, le=1)] = 0.5
    preserve_boundary: bool = True


class RetopoExtrudeArguments(ProjectionSettings):
    selector: MeshElementSelector
    offset: MeshVector

    @field_validator("selector")
    @classmethod
    def edge_domain(cls, value: MeshElementSelector) -> MeshElementSelector:
        if value.domain != "edge":
            raise ValueError("Boundary extrusion requires an edge selector")
        return value

    @field_validator("offset")
    @classmethod
    def nonzero(cls, value: list[float]) -> list[float]:
        if not any(value) or math.hypot(*value) > 1000:
            raise ValueError("Offset length must be positive and at most 1000")
        return value


class RetopoBridgeArguments(ProjectionSettings):
    loop_a: MeshElementSelector
    loop_b: MeshElementSelector
    twist: Annotated[int, Field(ge=-127, le=127)] = 0
    segments: Annotated[int, Field(ge=1, le=16)] = 1

    @field_validator("loop_a", "loop_b")
    @classmethod
    def edge_domain(cls, value: MeshElementSelector) -> MeshElementSelector:
        if value.domain != "edge":
            raise ValueError("Loop bridging requires two edge selectors")
        return value


type RetopoEditArguments = (
    RetopoSeedArguments
    | RetopoProjectArguments
    | RetopoExtrudeArguments
    | RetopoBridgeArguments
)


class DistanceSummary(Model):
    sample_count: int
    mean_distance: float
    rms_distance: float
    max_distance: float
    p95_distance: float


class Correspondence(Model):
    vertices: DistanceSummary
    face_centers: DistanceSummary
    face_normal_dot: Distribution


class BoundarySummary(Model):
    loop_index: int
    edge_count: int
    vertex_count: int
    kind: Literal["closed", "open", "branched"]
    perimeter: float
    bounds_min: Vector
    bounds_max: Vector
    edge_indices: BoundedIndices
    vertex_indices: BoundedIndices


class ValenceSummary(Model):
    valence_0: int
    valence_1: int
    valence_2: int
    valence_3: int
    valence_4: int
    valence_5: int
    valence_6_plus: int
    max_valence: int


class RetopoQuality(Model):
    mesh: MeshSummary
    quad_count: int
    triangle_count: int
    ngon_count: int
    boundary_edge_count: int
    boundary_loop_count: int
    boundary_chain_count: int
    branched_boundary_count: int
    non_manifold_edge_count: int
    inconsistent_winding_edge_count: int
    loose_vertex_count: int
    loose_edge_count: int
    degenerate_face_count: int
    valence: ValenceSummary
    edge_length: Distribution
    face_area: Distribution
    quad_aspect_ratio: Distribution
    extreme_aspect_ratio_count: int
    extreme_aspect_ratio_threshold: float = EXTREME_ASPECT_RATIO
    boundaries: list[BoundarySummary]
    boundaries_truncated: bool


class EvaluatedSurfaceSummary(Model):
    object_name: str
    vertex_count: int
    edge_count: int
    face_count: int
    triangle_count: int
    bounds_min_world: Vector | None
    bounds_max_world: Vector | None


class RetopoSummary(Model):
    source_object: str
    target_object: str
    guidance: str = GUIDANCE
    source: EvaluatedSurfaceSummary
    target: RetopoQuality
    target_modifiers: list[ModifierSummary]
    evaluated_target: EvaluatedSurfaceSummary
    authored_correspondence: Correspondence
    evaluated_correspondence: Correspondence
    blockers: list[str]


class RetopoCreateResult(Model):
    source_object: str
    target_object: str
    target_mesh: str
    matrix_world: list[list[float]]
    mesh: MeshSummary
    guidance: str = GUIDANCE


class PatchFrame(Model):
    center_source: Vector
    center_world: Vector
    normal_world: Vector
    tangent_u_world: Vector
    tangent_v_world: Vector


class RetopoEditResult(Model):
    source_object: str
    target_object: str
    topology_changed: bool
    indices_invalidated: bool
    mesh_isolated: bool
    selected_count: int
    moved_count: int
    created: ElementCounts
    created_faces: BoundedIndices
    projection: DistanceSummary
    before: RetopoQuality
    after: RetopoQuality
    correspondence_before: Correspondence
    correspondence_after: Correspondence
    patch: PatchFrame | None = None
