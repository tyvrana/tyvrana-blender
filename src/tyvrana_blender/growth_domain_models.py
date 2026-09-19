"""Surface fields and ordered attachment domains shared by growth authoring and QA."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32
from .reference_models import Name

type UV = Annotated[list[Float32], Field(min_length=2, max_length=2)]


class FieldControl(Model):
    uv: UV
    direction: UV = Field(description="Nonzero direction in surface UV coordinates.")
    length_scale: Float32 = Field(default=1, gt=0, le=100)

    @model_validator(mode="after")
    def direction_valid(self) -> Self:
        if sum(v * v for v in self.direction) < 1e-12:
            raise ValueError("Field direction must be nonzero")
        return self


class GrowthField(Model):
    controls: list[FieldControl] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({tuple(c.uv) for c in self.controls}) != len(self.controls):
            raise ValueError("Field control UV coordinates must be unique")
        return self


class GrowthRow(Model):
    name: Name
    path: list[UV] = Field(min_length=2, max_length=32)
    count: int | None = Field(default=16, ge=2, le=2048)
    spacing: Float32 | None = Field(
        default=None,
        gt=0,
        le=100,
        description=(
            "Surface-local arc spacing; set count=null. At most2048 roots per side."
        ),
    )
    family: Name | None = None
    layer: int | None = Field(default=None, ge=0, le=31)
    order: int = Field(default=0, ge=0, le=1023)
    overlap: Literal["none", "over_previous"] = Field(
        default="none",
        description="Intent only; collision QA/correction must verify it.",
    )
    mirror: Literal["u", "v"] | None = None
    mirror_center: Float32 = 0.5

    @model_validator(mode="after")
    def valid(self) -> Self:
        if (self.count is None) == (self.spacing is None):
            raise ValueError("Choose count or spacing with count=null")
        if any(
            sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) < 1e-12
            for a, b in zip(self.path[:-1], self.path[1:], strict=True)
        ):
            raise ValueError("Row path requires distinct consecutive UV points")
        return self


class GrowthFieldQuery(Model):
    region: Name
    uv: UV


class GrowthFieldSample(Model):
    region: str
    uv: list[float]
    position: list[float]
    direction: list[float]
    normal: list[float]
    length_scale: float
    face: int


class GrowthRowSummary(Model):
    region: str
    row: str
    mirrored: bool
    count: int
    layer: int
    order: int
    overlap: str
    minimum_spacing: float | None
    maximum_spacing: float | None
