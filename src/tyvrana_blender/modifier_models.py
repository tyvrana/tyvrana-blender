"""Typed object-owned modifier stacks and inspection-only evaluated geometry."""

from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    Field,
    Strict,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .mesh_models import Arguments, ManifoldSummary, MeshSummary
from .models import Model, ObjectName, Vector
from .numeric import Float32


def resource_name(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Resource names must not contain NUL characters")
    value.encode("utf-8")
    return value


type Name = Annotated[ObjectName, AfterValidator(resource_name)]

type Number = Annotated[Float32, Strict()]
type Axes = Annotated[list[Literal["x", "y", "z"]], Field(max_length=3)]
type ModifierType = Literal[
    "mirror", "subdivision_surface", "shrinkwrap", "boolean", "solidify", "triangulate"
]
type SubdivisionMode = Literal["catmull_clark", "simple"]
type UVSmooth = Literal[
    "none",
    "preserve_corners",
    "preserve_corners_and_junctions",
    "preserve_corners_junctions_and_concave",
    "preserve_boundaries",
    "smooth_all",
]
type BoundarySmooth = Literal["all", "preserve_corners"]
type WrapMethod = Literal["nearest_surface", "project", "target_normal_project"]
type WrapMode = Literal[
    "on_surface", "inside", "outside", "outside_surface", "above_surface"
]
type CullFace = Literal["off", "front", "back"]
type BooleanOperation = Literal["union", "intersect", "difference"]
type BooleanSolver = Literal["float", "exact", "manifold"]

MAX_SUBDIVISION_LEVEL = 6
MAX_MODIFIERS = 128


class MirrorPatch(Arguments):
    uv_flip_u: bool | None = None
    uv_flip_v: bool | None = None
    uv_flip_per_tile: bool | None = None
    uv_flip_offset_u: Annotated[Number, Field(ge=-10, le=10)] | None = None
    uv_flip_offset_v: Annotated[Number, Field(ge=-10, le=10)] | None = None
    uv_offset_u: Annotated[Number, Field(ge=-10, le=10)] | None = None
    uv_offset_v: Annotated[Number, Field(ge=-10, le=10)] | None = None
    axes: Axes | None = None
    bisect_axes: Axes | None = None
    bisect_flip_axes: Axes | None = None
    clipping: bool | None = None
    merge: bool | None = None
    merge_threshold: Annotated[Number, Field(ge=0)] | None = None
    mirror_object: Name | None = None
    clear_mirror_object: Literal[True] | None = None

    @field_validator("clear_mirror_object", mode="before")
    @classmethod
    def clear_is_boolean(cls, value: object) -> object:
        if value is not True:
            raise ValueError("clear_mirror_object must be the boolean true")
        return value

    @field_validator("axes", "bisect_axes", "bisect_flip_axes")
    @classmethod
    def unique_axes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Axes must be unique")
        return value

    @model_validator(mode="after")
    def valid_mirror(self) -> Self:
        if self.axes == []:
            raise ValueError("At least one mirror axis is required")
        if self.mirror_object is not None and self.clear_mirror_object is not None:
            raise ValueError("Set or clear mirror_object, not both")
        return self


class SubdivisionPatch(Arguments):
    mode: SubdivisionMode | None = None
    levels: Annotated[int, Field(ge=0, le=MAX_SUBDIVISION_LEVEL)] | None = None
    render_levels: Annotated[int, Field(ge=0, le=MAX_SUBDIVISION_LEVEL)] | None = None
    uv_smooth: UVSmooth | None = None
    boundary_smooth: BoundarySmooth | None = None
    use_creases: bool | None = None
    show_only_control_edges: bool | None = None


class ProjectionPatch(Arguments):
    axes: Axes | None = None
    positive: bool | None = None
    negative: bool | None = None
    cull_face: CullFace | None = None
    invert_cull: bool | None = None
    limit: Annotated[Number, Field(ge=0)] | None = None

    @field_validator("axes")
    @classmethod
    def unique_axes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Axes must be unique; empty projects along normals")
        return value


class ShrinkwrapPatch(Arguments):
    target: Name | None = None
    method: WrapMethod | None = None
    mode: WrapMode | None = None
    offset: Number | None = None
    projection: ProjectionPatch | None = None


class BooleanPatch(Arguments):
    operand_object: Name | None = None
    operation: BooleanOperation | None = None
    solver: BooleanSolver | None = None


class SolidifyPatch(Arguments):
    thickness: Number | None = None
    offset: Number | None = None
    even_thickness: bool | None = None
    rim: bool | None = None
    rim_only: bool | None = None
    quality_normals: bool | None = None


class TriangulatePatch(Arguments):
    quad_method: (
        Literal[
            "beauty",
            "fixed",
            "fixed_alternate",
            "shortest_diagonal",
            "longest_diagonal",
        ]
        | None
    ) = None
    ngon_method: Literal["beauty", "clip"] | None = None
    min_vertices: Annotated[int, Field(ge=4, le=128)] | None = None
    keep_custom_normals: bool | None = None


class ModifierInspectArguments(Arguments):
    object_name: Name


class EvaluatedMeshArguments(Arguments):
    object_name: Name
    uv_map: Name | None = None


class ModifierNamedArguments(Arguments):
    object_name: Name
    modifier_name: Name


class ModifierMoveArguments(ModifierNamedArguments):
    index: int = Field(ge=0)


class ModifierRemoveArguments(ModifierNamedArguments):
    pass


class ModifierApplyArguments(ModifierNamedArguments):
    pass


class ModifierCommonArguments(Arguments):
    object_name: Name
    enabled_viewport: bool | None = None
    enabled_render: bool | None = None
    show_in_editmode: bool | None = None


class CreateBase(ModifierCommonArguments):
    name: Name | None = None


class ConfigureBase(ModifierCommonArguments):
    modifier_name: Name


class MirrorCreate(CreateBase):
    type: Literal["mirror"]
    settings: MirrorPatch = Field(default_factory=MirrorPatch)


class SubdivisionCreate(CreateBase):
    type: Literal["subdivision_surface"]
    settings: SubdivisionPatch = Field(default_factory=SubdivisionPatch)


class ShrinkwrapCreate(CreateBase):
    type: Literal["shrinkwrap"]
    settings: ShrinkwrapPatch

    @model_validator(mode="after")
    def require_target(self) -> Self:
        if self.settings.target is None:
            raise ValueError("Shrinkwrap creation requires settings.target")
        return self


class BooleanCreate(CreateBase):
    type: Literal["boolean"]
    settings: BooleanPatch

    @model_validator(mode="after")
    def require_operand(self) -> Self:
        if self.settings.operand_object is None:
            raise ValueError("Boolean creation requires settings.operand_object")
        return self


class SolidifyCreate(CreateBase):
    type: Literal["solidify"]
    settings: SolidifyPatch = Field(default_factory=SolidifyPatch)


class TriangulateCreate(CreateBase):
    type: Literal["triangulate"]
    settings: TriangulatePatch = Field(default_factory=TriangulatePatch)


class TriangulateConfigure(ConfigureBase):
    type: Literal["triangulate"]
    settings: TriangulatePatch = Field(default_factory=TriangulatePatch)


class MirrorConfigure(ConfigureBase):
    type: Literal["mirror"]
    settings: MirrorPatch = Field(default_factory=MirrorPatch)


class SubdivisionConfigure(ConfigureBase):
    type: Literal["subdivision_surface"]
    settings: SubdivisionPatch = Field(default_factory=SubdivisionPatch)


class ShrinkwrapConfigure(ConfigureBase):
    type: Literal["shrinkwrap"]
    settings: ShrinkwrapPatch = Field(default_factory=ShrinkwrapPatch)


class BooleanConfigure(ConfigureBase):
    type: Literal["boolean"]
    settings: BooleanPatch = Field(default_factory=BooleanPatch)


class SolidifyConfigure(ConfigureBase):
    type: Literal["solidify"]
    settings: SolidifyPatch = Field(default_factory=SolidifyPatch)


type ModifierCreateArguments = Annotated[
    MirrorCreate
    | SubdivisionCreate
    | ShrinkwrapCreate
    | BooleanCreate
    | SolidifyCreate
    | TriangulateCreate,
    Field(discriminator="type"),
]
type ModifierConfigureArguments = Annotated[
    MirrorConfigure
    | SubdivisionConfigure
    | ShrinkwrapConfigure
    | BooleanConfigure
    | SolidifyConfigure
    | TriangulateConfigure,
    Field(discriminator="type"),
]
CREATE: TypeAdapter[ModifierCreateArguments] = TypeAdapter(ModifierCreateArguments)
CONFIGURE: TypeAdapter[ModifierConfigureArguments] = TypeAdapter(
    ModifierConfigureArguments
)


# Snapshots describe native state, including values outside input safety limits and
# incomplete modifiers made in Blender. Inspection must not "repair" that state.
class MirrorSettings(Model):
    uv_flip_u: bool
    uv_flip_v: bool
    uv_flip_per_tile: bool
    uv_flip_offset_u: Number
    uv_flip_offset_v: Number
    uv_offset_u: Number
    uv_offset_v: Number
    axes: Axes
    bisect_axes: Axes
    bisect_flip_axes: Axes
    clipping: bool
    merge: bool
    merge_threshold: Number
    mirror_object: str | None


class SubdivisionSettings(Model):
    mode: SubdivisionMode
    levels: int
    render_levels: int
    uv_smooth: UVSmooth
    boundary_smooth: BoundarySmooth
    use_creases: bool
    show_only_control_edges: bool


class ProjectionSettings(Model):
    axes: Axes
    positive: bool
    negative: bool
    cull_face: CullFace
    invert_cull: bool
    limit: Number


class ShrinkwrapSettings(Model):
    target: str | None
    method: str
    mode: WrapMode
    offset: Number
    projection: ProjectionSettings | None


class BooleanSettings(Model):
    operand_object: str | None
    operand_type: Literal["object", "collection"]
    operation: BooleanOperation
    solver: BooleanSolver


class SolidifySettings(Model):
    mode: Literal["simple", "complex"]
    thickness: Number
    offset: Number
    even_thickness: bool
    rim: bool
    rim_only: bool
    quality_normals: bool


class TriangulateSettings(Model):
    quad_method: str
    ngon_method: str
    min_vertices: int
    keep_custom_normals: bool


type ModifierSettings = (
    MirrorSettings
    | SubdivisionSettings
    | ShrinkwrapSettings
    | BooleanSettings
    | SolidifySettings
    | TriangulateSettings
)


class ModifierSummary(Model):
    name: str
    index: int
    type: str
    supported: bool
    enabled_viewport: bool
    enabled_render: bool
    show_in_editmode: bool
    show_on_cage: bool | None
    settings: ModifierSettings | None


class ModifierInspectResult(Model):
    object_name: str
    modifiers: list[ModifierSummary]


class ModifierRemoveResult(ModifierInspectResult):
    removed: str


class ModifierApplyResult(ModifierInspectResult):
    applied: str
    mesh: MeshSummary


class ModifierStackEntry(Model):
    name: str
    type: str


class TangentRepeatability(Model):
    repeated_sha256: str
    maximum_component_delta: float
    changed_corner_count: int
    handedness_change_count: int


class MeshSurfaceBasis(Model):
    geometry_sha256: str
    shading_flags_sha256: str
    corner_normals_sha256: str
    uv_map: str | None
    uv_sha256: str | None
    tangents_sha256: str | None
    tangent_repeatability: TangentRepeatability | None
    smooth_face_count: int
    sharp_edge_count: int
    seam_edge_count: int
    has_custom_normals: bool
    creased_edge_count: int
    creased_vertex_count: int
    negative_bitangent_count: int | None
    zero_tangent_count: int | None


class EvaluatedMeshSummary(Model):
    object_name: str
    source_mesh_name: str
    evaluation: Literal["viewport"] = "viewport"
    vertex_count: int
    edge_count: int
    face_count: int
    loop_count: int
    bounds_min: Vector | None
    bounds_max: Vector | None
    manifold_summary: ManifoldSummary
    modifier_count: int
    modifier_stack: list[ModifierStackEntry]
    authored_basis: MeshSurfaceBasis
    evaluated_basis: MeshSurfaceBasis
    viewport_render_settings_differences: list[str]
