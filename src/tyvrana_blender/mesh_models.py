"""Explicit object-local selectors and bounded polygonal modeling contracts."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, Strict, field_validator, model_validator

from .models import Model, ObjectName, Vector
from .numeric import Float32

type MeshVector = Annotated[
    list[Annotated[Float32, Strict()]], Field(min_length=3, max_length=3)
]

type Domain = Literal["vertex", "edge", "face"]
type Index = Annotated[int, Field(ge=0)]
type Positive = Annotated[Float32, Field(gt=0)]
type Unit = Annotated[Float32, Field(ge=0, le=1)]

MAX_QUERY_RESULTS = 256
MAX_FACE_VERTICES = 128
MAX_SELECTOR_INDICES = 4096


class Arguments(Model):
    @model_validator(mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("Omit optional properties; null is invalid")
        return value


class AllSelector(Arguments):
    domain: Domain
    mode: Literal["all"]


class IndexSelector(Arguments):
    domain: Domain
    mode: Literal["indices"]
    indices: Annotated[
        list[Index], Field(min_length=1, max_length=MAX_SELECTOR_INDICES)
    ]

    @field_validator("indices")
    @classmethod
    def unique(cls, values: list[int]) -> list[int]:
        if len(set(values)) != len(values):
            raise ValueError("Element indices must be unique")
        return sorted(values)


class BoxSelector(Arguments):
    domain: Literal["vertex", "face"]
    mode: Literal["box"]
    min: MeshVector
    max: MeshVector

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if any(a > b for a, b in zip(self.min, self.max, strict=True)):
            raise ValueError("Box minimum must not exceed maximum")
        return self


class NormalSelector(Arguments):
    domain: Literal["face"]
    mode: Literal["normal"]
    direction: MeshVector
    min_dot: Annotated[Float32, Field(ge=-1, le=1)]

    @field_validator("direction")
    @classmethod
    def nonzero(cls, value: list[float]) -> list[float]:
        if math.hypot(*value) == 0:
            raise ValueError("Normal direction must be nonzero")
        return value


class BoundarySelector(Arguments):
    domain: Literal["edge"]
    mode: Literal["boundary"]


class SeamSelector(Arguments):
    domain: Literal["edge"]
    mode: Literal["seam"]
    value: bool


type MeshElementSelector = Annotated[
    AllSelector
    | IndexSelector
    | BoxSelector
    | NormalSelector
    | BoundarySelector
    | SeamSelector,
    Field(discriminator="mode"),
]


class MeshInspectArguments(Arguments):
    object_name: ObjectName


class MeshSelectionArguments(MeshInspectArguments):
    selector: MeshElementSelector


class MeshQueryArguments(MeshSelectionArguments):
    limit: int = Field(default=64, ge=1, le=MAX_QUERY_RESULTS)


class TransformFalloff(Arguments):
    center: MeshVector
    radii: MeshVector

    @field_validator("radii")
    @classmethod
    def positive_radii(cls, value: list[float]) -> list[float]:
        if any(radius <= 0 for radius in value):
            raise ValueError("Falloff radii must be positive")
        return value


class MeshTransformArguments(MeshSelectionArguments):
    translation: MeshVector | None = None
    rotation: MeshVector | None = None
    scale: MeshVector | None = None
    pivot: Literal["median", "origin"] | MeshVector = "median"
    falloff: TransformFalloff | None = None

    @model_validator(mode="after")
    def needs_transform(self) -> Self:
        if self.translation is None and self.rotation is None and self.scale is None:
            raise ValueError("Supply translation, rotation, or scale")
        if self.falloff is not None and (
            self.translation is None
            or self.rotation is not None
            or self.scale is not None
        ):
            raise ValueError("Falloff supports translation only")
        return self


class FaceArguments(MeshSelectionArguments):
    @field_validator("selector")
    @classmethod
    def face_domain(cls, value: MeshElementSelector) -> MeshElementSelector:
        if value.domain != "face":
            raise ValueError("This operation requires a face selector")
        return value


class EdgeArguments(MeshSelectionArguments):
    @field_validator("selector")
    @classmethod
    def edge_domain(cls, value: MeshElementSelector) -> MeshElementSelector:
        if value.domain != "edge":
            raise ValueError("This operation requires an edge selector")
        return value


class MeshExtrudeArguments(FaceArguments):
    offset: MeshVector
    scale: MeshVector = Field(default_factory=lambda: [1.0, 1.0, 1.0])

    @field_validator("offset")
    @classmethod
    def nonzero_offset(cls, value: list[float]) -> list[float]:
        if not any(value):
            raise ValueError("Extrusion offset must be nonzero")
        return value

    @field_validator("scale")
    @classmethod
    def positive_scale(cls, value: list[float]) -> list[float]:
        if any(component <= 0 for component in value):
            raise ValueError("Extruded cap scale must be positive")
        return value


class MeshInsetArguments(FaceArguments):
    thickness: Positive
    depth: Float32 = 0.0
    even_offset: bool = True


class MeshBevelArguments(EdgeArguments):
    width: Positive
    segments: int = Field(default=1, ge=1, le=16)
    profile: Unit = 0.5


class MeshSubdivideArguments(EdgeArguments):
    cuts: int = Field(default=1, ge=1, le=32)
    smooth: Unit = 0.0


class MeshDeleteArguments(MeshSelectionArguments):
    face_mode: Literal["faces_only", "faces_and_unused"] | None = None

    @model_validator(mode="after")
    def face_setting(self) -> Self:
        if self.face_mode is not None and self.selector.domain != "face":
            raise ValueError("face_mode requires a face selector")
        return self


class MeshMergeArguments(MeshSelectionArguments):
    mode: Literal["center"] = "center"

    @field_validator("selector")
    @classmethod
    def vertex_domain(cls, value: MeshElementSelector) -> MeshElementSelector:
        if value.domain != "vertex":
            raise ValueError("Merge requires a vertex selector")
        return value


class MeshSeamArguments(EdgeArguments):
    seam: bool


class MeshShadingArguments(FaceArguments):
    smooth: bool


class MeshNormalsArguments(MeshInspectArguments):
    inside: bool = False


class ManifoldSummary(Model):
    boundary_edge_count: int
    manifold_edge_count: int
    non_manifold_edge_count: int
    loose_vertex_count: int
    loose_edge_count: int


class MeshSummary(Model):
    object_name: str
    mesh_name: str
    mesh_users: int
    vertex_count: int
    edge_count: int
    face_count: int
    loop_count: int
    bounds_min: Vector | None
    bounds_max: Vector | None
    material_slot_count: int
    uv_map_count: int
    has_shape_keys: bool
    manifold_summary: ManifoldSummary


class VertexDetail(Model):
    index: int
    co: Vector


class EdgeDetail(Model):
    index: int
    vertices: Annotated[list[int], Field(min_length=2, max_length=2)]
    seam: bool
    sharp: bool
    boundary: bool
    manifold: bool


class FaceDetail(Model):
    index: int
    vertices: Annotated[list[int], Field(max_length=MAX_FACE_VERTICES)]
    vertex_count: int
    vertices_truncated: bool
    center: Vector
    normal: Vector
    area: Float32
    material_index: int
    smooth: bool


class QueryBase(Model):
    object_name: str
    matched_count: int
    truncated: bool


class VertexQueryResult(QueryBase):
    domain: Literal["vertex"] = "vertex"
    elements: list[VertexDetail]


class EdgeQueryResult(QueryBase):
    domain: Literal["edge"] = "edge"
    elements: list[EdgeDetail]


class FaceQueryResult(QueryBase):
    domain: Literal["face"] = "face"
    elements: list[FaceDetail]


type MeshQueryResult = VertexQueryResult | EdgeQueryResult | FaceQueryResult


class ElementCounts(Model):
    vertices: int
    edges: int
    faces: int


class ElementSelection(Model):
    domain: Domain
    count: int


class BoundedIndices(Model):
    indices: Annotated[list[int], Field(max_length=MAX_QUERY_RESULTS)]
    total: int
    truncated: bool


class MeshEditResult(Model):
    object_name: str
    selected: ElementSelection
    created: ElementCounts
    removed: ElementCounts
    mesh: MeshSummary
    region_faces: BoundedIndices | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    transformed_vertices: int | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    changed_edges: int | None = Field(default=None, exclude_if=lambda v: v is None)
    changed_faces: int | None = Field(default=None, exclude_if=lambda v: v is None)
