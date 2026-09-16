"""Bounded relative shape authoring and same-topology correction targets."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .deformation_models import RatioDistribution
from .mesh_models import (
    AllSelector,
    Arguments,
    MeshElementSelector,
    MeshVector,
    TransformFalloff,
)
from .models import Model
from .modifier_models import Name
from .numeric import Float32


def all_vertices() -> AllSelector:
    return AllSelector(mode="all", domain="vertex")


class SparseDelta(Model):
    vertex: int = Field(ge=0, le=99999)
    delta: MeshVector


class SparseCorrection(Arguments):
    mode: Literal["sparse"]
    deltas: list[SparseDelta] = Field(min_length=1, max_length=65536)
    space: Literal["local", "world"] = "local"
    operation: Literal["replace", "add"] = "replace"

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({d.vertex for d in self.deltas}) != len(self.deltas):
            raise ValueError("Sparse delta vertices must be unique")
        return self


class RegionCorrection(Arguments):
    mode: Literal["region"]
    selector: MeshElementSelector = Field(default_factory=all_vertices)
    delta: MeshVector
    space: Literal["local", "world"] = "local"
    operation: Literal["replace", "add"] = "replace"
    falloff: TransformFalloff | None = Field(
        default=None,
        description="Smooth ellipsoidal falloff in authored object-local coordinates.",
    )


class CapturedCorrection(Arguments):
    mode: Literal["captured_target"]
    target: Name
    selector: MeshElementSelector = Field(default_factory=all_vertices)
    tolerance: Float32 = Field(default=0.00001, gt=0, le=0.01)


type Correction = Annotated[
    SparseCorrection | RegionCorrection | CapturedCorrection,
    Field(discriminator="mode"),
]


class ShapeKeyEdit(Arguments):
    name: Name
    create: bool = False
    rename: Name | None = None
    relative_to: Name | None = None
    value: Float32 | None = Field(default=None, ge=-10, le=10)
    minimum: Float32 | None = Field(default=None, ge=-10, le=10)
    maximum: Float32 | None = Field(default=None, ge=-10, le=10)
    mute: bool | None = None
    vertex_group: str | None = Field(
        default=None,
        max_length=63,
        description="Empty clears the native mask; otherwise an existing group.",
    )
    correction: Correction | None = None


class ShapeKeysEditArguments(Arguments):
    object_name: Name
    keys: list[ShapeKeyEdit] = Field(min_length=1, max_length=16)
    expected_topology_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({k.name for k in self.keys}) != len(self.keys):
            raise ValueError("Edit each shape key once per batch")
        if (
            sum(isinstance(k.correction, CapturedCorrection) for k in self.keys)
            and len(self.keys) != 1
        ):
            raise ValueError(
                "Capture requires a single-key batch to preserve the explicit baseline"
            )
        return self


class ShapeKeysInspectArguments(Arguments):
    object_name: Name
    names: list[Name] | None = Field(default=None, min_length=1, max_length=32)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16, ge=1, le=32)
    detail_key: Name | None = None
    detail_offset: int = Field(default=0, ge=0)
    detail_limit: int = Field(default=0, ge=0, le=256)


class ShapeKeySummary(Model):
    name: str
    relative_to: str
    value: float
    minimum: float
    maximum: float
    mute: bool
    locked: bool
    vertex_group: str
    affected_vertices: int
    maximum_delta: float
    mean_delta: float
    driven: bool


class ShapeKeysSummary(Model):
    object_name: str
    topology_sha256: str
    vertex_count: int
    relative: bool
    delta_epsilon: float = 1e-7
    basis: str | None
    total: int
    keys: list[ShapeKeySummary]
    truncated: bool
    details: list[SparseDelta]
    detail_total: int
    details_truncated: bool


class ShapeKeysEditResult(Model):
    object_name: str
    topology_sha256: str
    changed: list[str]
    capture_max_error: float | None = None


class ShapeKeysRemoveArguments(Arguments):
    object_name: Name
    names: list[Name] = Field(min_length=1, max_length=32)
    remove_basis: bool = False


class ShapeKeysRemoveResult(Model):
    object_name: str
    removed: list[str]
    remaining: int


class CaptureTargetArguments(Arguments):
    object_name: Name
    name: Name


class CaptureTargetResult(Model):
    source: str
    target: str
    vertex_count: int
    topology_sha256: str
    baseline_sha256: str


class ComparisonPair(Arguments):
    object_name: Name
    target: Name
    selector: MeshElementSelector = Field(
        default_factory=all_vertices,
        description=(
            "Authored selection; comparison requires evaluated ordered "
            "topology identical to the authored mesh."
        ),
    )


class DeformationCompareArguments(Arguments):
    pairs: list[ComparisonPair] = Field(min_length=1, max_length=8)
    sample_limit: int = Field(default=4, ge=0, le=16)


class DeviationSample(Model):
    vertex: int
    distance: float


class TargetDeviation(Model):
    object_name: str
    target: str
    vertex_count: int
    distance: RatioDistribution
    rms: float
    worst: list[DeviationSample]


class DeformationCompareResult(Model):
    comparisons: list[TargetDeviation]
    processing_seconds: float


class ShapeValue(Model):
    object_name: Name
    key: Name
    value: Float32 = Field(ge=-10, le=10)
