"""Bounded shader graph summaries and typed auxiliary-node edits."""

import math
from typing import Annotated, Any, Literal, Self

from pydantic import Field, FiniteFloat, field_validator, model_validator
from tyvrana_protocol import JsonValue

from .material_author_models import RampStop
from .models import InspectArguments, Model, ObjectName, PageInfo
from .numeric import Float32, Vector32

type NodeType = Literal[
    "image_texture", "texture_coordinate", "mapping", "normal_map", "bump"
]
type Interpolation = Literal["linear", "closest", "cubic", "smart"]
type Projection = Literal["flat", "box", "sphere", "tube"]
type Extension = Literal["repeat", "extend", "clip", "mirror"]
type VectorType = Literal["point", "texture", "vector", "normal"]
type NormalSpace = Literal["tangent", "object", "world"]
type SocketDefault = (
    bool | int | FiniteFloat | Annotated[list[FiniteFloat], Field(max_length=4)] | None
)

NODE_TYPES = {
    "image_texture": "ShaderNodeTexImage",
    "texture_coordinate": "ShaderNodeTexCoord",
    "mapping": "ShaderNodeMapping",
    "normal_map": "ShaderNodeNormalMap",
    "bump": "ShaderNodeBump",
}
NODE_FIELDS = {
    "image_texture": {"image_name", "interpolation", "projection", "extension"},
    "texture_coordinate": {"from_instancer"},
    "mapping": {"vector_type", "location", "rotation", "scale"},
    "normal_map": {"strength", "space", "uv_map"},
    "bump": {"strength", "distance", "invert"},
}
NODE_SOCKETS = {
    "location": "Location",
    "rotation": "Rotation",
    "scale": "Scale",
    "strength": "Strength",
    "distance": "Distance",
}


class ImageTextureSettings(Model):
    image_name: str | None
    interpolation: Interpolation
    projection: Projection
    extension: Extension


class TextureCoordinateSettings(Model):
    from_instancer: bool
    object_name: str | None


class MappingSettings(Model):
    vector_type: VectorType
    location: Vector32
    rotation: Vector32
    scale: Vector32


class NormalMapSettings(Model):
    strength: Float32
    space: str
    uv_map: str
    convention: str
    base: str


class BumpSettings(Model):
    strength: Float32
    distance: Float32
    invert: bool


class MaterialOutputSettings(Model):
    active: bool
    target: str


class PrincipledSettings(Model):
    subsurface_method: str
    distribution: str


class UVMapSettings(Model):
    uv_map: str
    from_instancer: bool


class NoiseSettings(Model):
    dimensions: str
    noise_type: str
    normalize: bool


class RampSettings(Model):
    color_mode: str
    interpolation: str
    hue_interpolation: str
    stops: list[RampStop] = Field(max_length=8)
    stop_count: int
    stops_truncated: bool


class MixSettings(Model):
    data_type: str
    factor_mode: str
    blend: str
    clamp_factor: bool
    clamp_result: bool


class MathSettings(Model):
    operation: str
    clamp: bool | None


class DisplacementSettings(Model):
    space: str


class TangentSettings(Model):
    direction: str
    axis: str
    uv_map: str


type NodeSettings = (
    ImageTextureSettings
    | TextureCoordinateSettings
    | MappingSettings
    | NormalMapSettings
    | BumpSettings
    | MaterialOutputSettings
    | PrincipledSettings
    | UVMapSettings
    | NoiseSettings
    | RampSettings
    | MixSettings
    | MathSettings
    | DisplacementSettings
    | TangentSettings
)


class SocketSummary(Model):
    name: str
    identifier: str
    socket_type: str
    linked: bool
    enabled: bool
    default_value: SocketDefault


class NodeSummary(Model):
    node_name: str
    node_type: str
    label: str
    muted: bool
    inputs: list[SocketSummary] = Field(max_length=64)
    input_count: int
    output_count: int
    sockets_included: bool
    sockets_truncated: bool
    outputs: list[SocketSummary] = Field(max_length=64)
    settings: NodeSettings | None


class LinkSummary(Model):
    from_node: str
    from_socket: str
    to_node: str
    to_socket: str
    valid: bool
    muted: bool


class ShaderGraphSummary(Model):
    material_name: str
    node_tree_present: bool
    nodes: list[NodeSummary]
    node_page: PageInfo
    link_page: PageInfo
    links: list[LinkSummary]


class ShaderInspectArguments(InspectArguments):
    material_name: ObjectName
    include_sockets: bool = False
    link_offset: int = Field(default=0, ge=0, le=1000000)
    link_limit: int = Field(default=32, ge=1, le=128)


class NodePatch(Model):
    image_name: ObjectName | None = None
    interpolation: Interpolation | None = None
    projection: Projection | None = None
    extension: Extension | None = None
    from_instancer: bool | None = None
    vector_type: VectorType | None = None
    location: Vector32 | None = None
    rotation: Vector32 | None = None
    scale: Vector32 | None = None
    strength: Float32 | None = None
    space: NormalSpace | None = None
    uv_map: str | None = None
    distance: Float32 | None = None
    invert: bool | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an unchanged property; null is invalid")
        return value

    def settings_for(self, node_type: str) -> dict[str, JsonValue]:
        values = self.model_dump(
            mode="json",
            exclude={"material_name", "node_name", "node_type", "name"},
            exclude_unset=True,
        )
        unsupported = values.keys() - NODE_FIELDS[node_type]
        if unsupported:
            raise ValueError(
                "Fields do not apply to this node: " + ", ".join(sorted(unsupported))
            )
        return values


class NodeCreateArguments(NodePatch):
    material_name: ObjectName
    node_type: NodeType
    name: ObjectName | None = None

    @model_validator(mode="after")
    def applicable_fields(self) -> Self:
        self.settings_for(self.node_type)
        return self


class NodeConfigureArguments(NodePatch):
    material_name: ObjectName
    node_name: ObjectName


class NodeDeleteArguments(Model):
    material_name: ObjectName
    node_name: ObjectName


class NodeDeleteResult(Model):
    material_name: str
    deleted: str


class ConnectArguments(Model):
    material_name: ObjectName
    from_node: ObjectName
    from_socket: ObjectName
    to_node: ObjectName
    to_socket: ObjectName
    replace_existing: bool = False


class DisconnectArguments(Model):
    material_name: ObjectName
    to_node: ObjectName
    to_socket: ObjectName


class DisconnectResult(Model):
    material_name: str
    to_node: str
    to_socket: str
    removed: int


def safe_socket_default(value: Any) -> SocketDefault:
    """Only finite numeric scalars and small numeric vectors, never RNA pointers."""
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list | tuple) and len(value) <= 4:
        if all(type(v) in (int, float) and math.isfinite(v) for v in value):
            return [float(v) for v in value]
    return None


def compatible_sockets(source: str, target: str) -> bool:
    """Blender shader numeric conversions; shader outputs require shader inputs."""
    supported = {"VALUE", "INT", "BOOLEAN", "VECTOR", "RGBA", "SHADER"}
    return (
        source in supported
        and target in supported
        and (source != "SHADER" or target == "SHADER")
    )
