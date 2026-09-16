"""Deterministic pose-set evaluation with fully restored native channels."""

from typing import Self

from pydantic import Field, model_validator

from .corrective_models import ComparisonPair, ShapeValue, TargetDeviation
from .deformation_models import DeformationQA
from .models import Model
from .reference_models import Name
from .region_models import FrameRegion
from .rig_models import DeformationInspectArguments, PoseBone

MAX_SWEEP_VERTEX_SAMPLES = 1_000_000


class EvaluationPose(Model):
    name: Name
    bones: list[PoseBone] = Field(
        default_factory=list,
        max_length=128,
        description=(
            "Unspecified channels reset to rest for every pose; empty bones "
            "evaluates rest. Joint limits still apply."
        ),
    )

    shape_values: list[ShapeValue] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Named key channels reset to zero before every pose and baseline, "
            "then apply these values; all original values restore afterward. "
            "Automatic pose drivers are not authored."
        ),
    )
    targets: list[ComparisonPair] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({b.name for b in self.bones}) != len(self.bones):
            raise ValueError("Pose bones must be unique")
        if len({(v.object_name, v.key) for v in self.shape_values}) != len(
            self.shape_values
        ):
            raise ValueError("Set each key channel once per pose")
        return self


class DeformationRegion(FrameRegion):
    name: Name


class DeformationSweepArguments(DeformationInspectArguments):
    """Up to 128 mesh/pose/region summaries and 384 bounded detail units.

    Detail units: summary count times (1+2*sample_limit+len(bone_names)).
    Native evaluated work is capped at 1000000 vertex/pose samples.
    """

    poses: list[EvaluationPose] = Field(min_length=1, max_length=16)
    regions: list[DeformationRegion] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Select evaluated REST vertices in each frame box. "
            "Edges/triangles require all endpoints. Frames are frozen before "
            "the sweep; bone frames always use rest."
        ),
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({p.name for p in self.poses}) != len(self.poses) or len(
            {r.name for r in self.regions}
        ) != len(self.regions):
            raise ValueError("Pose and region names must be unique")
        if len(self.objects) * len(self.poses) * (1 + len(self.regions)) > 128:
            raise ValueError(
                "Sweep exceeds 128 mesh/pose/region summaries; reduce the batch"
            )
        summaries = len(self.objects) * len(self.poses) * (1 + len(self.regions))
        if summaries * (1 + 2 * self.sample_limit + len(self.bone_names)) > 384:
            raise ValueError(
                "Sweep detail budget exceeds 384 units; reduce poses/regions, "
                "sample_limit, or bone_names"
            )
        if self.contacts:
            raise ValueError(
                "Use deformation.inspect for contact probes; sweeps report "
                "topology strain"
            )
        return self


class RegionEvaluation(Model):
    name: str
    vertex_count: int
    qa: DeformationQA


class PoseMeshEvaluation(Model):
    object_name: str
    vertex_count: int
    qa: DeformationQA
    regions: list[RegionEvaluation]


class PoseEvaluation(Model):
    name: str
    meshes: list[PoseMeshEvaluation]
    evaluated_rotations: dict[str, list[float]]
    shape_values: list[ShapeValue] = Field(default_factory=list)
    target_deviations: list[TargetDeviation] = Field(default_factory=list)


class DeformationSweepResult(Model):
    armature_object: str
    poses: list[PoseEvaluation]
    restored: bool
    inspection_seconds: float
    evaluated_vertex_samples: int
    limitations: list[str]
