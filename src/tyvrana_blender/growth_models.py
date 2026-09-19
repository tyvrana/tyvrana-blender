"""Typed semantic surface-rooted growth; no node or property escape hatch."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .deformation_sweep_models import EvaluationPose
from .growth_domain_models import (
    GrowthField,
    GrowthFieldQuery,
    GrowthFieldSample,
    GrowthRow,
    GrowthRowSummary,
)
from .mesh_models import (
    AllSelector,
    BoxSelector,
    IndexSelector,
    NormalSelector,
    RegionSelector,
)
from .models import Model
from .numeric import Float32, Vector32
from .reference_models import Name

type FaceSelector = Annotated[
    AllSelector | IndexSelector | BoxSelector | NormalSelector | RegionSelector,
    Field(discriminator="mode"),
]


class GrowthTemplate(Model):
    object_name: Name = Field(description="Shared static mesh with local Z in [0,1].")
    mode: Literal["instances", "deform"] = "instances"
    width: Float32 = Field(default=1, gt=0, le=100)
    thickness: Float32 = Field(default=1, gt=0, le=100)


class GrowthFamily(Model):
    name: Name
    length: Float32 = Field(default=0.2, gt=0, le=100)
    radius: Float32 = Field(default=0.002, gt=0, le=1)
    tip_radius: Float32 = Field(
        default=0.1, ge=0, le=1, description="Tip radius as fraction of root radius."
    )
    shape: list[Vector32] = Field(
        default_factory=lambda: [[0.0, 0.0, 0.0], [0.2, 0.0, 0.5], [0.4, 0.0, 1.0]],
        min_length=2,
        max_length=32,
        description=(
            "Normalized root-frame points: X projected region flow, Z surface"
            " normal. First point must be zero. Scaled by length."
        ),
    )
    length_variation: Float32 = Field(default=0, ge=0, le=0.9)
    roll: Float32 = Field(
        default=0,
        ge=-100,
        le=100,
        description="Native minimum-twist frame roll in radians.",
    )
    layer: int = Field(default=0, ge=0, le=31)
    offset: Float32 = Field(
        default=0,
        ge=0,
        le=1,
        description="Root offset along the surface normal in surface-local units.",
    )
    material: Name | None = None
    template: GrowthTemplate | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> Self:
        if any(abs(x) > 1e-7 for x in self.shape[0]) or any(
            sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) < 1e-12
            for a, b in zip(self.shape[:-1], self.shape[1:], strict=True)
        ):
            raise ValueError("Shape starts at zero and has distinct consecutive points")
        return self


class GrowthRegion(Model):
    name: Name
    selector: FaceSelector = Field(
        default_factory=lambda: AllSelector(domain="face", mode="all")
    )
    family: Name
    guides: int = Field(default=32, ge=0, le=10000)
    children: int = Field(
        default=0,
        ge=0,
        le=50000,
        description=(
            "Zero outputs the authored guides; otherwise generate exactly "
            "this many interpolated curves in this region."
        ),
    )
    flow: Vector32 = Field(
        default_factory=lambda: [1.0, 0.0, 0.0],
        description=(
            "Surface-local vector projected on each root tangent plane; "
            "singular projection is rejected."
        ),
    )
    field: GrowthField | None = Field(
        default=None,
        description=(
            "UV inverse-distance direction/length field, replacing constant flow."
        ),
    )
    rows: list[GrowthRow] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "Ordered UV root paths within this face domain; requires guides=children=0."
        ),
    )

    @model_validator(mode="after")
    def face_region(self) -> Self:
        if self.selector.domain != "face" or sum(x * x for x in self.flow) < 1e-12:
            raise ValueError("Region requires a face selector and nonzero flow")
        if self.rows:
            if self.guides or self.children:
                raise ValueError("Ordered rows require guides=children=0")
            if len({r.name for r in self.rows}) != len(self.rows):
                raise ValueError("Row names must be unique within a region")
        elif not self.guides:
            raise ValueError("A region requires guides or ordered rows")
        return self


class GrowthCreateArguments(Model):
    name: Name
    surface: Name
    uv_map: Name = "UVMap"
    seed: int = Field(default=0, ge=0, le=2147483647)
    neighbors: int = Field(default=4, ge=1, le=16)
    families: list[GrowthFamily] = Field(min_length=1, max_length=8)
    regions: list[GrowthRegion] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        names = {x.name for x in self.families}
        if len(names) != len(self.families) or len(
            {x.name for x in self.regions}
        ) != len(self.regions):
            raise ValueError("Family and region names must be unique")
        if any(x.family not in names for x in self.regions):
            raise ValueError("Each region references a declared family")
        rows = [r for region in self.regions for r in region.rows]
        if len(rows) > 64 or any(r.family and r.family not in names for r in rows):
            raise ValueError("At most64 rows; row families must be declared")
        ordered = sum((r.count or 0) * (2 if r.mirror else 1) for r in rows)
        guides = sum(x.guides for x in self.regions) + ordered
        total = sum(x.children or x.guides for x in self.regions) + ordered
        if guides > 10000 or total > 50000:
            raise ValueError("System exceeds10000 guides or50000 evaluated curves")
        points = {f.name: len(f.shape) for f in self.families}
        if (
            sum((r.children or r.guides) * points[r.family] for r in self.regions)
            + sum(
                (r.count or 0)
                * (2 if r.mirror else 1)
                * points[r.family or region.family]
                for region in self.regions
                for r in region.rows
            )
            > 800000
        ):
            raise ValueError("System exceeds800000 evaluated curve points")
        return self


class GrowthGuideEdit(Model):
    root_id: int = Field(ge=1)
    points: list[Vector32] | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        description=(
            "Full surface-local rest shape, with first point equal to the "
            "bound root. IDs and UV attachment remain unchanged."
        ),
    )
    length_scale: Float32 | None = Field(default=None, gt=0, le=100)
    family: Name | None = None

    @model_validator(mode="after")
    def changes(self) -> Self:
        if all(x is None for x in [self.points, self.length_scale, self.family]):
            raise ValueError("Guide edit requires a change")
        if self.points is not None and self.length_scale is not None:
            raise ValueError("Choose points or length_scale per edit")
        return self


class GrowthConfigureArguments(Model):
    object_name: Name
    families: list[GrowthFamily] | None = Field(
        default=None, min_length=1, max_length=8
    )
    regions: list[GrowthRegion] | None = Field(
        default=None, min_length=1, max_length=16
    )
    neighbors: int | None = Field(default=None, ge=1, le=16)
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    guides: list[GrowthGuideEdit] = Field(default_factory=list, max_length=256)
    rebind: bool = Field(
        default=False,
        description=(
            "Explicitly regenerate roots/rest guides after invalidation, "
            "changed regions or seed. Such rebindings allocate new root "
            "identities; ordinary style/density edits preserve surviving "
            "roots."
        ),
    )

    @model_validator(mode="after")
    def edits(self) -> Self:
        if len({x.root_id for x in self.guides}) != len(self.guides):
            raise ValueError("Edit each root once")
        if self.guides and self.rebind:
            raise ValueError("Rebind and guide editing must be separate operations")
        if (
            all(
                x is None
                for x in [self.families, self.regions, self.neighbors, self.seed]
            )
            and not self.guides
            and not self.rebind
        ):
            raise ValueError("Configure requires changes")
        return self


class GrowthQA(Model):
    sampled_roots: int = 0
    maximum_root_error: float | None = None
    rms_root_error: float | None = None
    tangent_normal_min: float | None = None
    tangent_normal_max: float | None = None
    tangent_flow_mean: float | None = None
    reversed_guides: int = 0
    frame_flips: int = 0
    template_samples: int = 0
    clearance_samples: int = 0
    below_clearance: int = 0
    penetrating_samples: int = 0
    minimum_signed_clearance: float | None = None
    scope: str = (
        "Sampled guide segments against oriented nearest surface; root "
        "exclusion applies. Not exact global collision certification."
    )


class GrowthSummary(Model):
    object_name: str
    system_id: str
    surface: str
    uv_map: str
    guides: int
    generated_curves: int
    evaluated_curves: int
    evaluated_points: int
    regions: list[str]
    families: list[str]
    templates: list[str]
    node_count: int
    valid: bool
    invalid_roots: int
    warnings: list[str]
    equivalent_template_vertices: int
    instance_count: int
    qa: GrowthQA


class GrowthDelta(Model):
    summary: GrowthSummary
    created_guides: int = 0
    updated_guides: int = 0
    removed_guides: int = 0
    roots_rebound: bool = False
    families_changed: int = 0


class GrowthQAArguments(Model):
    object_name: Name
    qa_samples: int = Field(default=64, ge=0, le=512)
    template_samples: int = Field(
        default=0,
        ge=0,
        le=4096,
        description=(
            "Opt-in template vertex samples from shared prototypes/native paths; "
            "does not realize full template output."
        ),
    )
    clearance_objects: list[Name] = Field(default_factory=list, max_length=8)
    clearance: Float32 = Field(default=0.001, ge=0, le=1)
    root_exclusion: Float32 = Field(
        default=0.1,
        ge=0,
        le=0.5,
        description="Exclude this fraction of each guide's arc length near its root.",
    )


class GrowthInspectArguments(GrowthQAArguments):
    guide_offset: int = Field(default=0, ge=0)
    guide_limit: int = Field(default=0, ge=0, le=32)
    include_points: bool = False
    include_recipe: bool = False
    include_rows: bool = False
    field_samples: list[GrowthFieldQuery] = Field(default_factory=list, max_length=32)


class GrowthGuide(Model):
    root_id: int
    region: str
    family: str
    uv: list[float]
    points: list[list[float]] | None = None
    row: str | None = None
    sequence: int | None = None
    mirrored: bool = False


class GrowthInspectResult(Model):
    summary: GrowthSummary
    guides: list[GrowthGuide]
    next_guide_offset: int | None
    recipe: GrowthCreateArguments | None = None
    rows: list[GrowthRowSummary] = Field(default_factory=list)
    field_samples: list[GrowthFieldSample] = Field(default_factory=list)


class GrowthSampleArguments(GrowthQAArguments):
    armature_object: Name | None = None
    poses: list[EvaluationPose] = Field(default_factory=list, max_length=16)
    frames: list[Float32] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def sweep(self) -> Self:
        if bool(self.poses) == bool(self.frames):
            raise ValueError("Choose poses or frames")
        if any(p.bones for p in self.poses) and self.armature_object is None:
            raise ValueError("Bone poses require armature_object")
        return self


class GrowthSampleRow(Model):
    sample: str
    valid: bool
    qa: GrowthQA


class GrowthSampleResult(Model):
    object_name: str
    restored: bool
    samples: list[GrowthSampleRow]


class GrowthRemoveArguments(Model):
    object_name: Name


class GrowthRemoveResult(Model):
    object_name: str
    removed: bool
