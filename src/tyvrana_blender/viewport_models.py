"""Bounded, explicit interactive viewport selection and framing."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .mesh_models import Arguments
from .models import Model, ObjectName, Vector

ViewportId = Annotated[str, Field(pattern=r"^[0-9]+:[0-9]+$")]


class ViewportInspectArguments(Arguments):
    viewport_id: ViewportId | None = None


class ViewportFrameArguments(Arguments):
    viewport_id: ViewportId
    object_names: Annotated[list[ObjectName], Field(min_length=1, max_length=64)]

    @field_validator("object_names")
    @classmethod
    def unique_objects(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Object names must be unique")
        return value


class ViewportState(Model):
    viewport_id: str
    scene_name: str
    view_layer_name: str
    width: int
    height: int
    selected_count: int
    selected_objects: list[str]
    active_object: str | None
    view_location: Vector
    view_rotation: tuple[float, float, float, float]
    view_distance: float
    perspective: Literal["PERSP", "ORTHO", "CAMERA"]
    local_view: bool
    quad_view: bool


class ViewportInspection(Model):
    viewports: list[ViewportState]


class ViewDirection(Model):
    direction: Vector = Field(description="World direction from eye towards target")
    up: Vector = Field(default_factory=lambda: [0.0, 0.0, 1.0])

    @model_validator(mode="after")
    def independent(self) -> Self:
        a, b = self.direction, self.up
        cross = [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]
        if sum(c * c for c in cross) < 1e-12:
            raise ValueError("Direction and up must be nonzero and nonparallel")
        return self


Orientation = (
    Literal["front", "rear", "left", "right", "top", "bottom", "oblique"]
    | ViewDirection
)


class ViewSettings(Model):
    orientation: Orientation
    target: Vector | None = None
    distance: float | None = Field(default=None, gt=0.00001, le=1000000)
    perspective: Literal["PERSP", "ORTHO"] = "ORTHO"


class ViewportConfigureArguments(ViewSettings):
    viewport_id: ViewportId


class ViewportCaptureArguments(Arguments):
    viewport_id: ViewportId
    views: list[ViewSettings] = Field(default_factory=list, max_length=6)
    max_pixels: int = Field(default=16000000, ge=4096, le=32000000)


class ViewportCapture(Model):
    state: ViewportState
    captured_at: str
    x: int
    y: int
    width: int
    height: int


class ViewportCaptureResult(Model):
    width: int
    height: int
    captures: list[ViewportCapture]
    restored: bool
    scope: Literal["viewport_region"] = "viewport_region"
