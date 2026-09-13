"""Explicit destructive blockout remeshing and bounded consequence reports."""

import math
from typing import Annotated, Literal

from pydantic import Field

from .mesh_models import Arguments, MeshSummary
from .models import Model
from .modifier_models import Name, Number
from .numeric import Float32, binary32

MAX_GRID_CELLS = 2_000_000
MAX_GRID_AXIS = 4096
MAX_GRID_COORDINATE = 1_000_000
GRID_PADDING = 8
MAX_ATTRIBUTES = 32
MAX_ATTRIBUTE_COMPONENTS = 8_000_000
MIN_VOXEL_SIZE = binary32(0.0001)

GUIDANCE = (
    "Destructive topology replacement for rough organic blockout, not production "
    "retopology. Inspect consequences with the intended settings before use. "
    "Production UVs, weights, shape keys and Multires are protected by blockers. "
    "Reprojected attributes are approximate; small details and regions may vanish. "
    "All previous mesh indices become invalid: reinspect/query after remeshing, "
    "then raycast before sculpting and render to verify the form."
)


class VoxelRemeshSettings(Arguments):
    voxel_size: Annotated[Float32, Field(ge=MIN_VOXEL_SIZE, le=1000)]
    adaptivity: Annotated[Float32, Field(ge=0, le=1)] = 0.0
    preserve_volume: bool = True
    fix_poles: bool = True
    preserve_attributes: bool = True


class VoxelRemeshArguments(VoxelRemeshSettings):
    object_name: Name


class VoxelRemeshInspectArguments(VoxelRemeshArguments):
    voxel_size: Annotated[Float32, Field(ge=MIN_VOXEL_SIZE, le=1000)] = 0.1


class Distribution(Model):
    min: Number
    max: Number
    mean: Number
    variance: Number
    coefficient_of_variation: Number


class VoxelMeshSummary(MeshSummary):
    edge_length: Distribution
    face_area: Distribution


class GridEstimate(Model):
    dimensions: list[int] | None
    cells: int | None
    cell_limit: int = MAX_GRID_CELLS
    padding_per_axis: int = GRID_PADDING
    exceeds_limit: bool
    coordinate_limit_exceeded: bool


def estimate_grid(
    minimum: list[float], maximum: list[float], size: float
) -> GridEstimate:
    """Bound float division before integer conversion/multiplication."""
    ratios = [(b - a) / size for a, b in zip(minimum, maximum, strict=True)]
    far = any(abs(v) / size > MAX_GRID_COORDINATE for v in [*minimum, *maximum])
    if any(not math.isfinite(v) or v > MAX_GRID_AXIS - GRID_PADDING for v in ratios):
        return GridEstimate(
            dimensions=None,
            cells=None,
            exceeds_limit=True,
            coordinate_limit_exceeded=far,
        )
    dimensions = [math.ceil(v) + GRID_PADDING for v in ratios]
    cells = math.prod(dimensions)
    return GridEstimate(
        dimensions=dimensions,
        cells=cells,
        exceeds_limit=cells > MAX_GRID_CELLS,
        coordinate_limit_exceeded=far,
    )


class RemeshBlocker(Model):
    code: str
    message: str


class RemeshDataEffect(Model):
    code: str
    behavior: Literal["preserved", "reprojected", "discarded", "rebuilt"]
    names: Annotated[list[str], Field(max_length=MAX_ATTRIBUTES)] = Field(
        default_factory=list
    )
    message: str


class VoxelRemeshSummary(Model):
    object_name: str
    guidance: str = GUIDANCE
    mesh: VoxelMeshSummary
    settings: VoxelRemeshSettings
    effective_fix_poles: bool
    grid: GridEstimate
    shared_mesh: bool
    blockers: list[RemeshBlocker]
    destructive_effects: list[RemeshDataEffect]
    preservable_data: list[RemeshDataEffect]


class VoxelRemeshResult(Model):
    object_name: str
    before: VoxelMeshSummary
    after: VoxelMeshSummary
    settings: VoxelRemeshSettings
    effective_fix_poles: bool
    grid: GridEstimate
    isolated_shared_mesh: bool
    indices_invalidated: Literal[True] = True
    lost_or_rebuilt_data: list[RemeshDataEffect]
    preserved_data: list[RemeshDataEffect]
