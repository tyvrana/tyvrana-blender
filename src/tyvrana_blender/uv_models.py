"""Whole-mesh UV map inspection, management and deterministic operators."""

from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, field_validator, model_validator

from .models import Model, ObjectName
from .numeric import Float32, Nonnegative32

type UVPair = Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]
type UnitSetting = Annotated[Float32, Field(ge=0, le=1)]
type UnwrapMethod = Literal[
    "angle_based",
    "conformal",
    "minimum_stretch",
    "smart_project",
    "cube_project",
    "cylinder_project",
    "sphere_project",
]


class UVMapSummary(Model):
    name: str
    active: bool
    active_render: bool
    loop_count: int
    uv_min: UVPair | None
    uv_max: UVPair | None
    out_of_unit_square_count: int
    pinned_count: int


class UVInspectResult(Model):
    object_name: str
    active_map: str | None
    active_render_map: str | None
    mesh_users: int
    maps: list[UVMapSummary]


def map_summary(
    name: str,
    active: bool,
    active_render: bool,
    coordinates: Iterable[tuple[float, float]],
    pinned_count: int,
) -> UVMapSummary:
    count = outside = 0
    minimum = [float("inf"), float("inf")]
    maximum = [-float("inf"), -float("inf")]
    for u, v in coordinates:
        count += 1
        minimum = [min(minimum[0], u), min(minimum[1], v)]
        maximum = [max(maximum[0], u), max(maximum[1], v)]
        # Native packers can leave roundoff just beyond the unit square.
        outside += not (-1e-6 <= u <= 1 + 1e-6 and -1e-6 <= v <= 1 + 1e-6)
    return UVMapSummary(
        name=name,
        active=active,
        active_render=active_render,
        loop_count=count,
        uv_min=minimum if count else None,
        uv_max=maximum if count else None,
        out_of_unit_square_count=outside,
        pinned_count=pinned_count,
    )


class UVInspectArguments(Model):
    object_name: ObjectName

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an optional property; null is invalid")
        return value


class UVCreateArguments(UVInspectArguments):
    name: ObjectName | None = None
    set_active: bool = True
    set_render: bool | None = None


class UVSetActiveArguments(UVInspectArguments):
    name: ObjectName
    set_active: bool = True
    set_render: bool = True

    @model_validator(mode="after")
    def at_least_one(self) -> Self:
        if not self.set_active and not self.set_render:
            raise ValueError("Set at least one of the editing or render map")
        return self


class UVUnwrapArguments(UVInspectArguments):
    uv_map: ObjectName | None = None
    method: UnwrapMethod
    correct_aspect: bool = True
    margin: UnitSetting | None = None
    fill_holes: bool | None = None
    angle_limit: Annotated[Float32, Field(ge=0, le=1.5707963705062866)] | None = None
    island_margin: UnitSetting | None = None
    area_weight: UnitSetting | None = None
    cube_size: Nonnegative32 | None = None
    scale_to_bounds: bool | None = None
    iterations: Annotated[int, Field(ge=1, le=100)] | None = None
    no_flip: bool | None = None

    @model_validator(mode="after")
    def applicable_settings(self) -> Self:
        allowed = {
            "angle_based": {"margin", "fill_holes"},
            "conformal": {"margin", "fill_holes"},
            "minimum_stretch": {"margin", "fill_holes", "iterations", "no_flip"},
            "smart_project": {
                "angle_limit",
                "island_margin",
                "area_weight",
                "scale_to_bounds",
            },
            "cube_project": {"cube_size", "scale_to_bounds"},
            "cylinder_project": {"scale_to_bounds"},
            "sphere_project": {"scale_to_bounds"},
        }
        invalid = (
            self.model_fields_set
            - {
                "object_name",
                "uv_map",
                "method",
                "correct_aspect",
            }
            - allowed[self.method]
        )
        if invalid:
            raise ValueError(
                "Settings do not apply to this unwrap method: "
                + ", ".join(sorted(invalid))
            )
        return self


class UVIslandWeight(Model):
    face_index: Annotated[int, Field(ge=0)]
    weight: Annotated[Float32, Field(gt=0, le=10)]


class UVPackTarget(UVInspectArguments):
    uv_map: ObjectName | None = None
    density_weight: Annotated[Float32, Field(gt=0, le=10)] = 1
    island_weights: list[UVIslandWeight] = Field(default_factory=list, max_length=256)


class UVLayoutArguments(Model):
    objects: list[ObjectName] = Field(min_length=1, max_length=16)
    uv_map: ObjectName | None = None
    evaluated: bool = False
    resolution: int = Field(default=4096, ge=64, le=16384)
    layout_image: bool = False
    image_size: int = Field(default=1024, ge=128, le=1024)

    @field_validator("*", mode="before")
    @classmethod
    def no_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit optional settings instead of passing null")
        return value

    @field_validator("objects")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Object names must be unique")
        return value


class UVPackArguments(Model):
    objects: list[UVPackTarget] = Field(min_length=1, max_length=16)
    bounds_min: UVPair = Field(default_factory=lambda: [0.0, 0.0])
    bounds_max: UVPair = Field(default_factory=lambda: [1.0, 1.0])
    resolution: int = Field(default=4096, ge=64, le=16384)
    padding_pixels: int = Field(default=16, ge=1, le=256)
    rotate: bool = True

    @field_validator("*", mode="before")
    @classmethod
    def no_null(cls, value: object) -> object:
        return UVLayoutArguments.no_null(value)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        names = [obj.object_name for obj in self.objects]
        if len(set(names)) != len(names):
            raise ValueError("Object names must be unique")
        if any(
            not 0 <= a < b <= 1
            for a, b in zip(self.bounds_min, self.bounds_max, strict=True)
        ):
            raise ValueError(
                "Packing bounds must be a nonempty rectangle inside the unit tile"
            )
        if (
            min(b - a for a, b in zip(self.bounds_min, self.bounds_max, strict=True))
            <= 2 * self.padding_pixels / self.resolution
        ):
            raise ValueError("Padding leaves no interior space")
        return self


class UVMetricSummary(Model):
    minimum: float | None
    p05: float | None
    median: float | None
    p95: float | None
    maximum: float | None


class UVIslandSummary(Model):
    object_name: str
    island_id: int
    face_count: int
    face_indices: list[int]
    face_indices_truncated: bool
    bounds_min: UVPair
    bounds_max: UVPair
    uv_area: float
    world_area: float
    texel_density: float
    face_density: UVMetricSummary
    anisotropy: UVMetricSummary
    angle_error_degrees: UVMetricSummary
    flipped_faces: int
    degenerate_faces: int
    tiles: list[int]


class UVFaceIssue(Model):
    object_name: str
    face_index: int
    anisotropy: float | None
    angle_error_degrees: float
    texel_density: float


class UVOverlap(Model):
    first_object: str
    first_face: int
    second_object: str
    second_face: int
    uv_area: float


class UVLayoutResult(Model):
    evaluated: bool
    resolution: int
    island_count: int
    islands: list[UVIslandSummary]
    face_count: int
    triangle_count: int
    flipped_face_count: int
    degenerate_face_count: int
    overlap_pair_count: int
    overlaps: list[UVOverlap]
    overlaps_truncated: bool
    worst_faces: list[UVFaceIssue]
    bounds_min: UVPair
    bounds_max: UVPair
    tiles: list[int]
    out_of_unit_face_count: int
    uv_area: float
    world_area: float
    minimum_island_gap_pixels: float | None
    minimum_tile_border_pixels: float
    density: UVMetricSummary
    anisotropy: UVMetricSummary
    angle_error_degrees: UVMetricSummary
    limitations: list[str]


class UVPackResult(Model):
    objects: list[UVInspectResult]
    island_count: int
    bounds_min: UVPair
    bounds_max: UVPair
    resolution: int
    padding_pixels: int
    shape_method: Literal["bounding_boxes"] = "bounding_boxes"
