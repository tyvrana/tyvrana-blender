"""Bounded native rod dynamics and durable sampled growth playback."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32, Vector32
from .reference_models import Name


class DynamicsSettings(Model):
    strategy: Literal["native_rods"] = "native_rods"
    stretchiness: Float32 = Field(default=0, ge=0, le=1)
    bendiness: Float32 = Field(default=0.2, ge=0, le=1)
    root_bendiness: Float32 = Field(default=0.1, ge=0, le=1)
    mass: Float32 = Field(default=0.01, ge=0.00001, le=100)
    linear_damping: Float32 = Field(default=3, ge=0, le=100)
    angular_damping: Float32 = Field(default=3, ge=0, le=100)
    gravity: Vector32 = Field(default_factory=lambda: [0, 0, -9.81])
    surface_collision: bool = True
    colliders: list[Name] = Field(default_factory=list, max_length=8)
    friction: Float32 = Field(default=0.2, ge=0, le=1)
    margin: Float32 = Field(default=0.001, ge=0, le=1)
    edge_contacts: bool = True
    substeps: int = Field(default=8, ge=1, le=64)
    constraint_steps: int = Field(default=8, ge=1, le=64)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.colliders)) != len(self.colliders):
            raise ValueError("Collider names must be unique")
        if any(abs(v) > 1000 for v in self.gravity):
            raise ValueError("Gravity components must be within 1000 units/s²")
        return self


class DynamicsObjectArguments(Model):
    object_name: Name


class DynamicsBakeArguments(DynamicsObjectArguments):
    settings: DynamicsSettings = Field(default_factory=DynamicsSettings)
    frame_start: int = Field(ge=1, le=100000)
    frame_end: int = Field(ge=1, le=100000)
    replace: bool = False
    max_points: int = Field(default=20000, ge=1, le=50000)
    max_cached_points: int = Field(default=1000000, ge=1, le=4000000)
    max_solver_work: int = Field(default=20000000, ge=1, le=100000000)
    max_seconds: Float32 = Field(default=60, gt=0, le=600)

    @model_validator(mode="after")
    def range_valid(self) -> Self:
        if not 1 <= self.frame_end - self.frame_start <= 127:
            raise ValueError("Bake 2..128 consecutive integer frames")
        return self


class DynamicsJobArguments(Model):
    job_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]+$")


class DynamicsCache(Model):
    object_name: str
    cached: bool
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
    settings: DynamicsSettings | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    points_per_frame: int = 0
    cache_bytes: int = 0
    cache_sha256: str | None = None
    maximum_root_error: float = 0
    maximum_solver_root_error: float = 0
    maximum_displacement: float = 0
    processing_seconds: float = 0
    solver: str = "Blender Hair Dynamics XPBD (experimental)"


class DynamicsJobStatus(Model):
    job_id: str
    object_name: str
    state: Literal["queued", "running", "completed", "failed", "cancelled"]
    completed_frames: int = 0
    frame_count: int
    elapsed_seconds: float = 0
    result: DynamicsCache | None = None
    error: str | None = None
