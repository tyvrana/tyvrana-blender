"""Bounded regional distortion and nearest-surface contact diagnostics."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from .mesh_models import Arguments, MeshVector
from .models import Model
from .modifier_models import Name


class ContactProbe(Arguments):
    source_object: Name
    target_object: Name
    rest_min: MeshVector
    rest_max: MeshVector

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.source_object == self.target_object:
            raise ValueError("Contact requires separate source and target meshes")
        if any(a > b for a, b in zip(self.rest_min, self.rest_max, strict=True)):
            raise ValueError("Contact rest minimum must not exceed maximum")
        return self


class RatioDistribution(Model):
    count: int
    minimum: float | None
    p05: float | None
    p50: float | None
    p95: float | None
    p99: float | None
    maximum: float | None


class EdgeDistortion(Model):
    vertices: list[int]
    rest_world: list[MeshVector]
    posed_world: list[MeshVector]
    rest_length: float
    posed_length: float
    ratio: float
    endpoint_bones: list[str | None]


class BoneDeformation(Model):
    bone: str
    vertex_count: int
    displacement_max: float
    displacement_p95: float
    edge_ratios: RatioDistribution
    triangle_area_ratios: RatioDistribution


class ContactSummary(Model):
    source_object: str
    target_object: str
    vertex_count: int
    rest_distance: RatioDistribution
    posed_distance: RatioDistribution
    separation_increase_max: float
    rest_outside_max: float
    posed_outside_max: float
    rest_penetration_max: float
    posed_penetration_max: float


class DeformationQA(Model):
    edge_ratios: RatioDistribution
    triangle_area_ratios: RatioDistribution
    worst_edges: list[EdgeDistortion]
    bone_regions: list[BoneDeformation]
    rest_volume: float | None
    posed_volume: float | None
    volume_ratio: float | None


type ContactProbes = Annotated[list[ContactProbe], Field(max_length=8)]
