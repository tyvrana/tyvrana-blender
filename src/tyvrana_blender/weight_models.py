"""Compact regional editing of owned deform weights."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .mesh_models import AllSelector, Arguments, MeshElementSelector, MeshVector
from .models import Model
from .modifier_models import Name
from .numeric import Float32
from .rig_models import BindingSummary, Influence

type Influences = Annotated[list[Influence], Field(min_length=1, max_length=4)]


def unique_weights(values: list[Influence]) -> None:
    if len({v.bone for v in values}) != len(values):
        raise ValueError("Influences must name each bone once")


class ConstantWeights(Arguments):
    mode: Literal["constant"]
    influences: Influences

    @model_validator(mode="after")
    def unique(self) -> Self:
        unique_weights(self.influences)
        return self


class GradientWeights(Arguments):
    mode: Literal["gradient"]
    start: MeshVector
    end: MeshVector
    start_influences: Influences
    end_influences: Influences
    interpolation: Literal["linear", "smoothstep"] = "smoothstep"

    @model_validator(mode="after")
    def valid(self) -> Self:
        unique_weights(self.start_influences)
        unique_weights(self.end_influences)
        if len({i.bone for i in self.start_influences + self.end_influences}) > 4:
            raise ValueError("Gradient endpoints may name at most four bones total")
        if sum((a - b) ** 2 for a, b in zip(self.start, self.end, strict=True)) < 1e-12:
            raise ValueError("Gradient endpoints must be at least 0.000001 apart")
        if any(abs(v) > 10000 for v in self.start + self.end):
            raise ValueError("Gradient coordinates must be within 10000 units")
        return self


class NormalizeWeights(Arguments):
    mode: Literal["normalize"]


type WeightProfile = Annotated[
    ConstantWeights | GradientWeights | NormalizeWeights, Field(discriminator="mode")
]


class WeightLayer(Arguments):
    selector: MeshElementSelector
    weights: WeightProfile

    @model_validator(mode="after")
    def vertices(self) -> Self:
        if self.selector.domain != "vertex":
            raise ValueError("Weight selectors must use the vertex domain")
        return self


class WeightsAssignArguments(Arguments):
    object_name: Name
    layers: Annotated[list[WeightLayer], Field(min_length=1, max_length=32)]
    max_influences: Annotated[int, Field(ge=1, le=4)] = 4
    smooth_iterations: Annotated[int, Field(ge=0, le=20)] = 0
    smooth_factor: Annotated[Float32, Field(gt=0, le=1)] = 0.5
    fixed_selector: MeshElementSelector | None = None
    allow_unweighted: bool = False

    @model_validator(mode="after")
    def fixed(self) -> Self:
        if self.fixed_selector is not None and (
            self.fixed_selector.domain != "vertex" or not self.smooth_iterations
        ):
            raise ValueError("Fixed vertices require smoothing and vertex domain")
        return self


class WeightsInspectArguments(Arguments):
    object_name: Name
    selector: MeshElementSelector = Field(
        default_factory=lambda: AllSelector(mode="all", domain="vertex")
    )
    bone_names: Annotated[list[Name], Field(min_length=1, max_length=128)] | None = None
    filter: Literal["all", "unweighted", "non_normalized", "multiple"] = "all"
    sample_limit: Annotated[int, Field(ge=0, le=32)] = 8

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.selector.domain != "vertex":
            raise ValueError("Weight selectors must use the vertex domain")
        if self.bone_names is not None and len(set(self.bone_names)) != len(
            self.bone_names
        ):
            raise ValueError("Inspect each bone once")
        return self


class WeightSample(Model):
    vertex: int
    position_local: MeshVector
    weight_sum: float
    influences: list[Influence]
    influences_truncated: bool


class WeightGroupSummary(Model):
    bone: str
    locked: bool
    weighted_vertices: int
    dominant_vertices: int
    minimum: float
    maximum: float
    mean: float


class WeightsSummary(Model):
    binding: BindingSummary
    selected_vertex_count: int
    matching_vertex_count: int
    unweighted_vertex_count: int
    non_normalized_vertex_count: int
    multiple_influence_vertex_count: int
    groups: list[WeightGroupSummary]
    samples: list[WeightSample]


class WeightsAssignment(Model):
    binding: BindingSummary
    selected_vertex_count: int
    changed_vertex_count: int
    layer_vertex_counts: list[int]
    fixed_vertex_count: int
    smooth_iterations: int
    processing_seconds: float
