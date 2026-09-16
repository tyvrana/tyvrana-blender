"""General vertex-group lifecycle and rest-surface influence transfer."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .corrective_models import all_vertices
from .mesh_models import Arguments, MeshElementSelector, MeshVector
from .models import Model
from .modifier_models import Name
from .numeric import Float32


class GroupLayer(Arguments):
    selector: MeshElementSelector
    weight: Float32 = Field(ge=0, le=1)


class GroupEdit(Arguments):
    name: Name
    create: bool = False
    rename: Name | None = None
    remove: bool = False
    locked: bool | None = None
    layers: list[GroupLayer] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.remove and (
            self.create or self.rename or self.locked is not None or self.layers
        ):
            raise ValueError("Removal cannot include other group edits")
        return self


class GroupsConfigureArguments(Arguments):
    object_name: Name
    groups: list[GroupEdit] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({g.name for g in self.groups}) != len(self.groups):
            raise ValueError("Edit each group once")
        return self


class GroupState(Model):
    name: str
    locked: bool
    weighted_vertices: int


class GroupsResult(Model):
    object_name: str
    changed: list[str]
    groups: list[GroupState]
    total: int


class GroupMapping(Arguments):
    source: Name
    target: Name


class MirrorTransfer(Arguments):
    axis: Literal["x", "y", "z"]
    origin: MeshVector = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        description="Reflection plane origin in world rest coordinates.",
    )


class WeightsTransferArguments(Arguments):
    source: Name
    target: Name
    groups: list[GroupMapping] = Field(min_length=1, max_length=128)
    selector: MeshElementSelector = Field(default_factory=all_vertices)
    max_distance: Float32 = Field(gt=0, le=1000)
    mirror: MirrorTransfer | None = None
    normalize: bool = True
    max_influences: int = Field(default=4, ge=1, le=4)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({g.source for g in self.groups}) != len(self.groups) or len(
            {g.target for g in self.groups}
        ) != len(self.groups):
            raise ValueError("Map each source and target group once")
        if self.source == self.target and self.mirror is None:
            raise ValueError("Same-object transfer requires an explicit reflection")
        return self


class WeightsTransferResult(Model):
    source: str
    target: str
    selected_vertices: int
    groups: int
    max_distance: float
    mean_distance: float
    normalized: bool
    processing_seconds: float
