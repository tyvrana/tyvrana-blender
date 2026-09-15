"""Bounded selected-to-active geometric normal transfer contracts."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .models import Model, ObjectName
from .numeric import Nonnegative32
from .uv_models import UVMetricSummary


class BakeTarget(Model):
    target: ObjectName
    sources: list[ObjectName] = Field(min_length=1, max_length=16)
    uv_map: ObjectName
    cage_extrusion: Nonnegative32 | None = None
    max_ray_distance: Nonnegative32 | None = None
    use_cage: bool = True

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.target in self.sources or len(set(self.sources)) != len(self.sources):
            raise ValueError("Sources must be distinct and exclude the target")
        if (self.cage_extrusion is None) != (self.max_ray_distance is None):
            raise ValueError("Provide both ray settings or omit both for analysis")
        if self.max_ray_distance is not None and self.max_ray_distance <= 0:
            raise ValueError("Use a finite positive ray distance; zero is unbounded")
        return self


class BakeInspectArguments(Model):
    targets: list[BakeTarget] = Field(min_length=1, max_length=16)
    sample_limit: int = Field(default=8192, ge=64, le=32768)
    image: ObjectName | None = None

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value: list[BakeTarget]) -> list[BakeTarget]:
        if len({t.target for t in value}) != len(value):
            raise ValueError("Bake targets must be distinct")
        target_names = {t.target for t in value}
        if any(target_names.intersection(t.sources) for t in value):
            raise ValueError("Production targets cannot also be sources in the batch")
        return value


class BakeImageArguments(Model):
    targets: list[BakeTarget] = Field(min_length=1, max_length=16)
    name: ObjectName
    resolution: int = Field(default=4096, ge=64, le=4096)
    type: Literal["normal"] = "normal"
    normal_space: Literal["tangent"] = "tangent"
    margin: int = Field(default=12, ge=0, le=64)
    margin_type: Literal["extend", "adjacent_faces"] = "extend"
    device: Literal["cpu", "gpu"] = "gpu"
    samples: int = Field(default=1, ge=1, le=64)

    @model_validator(mode="after")
    def explicit_projection(self) -> Self:
        BakeInspectArguments.unique_targets(self.targets)
        if any(t.cage_extrusion is None for t in self.targets):
            raise ValueError("Bake execution requires explicit bounded ray settings")
        if self.margin * 2 >= self.resolution:
            raise ValueError("Margin must leave image interior")
        return self


class BakeRaySummary(Model):
    target: str
    sources: list[str]
    evaluated_vertices: int
    evaluated_triangles: int
    sampled_points: int
    nearest_distance: UVMetricSummary
    source_normal_angle_degrees: UVMetricSummary
    suggested_extrusion: float
    suggested_max_ray_distance: float
    tested_extrusion: float
    tested_max_ray_distance: float
    ray_misses: int
    backface_hits: int
    hit_distance: UVMetricSummary
    worst_faces: list[int]
    authored_uv_sha256: str
    evaluated_uv_sha256: str
    seam_sha256: str


class NormalImageQA(Model):
    image: str
    resolution: list[int]
    color_space: str
    float_buffer: bool
    covered_texels: int
    coverage_fraction: float
    uncovered_alpha_texels: int
    invalid_normal_texels: int
    negative_z_texels: int
    nonfinite_components: int
    length: UVMetricSummary
    tilt_degrees: UVMetricSummary
    per_target: dict[str, dict[str, int | float]]
    limitations: list[str]


class BakeInspectResult(Model):
    supported_types: list[str]
    target_evaluation: str
    normal_convention: str
    compute_backend: str
    enabled_devices: list[str]
    targets: list[BakeRaySummary]
    image_qa: NormalImageQA | None
    limitations: list[str]


class BakeTiming(Model):
    target: str
    seconds: float


class BakeImageResult(Model):
    image: str
    resolution: int
    normal_convention: str
    target_evaluation: str
    device: str
    compute_backend: str
    enabled_devices: list[str]
    bake_seconds: float
    total_seconds: float
    targets: list[BakeTiming]
    qa: NormalImageQA


class ImageSaveArguments(Model):
    name: ObjectName
    filepath: str = Field(min_length=1, max_length=4096)
    format: Literal["png"] = "png"
    bit_depth: Literal[8, 16] = 16
    overwrite: bool = False
    pack: bool = True


class ImageSaveResult(Model):
    image: str
    filepath: str
    byte_size: int
    bit_depth: int
    packed: bool
    sha256: str
    maximum_roundtrip_error: float
    preview_width: int
    preview_height: int
    preview_bit_depth: Literal[8] = 8


class BakeStatusArguments(Model):
    job_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]+$")


class BakeJobStatus(Model):
    job_id: str
    image: str
    state: Literal["queued", "running", "completed", "failed"]
    completed_targets: int
    target_count: int
    result: BakeImageResult | None = None
    error: str | None = None
