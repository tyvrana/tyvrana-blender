"""Finite native placement rules; source points reuse measurement semantics."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32, Vector32
from .organization_models import Name
from .reference_models import GeometryPoint, PointSource, WorldPoint

type Dimension = Annotated[Float32, Field(gt=0.000001, le=1000000)]
type Dimensions = Annotated[list[Dimension | None], Field(min_length=3, max_length=3)]


class BetweenPlacement(Model):
    kind: Literal["between"]
    start: PointSource
    end: PointSource
    axis: Literal["X", "Y", "Z"] = "Y"
    up: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    roll: Float32 = Field(default=0, ge=-100, le=100)
    dimensions: Dimensions | None = None
    fit_length: bool = True


class FramePlacement(Model):
    kind: Literal["frame"] = "frame"
    target: PointSource = Field(
        default_factory=lambda: WorldPoint(kind="world", point=[0, 0, 0])
    )
    local_anchor: Vector32 | GeometryPoint = Field(
        default_factory=lambda: [0.0, 0.0, 0.0]
    )
    direction: Vector32 = Field(default_factory=lambda: [0.0, 1.0, 0.0])
    axis: Literal["X", "Y", "Z"] = "Y"
    up: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    roll: Float32 = Field(default=0, ge=-100, le=100)
    dimensions: Dimensions | None = None


class FitPlacement(Model):
    kind: Literal["fit"]
    dimensions: Dimensions


class DatumPlacement(Model):
    kind: Literal["datums"]
    origin: PointSource
    x_axis: PointSource
    y_axis: PointSource
    z_axis: PointSource


class MirrorPlacement(Model):
    kind: Literal["mirror"]
    source: Name
    plane_point: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    plane_normal: Vector32 = Field(default_factory=lambda: [1.0, 0.0, 0.0])

    @model_validator(mode="after")
    def normal(self) -> Self:
        if math.hypot(*self.plane_normal) < 1e-8:
            raise ValueError("Mirror plane normal must be nonzero")
        return self


type PlacementRule = Annotated[
    BetweenPlacement | FramePlacement | FitPlacement | MirrorPlacement | DatumPlacement,
    Field(discriminator="kind"),
]


class PlacementEdit(Model):
    name: Name
    rule: PlacementRule


class PlacementArguments(Model):
    placements: list[PlacementEdit] = Field(default_factory=list, max_length=64)
    refresh: list[Name] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        names = [p.name for p in self.placements] + self.refresh
        if not names or len(names) != len(set(names)) or len(names) > 64:
            raise ValueError(
                "Provide 1..64 distinct placements or stored-rule refreshes"
            )
        return self


class PlacementSummary(Model):
    name: str
    revision: int
    rule: PlacementRule
    dimensions: Vector32
    world_matrix: list[list[float]]
    valid: bool


class PlacementResult(Model):
    placements: list[PlacementSummary]
