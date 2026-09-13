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

    @model_validator(mode="after")
    def applicable_settings(self) -> Self:
        allowed = {
            "angle_based": {"margin", "fill_holes"},
            "conformal": {"margin", "fill_holes"},
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


class UVPackArguments(UVInspectArguments):
    uv_map: ObjectName | None = None
    margin: UnitSetting = 0.001
    rotate: bool = True
    scale: bool = True
