"""Bounded directional broad-template correction and durable offset playback."""

from typing import Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32, Vector32
from .reference_models import Name


class LayerObjectArguments(Model):
    object_name: Name


class LayerCorrectArguments(LayerObjectArguments):
    frame_start: Float32 = Field(ge=1, le=100000)
    frame_end: Float32 = Field(ge=1, le=100000)
    samples: int = Field(default=9, ge=2, le=64)
    colliders: list[Name] = Field(default_factory=list, max_length=8)
    direction: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    clearance: Float32 = Field(default=0.001, gt=0, le=1)
    maximum_lift: Float32 = Field(default=0.1, gt=0, le=10)
    steps: int = Field(default=16, ge=2, le=64)
    pin_end: Float32 = Field(default=0.05, ge=0, le=0.5)
    full_lift: Float32 = Field(default=0.3, gt=0, le=1)
    max_vertices: int = Field(default=20000, ge=1, le=100000)
    max_cached_vertices: int = Field(default=250000, ge=1, le=1000000)
    max_triangle_tests: int = Field(default=200000, ge=1, le=2000000)
    max_seconds: Float32 = Field(default=10, gt=0, le=30)
    replace: bool = False

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.frame_end <= self.frame_start:
            raise ValueError("frame_end must exceed frame_start")
        if self.full_lift <= self.pin_end:
            raise ValueError("full_lift must exceed the pinned root band")
        if sum(v * v for v in self.direction) < 1e-12:
            raise ValueError("direction must be nonzero in growth-object space")
        if len(set(self.colliders)) != len(self.colliders):
            raise ValueError("colliders must be distinct")
        return self


class LayerConflict(Model):
    frame: float
    element: str
    obstacle: str
    distance: float | None = None
    reason: str


class LayerCache(Model):
    object_name: str
    cached: bool
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
    settings: LayerCorrectArguments | None = None
    samples: int = 0
    coordination_passes: int = 0
    elements: int = 0
    vertices_per_sample: int = 0
    maximum_displacement: float = 0
    maximum_root_error: float = 0
    triangle_tests: int = 0
    ray_tests: int = 0
    processing_seconds: float = 0
    cache_bytes: int = 0
    cache_sha256: str | None = None
    conflicts: list[LayerConflict] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Directional kinematic correction, not physical sheet simulation.",
            "Triangle clearance at cache samples; "
            "interpolated times require geometry QA.",
            "Layer-side checks use vertex/centroid rays; "
            "not a continuous overlap proof.",
            "Pins use template local Z; growth Path/shaft output remains authored.",
        ]
    )
