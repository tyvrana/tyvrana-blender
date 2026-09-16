"""Sampled separation and material-triangle sliding references, not simulation."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .deformation_models import RatioDistribution
from .mesh_models import AllSelector, MeshElementSelector
from .models import Model
from .modifier_models import Name
from .numeric import Float32

type ReferenceName = Annotated[Name, Field(max_length=128)]


def all_vertices() -> AllSelector:
    return AllSelector(mode="all", domain="vertex")


class LayerPair(Model):
    source: Name
    target: Name
    selector: MeshElementSelector = Field(
        default_factory=all_vertices,
        description=(
            "Select current evaluated source topology. Persisted references "
            "freeze sampled vertex indices instead."
        ),
    )
    sample_count: int = Field(default=1024, ge=1, le=8192)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.source == self.target:
            raise ValueError("Layer pair requires different source and target objects")
        return self


class CurrentLayerQuery(LayerPair):
    mode: Literal["current"] = "current"


class ReferenceLayerQuery(Model):
    mode: Literal["reference"]
    name: ReferenceName


type LayerQuery = Annotated[
    CurrentLayerQuery | ReferenceLayerQuery, Field(discriminator="mode")
]


class LayerInspectArguments(Model):
    queries: list[LayerQuery] = Field(
        default_factory=list,
        max_length=8,
        description="Empty lists saved QA reference names without evaluating geometry.",
    )
    contact_distance: Float32 = Field(default=0.001, ge=0, le=1000000)
    minimum_separation: Float32 = Field(default=0, ge=0, le=1000000)
    normal_epsilon: Float32 = Field(default=0.000001, gt=0, le=1)
    ray_direction: Literal["none", "source_normal", "opposite_source_normal"] = "none"
    ray_limit: Float32 = Field(default=1, gt=0, le=1000000)
    worst_limit: int = Field(default=8, ge=0, le=32)


class LayerReferenceSpec(LayerPair):
    name: ReferenceName
    replace: bool = False


class LayerCaptureArguments(Model):
    references: list[LayerReferenceSpec] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({r.name for r in self.references}) != len(self.references):
            raise ValueError("Capture each reference name once")
        return self


class LayerRemoveArguments(Model):
    names: list[ReferenceName] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.names)) != len(self.names):
            raise ValueError("Remove unique reference names")
        return self


class LayerReferencesResult(Model):
    names: list[str]
    changed: list[str]


class LayerDistribution(RatioDistribution):
    mean: float | None


class LayerWorst(Model):
    vertex: int
    position_world: list[float]
    nearest_world: list[float]
    separation: float
    oriented_normal_gap: float
    tangential_delta: list[float] | None = None
    normal_change: float | None = None


class LayerSummary(Model):
    source: str | None
    target: str | None
    reference: str | None = None
    valid: bool = True
    reason: str | None = None
    selected_vertices: int = 0
    sampled_vertices: int = 0
    source_topology_sha256: str | None = None
    target_topology_sha256: str | None = None
    separation: LayerDistribution | None = None
    oriented_normal_gap: LayerDistribution | None = None
    contact_samples: int = 0
    below_minimum_samples: int = 0
    negative_side_samples: int = 0
    negative_side_depth_max: float | None = None
    normal_ray_distance: LayerDistribution | None = None
    normal_ray_misses: int = 0
    tangential_movement: LayerDistribution | None = None
    normal_change: LayerDistribution | None = None
    worst: list[LayerWorst] = Field(default_factory=list)


class LayerInspectResult(Model):
    layers: list[LayerSummary]
    references: list[str]
    evaluated_vertices: int
    processing_seconds: float
