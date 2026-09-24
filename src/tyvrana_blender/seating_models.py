"""Bounded multi-interface rigid seating within object-set placement."""

from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from .mechanics_models import MechanicsRegion
from .models import Model
from .numeric import Vector32
from .organization_models import Name
from .reference_models import PointSource


class Interface(Model):
    name: Name
    weight: FiniteFloat = Field(default=1, gt=0, le=1000)
    tolerance: FiniteFloat = Field(default=0.0001, gt=0, le=100)


class PointInterface(Interface):
    kind: Literal["point"]
    source: PointSource
    target: PointSource


class FrameInterface(Interface):
    kind: Literal["frame"]
    source_object: Name
    target_object: Name
    angular_tolerance: FiniteFloat = Field(default=0.001, gt=0, le=1)


class SurfaceInterface(Interface):
    kind: Literal["surface"]
    source: MechanicsRegion
    target: MechanicsRegion
    gap: FiniteFloat = Field(default=0, ge=0, le=100)
    normals: Literal["none", "parallel", "opposed"] = "none"
    angular_tolerance: FiniteFloat = Field(default=0.01, gt=0, le=1)
    align_centers: bool = False


type SeatingInterface = Annotated[
    PointInterface | FrameInterface | SurfaceInterface, Field(discriminator="kind")
]


class SeatingGuard(Model):
    name: Name
    source: MechanicsRegion
    target: MechanicsRegion
    mode: Literal["closed_solid", "oriented_patch"] = "closed_solid"
    allowed_side: Literal["positive", "negative"] = "positive"
    minimum_clearance: FiniteFloat = Field(default=0, ge=0, le=100)
    maximum_penetration: FiniteFloat = Field(default=0, ge=0, le=100)
    hard: bool = True
    weight: FiniteFloat = Field(default=1, gt=0, le=1000)

    @model_validator(mode="after")
    def envelope(self) -> Self:
        if self.minimum_clearance and self.maximum_penetration:
            raise ValueError("Choose positive clearance or a penetration envelope")
        return self


class SeatingInitial(Model):
    translation: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation_vector: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    mirror_axis: Literal["X", "Y", "Z"] | None = Field(
        default=None,
        description="Reflect this estimate only: translation is polar, rotation axial.",
    )


class RigidSeating(Model):
    members: list[Name] = Field(min_length=1, max_length=64)
    include_descendants: bool = True
    interfaces: list[SeatingInterface] = Field(min_length=2, max_length=8)
    guards: list[SeatingGuard] = Field(default_factory=list, max_length=8)
    translation_limit: Vector32 = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    rotation_limit: Vector32 = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    initial: SeatingInitial = Field(default_factory=SeatingInitial)
    apply: bool = True
    iterations: int = Field(default=40, ge=1, le=80)
    max_evaluations: int = Field(default=600, ge=20, le=1600)
    convergence_tolerance: FiniteFloat = Field(default=0.000001, gt=0, le=0.01)
    sample_limit: int = Field(default=64, ge=4, le=256)
    max_tests: int = Field(default=2000000, ge=100, le=2000000)
    worst_limit: int = Field(default=4, ge=0, le=8)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(set(self.members)) != len(self.members):
            raise ValueError("Moving members must be distinct")
        names = [x.name for x in self.interfaces] + [x.name for x in self.guards]
        if len(set(names)) != len(names):
            raise ValueError("Interface/guard names must be distinct")
        if any(v < 0 or v > 10000 for v in self.translation_limit):
            raise ValueError("Translation limits must be within 0..10000")
        if any(v < 0 or v > 3.141592653589793 for v in self.rotation_limit):
            raise ValueError("Rotation-vector limits must be within 0..pi")
        return self


class SeatingResidual(Model):
    name: str
    maximum_distance: float
    angular_error: float = 0
    satisfied: bool


class SeatingResult(Model):
    status: Literal["SOLVED", "INFEASIBLE", "NO_CONVERGENCE", "POORLY_CONDITIONED"]
    applied: bool
    translation: list[float]
    rotation_vector: list[float]
    pivot: list[float]
    world_delta: list[list[float]]
    iterations: int
    evaluations: int
    objective: float
    interfaces: list[SeatingResidual]
    worst_clearance: float | None
    worst_penetration: float | None
    active_bounds: list[str]
    moved_member_count: int
    preserved_relative_transforms: bool
    issues: list[str]
    evaluated_vertices: int
    geometry_tests: int
    processing_seconds: float
    limitations: list[str]
