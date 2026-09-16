"""Bounded native curve, profile and attachment contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import InspectArguments, Model, PageInfo
from .numeric import Float32, Vector32
from .organization_models import Memberships, Name, Tag, Tags, scene_root

type SplineType = Literal["POLY", "BEZIER", "NURBS"]


class CurvePoint(Model):
    co: Vector32
    radius: Float32 = Field(default=1, ge=0, le=1000)
    tilt: Float32 = Field(
        default=0, ge=-1000, le=1000, description="Radians about the tangent."
    )
    weight: Float32 = Field(default=1, gt=0, le=1000)
    handle_type: Literal["AUTO", "VECTOR", "FREE", "ALIGNED"] = "AUTO"
    left: Vector32 | None = None
    right: Vector32 | None = None

    @model_validator(mode="after")
    def handles(self) -> Self:
        if self.handle_type in {"FREE", "ALIGNED"}:
            if self.left is None or self.right is None:
                raise ValueError(
                    "FREE/ALIGNED handles require both left and right coordinates"
                )
        elif self.left is not None or self.right is not None:
            raise ValueError("AUTO/VECTOR handles are computed by Blender")
        return self


class SplineSpec(Model):
    type: SplineType | None = Field(
        default=None,
        description="Omit to use shared spline_type; resolved to a native spline.",
    )
    points: list[CurvePoint] = Field(min_length=2, max_length=256)
    cyclic: bool = False
    order: int = Field(default=3, ge=2, le=6)
    endpoint: bool = True

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.cyclic and len(self.points) < 3:
            raise ValueError("Cyclic splines need at least three points")
        for a, b in zip(self.points, self.points[1:], strict=False):
            if sum((x - y) ** 2 for x, y in zip(a.co, b.co, strict=True)) < 1e-12:
                raise ValueError("Consecutive curve points must be distinct")
        if (
            self.cyclic
            and sum(
                (a - b) ** 2
                for a, b in zip(self.points[0].co, self.points[-1].co, strict=True)
            )
            < 1e-12
        ):
            raise ValueError(
                "Cyclic splines close implicitly; do not repeat the first point"
            )
        if self.type == "NURBS" and len(self.points) < self.order:
            raise ValueError("NURBS point count must be at least its order")
        if self.type in {"POLY", "NURBS"} and any(
            p.handle_type != "AUTO" or p.left is not None or p.right is not None
            for p in self.points
        ):
            raise ValueError("Handles apply only to BEZIER splines")
        return self


class NoProfile(Model):
    kind: Literal["none"] = "none"


class CircleProfile(Model):
    kind: Literal["circle"]
    radius: Float32 = Field(gt=0, le=1000)
    resolution: int = Field(default=8, ge=3, le=64)
    caps: bool = True


class ObjectProfile(Model):
    kind: Literal["object"]
    object: Name
    scale: Float32 = Field(default=1, gt=0, le=1000)
    caps: bool = True


type Profile = Annotated[
    NoProfile | CircleProfile | ObjectProfile, Field(discriminator="kind")
]


class CurveSettings(Model):
    spline_type: SplineType = "BEZIER"
    resolution: int = Field(default=12, ge=1, le=64)
    profile: Profile = Field(default_factory=NoProfile)
    material: Name | None = None


class CurveSettingsPatch(Model):
    spline_type: SplineType | None = None
    resolution: int | None = Field(default=None, ge=1, le=64)
    profile: Profile | None = None
    material: Name | None = None

    @model_validator(mode="after")
    def nulls(self) -> Self:
        for key in self.model_fields_set - {"material"}:
            if getattr(self, key) is None:
                raise ValueError(
                    f"{key} cannot be null; use profile kind none to clear a sweep"
                )
        return self


class ObjectAnchor(Model):
    kind: Literal["object"]
    object: Name


class BoneAnchor(Model):
    kind: Literal["bone"]
    object: Name
    bone: Name


class SurfaceAnchor(Model):
    kind: Literal["surface"]
    object: Name
    face: int = Field(
        ge=0,
        le=499999,
        description=(
            "Authored triangular polygon index; evaluated connectivity must "
            "match authored topology."
        ),
    )
    barycentric: Vector32 = Field(
        description=(
            "Nonnegative weights for the three authored polygon vertices, "
            "summing to one."
        )
    )

    @model_validator(mode="after")
    def weights(self) -> Self:
        if (
            min(self.barycentric) < 0
            or max(self.barycentric) > 1
            or abs(sum(self.barycentric) - 1) > 1e-6
        ):
            raise ValueError("Barycentric weights must be in [0,1] and sum to one")
        return self


type Anchor = Annotated[
    ObjectAnchor | BoneAnchor | SurfaceAnchor, Field(discriminator="kind")
]


class CurveBinding(Model):
    spline: int = Field(ge=0, le=7)
    point: int = Field(default=0, ge=0, le=255)
    target: Anchor
    offset: Vector32 = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        description=(
            "Target-local object/bone coordinates, or triangle-frame "
            "coordinates (X=edge 0→1, Z=face normal, Y=Z cross X), in target "
            "local units."
        ),
    )
    follow: Literal["point", "spline"] = Field(
        default="point",
        description=(
            "point moves one control point; spline rigidly follows the target"
            " frame with this point snapped to offset. Unbound points retain "
            "authored coordinates."
        ),
    )


class CurveSpec(Model):
    name: Name
    splines: list[SplineSpec] = Field(min_length=1, max_length=8)
    settings: CurveSettingsPatch = Field(default_factory=CurveSettingsPatch)
    bindings: list[CurveBinding] = Field(default_factory=list, max_length=32)
    space: Literal["local", "world"] = "local"
    location: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Vector32 = Field(
        default_factory=lambda: [0.0, 0.0, 0.0], description="XYZ Euler radians."
    )
    scale: Float32 = Field(
        default=1, gt=0, le=1000, description="Positive uniform object scale."
    )
    collections: Memberships | None = None
    role: Tag | None = None
    tags: Tags = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if sum(len(s.points) for s in self.splines) > 1024:
            raise ValueError("A curve accepts at most 1024 authored points")
        covered = set()
        for b in self.bindings:
            if b.spline >= len(self.splines) or b.point >= len(
                self.splines[b.spline].points
            ):
                raise ValueError("Attachment references a missing spline or point")
            indices = (
                range(len(self.splines[b.spline].points))
                if b.follow == "spline"
                else [b.point]
            )
            for i in indices:
                if (b.spline, i) in covered:
                    raise ValueError("Attachments may not overlap control points")
                covered.add((b.spline, i))
        return self


class CurveCreateArguments(Model):
    curves: list[CurveSpec] = Field(min_length=1, max_length=64)
    defaults: CurveSettings = Field(default_factory=CurveSettings)
    collections: Memberships = Field(default_factory=scene_root)
    role: Tag | None = None
    tags: Tags = Field(default_factory=list)
    sample_limit: int = Field(default=8, ge=0, le=64)

    @model_validator(mode="after")
    def budget(self) -> Self:
        if len({c.name for c in self.curves}) != len(self.curves):
            raise ValueError("Curve names must be unique")
        if sum(len(s.points) for c in self.curves for s in c.splines) > 4096:
            raise ValueError("Curve batch exceeds 4096 authored points")
        if sum(len(c.bindings) for c in self.curves) > 128:
            raise ValueError("Curve batch exceeds 128 attachments")
        return self


class PointRange(Model):
    spline: int = Field(ge=0, le=7)
    start: int = Field(ge=0, le=255)
    points: list[CurvePoint] = Field(
        min_length=1,
        max_length=256,
        description=(
            "Full point replacements at a contiguous range; topology unchanged."
        ),
    )


class CurveEdit(Model):
    name: Name
    splines: list[SplineSpec] | None = Field(
        default=None,
        min_length=1,
        max_length=8,
        description=(
            "Full spline-list replacement supports adding/removing/reordering splines."
        ),
    )
    ranges: list[PointRange] = Field(default_factory=list, max_length=32)
    settings: CurveSettingsPatch = Field(default_factory=CurveSettingsPatch)
    bindings: list[CurveBinding] | None = Field(
        default=None,
        max_length=32,
        description=(
            "Full replacement/rebind; [] detaches. Omit to preserve existing "
            "anchors and bind frames."
        ),
    )

    @model_validator(mode="after")
    def changes(self) -> Self:
        if self.splines is not None and self.ranges:
            raise ValueError("Choose full splines or point ranges")
        if (
            self.splines is None
            and not self.ranges
            and not self.settings.model_fields_set
            and self.bindings is None
        ):
            raise ValueError("Provide a curve edit")
        return self


class CurveConfigureArguments(Model):
    curves: list[CurveEdit] = Field(min_length=1, max_length=64)
    sample_limit: int = Field(default=8, ge=0, le=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({c.name for c in self.curves}) != len(self.curves):
            raise ValueError("Curve edit names must be unique")
        return self


class CurveInspectArguments(InspectArguments):
    collection: Name | None = None
    limit: int = Field(default=8, ge=1, le=64)
    space: Literal["local", "world"] = "world"
    spline: int | None = Field(default=None, ge=0, le=7)
    point_offset: int = Field(default=0, ge=0, le=255)
    point_limit: int = Field(
        default=0,
        ge=0,
        le=64,
        description=(
            "Authored local-space point rows; sample/bounds space "
            "is selected separately."
        ),
    )
    samples: int = Field(
        default=0,
        ge=0,
        le=64,
        description=(
            "Per-spline samples equally spaced by evaluated arc length, "
            "including open endpoints. Zero omits sample rows; summary still "
            "evaluates length/bounds."
        ),
    )

    @model_validator(mode="after")
    def sample_count(self) -> Self:
        if self.samples == 1:
            raise ValueError("Use zero samples to omit detail, or 2..64 samples")
        return self


class CurveSample(Model):
    factor: float
    position: Vector32
    tangent: Vector32
    normal: Vector32
    binormal: Vector32
    radius: float
    tilt: float


class SplineSummary(Model):
    index: int
    type: SplineType
    point_count: int
    cyclic: bool
    length: float
    start: Vector32
    end: Vector32
    radius_range: list[float]
    tilt_range: list[float]
    points: list[CurvePoint]
    points_truncated: bool
    samples: list[CurveSample]


class BindingSummary(Model):
    spline: int
    point: int
    follow: Literal["point", "spline"]
    target: Anchor
    offset: Vector32
    valid: bool
    issue: str | None
    intended_world: Vector32 | None
    evaluated_world: Vector32 | None
    error: float | None


class CurveSummary(Model):
    name: str
    spline_count: int
    point_count: int
    profile: Profile
    material: str | None
    space: Literal["local", "world"]
    length: float | None
    minimum: Vector32 | None
    maximum: Vector32 | None
    evaluated_vertices: int | None
    evaluated_faces: int | None
    valid: bool
    issues: list[str] = Field(max_length=8)
    splines: list[SplineSummary]
    bindings: list[BindingSummary]


class CurveResult(Model):
    curve_count: int
    point_count: int
    curves: list[CurveSummary]
    truncated: bool


class CurveInspectResult(Model):
    curves: list[CurveSummary]
    page: PageInfo


class CurveRemoveArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.names)) != len(self.names):
            raise ValueError("Removal names must be unique")
        return self


class CurveRemoveResult(Model):
    removed: list[str]
