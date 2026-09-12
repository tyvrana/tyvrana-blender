"""Typed camera contracts, independent of Blender's Python module."""

import struct
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from .models import Model, ObjectName, Vector

FLOAT32_MAX = float.fromhex("0x1.fffffep+127")
CLIP_MIN = float(struct.unpack("f", struct.pack("f", 1e-6))[0])


def binary32(value: float) -> float:
    """Validate the precision Blender will store, without clamping or underflow."""
    if not -FLOAT32_MAX <= value <= FLOAT32_MAX:
        raise ValueError("Value exceeds Blender's finite float32 range")
    rounded = float(struct.unpack("f", struct.pack("f", value))[0])
    if value != 0 and rounded == 0:
        raise ValueError("Value underflows Blender's float32 precision")
    return rounded


def camera_vector(value: list[float]) -> list[float]:
    return [binary32(component) for component in value]


type CameraFloat = Annotated[
    FiniteFloat,
    Field(ge=-FLOAT32_MAX, le=FLOAT32_MAX),
    AfterValidator(binary32),
]
type Lens = Annotated[
    FiniteFloat, Field(ge=1, le=FLOAT32_MAX), AfterValidator(binary32)
]
type OrthoScale = Annotated[
    FiniteFloat, Field(gt=0, le=FLOAT32_MAX), AfterValidator(binary32)
]
type ClipDistance = Annotated[
    FiniteFloat, Field(ge=CLIP_MIN, le=FLOAT32_MAX), AfterValidator(binary32)
]
type CameraVector = Annotated[Vector, AfterValidator(camera_vector)]
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
    shift_x: CameraFloat = 0.0
    shift_y: CameraFloat = 0.0

    @model_validator(mode="after")
    def coherent_clipping(self) -> Self:
        if binary32(self.clip_end) <= binary32(self.clip_start):
            raise ValueError("clip_end must exceed clip_start at Blender precision")
        return self


class CameraCreateArguments(CameraProperties):
    name: ObjectName | None = None
    location: CameraVector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: CameraVector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: CameraVector = Field(default_factory=lambda: [1.0, 1.0, 1.0])
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
    shift_x: CameraFloat | None = None
    shift_y: CameraFloat | None = None

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
    active_camera: str | None
    cameras: list[CameraSummary]
