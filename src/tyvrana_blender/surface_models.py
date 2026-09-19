"""Bounded source-independent patch networks and sparse semantic revisions."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32, Vector32
from .organization_models import Memberships, Name, scene_root

type Handle = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\w.-]+$")]
type UV = Annotated[
    list[Annotated[Float32, Field(ge=0, le=1)]], Field(min_length=2, max_length=2)
]
type UVRadius = Annotated[
    list[Annotated[Float32, Field(gt=0, le=1)]], Field(min_length=2, max_length=2)
]
type Thickness = Annotated[Float32, Field(gt=0, le=1000)]


class SurfaceNode(Model):
    id: Handle
    point: Vector32 = Field(
        description="Object-local control position, in scene units."
    )


class SurfaceCurve(Model):
    id: Handle
    start: Handle
    end: Handle
    through: list[Vector32] = Field(default_factory=list, max_length=8)
    interpolation: Literal["linear", "catmull_rom"] = "catmull_rom"
    samples: int = Field(default=16, ge=4, le=64)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.start == self.end:
            raise ValueError("Curve endpoints must be distinct node IDs")
        return self


class SurfacePatch(Model):
    id: Handle
    boundaries: list[Handle] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Four shared curve IDs in cyclic order. Orientation is inferred "
            "from endpoints. UV follows corners (0,0), (1,0), (1,1), (0,1)."
        ),
    )
    resolution: int = Field(default=16, ge=4, le=48)
    thickness: list[Thickness] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        description=(
            "Optional thickness at the four cyclic corners; shared boundaries "
            "average incident values."
        ),
    )


class EllipseOpening(Model):
    kind: Literal["ellipse"] = "ellipse"
    id: Handle
    patch: Handle
    center: UV
    radii: UVRadius
    rotation: Float32 = Field(default=0, ge=-100, le=100)
    samples: int = Field(default=24, ge=8, le=64)
    support_width: Float32 = Field(
        default=0.025, ge=0.005, le=0.2, validate_default=True
    )


class PolygonOpening(Model):
    kind: Literal["polygon"]
    id: Handle
    patch: Handle
    points: list[UV] = Field(min_length=3, max_length=16)
    subdivisions: int = Field(default=2, ge=1, le=4)
    support_width: Float32 = Field(
        default=0.025, ge=0.005, le=0.2, validate_default=True
    )


type SurfaceOpening = Annotated[
    EllipseOpening | PolygonOpening, Field(discriminator="kind")
]


class BulgeFeature(Model):
    kind: Literal["bulge"] = "bulge"
    id: Handle
    patch: Handle
    center: UV
    radii: UVRadius
    height: Float32 = Field(ge=-1000, le=1000)
    refinement: int = Field(default=3, ge=1, le=4)


class RidgeFeature(Model):
    kind: Literal["ridge"]
    id: Handle
    patch: Handle
    path: list[UV] = Field(min_length=2, max_length=8)
    width: Float32 = Field(gt=0.005, le=0.5)
    height: Float32 = Field(ge=-1000, le=1000)
    refinement: int = Field(default=3, ge=1, le=4)


class RingFeature(Model):
    kind: Literal["ring"]
    id: Handle
    patch: Handle
    center: UV
    radii: UVRadius
    width: Float32 = Field(gt=0.005, le=0.5)
    height: Float32 = Field(ge=-1000, le=1000)
    refinement: int = Field(default=3, ge=1, le=4)


type SurfaceFeature = Annotated[
    BulgeFeature | RidgeFeature | RingFeature, Field(discriminator="kind")
]


class SurfaceSpec(Model):
    name: Name
    nodes: list[SurfaceNode] = Field(min_length=4, max_length=128)
    curves: list[SurfaceCurve] = Field(min_length=4, max_length=96)
    patches: list[SurfacePatch] = Field(min_length=1, max_length=32)
    openings: list[SurfaceOpening] = Field(default_factory=list, max_length=32)
    features: list[SurfaceFeature] = Field(default_factory=list, max_length=64)
    thickness: Thickness = Field(default=0.05, validate_default=True)
    topology: Literal["triangles", "quad_dominant"] = "quad_dominant"
    smooth: bool = True

    @model_validator(mode="after")
    def network(self) -> Self:
        groups = (self.nodes, self.curves, self.patches, self.openings, self.features)
        ids = [x.id for group in groups for x in group]
        if len(ids) != len(set(ids)):
            raise ValueError("All node/curve/patch/opening/feature IDs must be unique")
        nodes = {x.id: x for x in self.nodes}
        curves = {x.id: x for x in self.curves}
        patches = {x.id for x in self.patches}
        for c in self.curves:
            if c.start not in nodes or c.end not in nodes:
                raise ValueError("Curve references an unknown node")
            points = [nodes[c.start].point, *c.through, nodes[c.end].point]
            if any(
                math.dist(a, b) < 1e-6 for a, b in zip(points, points[1:], strict=False)
            ):
                raise ValueError("Consecutive contour control points must be distinct")
        for p in self.patches:
            if len(set(p.boundaries)) != 4 or any(
                c not in curves for c in p.boundaries
            ):
                raise ValueError("Patch requires four distinct existing curves")
        if any(x.patch not in patches for x in [*self.openings, *self.features]):
            raise ValueError("Opening/feature references an unknown patch")
        if len(self.model_dump_json().encode()) > 131072:
            raise ValueError("Surface constraints exceed128 KiB")
        return self


class SurfaceCreateArguments(Model):
    surfaces: list[SurfaceSpec] = Field(min_length=1, max_length=4)
    collections: Memberships = Field(default_factory=scene_root)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({s.name for s in self.surfaces}) != len(self.surfaces):
            raise ValueError("Surface names must be unique")
        if sum(len(s.patches) for s in self.surfaces) > 64:
            raise ValueError("Batch exceeds 64 patches")
        return self


class SurfaceCurveEdit(Model):
    id: Handle
    through: list[Vector32] | None = Field(default=None, max_length=8)
    samples: int | None = Field(default=None, ge=4, le=64)
    interpolation: Literal["linear", "catmull_rom"] | None = None


class SurfacePatchEdit(Model):
    id: Handle
    thickness: list[Thickness] | None = Field(default=None, min_length=4, max_length=4)
    resolution: int | None = Field(default=None, ge=4, le=48)


class SurfaceRevision(Model):
    name: Name
    expected_revision: int = Field(ge=1)
    nodes: list[SurfaceNode] = Field(default_factory=list, max_length=128)
    curves: list[SurfaceCurveEdit] = Field(default_factory=list, max_length=96)
    patches: list[SurfacePatchEdit] = Field(default_factory=list, max_length=32)
    openings: list[SurfaceOpening] = Field(default_factory=list, max_length=32)
    features: list[SurfaceFeature] = Field(default_factory=list, max_length=64)
    thickness: Thickness | None = None
    topology_policy: Literal["preserve", "rebuild"] = "preserve"

    @model_validator(mode="after")
    def edits(self) -> Self:
        groups = (self.nodes, self.curves, self.patches, self.openings, self.features)
        ids = [x.id for group in groups for x in group]
        if len(ids) != len(set(ids)) or (not ids and self.thickness is None):
            raise ValueError("Provide nonempty revisions with unique IDs")
        return self


class SurfaceConfigureArguments(Model):
    surfaces: list[SurfaceRevision] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({s.name for s in self.surfaces}) != len(self.surfaces):
            raise ValueError("Revision targets must be unique")
        return self


class SurfaceNetworkInspectArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=8)
    include_constraints: bool = False
    region_ids: list[Handle] = Field(default_factory=list, max_length=32)
    region_limit: int = Field(default=12, ge=0, le=64)

    @model_validator(mode="after")
    def detail_bound(self) -> Self:
        if self.include_constraints and len(self.names) != 1:
            raise ValueError("Constraint inspection is limited to one surface")
        return self


class SurfaceRegion(Model):
    id: str
    kind: Literal["patch", "opening", "feature", "junction", "boundary"]
    face_count: int


class SurfaceSummary(Model):
    name: str
    surface_id: str
    revision: int
    topology_revision: int
    topology_changed: bool = False
    valid: bool
    topology: str
    patch_count: int
    opening_count: int
    junction_count: int
    vertex_count: int
    edge_count: int
    face_count: int
    triangle_count: int
    quad_count: int
    boundary_edges: int
    non_manifold_edges: int
    components: int
    max_vertex_valence: int
    bounds_min: Vector32
    bounds_max: Vector32
    thickness_min: float
    thickness_max: float
    regions: list[SurfaceRegion]
    region_count: int
    regions_truncated: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
    constraints: SurfaceSpec | None = None


class SurfaceResult(Model):
    surfaces: list[SurfaceSummary]
    processing_seconds: float
