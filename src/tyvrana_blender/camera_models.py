"""Typed camera contracts, independent of Blender's Python module."""

from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from .models import Model, ObjectName, PageInfo, Vector
from .numeric import FLOAT32_MAX, Float32, Vector32, binary32

CLIP_MIN = binary32(1e-6)


type Lens = Annotated[
    FiniteFloat, Field(ge=1, le=FLOAT32_MAX), AfterValidator(binary32)
]
type OrthoScale = Annotated[
    FiniteFloat, Field(gt=0, le=FLOAT32_MAX), AfterValidator(binary32)
]
type ClipDistance = Annotated[
    FiniteFloat, Field(ge=CLIP_MIN, le=FLOAT32_MAX), AfterValidator(binary32)
]
type Projection = Literal["perspective", "orthographic", "panoramic", "custom"]
type ConfigurableProjection = Literal["perspective", "orthographic"]
type SensorFit = Literal["auto", "horizontal", "vertical"]

PROJECTIONS: dict[str, Projection] = {
    "PERSP": "perspective",
    "ORTHO": "orthographic",
    "PANO": "panoramic",
    "CUSTOM": "custom",
}


def normalize_projection(value: str) -> Projection:
    return PROJECTIONS[value]


class CameraProperties(Model):
    """Complete writable data state, including stored inactive lens/scale values."""

    projection: ConfigurableProjection = "perspective"
    lens_mm: Lens = 50.0
    ortho_scale: OrthoScale = 6.0
    clip_start: ClipDistance = 0.1
    clip_end: ClipDistance = 1000.0
    shift_x: Float32 = 0.0
    shift_y: Float32 = 0.0

    @model_validator(mode="after")
    def coherent_clipping(self) -> Self:
        if binary32(self.clip_end) <= binary32(self.clip_start):
            raise ValueError("clip_end must exceed clip_start at Blender precision")
        return self


class CameraCreateArguments(CameraProperties):
    name: ObjectName | None = None
    location: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: Vector32 = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    make_active: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def non_null_name(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit the name to use Blender's default; null is invalid")
        return value

    @model_validator(mode="after")
    def meaningful_optics(self) -> Self:
        validate_optics(self.projection, self.model_fields_set)
        return self


class CameraConfigureArguments(Model):
    name: ObjectName
    projection: ConfigurableProjection | None = None
    lens_mm: Lens | None = None
    ortho_scale: OrthoScale | None = None
    clip_start: ClipDistance | None = None
    clip_end: ClipDistance | None = None
    shift_x: Float32 | None = None
    shift_y: Float32 | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an unchanged property; null is invalid")
        return value

    @model_validator(mode="after")
    def coherent_supplied_properties(self) -> Self:
        if (
            self.clip_start is not None
            and self.clip_end is not None
            and self.clip_end <= self.clip_start
        ):
            raise ValueError("clip_end must exceed clip_start at Blender precision")
        if self.projection is not None:
            validate_optics(self.projection, self.model_fields_set)
        return self


def validate_optics(projection: ConfigurableProjection, fields: set[str]) -> None:
    if projection == "perspective" and "ortho_scale" in fields:
        raise ValueError("ortho_scale requires orthographic projection")
    if projection == "orthographic" and "lens_mm" in fields:
        raise ValueError("lens_mm requires perspective projection")


class CameraSetActiveArguments(Model):
    name: ObjectName


class CameraSummary(Model):
    name: str
    active: bool
    projection: Projection
    location: Vector
    rotation: Vector
    scale: Vector
    lens_mm: FiniteFloat | None
    ortho_scale: FiniteFloat | None
    clip_start: FiniteFloat
    clip_end: FiniteFloat
    shift_x: FiniteFloat
    shift_y: FiniteFloat
    sensor_width_mm: FiniteFloat
    sensor_height_mm: FiniteFloat
    sensor_fit: SensorFit


class CameraInspectResult(Model):
    page: PageInfo
    active_camera: str | None
    cameras: list[CameraSummary]
