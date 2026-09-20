"""Bounded surface picking, Multires ownership and semantic sculpt contracts."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .mesh_models import Arguments, FaceArguments, MeshVector
from .models import Model, RenderViewProjection, Vector
from .modifier_models import Name, Number
from .region_models import FrameRegion

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


class StrokeSample(Arguments):
    location: MeshVector
    pressure: Unit = 1.0


class StrokeArguments(SculptInspectArguments):
    samples: Annotated[
        list[StrokeSample], Field(min_length=1, max_length=MAX_STROKE_SAMPLES)
    ]
    # Blender 5.2's minimum unprojected diameter is 0.001 Blender units.
    radius: Annotated[Number, Field(ge=0.0005, le=1_000_000)]
    strength: Unit
    symmetry: Symmetry = Field(default_factory=Symmetry)


class ImageStrokeSample(Arguments):
    u: Unit
    v: Unit
    pressure: Unit = 1.0


class ImageStrokePath(Arguments):
    view: RenderViewProjection
    samples: list[ImageStrokeSample] = Field(min_length=1, max_length=256)


class SculptStrokeArguments(StrokeArguments):
    samples: list[StrokeSample] = Field(default_factory=list, max_length=256)
    image_path: ImageStrokePath | None = Field(
        default=None,
        description="Alternative to object-space samples: use an inspection tile's "
        "view and normalized image coordinates (u rightward, v downward). All "
        "samples must hit the named object's current evaluated surface. Other "
        "objects do not occlude this target. Rerender after changing geometry.",
    )
    brush: Brush
    invert: bool = False

    @model_validator(mode="after")
    def flatten_needs_motion(self) -> Self:
        if bool(self.samples) == (self.image_path is not None):
            raise ValueError("Supply object-space samples or image_path, exclusively")
        points = (
            [(p.u, p.v) for p in self.image_path.samples]
            if self.image_path is not None
            else [tuple(p.location) for p in self.samples]
        )
        if self.brush == "flatten" and all(p == points[0] for p in points):
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


class MaskStatistics(Model):
    present: bool
    sample_count: int
    min: Unit
    max: Unit
    mean: Unit
    masked_fraction: Unit
    fully_masked_fraction: Unit
    unmasked_fraction: Unit


class SculptMaskSummary(Model):
    object_name: str
    multires_level: int
    effective_values_available: bool
    # Multires grid storage is not exposed by Blender RNA. These statistics
    # describe only the authored point attribute, never interpolated grid masks.
    statistics_scope: Literal["base_mesh"] = "base_mesh"
    base_mesh: MaskStatistics


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
    mask: SculptMaskSummary
    hidden_geometry: bool


class MaskInspectArguments(SculptInspectArguments):
    pass


class MaskClearArguments(SculptInspectArguments):
    pass


class MaskInvertArguments(SculptInspectArguments):
    pass


class MaskStrokeArguments(StrokeArguments):
    mode: Literal["add", "subtract"]


class MaskStrokeResult(Model):
    object_name: str
    mode: Literal["add", "subtract"]
    sample_count: int
    radius: Number
    strength: Unit
    symmetry: Symmetry
    snapped_locations: list[Vector]
    max_snap_distance: Number
    mask: SculptMaskSummary


type FaceSetId = Annotated[int, Field(ge=1, le=2_147_483_647)]
MAX_FACE_SETS = 256


class FaceSetsInspectArguments(SculptInspectArguments):
    pass


class FaceSetsAssignArguments(FaceArguments):
    object_name: Name
    face_set_id: FaceSetId | None = None


class FaceSetsInitializeArguments(SculptInspectArguments):
    mode: Literal["loose_parts", "materials", "uv_seams", "sharp_edges"]


class FaceSetSummary(Model):
    id: int
    face_count: int
    hidden_face_count: int
    area: Number
    bounds_min: Vector
    bounds_max: Vector


class FaceSetsSummary(Model):
    object_name: str
    authored: bool
    scope: Literal["base_mesh"] = "base_mesh"
    face_sets: list[FaceSetSummary]
    unassigned_face_count: int


class FaceSetsAssignResult(Model):
    assigned_id: FaceSetId
    assigned_face_count: int
    mesh_isolated: bool
    summary: FaceSetsSummary


class FilterAxes(Arguments):
    x: bool = True
    y: bool = True
    z: bool = True

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not (self.x or self.y or self.z):
            raise ValueError("Enable at least one filter axis")
        return self


type FilterType = Literal[
    "smooth", "surface_smooth", "relax", "inflate", "scale", "fair"
]


class SurfaceFairing(Arguments):
    """Local base-mesh fairing; no vertex indices or coordinate payloads."""

    regions: list[FrameRegion] = Field(default_factory=list, max_length=8)
    vertex_group: Name | None = None
    protect_vertex_group: Name | None = None
    max_distance: Positive
    # This ratio is compared in Python, not assigned to a Blender float32 RNA
    # property. Rounding 0.1 to float32 would exceed its own public upper bound.
    max_thinning: Annotated[float, Field(gt=0, le=0.1, allow_inf_nan=False)] = 0.1
    boundary_rings: Annotated[int, Field(ge=1, le=8)] = 3
    max_points: Annotated[int, Field(ge=1, le=262144)] = 262144
    max_work: Annotated[int, Field(ge=1, le=64000000)] = 16000000
    max_triangle_tests: Annotated[int, Field(ge=1, le=2000000)] = 200000

    @model_validator(mode="after")
    def local(self) -> Self:
        if self.regions and self.vertex_group:
            raise ValueError("Choose regions or a vertex group, not both")
        return self


class SurfaceFairingSummary(Model):
    affected_points: int
    pinned_points: int
    maximum_displacement: Number
    thickness_samples: int
    minimum_thickness_ratio: Number | None = Field(
        description=(
            "Conservative lower bound on corresponding local thickness ratios; "
            "uncertain anchors and the worst case are checked independently"
        )
    )
    triangle_tests: int
    mesh_isolated: bool


class SculptFilterArguments(SculptInspectArguments):
    type: FilterType
    strength: Annotated[Number, Field(ge=-1, le=1)]
    iterations: Annotated[int, Field(ge=1, le=100)] = 1
    axes: FilterAxes = Field(default_factory=FilterAxes)
    orientation: Literal["local", "world"] = "local"
    fairing: SurfaceFairing | None = None

    @model_validator(mode="after")
    def refinement_strength(self) -> Self:
        if (
            self.type in {"smooth", "surface_smooth", "relax", "fair"}
            and self.strength < 0
        ):
            raise ValueError("Refinement filters require nonnegative strength")
        if self.type in {"inflate", "scale"} and self.iterations != 1:
            raise ValueError("Inflate and scale support one iteration per operation")
        if self.type == "scale" and self.strength <= -1:
            raise ValueError("Scale strength must be greater than -1")
        if (self.type == "fair") != (self.fairing is not None):
            raise ValueError("Fair mode requires fairing settings exclusively")
        if self.type == "fair" and (
            self.iterations > 40
            or self.strength <= 0
            or not all(self.axes.model_dump().values())
            or self.orientation != "local"
        ):
            raise ValueError(
                "Fair mode needs all local axes, positive strength "
                "and at most 40 iterations"
            )
        return self


class SculptFilterResult(Model):
    object_name: str
    type: FilterType
    strength: Number
    iterations: int
    axes: FilterAxes
    orientation: Literal["local", "world"]
    multires_level: int
    bounds_before_min: Vector
    bounds_before_max: Vector
    bounds_after_min: Vector
    bounds_after_max: Vector
    changed: bool
    mask: SculptMaskSummary
    fairing: SurfaceFairingSummary | None = None
