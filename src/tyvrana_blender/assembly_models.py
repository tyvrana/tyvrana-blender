"""Sparse mixed geometry families with bounded variation and stable members."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .form_models import FormPart, FormSpec
from .loft_models import LoftEnds, LoftSectionEdit, LoftSpec
from .models import Model
from .numeric import Float32, Vector32
from .organization_models import Memberships, Name, scene_root
from .placement_models import PlacementRule
from .surface_models import Handle, SurfaceNode, SurfaceOpening, SurfaceSpec, Thickness

type Scale = Annotated[
    list[Annotated[Float32, Field(gt=0.00001, le=10000)]],
    Field(min_length=3, max_length=3),
]


def one() -> list[float]:
    return [1.0, 1.0, 1.0]


def zero() -> list[float]:
    return [0.0, 0.0, 0.0]


class LoftTemplate(Model):
    kind: Literal["loft"]
    id: Handle
    spec: LoftSpec


class SurfaceTemplate(Model):
    kind: Literal["surface"]
    id: Handle
    spec: SurfaceSpec


class FormTemplate(Model):
    kind: Literal["form"]
    id: Handle
    spec: FormSpec


type AssemblyTemplate = Annotated[
    LoftTemplate | SurfaceTemplate | FormTemplate, Field(discriminator="kind")
]


class FeatureStrength(Model):
    id: Handle
    height: Float32 = Field(ge=-1000, le=1000)


class ShapeOverride(Model):
    form_parts: list[FormPart] = Field(default_factory=list, max_length=32)
    sections: list[LoftSectionEdit] = Field(default_factory=list, max_length=32)
    features: list[FeatureStrength] = Field(default_factory=list, max_length=32)
    nodes: list[SurfaceNode] = Field(default_factory=list, max_length=32)
    openings: list[SurfaceOpening] = Field(default_factory=list, max_length=8)
    thickness: Thickness | None = None
    ends: LoftEnds | None = None

    @model_validator(mode="after")
    def handles(self) -> Self:
        for values in (
            self.sections,
            self.features,
            self.nodes,
            self.openings,
            self.form_parts,
        ):
            if len({v.id for v in values}) != len(values):
                raise ValueError("Override handles must be unique within each kind")
        return self


class FamilyMemberOverride(Model):
    index: int = Field(ge=0, le=63)
    shape: ShapeOverride = Field(default_factory=ShapeOverride)
    scale: Scale | None = None
    offset: Vector32 = Field(default_factory=zero)
    rotation: Vector32 = Field(default_factory=zero)
    placement: PlacementRule | None = None


class FamilyMorph(Model):
    position: Float32 = Field(ge=0, le=1)
    template: Handle


class AssemblyFamily(Model):
    id: Handle
    template: Handle | None = None
    morphs: list[FamilyMorph] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Interpolate corresponding numeric shape handles through related "
            "templates along the family. Keep construction kind, handles and "
            "discrete topology settings compatible; member overrides apply "
            "after interpolation."
        ),
    )
    mirror_of: Handle | None = None
    count: int = Field(
        default=1, ge=1, le=64, description="Mirrors inherit source count when omitted."
    )
    path: list[Vector32] = Field(
        default_factory=lambda: [[0.0, 0.0, 0.0]], min_length=1, max_length=32
    )
    align_path: bool = False
    up: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    scale_start: Scale = Field(default_factory=one)
    scale_end: Scale = Field(default_factory=one)
    rotation_start: Vector32 = Field(default_factory=zero)
    rotation_end: Vector32 = Field(default_factory=zero)
    feature_scale: list[Annotated[Float32, Field(ge=0, le=10)]] = Field(
        default_factory=lambda: [1.0, 1.0], min_length=2, max_length=2
    )
    mirror_point: Vector32 = Field(default_factory=zero)
    mirror_normal: Vector32 = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    overrides: list[FamilyMemberOverride] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def members(self) -> Self:
        if (self.template is None) == (self.mirror_of is None):
            raise ValueError("A family requires exactly one template or mirror_of")
        positions = [v.position for v in self.morphs]
        if positions != sorted(set(positions)) or (self.mirror_of and self.morphs):
            raise ValueError(
                "Morph positions must increase; mirrors inherit source morphology"
            )
        indices = [v.index for v in self.overrides]
        unresolved_mirror = self.mirror_of and "count" not in self.model_fields_set
        if len(set(indices)) != len(indices) or (
            not unresolved_mirror and any(i >= self.count for i in indices)
        ):
            raise ValueError("Member override indices must be unique and in range")
        if self.count > 1 and not self.mirror_of and len(self.path) < 2:
            raise ValueError("Repeated families require at least two path points")
        if math.hypot(*self.mirror_normal) < 1e-8:
            raise ValueError("Mirror plane normal must be nonzero")
        if self.mirror_of and (
            self.path != [[0, 0, 0]]
            or self.align_path
            or self.scale_start != one()
            or self.scale_end != one()
            or self.rotation_start != zero()
            or self.rotation_end != zero()
        ):
            raise ValueError(
                "Mirrored families inherit source layout; use member overrides "
                "for placement/scale/rotation"
            )
        return self


class AssemblySpec(Model):
    max_vertices: int = Field(default=131072, ge=1024, le=1048576)
    name: Name
    templates: list[AssemblyTemplate] = Field(min_length=1, max_length=16)
    families: list[AssemblyFamily] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        templates = {t.id for t in self.templates}
        families = {f.id: f for f in self.families}
        if len(templates) != len(self.templates) or len(families) != len(self.families):
            raise ValueError("Template and family IDs must be unique")
        resolved: dict[str, int] = {}
        visiting: set[str] = set()

        def resolve(f: AssemblyFamily) -> int:
            if f.id in resolved:
                return resolved[f.id]
            if f.id in visiting:
                raise ValueError(f"Mirror dependency cycle at family {f.id}")
            visiting.add(f.id)
            count = f.count
            if f.mirror_of:
                source = families.get(f.mirror_of)
                if source is None:
                    raise ValueError(
                        f"Family {f.id}: unknown mirror source {f.mirror_of}"
                    )
                source_count = resolve(source)
                if "count" not in f.model_fields_set:
                    count = source_count
                elif count != source_count:
                    raise ValueError(
                        f"Family {f.id}: mirror count must match source "
                        f"{source.id} ({source_count})"
                    )
            if any(o.index >= count for o in f.overrides):
                raise ValueError(f"Family {f.id}: member override index exceeds count")
            visiting.remove(f.id)
            resolved[f.id] = count
            return count

        for f in self.families:
            if f.template and f.template not in templates:
                raise ValueError("Family references an unknown template")
            if any(m.template not in templates for m in f.morphs):
                raise ValueError("Morph references an unknown template")
            resolve(f)
        object.__setattr__(
            self,
            "families",
            [f.model_copy(update={"count": resolved[f.id]}) for f in self.families],
        )
        if sum(f.count for f in self.families) > 64:
            raise ValueError(
                "Assembly exceeds64 components; split independent subsystems"
            )
        if len(self.model_dump_json().encode()) > 262144:
            raise ValueError("Assembly constraints exceed256 KiB")
        return self


class AssemblyCreateArguments(AssemblySpec):
    collections: Memberships = Field(default_factory=scene_root)


class AssemblyMemberEdit(FamilyMemberOverride):
    family: Handle


class AssemblyFamilyEdit(AssemblyFamily):
    """Only supplied fields replace the stored family; validate after composition."""

    @model_validator(mode="after")
    def members(self) -> Self:
        return self


class AssemblyConfigureArguments(Model):
    max_vertices: int | None = Field(default=None, ge=1024, le=1048576)
    name: Name
    expected_revision: int = Field(ge=1)
    templates: list[AssemblyTemplate] = Field(default_factory=list, max_length=16)
    families: list[AssemblyFamilyEdit] = Field(
        default_factory=list,
        max_length=32,
        description="Patch selected family fields by ID; omitted settings and "
        "exceptions survive.",
    )
    members: list[AssemblyMemberEdit] = Field(default_factory=list, max_length=64)
    refresh_placements: bool = False
    topology_policy: Literal["preserve", "rebuild"] = "preserve"

    @model_validator(mode="after")
    def changes(self) -> Self:
        if not (
            self.templates or self.families or self.members or self.refresh_placements
        ):
            raise ValueError(
                "Supply template/family/member changes or refresh_placements"
            )
        for values in (self.templates, self.families):
            if len({v.id for v in values}) != len(values):
                raise ValueError("Revision IDs must be unique")
        if len({(m.family, m.index) for m in self.members}) != len(self.members):
            raise ValueError("Member changes must be unique")
        return self


class AssemblyInspectArguments(Model):
    name: Name
    families: list[Handle] = Field(default_factory=list, max_length=32)
    offset: int = Field(default=0, ge=0, le=64)
    limit: int = Field(default=16, ge=0, le=64)
    include_spec: bool = False


class AssemblyComponent(Model):
    name: str
    member_id: str
    resource_id: str
    family: str
    index: int
    kind: Literal["loft", "surface", "form"]
    revision: int
    vertex_count: int
    face_count: int
    dimensions: Vector32
    bounds_min: Vector32
    bounds_max: Vector32
    region_ids: list[str]
    region_count: int
    valid: bool
    mirrored_from: str | None = None


class AssemblyResult(Model):
    name: str
    assembly_id: str
    revision: int
    component_count: int
    family_count: int
    vertex_count: int
    face_count: int
    valid: bool
    changed_members: list[str]
    components: list[AssemblyComponent]
    components_truncated: bool = False
    next_offset: int | None = None
    spec: AssemblySpec | None = None
