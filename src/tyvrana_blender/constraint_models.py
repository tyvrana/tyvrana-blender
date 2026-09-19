"""Owned native constraints, transform matching and explicit space changes."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .keying_models import MatchKeying
from .models import Model
from .numeric import Float32, Vector32
from .reference_models import Name

type Space = Literal["WORLD", "LOCAL", "POSE", "LOCAL_WITH_PARENT"]
type Unit = Annotated[Float32, Field(ge=0, le=1)]


class RigEndpoint(Model):
    object_name: Name
    bone: Name | None = None


class CopyTransform(Model):
    kind: Literal["copy_transforms"]
    target: RigEndpoint
    owner_space: Space = "WORLD"
    target_space: Space = "WORLD"
    mix: Literal["REPLACE", "BEFORE_FULL", "AFTER_FULL"] = "REPLACE"


def all_axes() -> list[Literal["x", "y", "z"]]:
    return ["x", "y", "z"]


class CopyRotation(Model):
    kind: Literal["copy_rotation"]
    target: RigEndpoint
    owner_space: Space = "WORLD"
    target_space: Space = "WORLD"
    axes: list[Literal["x", "y", "z"]] = Field(
        default_factory=all_axes, min_length=1, max_length=3
    )
    mix: Literal["REPLACE", "ADD", "BEFORE", "AFTER"] = "REPLACE"


class CopyLocation(Model):
    kind: Literal["copy_location"]
    target: RigEndpoint
    owner_space: Space = "WORLD"
    target_space: Space = "WORLD"
    axes: list[Literal["x", "y", "z"]] = Field(
        default_factory=all_axes, min_length=1, max_length=3
    )
    offset: bool = False


class ConstraintRange(Model):
    minimum: Float32 = Field(ge=-1000000, le=1000000)
    maximum: Float32 = Field(ge=-1000000, le=1000000)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Minimum exceeds maximum")
        return self


class LimitLocation(Model):
    kind: Literal["limit_location"]
    owner_space: Space = "LOCAL"
    x: ConstraintRange | None = None
    y: ConstraintRange | None = None
    z: ConstraintRange | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.x is None and self.y is None and self.z is None:
            raise ValueError("Enable at least one location axis")
        return self


class Track(Model):
    kind: Literal["damped_track"]
    target: RigEndpoint
    axis: Literal["X", "Y", "Z", "NEGATIVE_X", "NEGATIVE_Y", "NEGATIVE_Z"] = "Y"


class ChildOf(Model):
    kind: Literal["child_of"]
    target: RigEndpoint
    maintain_transform: bool = True


class Floor(Model):
    kind: Literal["floor"]
    target: RigEndpoint
    axis: Literal["X", "Y", "Z", "NEGATIVE_X", "NEGATIVE_Y", "NEGATIVE_Z"] = "Z"
    offset: Float32 = Field(default=0, ge=-10000, le=10000)
    use_rotation: bool = True


class IK(Model):
    kind: Literal["ik"]
    target: RigEndpoint
    pole: RigEndpoint | None = None
    pole_angle: Float32 = Field(default=0, ge=-math.pi, le=math.pi)
    chain_length: int = Field(ge=1, le=16)
    iterations: int = Field(default=64, ge=1, le=256)
    use_rotation: bool = False
    use_stretch: bool = False


type ConstraintSettings = Annotated[
    CopyTransform
    | CopyRotation
    | CopyLocation
    | LimitLocation
    | Track
    | ChildOf
    | Floor
    | IK,
    Field(discriminator="kind"),
]


class ConstraintSpec(Model):
    name: Name
    owner: RigEndpoint
    settings: ConstraintSettings
    influence: Unit = 1


class ConstraintsConfigureArguments(Model):
    constraints: list[ConstraintSpec] = Field(min_length=1, max_length=64)
    replace: bool = False

    @model_validator(mode="after")
    def unique(self) -> Self:
        keys = [(c.owner.object_name, c.owner.bone, c.name) for c in self.constraints]
        if len(keys) != len(set(keys)):
            raise ValueError("Constraint owner/name must be unique")
        return self


class ConstraintRef(Model):
    owner: RigEndpoint
    name: Name


class ConstraintsInspectArguments(Model):
    owners: list[RigEndpoint] = Field(min_length=1, max_length=16)
    include_definitions: bool = False


class ConstraintsRemoveArguments(Model):
    constraints: list[ConstraintRef] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        keys = [(c.owner.object_name, c.owner.bone, c.name) for c in self.constraints]
        if len(keys) != len(set(keys)):
            raise ValueError("Constraint owner/name must be unique")
        return self


class ConstraintSummary(Model):
    name: str
    owner: RigEndpoint
    type: str
    influence: float
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=4)
    definition: ConstraintSpec | None = None
    evaluated_location: Vector32
    evaluated_rotation: list[float] = Field(min_length=4, max_length=4)


class ConstraintsResult(Model):
    constraints: list[ConstraintSummary]
    processing_seconds: float


class TransformMatch(Model):
    source: RigEndpoint
    target: RigEndpoint


class PoseMatchArguments(Model):
    matches: list[TransformMatch] = Field(min_length=1, max_length=64)
    tolerance: Float32 = Field(default=0.0001, gt=0, le=0.01)
    keying: MatchKeying | None = None

    @model_validator(mode="after")
    def unique_targets(self) -> Self:
        keys = [(c.target.object_name, c.target.bone) for c in self.matches]
        if len(keys) != len(set(keys)):
            raise ValueError("Match targets must be unique")
        return self


class PoseMatchResult(Model):
    matched: int
    maximum_matrix_error: float
    processing_seconds: float


class SpaceSwitchArguments(Model):
    owner: RigEndpoint
    constraint: Name
    target: RigEndpoint
    maintain_transform: bool = True
    tolerance: Float32 = Field(default=0.0001, gt=0, le=0.01)
    keying: MatchKeying | None = None
