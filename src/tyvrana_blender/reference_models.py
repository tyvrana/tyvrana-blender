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


class LandmarkResult(Model):
    landmarks: list[LandmarkSummary]


class LandmarkInspectArguments(InspectArguments):
    object: Name | None = None
    category: str | None = Field(default=None, max_length=64)
    attachment: Literal["world", "object"] | None = None


class LandmarkInspectResult(LandmarkResult):
    page: PageInfo


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


class ReferencePoint(Model):
    kind: Literal["reference_pixel"]
    reference: Name
    pixel: Pixel = Field(
        description="Continuous image-edge coordinates, origin bottom-left; "
        "[0,0]..[width,height]. Pixel centers are at half-integers."
    )


type PointSource = Annotated[
    WorldPoint | ObjectPoint | LandmarkPoint | ReferencePoint,
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
