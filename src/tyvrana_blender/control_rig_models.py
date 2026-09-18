"""Explicit two-segment FK/IK networks over existing native bones and controls."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .constraint_models import RigEndpoint
from .models import Model
from .numeric import Float32
from .reference_models import Name


class ControlRigConfigureArguments(Model):
    object_name: Name
    name: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    fk: list[Name] = Field(min_length=2, max_length=2)
    ik: list[Name] = Field(min_length=2, max_length=2)
    deform: list[Name] = Field(min_length=2, max_length=2)
    target: RigEndpoint
    pole: RigEndpoint
    pole_angle: Float32 = Field(default=0, ge=-3.14159265, le=3.14159265)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.fk + self.ik + self.deform)) != 6:
            raise ValueError("Use six distinct FK, IK and deform bones")
        return self


class ControlRigInspectArguments(Model):
    object_name: Name
    name: Name


class ControlRigSwitchArguments(ControlRigInspectArguments):
    mode: Literal["FK", "IK"]
    match: bool = True
    tolerance: Float32 = Field(default=0.001, gt=0, le=0.01)


class ControlRigResult(Model):
    name: str
    object_name: str
    mode: Literal["FK", "IK"]
    definition: ControlRigConfigureArguments
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
    maximum_output_error: float
    maximum_match_error: float | None = None
    processing_seconds: float
