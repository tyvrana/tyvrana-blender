"""Bounded authored topology summaries and coordinated quad-strip refinement."""

from typing import Annotated

from pydantic import Field, field_validator

from .deformation_models import RatioDistribution
from .mesh_models import (
    AllSelector,
    ElementCounts,
    Index,
    MeshElementSelector,
    MeshInspectArguments,
    MeshVector,
)
from .models import Model
from .numeric import Float32

type CutFactors = Annotated[
    list[Annotated[Float32, Field(gt=0, lt=1)]], Field(min_length=1, max_length=16)
]


def validate_factors(values: list[float]) -> list[float]:
    if any(b - a < 1e-5 for a, b in zip([0.0, *values], [*values, 1.0], strict=True)):
        raise ValueError(
            "Factors must increase strictly, with at least 0.00001 between cuts "
            "and endpoints"
        )
    return values


class RingCut(Model):
    edge: Index
    from_vertex: Index
    factors: CutFactors

    @field_validator("factors")
    @classmethod
    def ordered(cls, values: list[float]) -> list[float]:
        return validate_factors(values)


class MeshInsertLoopsArguments(MeshInspectArguments):
    cuts: list[RingCut] = Field(
        min_length=1,
        max_length=16,
        description=(
            "Seed indices refer to one input snapshot. Complete quad strips "
            "must not overlap faces; 4096 transverse edges per strip, 16 cuts "
            "per strip; 20M estimated element/cut work units. Native BMesh "
            "interpolates UVs/weights; connectivity "
            "edits invalidate index references and surface attachments."
        ),
    )


class TopologyInspectArguments(MeshInspectArguments):
    selector: MeshElementSelector = Field(
        default_factory=lambda: AllSelector(domain="vertex", mode="all")
    )
    sample_limit: int = Field(default=8, ge=0, le=64)


class PoleSample(Model):
    vertex: int
    valence: int
    boundary: bool
    world: MeshVector


class TopologySummary(Model):
    object_name: str
    topology_sha256: str
    selected: ElementCounts
    quad_count: int
    triangle_count: int
    ngon_count: int
    boundary_edge_count: int
    non_manifold_edge_count: int
    degenerate_face_count: int
    valence: dict[str, int]
    edge_lengths: RatioDistribution
    face_areas: RatioDistribution
    quad_aspect: RatioDistribution
    pole_count: int
    poles: list[PoleSample]
    poles_truncated: bool
    inspection_seconds: float
