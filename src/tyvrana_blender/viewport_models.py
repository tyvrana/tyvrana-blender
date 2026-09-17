"""Bounded, explicit interactive viewport selection and framing."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

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
    view_distance: float
    perspective: Literal["PERSP", "ORTHO", "CAMERA"]
    local_view: bool
    quad_view: bool


class ViewportInspection(Model):
    viewports: list[ViewportState]
