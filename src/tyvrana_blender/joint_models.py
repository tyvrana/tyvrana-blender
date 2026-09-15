"""Explicit native joint frames and principal XYZ rotation limits."""

import math
from typing import Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from .models import InspectArguments, Model, PageInfo
from .modifier_models import Name
from .numeric import Vector32

MAX_Y = math.pi / 2 - 0.0001
MAX_XZ = math.pi - 0.0001


class AxisLimit(Model):
    minimum: FiniteFloat = Field(
        ge=-MAX_XZ,
        le=MAX_XZ,
        description="Lower XYZ Euler angle in radians, relative to rest.",
    )
    maximum: FiniteFloat = Field(ge=-MAX_XZ, le=MAX_XZ)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Axis minimum must not exceed maximum")
        return self


class JointLimits(Model):
    rotation_mode: Literal["XYZ"] = "XYZ"
    x: AxisLimit | None = None
    y: AxisLimit | None = None
    z: AxisLimit | None = None

    @model_validator(mode="after")
    def principal_branch(self) -> Self:
        if self.x is None and self.y is None and self.z is None:
            raise ValueError(
                "Enable at least one axis; null limits remove the joint constraint"
            )
        if self.y and (self.y.minimum < -MAX_Y or self.y.maximum > MAX_Y):
            raise ValueError("Y limits must stay inside +/- (pi/2 - 0.0001) radians")
        return self


class JointPatch(Model):
    name: Name
    limits: JointLimits | None = Field(
        description=(
            "Full limit replacement; null removes only this adapter-owned "
            "joint constraint. Null axes are unconstrained; [0,0] locks "
            "an axis."
        )
    )


class JointConfigureArguments(Model):
    object_name: Name
    joints: list[JointPatch] = Field(min_length=1, max_length=128)
    sample_limit: int = Field(default=16, ge=0, le=128)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({j.name for j in self.joints}) != len(self.joints):
            raise ValueError("Joint names must be unique")
        return self


class JointEvaluation(Model):
    requested_rotation: Vector32 = Field(
        description="Requested rest-relative XYZ Euler channels in radians."
    )
    evaluated_rotation: Vector32 = Field(
        description=(
            "Evaluated rest-relative XYZ radians, after native LOCAL "
            "rotation limits and parent evaluation."
        )
    )
    changed_by_constraint: bool


type StructureField = Literal["rest", "frame", "limits", "pose"]
type StructureSpace = Literal["world", "armature", "parent"]


def structure_fields() -> list[StructureField]:
    return ["rest", "limits"]


class StructureInspectArguments(InspectArguments):
    object_name: Name
    root: Name | None = Field(
        default=None,
        description=(
            "Optional inclusive bone subtree. Names/prefix then filter inside it."
        ),
    )
    limit: int = Field(default=16, ge=1, le=128)
    space: StructureSpace = Field(
        default="world",
        description=(
            "Endpoint/axis output space; parent means parent rest/pose "
            "frame (armature space for roots). Euler angles always use "
            "each bone rest frame."
        ),
    )
    fields: list[StructureField] = Field(
        default_factory=structure_fields, min_length=1, max_length=4
    )

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Structure fields must be unique")
        return self


class StructuralRest(Model):
    head: Vector32
    tail: Vector32
    length: float


class JointFrame(Model):
    center: Vector32 = Field(
        description=(
            "Native bone head is the joint center; no duplicate persistent point."
        )
    )
    x: Vector32
    y: Vector32 = Field(description="Longitudinal head-to-tail bone axis.")
    z: Vector32


class StructuralPose(JointEvaluation):
    head: Vector32
    tail: Vector32


class StructuralSegment(Model):
    name: str
    parent: str | None
    connected: bool
    rest: StructuralRest | None = None
    frame: JointFrame | None = None
    limits: JointLimits | None = None
    pose: StructuralPose | None = None
    joint_constraint: str | None
    constraint_count: int
    valid: bool
    issues: list[str] = Field(max_length=8)


class StructureSummary(Model):
    object_name: str
    bone_count: int
    joint_count: int
    valid: bool = Field(
        description=(
            "Validation of the entire bounded armature, including unsampled bones."
        )
    )
    invalid_bone_count: int
    space: StructureSpace
    segments: list[StructuralSegment]
    page: PageInfo


class BoneRename(Model):
    name: Name
    rename: Name


def orthonormal_axes(
    direction: list[float], reference: list[float]
) -> tuple[list[float], list[float], list[float]]:
    """Right-handed frame: normalize Y, project X reference, derive Z=X cross Y."""

    def normalize(v: list[float]) -> list[float]:
        length = math.hypot(*v)
        if not math.isfinite(length) or length < 1e-8:
            raise ValueError(
                "Frame directions must be finite, nonzero and noncollinear"
            )
        return [x / length for x in v]

    y = normalize(direction)
    ref = normalize(reference)
    dot = sum(a * b for a, b in zip(y, ref, strict=True))
    x = normalize([ref[i] - dot * y[i] for i in range(3)])
    z = [
        x[1] * y[2] - x[2] * y[1],
        x[2] * y[0] - x[0] * y[2],
        x[0] * y[1] - x[1] * y[0],
    ]
    return x, y, z
