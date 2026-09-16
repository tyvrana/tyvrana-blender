"""Bounded material recipes and explicitly supported shader node contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .material_models import MaterialAssignment, ShaderColor, SurfaceParameters
from .models import Model, ObjectName
from .numeric import Float32, Nonnegative32, Vector32

type Unit = Annotated[Float32, Field(ge=0, le=1)]
type NodeId = Annotated[
    str, Field(min_length=1, max_length=48, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
]
type ColorSpace = Literal["sRGB", "Non-Color", "Linear Rec.709"]
type Channel = Literal[
    "base_color",
    "roughness",
    "metallic",
    "normal",
    "bump",
    "displacement",
    "alpha",
    "emission_color",
    "anisotropy",
    "anisotropy_rotation",
    "subsurface_weight",
    "transmission_weight",
    "coat_weight",
    "sheen_weight",
]


class Node(Model):
    id: NodeId

    @model_validator(mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("Omit unchanged node fields; null is invalid")
        return value


class PrincipledNode(Node):
    type: Literal["principled"]
    parameters: SurfaceParameters = Field(default_factory=SurfaceParameters)
    subsurface_method: Literal["random_walk", "random_walk_skin", "burley"] | None = (
        None
    )


class OutputNode(Node):
    type: Literal["output"]


class ImageNode(Node):
    type: Literal["image_texture"]
    image: ObjectName | None = None
    color_space: ColorSpace | None = None
    interpolation: Literal["linear", "closest", "cubic"] | None = None
    projection: Literal["flat", "box", "sphere", "tube"] | None = None
    extension: Literal["repeat", "extend", "clip", "mirror"] | None = None


class CoordinateNode(Node):
    type: Literal["texture_coordinate"]
    object_name: ObjectName | None = None


class UVNode(Node):
    type: Literal["uv_map"]
    uv_map: Annotated[str, Field(max_length=63)] | None = None


class MappingNode(Node):
    type: Literal["mapping"]
    location: Vector32 | None = None
    rotation: Vector32 | None = None
    scale: Vector32 | None = None


class NoiseNode(Node):
    """Normalized 3D fBM; supported inputs are stable across this subset."""

    type: Literal["noise"]
    scale: Nonnegative32 | None = None
    detail: Annotated[Float32, Field(ge=0, le=15)] | None = None
    roughness: Unit | None = None
    lacunarity: Nonnegative32 | None = None
    distortion: Float32 | None = None


class RampStop(Model):
    position: Unit
    color: ShaderColor
    alpha: Unit = 1


class RampNode(Node):
    type: Literal["color_ramp"]
    stops: list[RampStop] | None = Field(default=None, min_length=2, max_length=8)
    interpolation: Literal["linear", "constant", "ease"] | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.stops and any(
            a.position >= b.position
            for a, b in zip(self.stops, self.stops[1:], strict=False)
        ):
            raise ValueError("Ramp positions must be strictly increasing")
        return self


class MixNode(Node):
    type: Literal["mix_color"]
    blend: Literal["mix", "multiply", "add", "overlay", "screen"] | None = None
    factor: Unit | None = None
    a: ShaderColor | None = None
    b: ShaderColor | None = None
    clamp_result: bool | None = None


class MathNode(Node):
    type: Literal["math"]
    operation: (
        Literal[
            "add",
            "subtract",
            "multiply",
            "divide",
            "minimum",
            "maximum",
            "power",
            "less_than",
            "greater_than",
        ]
        | None
    ) = None
    a: Float32 | None = None
    b: Float32 | None = None
    clamp: bool | None = None


class VectorMathNode(Node):
    type: Literal["vector_math"]
    operation: (
        Literal[
            "add",
            "subtract",
            "multiply",
            "cross_product",
            "dot_product",
            "scale",
            "normalize",
        ]
        | None
    ) = None
    a: Vector32 | None = None
    b: Vector32 | None = None
    scale: Float32 | None = None


class NormalNode(Node):
    type: Literal["normal_map"]
    space: Literal["tangent", "object", "world"] | None = None
    uv_map: Annotated[str, Field(max_length=63)] | None = None
    convention: Literal["opengl", "directx"] | None = None
    base: Literal["original", "displaced"] | None = None
    strength: Nonnegative32 | None = None


class BumpNode(Node):
    type: Literal["bump"]
    strength: Unit | None = None
    distance: Nonnegative32 | None = None
    invert: bool | None = None


class DisplacementNode(Node):
    type: Literal["displacement"]
    space: Literal["object", "world"] | None = None
    scale: Float32 | None = None
    midlevel: Float32 | None = None


class LayerWeightNode(Node):
    type: Literal["layer_weight"]
    blend: Unit | None = None


class FresnelNode(Node):
    type: Literal["fresnel"]
    ior: Annotated[Float32, Field(ge=1, le=1000)] | None = None


class TangentNode(Node):
    type: Literal["tangent"]
    direction: Literal["uv_map", "radial"] | None = None
    axis: Literal["x", "y", "z"] | None = None
    uv_map: Annotated[str, Field(max_length=63)] | None = None


type NodeSpec = Annotated[
    PrincipledNode
    | OutputNode
    | ImageNode
    | CoordinateNode
    | UVNode
    | MappingNode
    | NoiseNode
    | RampNode
    | MixNode
    | MathNode
    | VectorMathNode
    | NormalNode
    | BumpNode
    | DisplacementNode
    | LayerWeightNode
    | FresnelNode
    | TangentNode,
    Field(discriminator="type"),
]
type SocketName = Literal[
    "base_color",
    "metallic",
    "roughness",
    "ior",
    "alpha",
    "thin_wall",
    "normal",
    "diffuse_roughness",
    "subsurface_weight",
    "subsurface_radius",
    "subsurface_scale",
    "subsurface_anisotropy",
    "specular_ior_level",
    "specular_tint",
    "anisotropy",
    "anisotropy_rotation",
    "tangent",
    "transmission_weight",
    "coat_weight",
    "coat_roughness",
    "coat_ior",
    "coat_tint",
    "coat_normal",
    "sheen_weight",
    "sheen_roughness",
    "sheen_tint",
    "emission_color",
    "emission_strength",
    "thin_film_thickness",
    "thin_film_ior",
    "bsdf",
    "surface",
    "displacement",
    "color",
    "vector",
    "uv",
    "generated",
    "object",
    "location",
    "rotation",
    "scale",
    "factor",
    "detail",
    "lacunarity",
    "distortion",
    "a",
    "b",
    "value",
    "result",
    "strength",
    "height",
    "distance",
    "midlevel",
    "blend",
    "fresnel",
    "facing",
]


class Endpoint(Model):
    node: NodeId
    socket: SocketName


class GraphLink(Model):
    source: Endpoint
    target: Endpoint


class MaterialSettings(Model):
    displacement: Literal["bump", "displacement", "both"] | None = None
    surface: Literal["dithered", "blended"] | None = None
    thickness: Literal["sphere", "slab"] | None = None


class Assignment(Model):
    object_name: ObjectName
    slot_index: Annotated[int, Field(ge=0, le=32766)] = 0


class WriteMaterial(Model):
    name: ObjectName
    affect_shared: bool = False
    expected_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    assignments: list[Assignment] = Field(default_factory=list, max_length=64)


class GraphAuthorArguments(WriteMaterial):
    mode: Literal["create", "patch", "replace"] = "create"
    replace_unowned: bool = False
    nodes: list[NodeSpec] = Field(default_factory=list, max_length=64)
    links: list[GraphLink] = Field(default_factory=list, max_length=128)
    remove_nodes: list[NodeId] = Field(default_factory=list, max_length=64)
    disconnect: list[Endpoint] = Field(default_factory=list, max_length=128)
    settings: MaterialSettings = Field(default_factory=MaterialSettings)

    @model_validator(mode="after")
    def unique(self) -> Self:
        ids = [node.id for node in self.nodes]
        targets = [(link.target.node, link.target.socket) for link in self.links]
        if len(set(ids)) != len(ids) or len(set(targets)) != len(targets):
            raise ValueError(
                "Node IDs and link targets must be unique within a request"
            )
        if set(ids) & set(self.remove_nodes):
            raise ValueError("Cannot upsert and remove the same node")
        if self.mode != "patch" and (self.remove_nodes or self.disconnect):
            raise ValueError("Removal and disconnect require patch mode")
        if self.mode == "replace" and self.expected_fingerprint is None:
            raise ValueError("Replacement requires expected_fingerprint")
        return self


class Coordinates(Model):
    source: Literal["uv", "generated", "object"] = "uv"
    uv_map: Annotated[str, Field(max_length=63)] = ""
    object_name: ObjectName | None = None
    location: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Vector32 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: Vector32 = Field(default_factory=lambda: [1.0, 1.0, 1.0])

    @model_validator(mode="after")
    def meaningful(self) -> Self:
        if self.source != "uv" and self.uv_map:
            raise ValueError("uv_map requires UV coordinates")
        if self.source != "object" and self.object_name:
            raise ValueError("object_name requires object coordinates")
        return self


class TextureBinding(Model):
    channel: Channel
    image: ObjectName
    image_output: Literal["color", "alpha"] = "color"
    color_space: ColorSpace | None = None
    coordinates: Coordinates | None = None
    interpolation: Literal["linear", "closest", "cubic"] = "linear"
    projection: Literal["flat", "box", "sphere", "tube"] = "flat"
    extension: Literal["repeat", "extend", "clip", "mirror"] = "repeat"
    strength: Nonnegative32 = 1
    distance: Nonnegative32 = 0.1
    invert: bool = False
    normal_space: Literal["tangent", "object", "world"] = "tangent"
    normal_convention: Literal["opengl", "directx"] = "opengl"
    displacement_scale: Float32 = 0.01
    displacement_midlevel: Float32 = 0.5
    displacement_space: Literal["object", "world"] = "object"


class Variation(Model):
    channel: Literal["base_color", "roughness"]
    scale: Nonnegative32 = 5
    detail: Annotated[Float32, Field(ge=0, le=15)] = 2
    low: ShaderColor
    high: ShaderColor
    coordinates: Coordinates = Field(
        default_factory=lambda: Coordinates(source="generated")
    )


class Tangent(Model):
    direction: Literal["uv_map", "radial"] = "uv_map"
    uv_map: Annotated[str, Field(max_length=63)] = ""
    axis: Literal["x", "y", "z"] = "z"


class MaterialAuthorArguments(WriteMaterial):
    mode: Literal["create", "update"] = "create"
    parameters: SurfaceParameters = Field(default_factory=SurfaceParameters)
    subsurface_method: Literal["random_walk", "random_walk_skin", "burley"] | None = (
        None
    )
    coordinates: Coordinates | None = None
    textures: list[TextureBinding] = Field(default_factory=list, max_length=14)
    remove_textures: list[Channel] = Field(default_factory=list, max_length=14)
    variation: Variation | None = None
    remove_variation: bool = False
    tangent: Tangent | None = None
    remove_tangent: bool = False
    settings: MaterialSettings = Field(default_factory=MaterialSettings)

    @model_validator(mode="after")
    def unique_channels(self) -> Self:
        channels = [texture.channel for texture in self.textures]
        if len(channels) != len(set(channels)) or set(channels) & set(
            self.remove_textures
        ):
            raise ValueError(
                "Texture channels must be unique and not removed simultaneously"
            )
        if (
            self.variation
            and self.remove_variation
            or self.tangent
            and self.remove_tangent
        ):
            raise ValueError("Cannot set and remove the same branch")
        return self


class MaterialCopyArguments(Model):
    source: ObjectName
    name: ObjectName


class MaterialRemoveArguments(Model):
    name: ObjectName


class MaterialRemoveResult(Model):
    name: str


class AssignBatchArguments(Model):
    material_name: ObjectName
    assignments: list[Assignment] = Field(min_length=1, max_length=64)


class AssignBatchResult(Model):
    material_name: str
    assignments: list[MaterialAssignment] = Field(max_length=64)
