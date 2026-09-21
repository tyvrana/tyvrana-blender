"""Bounded geometric fitting and regional unilateral contact evidence."""

from typing import Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from .mesh_models import AllSelector, MeshElementSelector
from .models import Model
from .numeric import Vector32
from .reference_models import Name, PointSource


class MechanicsRegion(Model):
    object_name: Name
    selector: MeshElementSelector = Field(
        default_factory=lambda: AllSelector(mode="all", domain="vertex")
    )
    feature: Name | None = Field(
        default=None,
        description=(
            "Named authored loft/surface feature; requires unchanged evaluated "
            "topology."
        ),
    )

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if self.feature is not None and "selector" in self.model_fields_set:
            raise ValueError("Choose a named feature or a mesh selector")
        return self


class EvidenceDirection(Model):
    start: PointSource
    end: PointSource


class FitQuery(Model):
    name: Name
    method: Literal["sphere", "circle", "cylinder", "plane", "landmarks"]
    regions: list[MechanicsRegion] = Field(default_factory=list, max_length=8)
    points: list[PointSource] = Field(default_factory=list, max_length=64)
    axis: EvidenceDirection | None = None
    secondary: EvidenceDirection | None = None
    sample_limit: int = Field(default=2048, ge=4, le=8192)
    tolerance: FiniteFloat = Field(default=0.001, gt=0, le=1000)
    maximum_condition: FiniteFloat = Field(default=10000, ge=2, le=1000000)
    frame_length: FiniteFloat = Field(default=1, gt=0.000001, le=10000)
    expected_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def evidence(self) -> Self:
        if not self.regions and not self.points:
            raise ValueError("Provide bounded geometry and/or point evidence")
        if self.method == "landmarks" and self.axis is None:
            raise ValueError("Landmark frames require a directed axis")
        return self


class FitArguments(Model):
    fits: list[FitQuery] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({f.name for f in self.fits}) != len(self.fits):
            raise ValueError("Fit names must be unique")
        return self


class FitFrame(Model):
    head: Vector32
    tail: Vector32
    x_reference: Vector32


class GeometryEvidence(Model):
    object_name: str
    resource_id: str | None
    feature: str | None
    selection_sha256: str
    geometry_sha256: str


class FitSummary(Model):
    name: str
    method: str
    status: Literal["FITTED", "UNCERTAIN", "STALE"]
    center: Vector32 | None = None
    axis: Vector32 | None = None
    radius: float | None = None
    frame: FitFrame | None = None
    orientation: Literal["evidence", "conventional_roll", "unconstrained"]
    rms_error: float | None = None
    maximum_error: float | None = None
    condition: float | None = None
    support_count: int
    sample_count: int
    source_sha256: str
    sources: list[str]
    provenance: list[GeometryEvidence] = Field(default_factory=list, max_length=8)
    fresh: bool
    reasons: list[str] = Field(max_length=8)


class FitResult(Model):
    fits: list[FitSummary]
    evaluated_vertices: int
    processing_seconds: float


class ContactEnvelope(Model):
    name: Name
    source: MechanicsRegion
    target: MechanicsRegion
    mode: Literal["closed_solid", "oriented_patch"] = "closed_solid"
    allowed_side: Literal["positive", "negative"] = "positive"
    minimum_gap: FiniteFloat = Field(default=-0.00001, le=0, ge=-1000)
    maximum_gap: FiniteFloat = Field(default=0.001, ge=0, le=1000)
    boundary_margin: FiniteFloat = Field(default=0.00001, gt=0, le=1000)
    minimum_alignment: FiniteFloat = Field(default=0.8, ge=0.5, le=1)
    sample_limit: int = Field(default=256, ge=1, le=2048)
    expected_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.source.object_name == self.target.object_name:
            raise ValueError("An interface requires two distinct objects")
        return self


type ContactState = Literal[
    "SEPARATED", "PERMITTED_CONTACT", "INVALID_PENETRATION", "UNCERTAIN"
]


class ContactFinding(Model):
    source_vertex: int
    target_triangle: int | None
    point: Vector32
    signed_gap: float | None
    reason: str | None = None


class EnvelopeSummary(Model):
    provenance: list[GeometryEvidence] = Field(default_factory=list, max_length=2)
    name: str
    classification: ContactState
    source: str
    target: str
    source_sha256: str
    fresh: bool
    metric: str
    selected_vertices: int
    sampled_vertices: int
    permitted_samples: int
    separated_samples: int
    penetrating_samples: int
    uncertain_samples: int
    minimum_gap: float | None
    maximum_gap: float | None
    minimum_separation: float | None
    penetration_max: float | None
    contact_fraction: float
    worst: list[ContactFinding]
    reasons: list[str] = Field(max_length=8)


class ContactArguments(Model):
    envelopes: list[ContactEnvelope] = Field(min_length=1, max_length=8)
    worst_limit: int = Field(default=4, ge=0, le=16)
    max_tests: int = Field(default=200000, ge=1, le=2000000)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({e.name for e in self.envelopes}) != len(self.envelopes):
            raise ValueError("Envelope names must be unique")
        return self


class ContactResult(Model):
    envelopes: list[EnvelopeSummary]
    evaluated_vertices: int
    tests: int
    processing_seconds: float
    limitations: list[str]


class EnvelopeAggregate(Model):
    name: str
    counts: dict[ContactState, int]
    worst_classification: ContactState
    worst_frame: int
    minimum_gap: float | None
    maximum_gap: float | None
    penetration_max: float | None
    uncertain_samples: int
    failed_samples: int
