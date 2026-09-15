"""Bounded authored meshes and guided, UV-bound surface instances."""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from .mesh_models import MeshVector
from .models import Model, ObjectName
from .numeric import Float32

type UV = Annotated[list[Float32], Field(min_length=2, max_length=2)]
type Face = Annotated[list[int], Field(min_length=3, max_length=4)]
type Guide = Annotated[list[MeshVector], Field(min_length=2, max_length=32)]


class MeshCreateArguments(Model):
    name: ObjectName
    vertices: Annotated[list[MeshVector], Field(min_length=3, max_length=8192)]
    faces: Annotated[list[Face], Field(min_length=1, max_length=8192)]
    uv_map: ObjectName = "UVMap"
    corner_uvs: Annotated[list[UV], Field(max_length=32768)] | None = None
    materials: Annotated[list[ObjectName], Field(max_length=16)] = Field(
        default_factory=list
    )
    material_indices: Annotated[list[int], Field(max_length=8192)] | None = None
    smooth: bool = True
    hidden: bool = False

    @model_validator(mode="after")
    def valid_mesh(self) -> Self:
        if any(
            len(set(face)) != len(face)
            or min(face) < 0
            or max(face) >= len(self.vertices)
            for face in self.faces
        ):
            raise ValueError("Faces require distinct valid vertex indices")
        if len({tuple(sorted(f)) for f in self.faces}) != len(self.faces):
            raise ValueError("Duplicate faces are not allowed")
        if self.corner_uvs is not None and len(self.corner_uvs) != sum(
            len(f) for f in self.faces
        ):
            raise ValueError("Provide one UV coordinate per face corner")
        if self.material_indices is not None and (
            len(self.material_indices) != len(self.faces)
            or any(i < 0 or i >= len(self.materials) for i in self.material_indices)
        ):
            raise ValueError("Provide one valid material index per face")
        return self


class SurfaceDistribution(Model):
    model_config = ConfigDict(validate_default=True)

    surface_object: ObjectName
    prototype_object: ObjectName
    uv_map: ObjectName
    guides: Annotated[list[Guide], Field(min_length=2, max_length=16)]
    rows: Annotated[int, Field(ge=1, le=128)]
    columns: Annotated[int, Field(ge=2, le=128)]
    scale: MeshVector = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    scale_end: MeshVector | None = None
    scale_variation: MeshVector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    spacing_variation: Annotated[Float32, Field(ge=0, le=0.3)] = 0.1
    direction_variation: Annotated[Float32, Field(ge=0, le=0.5)] = 0.05
    stagger: Annotated[Float32, Field(ge=0, le=0.5)] = 0.5
    surface_offset: Annotated[Float32, Field(ge=-1, le=1)] = 0.0
    max_projection_distance: Annotated[Float32, Field(gt=0, le=10)] = 0.01
    direction_distance: Annotated[Float32, Field(ge=0.00001, le=1)] = 0.001
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] = 0
    layer: Annotated[int, Field(ge=0, le=255)] = 0

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.surface_object == self.prototype_object:
            raise ValueError("Surface and prototype must be separate objects")
        if self.rows * self.columns > 4096:
            raise ValueError("Distribution is limited to 4096 instances")
        if any(v <= 0 or v > 1000 for v in self.scale + (self.scale_end or [])):
            raise ValueError("Scale components must be positive and at most 1000")
        if any(v < 0 or v > 0.5 for v in self.scale_variation):
            raise ValueError("Scale variation components must be in 0..0.5")
        if any(
            all(a == b for a, b in zip(g[:-1], g[1:], strict=True)) for g in self.guides
        ):
            raise ValueError("Each guide must have nonzero length")
        return self


class SurfaceInstancesCreateArguments(Model):
    name: ObjectName
    distribution: SurfaceDistribution


class SurfaceInstancesConfigureArguments(Model):
    object_name: ObjectName
    distribution: SurfaceDistribution


class SurfaceInstancesInspectArguments(Model):
    object_name: ObjectName
    sample_limit: Annotated[int, Field(ge=0, le=16)] = 4


class InstanceSample(Model):
    id: int
    root_uv: UV
    direction_uv: UV
    root_world: MeshVector
    normal_world: MeshVector
    direction_world: MeshVector
    scale: MeshVector
    layer: int


class SurfaceInstancesSummary(Model):
    object_name: str
    surface_object: str
    prototype_object: str
    uv_map: str
    instance_count: int
    evaluated_instance_count: int
    prototype_vertices: int
    prototype_faces: int
    equivalent_vertices: int
    equivalent_faces: int
    stored_point_count: int
    node_count: int
    binding_sha256: str
    region_area: float
    root_spacing_min: float
    root_spacing_mean: float
    invalid_binding_count: int
    root_position_max_error: float
    surface_sample_count: int
    minimum_surface_clearance: float | None
    buried_surface_sample_count: int
    generation_seconds: float | None = None
    inspection_seconds: float
    samples: list[InstanceSample]
    limitations: list[str]
