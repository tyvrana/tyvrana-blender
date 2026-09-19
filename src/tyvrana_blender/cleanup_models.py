"""Explicit bounded mesh cleanup with inspectable postconditions."""

from typing import Self

from pydantic import Field, model_validator

from .models import Model
from .numeric import Float32
from .organization_models import Name


class CleanupArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=32)
    merge_distance: Float32 = Field(default=0.000001, ge=0, le=1, validate_default=True)
    remove_loose: bool = True
    remove_duplicates: bool = True
    dissolve_degenerate: bool = True
    recalculate_normals: bool = True
    fill_holes_up_to: int = Field(
        default=0,
        ge=0,
        le=64,
        description="Maximum boundary-loop edge count; 0 preserves all openings.",
    )
    remove_islands_max_faces: int = Field(default=0, ge=0, le=128)
    triangulate_ngons: bool = False
    require_closed: bool = False
    check_self_intersection: bool = True
    detach_construction: bool = Field(
        default=False,
        description="Relinquish generator revision metadata if cleanup changes "
        "managed geometry. Object/resource IDs remain; dependent QA becomes "
        "stale.",
    )
    preview: bool = False

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.names)) != len(self.names):
            raise ValueError("Cleanup object names must be unique")
        return self


class CleanupSummary(Model):
    name: str
    changed: bool
    vertices_before: int
    vertices_after: int
    faces_before: int
    faces_after: int
    boundary_edges: int
    non_manifold_edges: int
    max_valence: int
    self_contacts: int | None
    construction_detached: bool


class CleanupResult(Model):
    objects: list[CleanupSummary]
    preview: bool
    committed: bool
