"""Bounded surface picking, Multires ownership and semantic sculpt contracts."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .mesh_models import Arguments, MeshVector
from .models import Model, Vector
from .modifier_models import Name, Number

MAX_MULTIRES_LEVEL = 6
MAX_STROKE_SAMPLES = 256
SCALE_TOLERANCE = 1e-5

type Positive = Annotated[Number, Field(gt=0)]
type Unit = Annotated[Number, Field(ge=0, le=1)]
type Level = Annotated[int, Field(ge=0, le=MAX_MULTIRES_LEVEL)]
type Brush = Literal["draw", "smooth", "inflate", "clay", "crease", "flatten"]


class WorldRayArguments(Arguments):
    mode: Literal["world"]
    origin: MeshVector
    direction: MeshVector
    max_distance: Positive = 1_000_000.0

    @field_validator("direction")
    @classmethod
    def nonzero(cls, value: list[float]) -> list[float]:
        if math.hypot(*value) == 0:
            raise ValueError("Ray direction must be nonzero")
        return value


class CameraRayArguments(Arguments):
    mode: Literal["camera"]
    camera_name: Name | None = None
    u: Unit
    v: Unit
    width: Annotated[int, Field(ge=1, le=65536)] | None = None
    height: Annotated[int, Field(ge=1, le=65536)] | None = None
    max_distance: Positive = 1_000_000.0

    @model_validator(mode="after")
    def dimensions_together(self) -> Self:
        if (self.width is None) != (self.height is None):
            raise ValueError("Supply both width and height, or omit both")
        return self


type RaycastArguments = Annotated[
    WorldRayArguments | CameraRayArguments, Field(discriminator="mode")
]
RAYCAST: TypeAdapter[RaycastArguments] = TypeAdapter(RaycastArguments)


class RaycastResult(Model):
    hit: bool
    object_name: str | None = None
    object_type: str | None = None
    location_world: Vector | None = None
    normal_world: Vector | None = None
    location_object: Vector | None = None
    normal_object: Vector | None = None
    distance: Number | None = None
    evaluated_face_index: int | None = None


class MultiresInspectArguments(Arguments):
    object_name: Name


class MultiresCreateArguments(MultiresInspectArguments):
    name: Name | None = None


class MultiresSubdivideArguments(MultiresInspectArguments):
    modifier_name: Name | None = None
    levels: Annotated[int, Field(ge=1, le=MAX_MULTIRES_LEVEL)] = 1
    mode: Literal["catmull_clark", "simple", "linear"] = "catmull_clark"


class MultiresConfigureArguments(MultiresInspectArguments):
    modifier_name: Name | None = None
    viewport_level: Level | None = None
    sculpt_level: Level | None = None
    render_level: Level | None = None


class BaseTopology(Model):
    vertex_count: int
    edge_count: int
    face_count: int
    quad_face_count: int
    non_quad_face_count: int
    non_manifold_edge_count: int


class SculptDiagnostic(Model):
    code: str
    severity: Literal["warning", "blocker"]
    message: str


class MultiresSummary(Model):
    object_name: str
    present: bool
    modifier_name: str | None
    total_levels: int | None
    viewport_level: int | None
    sculpt_level: int | None
    render_level: int | None
    base_mesh: BaseTopology
    diagnostics: list[SculptDiagnostic]


class Symmetry(Arguments):
    x: bool = False
    y: bool = False
    z: bool = False


class SculptInspectArguments(Arguments):
    object_name: Name


class SculptSummary(Model):
    object_name: str
    object_mode: str
    object_scale: Vector
    scale_applied: bool
    multires: MultiresSummary
    effective_sculpt_level: int
    sculpt_vertex_count: int
    symmetry: Symmetry
    view3d_available: bool
    mask_present: bool
    hidden_geometry: bool


class StrokeSample(Arguments):
    location: MeshVector
    pressure: Unit = 1.0


class SculptStrokeArguments(SculptInspectArguments):
    brush: Brush
    samples: Annotated[
        list[StrokeSample], Field(min_length=1, max_length=MAX_STROKE_SAMPLES)
    ]
    # Blender 5.2's minimum unprojected diameter is 0.001 Blender units.
    radius: Annotated[Number, Field(ge=0.0005, le=1_000_000)]
    strength: Unit
    invert: bool = False
    symmetry: Symmetry = Field(default_factory=Symmetry)

    @model_validator(mode="after")
    def flatten_needs_motion(self) -> Self:
        if self.brush == "flatten" and all(
            sample.location == self.samples[0].location for sample in self.samples
        ):
            raise ValueError("Flatten requires a path with distinct surface locations")
        return self


class SculptStrokeResult(Model):
    object_name: str
    brush: Brush
    sample_count: int
    radius: Number
    strength: Unit
    invert: bool
    symmetry: Symmetry
    multires_level: int
    snapped_locations: list[Vector]
    max_snap_distance: Number
    bounds_before_min: Vector
    bounds_before_max: Vector
    bounds_after_min: Vector
    bounds_after_max: Vector
    changed: bool
