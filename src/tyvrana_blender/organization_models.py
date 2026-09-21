"""Bounded native collection and object-set authoring contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .models import InspectArguments, Model, PageInfo
from .numeric import Vector32

type Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]
type Tag = Annotated[
    str, Field(min_length=1, max_length=48, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
]
type Tags = Annotated[list[Tag], Field(max_length=16)]
type Memberships = Annotated[list[Name | None], Field(min_length=1, max_length=16)]


def unique(values: list[object], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def scene_root() -> list[str | None]:
    return [None]


class CollectionSpec(Model):
    name: Name
    parents: Memberships = Field(
        default_factory=scene_root,
        description=(
            "Exact parent collections; null is the scene root. Batch "
            "names may be forward references."
        ),
    )
    hide_viewport: bool = False
    hide_render: bool = False
    hide_select: bool = False

    @field_validator("parents")
    @classmethod
    def distinct(cls, value: list[str | None]) -> list[str | None]:
        unique(list(value), "Parents")
        return value


class CollectionCreateArguments(Model):
    collections: list[CollectionSpec] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique([x.name for x in self.collections], "Collection names")
        return self


class CollectionPatch(Model):
    name: Name
    rename: Name | None = None
    parents: Memberships | None = None
    hide_viewport: bool | None = None
    hide_render: bool | None = None
    hide_select: bool | None = None

    @model_validator(mode="after")
    def patch(self) -> Self:
        fields = self.model_fields_set - {"name"}
        if not fields or any(getattr(self, f) is None for f in fields):
            raise ValueError("Supply at least one non-null change")
        if self.parents is not None:
            unique(list(self.parents), "Parents")
        return self


class CollectionConfigureArguments(Model):
    collections: list[CollectionPatch] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique([x.name for x in self.collections], "Collection names")
        return self


class CollectionInspectArguments(InspectArguments):
    root: Name | None = Field(
        default=None,
        description=(
            "Null selects all current-scene collections; the master "
            "root itself is not a datablock row."
        ),
    )
    recursive: bool = True


class CollectionSummary(Model):
    name: str
    parents: list[str | None]
    parent_count: int
    parents_truncated: bool
    children: list[str]
    child_count: int
    children_truncated: bool
    object_count: int
    recursive_object_count: int
    hide_viewport: bool
    hide_render: bool
    hide_select: bool
    editable: bool


class CollectionResult(Model):
    collections: list[CollectionSummary]


class CollectionInspectResult(CollectionResult):
    page: PageInfo


class CollectionRemoveArguments(Model):
    name: Name
    mode: Literal["empty", "rehome"] = "empty"
    target: Name | None = Field(
        default=None,
        description=(
            "For rehome: null is the scene root; moves direct objects "
            "and child collections without deleting them."
        ),
    )

    @model_validator(mode="after")
    def target_mode(self) -> Self:
        if self.mode == "empty" and self.target is not None:
            raise ValueError("A target requires rehome mode")
        return self


class LocalRef(Model):
    key: Name


class ExistingRef(Model):
    name: Name


type ObjectRef = LocalRef | ExistingRef


class Member(Model):
    key: Name = Field(
        description=(
            "Unique request-local reference, returned with the actual "
            "object name. It is not a persistent identity."
        )
    )
    name: Name
    parent: ObjectRef | None = None
    collections: Memberships | None = Field(
        default=None,
        description=(
            "Exact memberships, overriding the batch default; null uses "
            "the batch default."
        ),
    )
    location: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Vector32 = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        description="XYZ Euler radians, relative to parent.",
    )
    scale: Vector32 = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    role: Tag | None = None
    tags: Tags = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(list(self.tags), "Tags")
        if self.collections is not None:
            unique(list(self.collections), "Collections")
        if any(
            abs(v) > 1e6
            for vector in (self.location, self.rotation, self.scale)
            for v in vector
        ):
            raise ValueError("Transform components must be within +/-1e6")
        if any(abs(v) < 1e-6 for v in self.scale):
            raise ValueError("Scale components must have magnitude at least 1e-6")
        return self


class PrimitiveMember(Member):
    kind: Literal["primitive"]
    primitive: Literal["cube", "plane", "uv_sphere", "cylinder"]
    material: Name | None = None


class EmptyMember(Member):
    kind: Literal["empty"]
    display_size: float = Field(default=1.0, ge=0.0001, le=1000, allow_inf_nan=False)


class CopyMember(Member):
    kind: Literal["copy"]
    source: ObjectRef
    data: Literal["linked", "independent"] = Field(
        default="linked",
        description=(
            "Mesh/light/camera datablock ownership; object transforms "
            "are always independent."
        ),
    )
    materials: Literal["shared", "independent"] = Field(
        default="shared",
        description=(
            "Independent copies material datablocks and their root "
            "shader graphs; nested groups/images remain shared. "
            "Requires independent mesh data."
        ),
    )

    @model_validator(mode="after")
    def ownership(self) -> Self:
        if self.materials == "independent" and self.data != "independent":
            raise ValueError("Independent materials require independent data")
        return self


type MemberSpec = Annotated[
    PrimitiveMember | EmptyMember | CopyMember, Field(discriminator="kind")
]


class ObjectSetCreateArguments(Model):
    collections: Memberships = Field(default_factory=scene_root)
    objects: list[MemberSpec] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(list(self.collections), "Collections")
        unique([x.key for x in self.objects], "Keys")
        unique([x.name for x in self.objects], "Object names")
        keys = {x.key for x in self.objects}
        for item in self.objects:
            refs = [item.parent]
            if isinstance(item, CopyMember):
                refs.append(item.source)
            if any(isinstance(ref, LocalRef) and ref.key not in keys for ref in refs):
                raise ValueError("Unknown request-local object key")
        return self


class CreatedObject(Model):
    key: str
    name: str
    type: str
    parent: str | None


class ObjectSetCreateResult(Model):
    objects: list[CreatedObject]


class ObjectPatch(Model):
    name: Name
    rename: Name | None = None
    parent: Name | None = Field(
        default=None,
        description=(
            "Omit to preserve; null clears the parent. Always preserves "
            "world transform."
        ),
    )
    collections: Memberships | None = Field(
        default=None,
        description=(
            "Replace exact memberships; link/unlink/move by editing "
            "this bounded set. Null entries mean scene root."
        ),
    )
    hide_viewport: bool | None = None
    hide_render: bool | None = None
    hide_select: bool | None = None
    role: Tag | None = Field(
        default=None, description="Omit to preserve; null clears the role."
    )
    tags: Tags | None = Field(
        default=None, description="Omit to preserve; an empty list clears tags."
    )

    @model_validator(mode="after")
    def patch(self) -> Self:
        fields = self.model_fields_set - {"name"}
        if not fields:
            raise ValueError("Supply at least one change")
        for f in (
            "rename",
            "collections",
            "tags",
            "hide_viewport",
            "hide_render",
            "hide_select",
        ):
            if f in fields and getattr(self, f) is None:
                raise ValueError(f"Omit unchanged {f}; null is not valid")
        if self.collections is not None:
            unique(list(self.collections), "Collections")
        if self.tags is not None:
            unique(list(self.tags), "Tags")
        return self


class ObjectSetConfigureArguments(Model):
    objects: list[ObjectPatch] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique([x.name for x in self.objects], "Object names")
        return self


class ObjectChange(Model):
    name: str
    previous_name: str | None = Field(default=None, exclude_if=lambda v: v is None)
    fields: list[str]


class ObjectSetConfigureResult(Model):
    matched_count: int
    changed_count: int
    unchanged_count: int
    skipped_count: int = 0
    issue_count: int = 0
    changes: list[ObjectChange] = Field(max_length=64)


type InspectField = Literal[
    "hierarchy",
    "memberships",
    "transforms",
    "metadata",
    "data",
    "visibility",
    "placement",
]


def default_fields() -> list[InspectField]:
    return ["hierarchy", "memberships", "metadata", "data"]


class ObjectSetInspectArguments(InspectArguments):
    collection: Name | None = None
    recursive: bool = True
    role: Tag | None = None
    tags: Tags = Field(default_factory=list, description="Match all these tags.")
    fields: list[InspectField] = Field(
        default_factory=default_fields,
        min_length=1,
        max_length=7,
    )

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(list(self.tags), "Tags")
        unique(list(self.fields), "Fields")
        return self


class HierarchyInfo(Model):
    parent: str | None
    parent_type: str
    children: list[str]
    child_count: int
    children_truncated: bool


class MembershipInfo(Model):
    collections: list[str | None]
    count: int
    truncated: bool


class TransformInfo(Model):
    world_matrix: list[Annotated[list[float], Field(min_length=4, max_length=4)]] = (
        Field(min_length=4, max_length=4)
    )
    location: Vector32
    rotation: Vector32
    scale: Vector32


class MetadataInfo(Model):
    role: str | None
    tags: list[str]
    valid: bool


class DataInfo(Model):
    name: str | None
    users: int
    library_linked: bool
    materials: list[str | None]
    material_count: int
    materials_truncated: bool


class VisibilityInfo(Model):
    hide_viewport: bool
    hide_render: bool
    hide_select: bool
    visible_in_view_layer: bool


class SetObjectSummary(Model):
    placement: "PlacementInfo | None" = None
    visibility: VisibilityInfo | None = None
    name: str
    type: str
    hierarchy: HierarchyInfo | None = None
    memberships: MembershipInfo | None = None
    transforms: TransformInfo | None = None
    metadata: MetadataInfo | None = None
    data: DataInfo | None = None


class PlacementInfo(Model):
    revision: int
    valid: bool
    dimensions: Vector32


class ObjectSetResult(Model):
    objects: list[SetObjectSummary]


class ObjectSetInspectResult(ObjectSetResult):
    page: PageInfo


class ObjectSetRemoveArguments(Model):
    names: list[Name] = Field(
        min_length=1,
        max_length=256,
        description="Explicit objects or complete assembly roots. Roots include their "
        "owned groups/members; expanded removal is limited to 256 objects.",
    )
    children: Literal["reject", "unparent"] = Field(
        default="reject",
        description=(
            "External children either block deletion or are unparented "
            "with world transforms preserved. Other external references "
            "always block deletion."
        ),
    )

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(list(self.names), "Names")
        return self


class OrganizationRemoveResult(Model):
    deleted: list[str]
    remaining: list[str]
    error: str | None = Field(
        default=None,
        description=(
            "A native deletion failure reports exact partial progress; "
            "no rollback is claimed for deleted IDs."
        ),
    )
    retained_data: Literal[True] = True


class ObjectSetRemoveResult(OrganizationRemoveResult):
    requested_count: int
    expanded_count: int
    removed_count: int
    skipped_count: int = 0
    blocked_count: int = 0
    issue_count: int = 0
