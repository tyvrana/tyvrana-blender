"""Typed light inputs and concise summaries, independent of bpy."""

import math
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)
from tyvrana_protocol import JsonValue

from .models import Model, ObjectName, PageInfo, Vector
from .numeric import Float32, Nonnegative32, Vector32, binary32

ANGLE_MAX = binary32(math.pi)
SPOT_MIN = binary32(math.pi / 180)
type LightType = Literal["point", "sun", "spot", "area"]
type AreaShape = Literal["square", "rectangle", "disk", "ellipse"]
type Color = Annotated[list[Nonnegative32], Field(min_length=3, max_length=3)]
type Angle = Annotated[FiniteFloat, Field(ge=0, le=ANGLE_MAX), AfterValidator(binary32)]
type SpotAngle = Annotated[
    FiniteFloat, Field(ge=SPOT_MIN, le=ANGLE_MAX), AfterValidator(binary32)
]
type Blend = Annotated[FiniteFloat, Field(ge=0, le=1), AfterValidator(binary32)]
type Exposure = Annotated[FiniteFloat, Field(ge=-32, le=32), AfterValidator(binary32)]

LIGHT_TYPES: dict[str, LightType] = {
    "POINT": "point",
    "SUN": "sun",
    "SPOT": "spot",
    "AREA": "area",
}
COMMON_FIELDS = {"color", "energy", "exposure", "normalize", "use_shadow"}
TYPE_FIELDS: dict[LightType, set[str]] = {
    "point": {"radius"},
    "sun": {"angle"},
    "spot": {"radius", "spot_size", "spot_blend"},
    "area": {"shape", "size", "size_y"},
}


class PointSettings(Model):
    radius: Nonnegative32 = 0.0


class SunSettings(Model):
    angle: Angle = 0.009180432185530663


class SpotSettings(PointSettings):
    spot_size: SpotAngle = 0.7853981852531433
    spot_blend: Blend = 0.15000000596046448


class AreaSettings(Model):
    shape: Literal["square", "disk"] = "square"
    size: Nonnegative32 = 0.25


class AreaDimensions(Model):
    shape: Literal["rectangle", "ellipse"]
    size: Nonnegative32 = 0.25
    size_y: Nonnegative32 = 0.25


type LightSettings = (
    PointSettings | SunSettings | SpotSettings | AreaSettings | AreaDimensions
)


class LightState(Model):
    type: LightType
    color: Color = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    energy: Float32 = 10.0
    exposure: Exposure = 0.0
    normalize: bool = True
    use_shadow: bool = True
    settings: LightSettings

    @model_validator(mode="after")
    def applicable_settings(self) -> Self:
        expected: dict[LightType, tuple[type[Model], ...]] = {
            "point": (PointSettings,),
            "sun": (SunSettings,),
            "spot": (SpotSettings,),
            "area": (AreaSettings, AreaDimensions),
        }
        if type(self.settings) not in expected[self.type]:
            raise ValueError("Settings do not match light type")
        return self

    def writable_values(self) -> dict[str, JsonValue]:
        return {
            **self.model_dump(mode="json", exclude={"type", "settings"}),
            **self.settings.model_dump(mode="json"),
        }


class LightSummary(LightState):
    name: str
    location: Vector
    rotation: Vector
    scale: Vector
    visible: bool
    hide_viewport: bool
    hide_render: bool
    parent: str | None


class LightInspectResult(Model):
    page: PageInfo
    lights: list[LightSummary]


def light_state(
    kind: LightType, values: dict[str, JsonValue], fields: set[str]
) -> LightState:
    """Validate applicable fields and the complete state before any mutation."""
    invalid = fields - COMMON_FIELDS - TYPE_FIELDS[kind]
    if invalid:
        raise ValueError(
            f"Properties not applicable to {kind}: {', '.join(sorted(invalid))}"
        )
    specific = {key: value for key, value in values.items() if key in TYPE_FIELDS[kind]}
    settings: LightSettings
    match kind:
        case "point":
            settings = PointSettings.model_validate(specific)
        case "sun":
            settings = SunSettings.model_validate(specific)
        case "spot":
            settings = SpotSettings.model_validate(specific)
        case "area":
            if specific.get("shape", "square") in ("rectangle", "ellipse"):
                settings = AreaDimensions.model_validate(specific)
            else:
                if "size_y" in fields:
                    raise ValueError("size_y requires rectangle or ellipse shape")
                specific.pop("size_y", None)
                settings = AreaSettings.model_validate(specific)
    return LightState.model_validate(
        {
            **{key: value for key, value in values.items() if key in COMMON_FIELDS},
            "type": kind,
            "settings": settings,
        }
    )


class LightPatch(Model):
    color: Color | None = None
    energy: Float32 | None = None
    exposure: Exposure | None = None
    normalize: bool | None = None
    use_shadow: bool | None = None
    radius: Nonnegative32 | None = None
    angle: Angle | None = None
    spot_size: SpotAngle | None = None
    spot_blend: Blend | None = None
    shape: AreaShape | None = None
    size: Nonnegative32 | None = None
    size_y: Nonnegative32 | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an unchanged property; null is invalid")
        return value


class LightCreateArguments(LightPatch):
    type: LightType
    name: ObjectName | None = None
    location: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: Vector32 = Field(default_factory=lambda: [1.0, 1.0, 1.0])

    def settings_state(self) -> LightState:
        values = self.model_dump(
            mode="json",
            exclude={"type", "name", "location", "rotation", "scale"},
            exclude_unset=True,
        )
        return light_state(self.type, values, set(values))

    @model_validator(mode="after")
    def valid_settings(self) -> Self:
        self.settings_state()
        return self


class LightConfigureArguments(LightPatch):
    name: ObjectName
