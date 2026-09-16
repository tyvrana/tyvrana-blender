"""Evaluated volume measurements and explicit frozen mesh snapshots."""

from typing import Self

from pydantic import Field, model_validator

from .models import Model
from .modifier_models import Name
from .organization_models import Memberships, Tag, Tags, scene_root


class VolumeQuery(Model):
    object_name: Name
    reference: Name | None = Field(
        default=None,
        description=(
            "Compare with this object's current evaluated volume. Use a frozen "
            "volume.snapshot mesh for a fixed reference; otherwise meshes use "
            "authored geometry and curves have no implicit rest baseline."
        ),
    )


class VolumeInspectArguments(Model):
    objects: list[VolumeQuery] = Field(min_length=1, max_length=16)
    section_samples: int = Field(default=0, ge=0, le=32)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.section_samples == 1 or self.section_samples * len(self.objects) > 256:
            raise ValueError("Use zero or 2..32 path samples and at most 256 total")
        return self


class VolumeMetrics(Model):
    vertex_count: int
    triangle_count: int
    closed_consistent: bool
    volume: float | None
    surface_area: float
    bounds_min: list[float]
    bounds_max: list[float]
    surface_centroid: list[float]


class PathSection(Model):
    spline: int
    factor: float
    position: list[float]
    radius_multiplier: float
    circle_radius: float | None


class VolumePath(Model):
    guide: str
    length: float
    reference_length: float | None
    length_ratio: float | None
    profile_kind: str
    control_radius_multiplier_min: float
    control_radius_multiplier_max: float
    circle_radius_min: float | None
    circle_radius_max: float | None
    attachment_count: int
    attachment_error_max: float
    sections: list[PathSection]


class VolumeSummary(Model):
    object_name: str
    representation: str
    valid: bool
    evaluated: VolumeMetrics
    reference_volume: float | None
    reference_kind: str | None
    volume_ratio: float | None
    volume_change_percent: float | None
    path: VolumePath | None
    modifiers: list[str]
    topology_sha256: str


class VolumeInspectResult(Model):
    volumes: list[VolumeSummary]
    evaluated_vertices: int
    processing_seconds: float


class VolumeSnapshotSpec(Model):
    source: Name
    name: Name


class VolumeSnapshotArguments(Model):
    objects: list[VolumeSnapshotSpec] = Field(min_length=1, max_length=16)
    collections: Memberships = Field(default_factory=scene_root)
    role: Tag | None = None
    tags: Tags = Field(default_factory=list)

    @model_validator(mode="after")
    def names(self) -> Self:
        if len({o.name for o in self.objects}) != len(self.objects):
            raise ValueError("Snapshot names must be unique")
        return self


class VolumeSnapshotSummary(Model):
    source: str
    object_name: str
    vertex_count: int
    face_count: int
    topology_sha256: str


class VolumeSnapshotResult(Model):
    snapshots: list[VolumeSnapshotSummary]
