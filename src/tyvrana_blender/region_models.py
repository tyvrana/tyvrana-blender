"""Rest-frame boxes shared by authored selection and evaluated deformation QA."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .reference_models import Name, Point


class WorldFrame(Model):
    kind: Literal["world"] = "world"
    origin: Point = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Point = Field(
        default_factory=lambda: [0.0, 0.0, 0.0], description="World XYZ Euler radians."
    )


class ObjectFrame(Model):
    kind: Literal["object"]
    object: Name


class BoneFrame(Model):
    kind: Literal["bone"]
    object: Name
    bone: Name


type RegionFrame = Annotated[
    WorldFrame | ObjectFrame | BoneFrame, Field(discriminator="kind")
]


class FrameRegion(Model):
    frame: RegionFrame = Field(
        default_factory=WorldFrame,
        description=(
            "Object local or native bone REST frame (head origin, "
            "longitudinal Y); world frame by default. Scale is retained."
        ),
    )
    min: Point
    max: Point

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if any(a > b for a, b in zip(self.min, self.max, strict=True)):
            raise ValueError("Region minimum must not exceed maximum")
        return self
