"""Bounded declarative armatures, skin weights and rest-relative poses."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .deformation_models import ContactProbes, ContactSummary, DeformationQA
from .mesh_models import Arguments, MeshVector
from .models import Model
from .modifier_models import Name
from .numeric import Float32

MAX_BONES = 128
MAX_VERTICES = 100_000
MAX_WEIGHT_WORK = 1_000_000
type Radius = Annotated[Float32, Field(ge=0, le=10000)]


class RestBone(Arguments):
    name: Name
    head: MeshVector
    tail: MeshVector
    parent: Name | None = None
    connected: bool = False
    roll: Float32 = 0.0
    deform: bool = True
    head_radius: Radius = 0.1
    tail_radius: Radius = 0.1
    envelope_distance: Radius = 0.25

    @model_validator(mode="after")
    def valid_bone(self) -> Self:
        if len(self.name.encode()) > 63:
            raise ValueError("Bone names must fit 63 UTF-8 bytes")
        if sum((a - b) ** 2 for a, b in zip(self.head, self.tail, strict=True)) < 1e-12:
            raise ValueError("Bone length must be at least 0.000001")
        if any(abs(v) > 10000 for v in self.head + self.tail):
            raise ValueError("Rest coordinates must be within 10000 units")
        if self.connected and self.parent is None:
            raise ValueError("Connected bones require a parent")
        return self


class ArmatureCreateArguments(Arguments):
    name: Name
    bones: Annotated[list[RestBone], Field(min_length=1, max_length=MAX_BONES)]

    @model_validator(mode="after")
    def hierarchy(self) -> Self:
        bones = {b.name: b for b in self.bones}
        if len(bones) != len(self.bones):
            raise ValueError("Bone names must be unique")
        for bone in self.bones:
            visited = {bone.name}
            parent = bone.parent
            while parent is not None:
                if parent not in bones:
                    raise ValueError(f'Parent bone "{parent}" is missing')
                if parent in visited:
                    raise ValueError("Bone hierarchy contains a cycle")
                visited.add(parent)
                parent = bones[parent].parent
            if bone.connected and bone.parent is not None:
                if (
                    max(
                        abs(a - b)
                        for a, b in zip(bone.head, bones[bone.parent].tail, strict=True)
                    )
                    > 1e-6
                ):
                    raise ValueError("Connected head must match the parent tail")
        return self


class ArmatureInspectArguments(Arguments):
    object_name: Name
    bone_names: Annotated[list[Name], Field(max_length=MAX_BONES)] | None = None
    sample_limit: Annotated[int, Field(ge=0, le=MAX_BONES)] = 16


class Influence(Arguments):
    bone: Name
    weight: Annotated[Float32, Field(gt=0, le=1)]


class VertexWeights(Arguments):
    vertex: Annotated[int, Field(ge=0, lt=MAX_VERTICES)]
    influences: Annotated[list[Influence], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def normalized(self) -> Self:
        if len({i.bone for i in self.influences}) != len(self.influences):
            raise ValueError("A vertex cannot repeat a bone influence")
        if abs(sum(i.weight for i in self.influences) - 1) > 1e-5:
            raise ValueError("Explicit vertex influences must sum to one")
        return self


class EnvelopeWeights(Arguments):
    method: Literal["envelopes"] = "envelopes"
    bones: Annotated[list[Name], Field(min_length=1, max_length=MAX_BONES)]
    max_influences: Annotated[int, Field(ge=1, le=4)] = 4

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.bones)) != len(self.bones):
            raise ValueError("Envelope bones must be unique")
        return self


class ExplicitWeights(Arguments):
    method: Literal["explicit"]
    vertices: Annotated[list[VertexWeights], Field(max_length=MAX_VERTICES)]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({v.vertex for v in self.vertices}) != len(self.vertices):
            raise ValueError("Vertex weights must be specified once per vertex")
        return self


class ArmatureBindArguments(Arguments):
    object_name: Name
    armature_object: Name
    weights: Annotated[EnvelopeWeights | ExplicitWeights, Field(discriminator="method")]
    modifier_index: Annotated[int, Field(ge=0, le=127)] = 0
    preserve_volume: bool = True
    allow_unweighted: bool = False
    replace_binding_target: bool = False


class PoseBone(Arguments):
    name: Name
    location: MeshVector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: MeshVector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: MeshVector = Field(default_factory=lambda: [1.0, 1.0, 1.0])

    @model_validator(mode="after")
    def bounded_pose(self) -> Self:
        if any(v <= 0 or v > 100 for v in self.scale):
            raise ValueError("Pose scale must be positive and at most 100")
        if any(abs(v) > 10000 for v in self.location + self.rotation):
            raise ValueError("Pose channels must be within 10000")
        return self


class ArmaturePoseArguments(Arguments):
    object_name: Name
    bones: Annotated[list[PoseBone], Field(max_length=MAX_BONES)] = Field(
        default_factory=list
    )
    reset: bool = False

    @model_validator(mode="after")
    def unique(self) -> Self:
        if not self.bones and not self.reset:
            raise ValueError("Provide pose bones or reset=true")
        if len({b.name for b in self.bones}) != len(self.bones):
            raise ValueError("Pose updates must name each bone once")
        return self


class DeformationInspectArguments(Arguments):
    armature_object: Name
    objects: Annotated[list[Name], Field(min_length=1, max_length=8)]
    sample_limit: Annotated[int, Field(ge=0, le=16)] = 4
    bone_names: Annotated[list[Name], Field(max_length=16)] = Field(
        default_factory=list
    )
    contacts: ContactProbes = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.objects)) != len(self.objects):
            raise ValueError("Inspect each mesh once")
        if len(set(self.bone_names)) != len(self.bone_names):
            raise ValueError("Inspect each bone region once")
        if any(c.source_object not in self.objects for c in self.contacts):
            raise ValueError("Contact source must be an inspected bound mesh")
        return self


class BoneSummary(Model):
    name: str
    parent: str | None
    connected: bool
    deform: bool
    rest_head_world: MeshVector
    rest_tail_world: MeshVector
    posed_head_world: MeshVector
    posed_tail_world: MeshVector
    location: MeshVector
    rotation_mode: str
    rotation_quaternion: Annotated[list[Float32], Field(min_length=4, max_length=4)]
    scale: MeshVector


class BindingSummary(Model):
    object_name: str
    armature_object: str
    modifier_name: str
    modifier_index: int
    preserve_volume: bool
    vertex_count: int
    weighted_vertex_count: int
    unweighted_vertex_count: int
    maximum_influences: int
    weight_sum_min: float
    weight_sum_max: float
    weights_sha256: str
    bone_vertex_counts: dict[str, int]
    binding_seconds: float | None = None


class ArmatureSummary(Model):
    object_name: str
    bone_count: int
    root_count: int
    pose_position: str
    rest_sha256: str
    pose_sha256: str
    bones: list[BoneSummary]
    bones_truncated: bool
    bindings: list[BindingSummary]


class DeformationSample(Model):
    vertex: int
    rest_world: MeshVector
    posed_world: MeshVector
    distance: float


class MeshDeformation(Model):
    object_name: str
    vertex_count: int
    triangle_count: int
    topology_sha256: str
    rest_geometry_sha256: str
    posed_geometry_sha256: str
    changed_vertex_count: int
    displacement_max: float
    displacement_mean: float
    rest_bounds_min: MeshVector
    rest_bounds_max: MeshVector
    posed_bounds_min: MeshVector
    posed_bounds_max: MeshVector
    edge_length_ratio_min: float
    edge_length_ratio_max: float
    triangle_area_ratio_min: float
    triangle_area_ratio_max: float
    collapsed_triangle_count: int
    samples: list[DeformationSample]
    qa: DeformationQA


class DeformationSummary(Model):
    model_config = ConfigDict(allow_inf_nan=False)
    armature_object: str
    meshes: list[MeshDeformation]
    restored_pose_position: str
    inspection_seconds: float
    contacts: list[ContactSummary]
    limitations: list[str]
