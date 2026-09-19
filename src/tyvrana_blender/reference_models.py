"""Bounded reference placement, persistent points and geometric comparisons."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .models import InspectArguments, Model, PageInfo
from .numeric import Float32

type Name = Annotated[str, Field(min_length=1, max_length=63, pattern=r"\S")]
type Coordinate = Annotated[Float32, Field(ge=-1e12, le=1e12)]
type Point = Annotated[list[Coordinate], Field(min_length=3, max_length=3)]
type Pixel = Annotated[list[Coordinate], Field(min_length=2, max_length=2)]
type Size = Annotated[Float32, Field(ge=0.0001, le=1000)]
type Distance = Annotated[Float32, Field(gt=0, le=1e12)]
type Unit = Literal["blender", "meters"]


class ReferenceProperties(Model):
    size: Size = 1.0
    opacity: Float32 = Field(default=0.5, ge=0, le=1)
    depth: Literal["default", "front", "back"] = "front"
    side: Literal["both", "front", "back"] = "both"
    orthographic: bool = True
    perspective: bool = False
    axis_aligned_only: bool = False
    hidden: bool = False


class ReferenceSpec(ReferenceProperties):
    name: Name
    image: Name
    collection: Name = "References"
    category: str = Field(default="", max_length=64)
    source_label: str = Field(default="", max_length=512)
    location: Point = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Point = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class ReferenceCreateArguments(Model):
    references: list[ReferenceSpec] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({r.name for r in self.references}) != len(self.references):
            raise ValueError("Reference names must be unique")
        return self


class ReferencePatch(ReferenceProperties):
    name: Name
    category: str = Field(default="", max_length=64)
    source_label: str = Field(default="", max_length=512)


class ReferenceConfigureArguments(Model):
    references: list[ReferencePatch] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({r.name for r in self.references}) != len(self.references):
            raise ValueError("Reference names must be unique")
        if any(len(r.model_fields_set) == 1 for r in self.references):
            raise ValueError("Each reference needs at least one changed property")
        return self


class ReferenceSummary(ReferenceProperties):
    name: str
    image: str | None
    image_size: list[int]
    local_dimensions: Point
    world_corners: list[Point]
    location: Point
    rotation: Point
    scale: Point
    category: str
    source_label: str
    collections: list[str]
    collection_count: int
    collections_truncated: bool
    packed: bool
    valid: bool
    renderable: Literal[False] = False


class ReferenceResult(Model):
    references: list[ReferenceSummary]


class ReferenceInspectArguments(InspectArguments):
    image: Name | None = None
    collection: Name | None = None
    category: str | None = Field(default=None, max_length=64)


class ReferenceInspectResult(ReferenceResult):
    page: PageInfo


class NamedRemoveArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=32)

    @field_validator("names")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Names must be unique")
        return value


class NamedRemoveResult(Model):
    removed: list[str]


class LandmarkSpec(Model):
    name: Name
    point: Point
    object: Name | None = Field(
        default=None,
        description="Attached object-local point; omit/null for a fixed world point. "
        "Follows object transforms, not mesh deformation or individual vertices.",
    )
    label: str = Field(default="", max_length=256)
    category: str = Field(default="", max_length=64)


class LandmarkSetArguments(Model):
    landmarks: list[LandmarkSpec] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({r.name for r in self.landmarks}) != len(self.landmarks):
            raise ValueError("Landmark names must be unique")
        return self


class LandmarkSummary(Model):
    name: str
    point: Point
    world_point: Point
    object: str | None
    label: str
    category: str
    attachment: Literal["world", "object"]
    valid: bool
    derived: bool = False
    stale: bool = False
    residual: float | None = None
    observation_ids: list[str] = Field(default_factory=list)


class LandmarkResult(Model):
    landmarks: list[LandmarkSummary]


class LandmarkInspectArguments(InspectArguments):
    object: Name | None = None
    category: str | None = Field(default=None, max_length=64)
    attachment: Literal["world", "object"] | None = None
    detail: Literal["points", "summary", "provenance"] = "points"
    derived_only: bool = False


class LandmarkInspectResult(LandmarkResult):
    page: PageInfo
    construction: "ConstructionReport | None" = None
    provenance: list["DerivationProvenance"] = Field(default_factory=list)
    provenance_truncated: bool = False


class WorldPoint(Model):
    kind: Literal["world"]
    point: Point


class ObjectPoint(Model):
    kind: Literal["object"]
    object: Name
    point: Point


class LandmarkPoint(Model):
    kind: Literal["landmark"]
    name: Name


class BonePoint(Model):
    kind: Literal["bone"]
    object: Name
    bone: Name
    endpoint: Literal["head", "tail"] = "head"
    state: Literal["rest", "evaluated"] = "evaluated"


class ReferencePoint(Model):
    kind: Literal["reference_pixel"]
    reference: Name
    pixel: Pixel = Field(
        description="Continuous image-edge coordinates, origin bottom-left; "
        "[0,0]..[width,height]. Pixel centers are at half-integers."
    )


class GeometryPoint(Model):
    kind: Literal["geometry"]
    object: Name
    region: Name | None = None
    position: Annotated[
        list[Annotated[Float32, Field(ge=0, le=1)]], Field(min_length=3, max_length=3)
    ] = Field(default_factory=lambda: [0.5, 0.5, 0.5])
    project: bool = Field(
        default=True,
        description="Project normalized local region-bounds position onto authored "
        "triangles. False returns the bounding-region point; not an inferred "
        "physical joint center.",
    )
    offset: Float32 = Field(
        default=0,
        ge=-1000,
        le=1000,
        description="Offset along projected face normal, in object-local units; "
        "requires project=true.",
    )

    @model_validator(mode="after")
    def offset_requires_surface(self) -> Self:
        if self.offset and not self.project:
            raise ValueError("Geometry point offset requires surface projection")
        return self


type PointSource = Annotated[
    WorldPoint
    | ObjectPoint
    | LandmarkPoint
    | ReferencePoint
    | BonePoint
    | GeometryPoint,
    Field(discriminator="kind"),
]


class Comparison(Model):
    target: Float32
    tolerance: Float32 = Field(ge=0, le=1e12)


class DistanceQuery(Model):
    kind: Literal["distance"]
    name: Name
    a: PointSource
    b: PointSource
    comparison: Comparison | None = None

    @model_validator(mode="after")
    def nonnegative_target(self) -> Self:
        if self.comparison and self.comparison.target < 0:
            raise ValueError("Distance target must be nonnegative")
        return self


class AngleQuery(Model):
    kind: Literal["angle"]
    name: Name
    a: PointSource
    vertex: PointSource
    b: PointSource
    comparison: Comparison | None = None

    @model_validator(mode="after")
    def angle_target(self) -> Self:
        if self.comparison and not 0 <= self.comparison.target <= 180:
            raise ValueError("Angle target must be in 0..180 degrees")
        return self


class BoundsQuery(Model):
    kind: Literal["bounds"]
    name: Name
    object: Name


type ScalarMeasurementQuery = Annotated[
    DistanceQuery | AngleQuery, Field(discriminator="kind")
]


type MeasurementQuery = Annotated[
    DistanceQuery | AngleQuery | BoundsQuery, Field(discriminator="kind")
]


class MeasurementArguments(Model):
    queries: list[MeasurementQuery] = Field(min_length=1, max_length=64)
    frame: Name | None = Field(
        default=None,
        description="Omit/null for world axes; otherwise express all points and "
        "authored mesh bounds in this object's local coordinate frame.",
    )
    unit: Unit = "blender"

    @model_validator(mode="after")
    def meaningful(self) -> Self:
        if len({q.name for q in self.queries}) != len(self.queries):
            raise ValueError("Measurement names must be unique")
        if self.frame is not None and self.unit == "meters":
            raise ValueError("Meters require the world frame; local axes may be scaled")
        return self


class MeasurementValue(Model):
    name: str
    kind: Literal["distance", "angle", "bounds"]
    value: float | None = None
    unit: Literal["blender", "meters", "degrees"]
    minimum: Point | None = None
    maximum: Point | None = None
    dimensions: Point | None = None
    target: float | None = None
    deviation: float | None = None
    tolerance: float | None = None
    within_tolerance: bool | None = None


class UnitsSummary(Model):
    system: Literal["none", "metric", "imperial"]
    meters_per_unit: Float32


class UnitsConfigureArguments(Model):
    system: Literal["none", "metric", "imperial"]
    meters_per_unit: Float32 = Field(ge=1e-8, le=1e8)


class MeasurementResult(Model):
    measurements: list[MeasurementValue]
    frame: str | None
    units: UnitsSummary


class ReferenceCalibrateArguments(Model):
    name: Name
    a: Pixel
    b: Pixel
    target_distance: Distance
    unit: Unit = "blender"

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.a == self.b:
            raise ValueError("Calibration needs distinct image points")
        return self


class ReferenceCalibrateResult(Model):
    name: str
    before_distance: float
    after_distance: float
    target_distance: float
    unit: Unit
    factor: float
    size: float
    anchor_world: Point


type Axis = Literal["x", "y", "z"]
type SignedAxis = Literal["x", "y", "z", "-x", "-y", "-z"]


class SourceObservation(Model):
    id: Name
    reference: Name
    pixel: Pixel = Field(
        description="Bottom-left image-edge coordinates; not world XYZ."
    )
    label: str = Field(default="", max_length=256)
    sigma_pixels: Float32 | None = Field(default=None, gt=0, le=10000)
    visibility: Literal["visible", "occluded"] = "visible"


class ObservationSetArguments(Model):
    observations: list[SourceObservation] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({o.id for o in self.observations}) != len(self.observations):
            raise ValueError("Observation IDs must be unique")
        return self


class ObservationSummary(SourceObservation):
    revision: int
    reference_id: str
    source_sha256: str
    source_dimensions: list[int]
    source_label: str
    stale: bool
    reference_local: Point | None = None
    world_point: Point | None = None


class ObservationInspectArguments(InspectArguments):
    reference: Name | None = None
    mapped_points: bool = False


class ObservationResult(Model):
    observations: list[ObservationSummary]
    page: PageInfo | None = None


class ObservationWriteResult(Model):
    count: int
    ids: list[str]


class ReferenceScale(Model):
    a: Pixel
    b: Pixel
    distance: Distance
    unit: Unit = "blender"

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.a == self.b:
            raise ValueError("Scale requires distinct source points")
        return self


class ReferenceRegistration(Model):
    reference: Name
    projection: Literal["plane", "orthographic", "perspective"] = Field(
        description=(
            "Explicit source assumption; perspective is rejected until "
            "camera calibration is supported. Display visibility is not calibration."
        )
    )
    calibration: ReferenceScale
    origin_pixel: Pixel
    horizontal: SignedAxis
    vertical: SignedAxis
    frame: Name | None = Field(
        default=None,
        description=(
            "World if null; otherwise an existing rigid, unit-scale object frame."
        ),
    )
    origin: Point = Field(default_factory=lambda: [0.0, 0.0, 0.0])

    @model_validator(mode="after")
    def axes(self) -> Self:
        if self.horizontal.lstrip("-") == self.vertical.lstrip("-"):
            raise ValueError("Horizontal and vertical axes must differ")
        return self


class RegistrationArguments(Model):
    registrations: list[ReferenceRegistration] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({r.reference for r in self.registrations}) != len(self.registrations):
            raise ValueError("References must be unique")
        return self


class RegistrationSummary(ReferenceRegistration):
    reference_id: str
    frame_id: str | None
    revision: int
    state: Literal["calibrated", "stale"]
    source_sha256: str
    basis_sha256: str


class RegistrationResult(Model):
    registrations: list[RegistrationSummary]
    page: PageInfo


class AxisConstraint(Model):
    axis: Axis
    value: Coordinate = Field(
        description=(
            "Known coordinate/plane in the selected construction frame, "
            "in Blender units."
        )
    )


class ObservationDerivation(Model):
    kind: Literal["observations"] = "observations"
    observations: list[Name] = Field(min_length=1, max_length=8)
    constraints: list[AxisConstraint] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.observations)) != len(self.observations):
            raise ValueError("Observation IDs must be unique")
        if len({c.axis for c in self.constraints}) != len(self.constraints):
            raise ValueError("Constraint axes must be unique")
        return self


class ReflectionDerivation(Model):
    kind: Literal["reflection"]
    landmark: Name
    axis: Axis
    plane: Coordinate = 0.0


class GeometryDerivation(Model):
    kind: Literal["geometry"]
    source: GeometryPoint


type Derivation = Annotated[
    ObservationDerivation | ReflectionDerivation | GeometryDerivation,
    Field(discriminator="kind"),
]


class LandmarkDerivation(Model):
    name: Name
    source: Derivation
    label: str = Field(default="", max_length=256)
    category: str = Field(default="", max_length=64)


class LandmarkDeriveArguments(Model):
    landmarks: list[LandmarkDerivation] = Field(min_length=1, max_length=32)
    frame: Name | None = None
    tolerance: Float32 = Field(default=0.001, gt=0, le=1e6)
    worst_limit: int = Field(default=8, ge=0, le=32)

    @model_validator(mode="after")
    def unique(self) -> Self:
        names = {s.name for s in self.landmarks}
        if len(names) != len(self.landmarks):
            raise ValueError("Landmark names must be unique")
        if any(
            isinstance(s.source, ReflectionDerivation) and s.source.landmark in names
            for s in self.landmarks
        ):
            raise ValueError(
                "Reflection requires a current landmark outside this batch"
            )
        return self


type SolveStatus = Literal[
    "solved",
    "underconstrained",
    "inconsistent",
    "stale_dependency",
    "registration_required",
]


class ConstructionPointResult(Model):
    name: str
    status: SolveStatus
    rank: int = Field(ge=0, le=3)
    residual: float | None = None
    worst_observation: str | None = None
    sigma: Point | None = None
    message: str = ""


class ConstructionReport(Model):
    count: int
    solved: int
    underconstrained: int
    inconsistent: int
    stale: int
    registration_required: int
    max_residual: float | None
    mean_residual: float | None
    worst: list[ConstructionPointResult]
    worst_truncated: bool
    frame: str | None
    frames: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    unit: Literal["blender"] = "blender"
    solve_seconds: float


class ObservationEvidence(Model):
    id: str
    revision: int
    reference_id: str
    registration_revision: int
    source_sha256: str
    basis_sha256: str
    pixel: Pixel
    sigma_pixels: float | None
    residual: float


class DerivationProvenance(Model):
    name: str
    specification: LandmarkDerivation
    frame: str | None
    frame_id: str | None
    frame_sha256: str
    observations: list[ObservationEvidence]
    reflected_landmark: str | None = None
    reflected_basis: str | None = None
    geometry_resource_id: str | None = None
    geometry_basis: str | None = None
    point: Point
    world_point: Point
    tolerance: float
    result: ConstructionPointResult
    stale: bool = False


LandmarkInspectResult.model_rebuild()
