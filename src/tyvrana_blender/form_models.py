"""Finite constructive forms, calibrated profiles and named local features."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tyvrana_protocol import ProtocolError

from .loft_models import LoftSpec
from .models import Model
from .numeric import Float32, Vector32
from .organization_models import Memberships, Name, scene_root
from .surface_models import SurfaceSpec


class ReferenceMask(Model):
    reference: Name
    channel: Literal["alpha", "luminance"] = Field(
        default="alpha",
        description=(
            "Alpha selects coverage; luminance requires foreground contrast in RGB."
        ),
    )
    threshold: Float32 = Field(default=0.5, ge=0, le=1)
    invert: bool = False


class FormPartBase(Model):
    id: Name
    operation: Literal["add", "subtract", "intersect"] = "add"
    blend: Float32 = Field(
        default=0,
        ge=0,
        le=1000,
        description=(
            "Local smooth Boolean radius in object units; approximate distance "
            "fields, not exact curvature continuity."
        ),
    )


class EllipsoidPart(FormPartBase):
    kind: Literal["ellipsoid"]
    center: Vector32
    radii: Annotated[
        list[Annotated[Float32, Field(gt=0, le=10000)]],
        Field(min_length=3, max_length=3),
    ]
    rotation: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class LoftPart(FormPartBase):
    kind: Literal["loft"]
    spec: LoftSpec


class SurfacePart(FormPartBase):
    kind: Literal["surface"]
    spec: SurfaceSpec


class SectionMaskPart(FormPartBase):
    kind: Literal["sections"]
    masks: list[ReferenceMask] = Field(
        min_length=2,
        max_length=24,
        description=(
            "Parallel registered planar cross-sections in one frame, ordered "
            "along their common normal. Interpolate signed contours between "
            "observed sections; end sections cap the volume."
        ),
    )


class SilhouettePart(FormPartBase):
    kind: Literal["silhouettes"]
    masks: list[ReferenceMask] = Field(
        min_length=2,
        max_length=12,
        description=(
            "Registered orthographic foregrounds constrain the visual hull. "
            "Hidden concavities require sections or local subtraction; "
            "perspective sources are not metric constraints."
        ),
    )
    bounds_min: Vector32
    bounds_max: Vector32

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if any(a >= b for a, b in zip(self.bounds_min, self.bounds_max, strict=True)):
            raise ValueError("Silhouette bounds must have positive extent")
        return self


type FormPart = Annotated[
    EllipsoidPart | LoftPart | SurfacePart | SectionMaskPart | SilhouettePart,
    Field(discriminator="kind"),
]


class ReferenceSurfaceFit(Model):
    masks: list[ReferenceMask] = Field(
        min_length=2,
        max_length=96,
        description="Registered planar cuts that constrain local surface shape. "
        "Use complementary observations around required features; this fit "
        "moves an existing surface and cannot create missing openings.",
    )
    radius: Float32 = Field(
        gt=0,
        le=1000,
        description="Neighborhood radius in the construction frame. Larger "
        "neighborhoods suppress sampling waves but may round narrow features. "
        "Compare closeups and measured profiles after fitting.",
    )
    max_distance: Float32 = Field(
        gt=0,
        le=1000,
        description="Maximum total displacement from the constructed surface.",
    )
    iterations: int = Field(default=3, ge=1, le=6)
    adaptive_neighborhood: bool = Field(
        default=False,
        description="Narrow the fitting neighborhood near changing surface "
        "normals to preserve folds while smoothing flatter regions. Uses more "
        "local observations and may leave sparse regions unsupported; inspect "
        "the fit summary and close views.",
    )

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len({m.reference for m in self.masks}) != len(self.masks):
            raise ValueError("Surface fitting references must be distinct")
        return self


class FormSpec(Model):
    name: Name
    parts: list[FormPart] = Field(min_length=1, max_length=32)
    voxel_size: Float32 = Field(
        gt=0,
        le=1000,
        description=(
            "Object-space sampling size; use several voxels across the "
            "smallest required feature."
        ),
    )
    adaptivity: Float32 = Field(default=0, ge=0, le=0.3)
    smoothing_passes: int = Field(
        default=1,
        ge=0,
        le=8,
        description=(
            "Bounded separable volume smoothing before meshing; each pass "
            "has standard deviation 0.707 voxel. Reduce sampling ripples, "
            "then compare thin features and dimensions; zero preserves raw fields."
        ),
    )
    max_voxels: int = Field(default=2097152, ge=4096, le=8388608)
    max_vertices: int = Field(default=131072, ge=64, le=524288)
    surface_fit: ReferenceSurfaceFit | None = None

    @model_validator(mode="after")
    def handles(self) -> Self:
        if self.parts[0].operation != "add":
            raise ValueError("First form part must add material")
        if len({p.id for p in self.parts}) != len(self.parts):
            raise ValueError("Form part IDs must be unique")
        if self.surface_fit and self.surface_fit.radius < self.voxel_size:
            raise ValueError("Surface fitting radius must be at least one voxel")
        if len(self.model_dump_json().encode()) > 262144:
            raise ValueError("Form intent exceeds256 KiB")
        return self


class FormCreateArguments(Model):
    forms: list[FormSpec] = Field(min_length=1, max_length=32)
    collections: Memberships = Field(default_factory=scene_root)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({f.name for f in self.forms}) != len(self.forms):
            raise ValueError("Form names must be unique")
        if sum(f.max_voxels * len(f.parts) for f in self.forms) > 268435456:
            raise ValueError("Form batch exceeds268435456 voxel-part evaluations")
        return self


class FormEdit(Model):
    name: Name
    expected_revision: int = Field(ge=1)
    parts: list[FormPart] = Field(
        default_factory=list,
        max_length=32,
        description=(
            "Replace or add named semantic parts; unchanged parts remain stored."
        ),
    )
    remove_parts: list[Name] = Field(default_factory=list, max_length=31)
    voxel_size: Float32 | None = Field(default=None, gt=0, le=1000)
    adaptivity: Float32 | None = Field(default=None, ge=0, le=0.3)
    smoothing_passes: int | None = Field(default=None, ge=0, le=8)
    max_voxels: int | None = Field(default=None, ge=4096, le=8388608)
    max_vertices: int | None = Field(default=None, ge=64, le=524288)
    surface_fit: ReferenceSurfaceFit | None = Field(
        default=None,
        description="Omit to retain fitting; null removes it during regeneration.",
    )

    @model_validator(mode="after")
    def changes(self) -> Self:
        if (
            not self.parts
            and not self.remove_parts
            and not self.model_fields_set - {"name", "expected_revision"}
        ):
            raise ValueError("Supply a form change")
        if len({p.id for p in self.parts}) != len(self.parts) or len(
            set(self.remove_parts)
        ) != len(self.remove_parts):
            raise ValueError("Part IDs must be unique")
        if {p.id for p in self.parts} & set(self.remove_parts):
            raise ValueError("Cannot replace and remove the same part")
        return self


class FormConfigureArguments(Model):
    forms: list[FormEdit] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({f.name for f in self.forms}) != len(self.forms):
            raise ValueError("Form names must be unique")
        return self


class FormInspectArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=32)
    include_spec: bool = False


class SurfaceFitSummary(Model):
    reference_points: int
    iterations: int
    unsupported_vertices: int
    displacement_limited_vertices: int
    maximum_displacement: float
    unsupported_samples: list[Vector32] = Field(default_factory=list, max_length=8)


class FormSummary(Model):
    name: str
    component_id: str
    revision: int
    vertex_count: int
    face_count: int
    voxel_size: float
    sampled_voxels: int
    bounds_min: Vector32
    bounds_max: Vector32
    parts: list[str]
    source_count: int
    valid: bool = Field(
        description="Mesh identity and source freshness; not a visual fidelity verdict."
    )
    issues: list[str]
    surface_fit: SurfaceFitSummary | None = None
    spec: FormSpec | None = None


class FormResult(Model):
    forms: list[FormSummary]
    processing_seconds: float


class FormJobArguments(Model):
    job_id: str = Field(min_length=32, max_length=32, pattern="^[a-f0-9]+$")


class FormJobStatus(Model):
    job_id: str
    state: Literal["queued", "running", "completed", "failed", "cancelled"]
    prepared_forms: int = 0
    total_forms: int
    elapsed_seconds: float = 0
    result: FormResult | None = None
    error: ProtocolError | None = None


class ReferenceShapeQuery(Model):
    id: Name
    objects: list[Name] = Field(min_length=1, max_length=32)
    mask: ReferenceMask

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.objects)) != len(self.objects):
            raise ValueError("Comparison objects must be unique")
        return self


class ReferenceCompareArguments(Model):
    comparisons: list[ReferenceShapeQuery] = Field(min_length=1, max_length=24)
    resolution: int = Field(default=256, ge=64, le=512)
    overlay: bool = True
    worst_limit: int = Field(default=4, ge=0, le=16)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({q.id for q in self.comparisons}) != len(self.comparisons):
            raise ValueError("Comparison IDs must be unique")
        if sum(len(q.objects) for q in self.comparisons) * self.resolution**2 > 8388608:
            raise ValueError("Reference comparison exceeds8388608 ray samples")
        if len({n for q in self.comparisons for n in q.objects}) > 64:
            raise ValueError("Compare at most64 distinct evaluated meshes per call")
        return self


class ReferenceBoundaryError(Model):
    pixel: list[float]
    world: Vector32
    deviation: float
    side: Literal["reference", "authored"]


class ReferenceShapeResult(Model):
    id: str
    reference: str
    projection: Literal["plane", "orthographic"]
    objects: list[str]
    overlap_iou: float
    disagreement_fraction: float
    boundary_mean: float | None
    boundary_p95: float | None
    boundary_max: float | None
    sampling_uncertainty: float
    worst: list[ReferenceBoundaryError]
    issues: list[str]
    source_sha256: str


class ReferenceCompareResult(Model):
    comparisons: list[ReferenceShapeResult]
    processing_seconds: float
    calibration: Literal["declared"] = "declared"
    overlay_legend: str = (
        "Gray: agreement; green: missing material; magenta: excess "
        "material. Distances use registered scene units; uncertainty "
        "excludes unprovided source/calibration error. Perspective images "
        "require qualitative visual review."
    )
