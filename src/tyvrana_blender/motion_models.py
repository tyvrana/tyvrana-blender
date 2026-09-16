"""Bounded native scalar channels, generated mappings and timeline sampling."""

from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from .corrective_models import ComparisonPair
from .layer_models import LayerInspectArguments
from .models import InspectArguments, Model, PageInfo
from .reference_models import Name
from .volume_models import VolumeQuery

type Scalar = Annotated[FiniteFloat, Field(ge=-1e9, le=1e9)]
type Frame = Annotated[int, Field(ge=-1048574, le=1048574)]
type ControlName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,47}$")]


class TransformChannel(Model):
    kind: Literal["transform"] = "transform"
    object_name: Name
    bone: Name | None = None
    property: Literal["location", "rotation", "scale"]
    axis: Literal["x", "y", "z"]
    # Sources use evaluated native LOCAL_SPACE, targets use local RNA channels.


class ShapeChannel(Model):
    kind: Literal["shape"] = "shape"
    object_name: Name
    key: Name


class PropertyChannel(Model):
    kind: Literal["property"] = "property"
    object_name: Name
    property: ControlName


class ConstraintChannel(Model):
    kind: Literal["constraint"] = "constraint"
    object_name: Name
    bone: Name | None = None
    constraint: Name


type Channel = Annotated[
    TransformChannel | ShapeChannel | PropertyChannel | ConstraintChannel,
    Field(discriminator="kind"),
]


class OutputClamp(Model):
    minimum: Scalar
    maximum: Scalar

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Clamp minimum must not exceed maximum")
        return self


class LinearMapping(Model):
    kind: Literal["linear"] = "linear"
    scale: Scalar = 1.0
    offset: Scalar = 0.0
    clamp: OutputClamp | None = None


class RemapMapping(Model):
    kind: Literal["remap"] = "remap"
    input_min: Scalar
    input_max: Scalar
    output_min: Scalar
    output_max: Scalar
    clamp: bool = True

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.input_max - self.input_min < 1e-8:
            raise ValueError("Remap input_max must exceed input_min by at least 1e-8")
        return self


type Mapping = Annotated[LinearMapping | RemapMapping, Field(discriminator="kind")]


class CouplingSpec(Model):
    name: ControlName
    source: Channel = Field(
        description=(
            "Transform sources are evaluated LOCAL_SPACE XYZ radians, "
            "including constraints; other sources are native scalar values."
        )
    )
    target: Channel = Field(
        description=(
            "Writable native local channels; rotations require XYZ. Shape "
            "value and constraint influence must stay in their native ranges."
            " No paths or expressions."
        )
    )
    mapping: Mapping


class CouplingConfigureArguments(Model):
    couplings: list[CouplingSpec] = Field(min_length=1, max_length=64)
    replace: bool = Field(
        default=False,
        description=(
            "Explicitly replace named owned relationships; unrelated drivers "
            "and keyed targets are protected. Batch preflight and rollback."
        ),
    )

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({c.name for c in self.couplings}) != len(self.couplings):
            raise ValueError("Coupling names must be unique")
        targets = [c.target.model_dump_json() for c in self.couplings]
        if len(set(targets)) != len(targets):
            raise ValueError("A target can have only one coupling")
        return self


class CouplingInspectArguments(InspectArguments):
    limit: int = Field(default=16, ge=1, le=64)


class MotionRemoveArguments(Model):
    names: list[Name] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.names)) != len(self.names):
            raise ValueError("Names must be unique")
        return self


class MotionNames(Model):
    names: list[str]


class CouplingSummary(Model):
    name: str
    source: Channel
    target: Channel
    mapping: Mapping
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
    source_value: float | None = None
    mapped_value: float | None = None
    target_value: float | None = None
    evaluated_target_value: float | None = None
    mapping_error: float | None = None
    constrained_error: float | None = None
    saturated: bool = False
    dependencies: list[str] = Field(default_factory=list, max_length=64)


class CouplingInspectResult(Model):
    couplings: list[CouplingSummary]
    page: PageInfo


class ScalarProperty(Model):
    object_name: Name
    name: ControlName
    value: Scalar
    minimum: Scalar = 0.0
    maximum: Scalar = 1.0

    @model_validator(mode="after")
    def range(self) -> Self:
        if not self.minimum <= self.value <= self.maximum:
            raise ValueError("Scalar value must lie inside minimum..maximum")
        return self


class PropertiesArguments(Model):
    properties: list[ScalarProperty] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({(p.object_name, p.name) for p in self.properties}) != len(
            self.properties
        ):
            raise ValueError("Scalar property targets must be unique")
        return self


class TimelineArguments(Model):
    frame_start: Frame | None = None
    frame_end: Frame | None = None
    frame: Frame | None = None
    subframe: FiniteFloat | None = Field(default=None, ge=0, lt=1)
    fps: int | None = Field(default=None, ge=1, le=240)
    fps_base: FiniteFloat | None = Field(default=None, ge=0.1, le=10)
    use_preview_range: bool | None = None
    preview_start: Frame | None = None
    preview_end: Frame | None = None

    @model_validator(mode="after")
    def present(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, n) is None for n in self.model_fields_set
        ):
            raise ValueError("Supply a timeline patch; omit unchanged fields, not null")
        return self


class TimelineInspectArguments(Model):
    pass


class TimelineState(Model):
    frame_start: int
    frame_end: int
    frame: int
    subframe: float
    fps: int
    fps_base: float
    effective_fps: float
    use_preview_range: bool
    preview_start: int
    preview_end: int


type Interpolation = Literal["CONSTANT", "LINEAR", "BEZIER"]


class MotionKey(Model):
    frame: Frame
    value: Scalar
    interpolation: Interpolation = "LINEAR"


class ActionChannel(Model):
    target: Channel
    keys: list[MotionKey] = Field(default_factory=list, max_length=512)
    remove_frames: list[Frame] = Field(default_factory=list, max_length=512)
    replace_keys: bool = Field(
        default=False,
        description=(
            "Replace keys only in this channel; otherwise upsert exact "
            "integer frames and remove requested frames."
        ),
    )
    remove_channel: bool = False
    extrapolation: Literal["CONSTANT", "LINEAR"] | None = Field(
        default=None,
        description=(
            "Omitted preserves an existing curve; new curves default "
            "CONSTANT. Bounded scalar targets require CONSTANT."
        ),
    )

    @model_validator(mode="after")
    def valid(self) -> Self:
        frames = [k.frame for k in self.keys]
        if len(set(frames)) != len(frames) or len(set(self.remove_frames)) != len(
            self.remove_frames
        ):
            raise ValueError("Key and removal frames must be unique")
        if set(frames) & set(self.remove_frames):
            raise ValueError("Cannot upsert and remove the same frame")
        if self.remove_channel and (
            self.keys or self.remove_frames or self.replace_keys
        ):
            raise ValueError("Channel removal cannot include key edits")
        if not self.remove_channel and not self.keys and not self.remove_frames:
            raise ValueError("Supply keys, remove_frames or remove_channel")
        return self


class ActionEditArguments(Model):
    name: Name
    channels: list[ActionChannel] = Field(min_length=1, max_length=128)
    create: bool = Field(
        default=False,
        description=(
            "Create a new owned native action; otherwise edit an existing "
            "owned action. Does not assign it."
        ),
    )

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if len({c.target.model_dump_json() for c in self.channels}) != len(
            self.channels
        ):
            raise ValueError("Action channel targets must be unique")
        if sum(len(c.keys) + len(c.remove_frames) for c in self.channels) > 8192:
            raise ValueError("Action edit exceeds 8192 key changes")
        return self


class ActionAssignArguments(Model):
    name: Name
    replace: bool = Field(
        default=False,
        description=(
            "Assign all declared owner slots; replacing an existing active "
            "action requires true. Previous actions are retained."
        ),
    )
    detach: bool = Field(
        default=False,
        description=(
            "Detach only this action from its declared owners; retain data "
            "and current channel values."
        ),
    )


class ActionRemoveArguments(Model):
    name: Name


class ActionInspectArguments(Model):
    name: Name
    offset: int = Field(default=0, ge=0, le=128)
    limit: int = Field(default=16, ge=1, le=128)
    key_limit: int = Field(
        default=0,
        ge=0,
        le=512,
        description=(
            "Keys per returned channel, default zero; total detailed keys "
            "capped at 1024."
        ),
    )

    @model_validator(mode="after")
    def bound(self) -> Self:
        if self.limit * self.key_limit > 1024:
            raise ValueError(
                "Action key detail exceeds 1024; reduce limit or key_limit"
            )
        return self


class ActionChannelSummary(Model):
    target: Channel
    key_count: int
    frame_min: float | None
    frame_max: float | None
    interpolations: list[str]
    extrapolation: str
    assigned: bool
    driven: bool
    current_value: float | None
    evaluated_value: float | None
    keys: list[MotionKey]
    keys_truncated: bool


class ActionResult(Model):
    name: str
    channel_count: int
    key_count: int
    slot_count: int
    frame_min: float | None
    frame_max: float | None
    channels: list[ActionChannelSummary]
    next_offset: int | None
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=8)


class FrameRange(Model):
    start: Frame
    end: Frame
    step: int = Field(default=1, ge=1, le=1048574)

    @model_validator(mode="after")
    def bound(self) -> Self:
        if self.end < self.start or (self.end - self.start) // self.step + 1 > 128:
            raise ValueError(
                "Frame range must be ordered and contain at most 128 samples"
            )
        return self


class NamedChannel(Model):
    name: ControlName
    channel: Channel


class MotionThreshold(Model):
    metric: str = Field(min_length=1, max_length=256)
    minimum: Scalar | None = None
    maximum: Scalar | None = None

    @model_validator(mode="after")
    def bound(self) -> Self:
        if self.minimum is None and self.maximum is None:
            raise ValueError("Threshold requires minimum or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Threshold minimum exceeds maximum")
        return self


class MotionSampleArguments(Model):
    frames: list[Frame] = Field(default_factory=list, max_length=128)
    range: FrameRange | None = None
    reference_frame: Frame | None = Field(
        default=None,
        description=(
            "Evaluated geometry reference; defaults to first sampled frame. "
            "This is not an implicit rest pose."
        ),
    )
    channels: list[NamedChannel] = Field(default_factory=list, max_length=64)
    couplings: list[ControlName] = Field(default_factory=list, max_length=64)
    armature_object: Name | None = None
    bones: list[Name] = Field(default_factory=list, max_length=64)
    objects: list[Name] = Field(default_factory=list, max_length=8)
    targets: list[ComparisonPair] = Field(default_factory=list, max_length=8)
    volumes: list[VolumeQuery] = Field(default_factory=list, max_length=8)
    layers: LayerInspectArguments | None = None
    thresholds: list[MotionThreshold] = Field(default_factory=list, max_length=32)
    detail_frames: list[Frame] = Field(default_factory=list, max_length=8)
    violation_limit: int = Field(default=16, ge=0, le=64)

    def samples(self) -> list[int]:
        values = set(self.frames)
        if self.range:
            values.update(range(self.range.start, self.range.end + 1, self.range.step))
        return sorted(values)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        samples = self.samples()
        if not 1 <= len(samples) <= 128:
            raise ValueError("Supply 1..128 unique explicit/range frames")
        if not set(self.detail_frames) <= set(samples):
            raise ValueError("Detailed frames must belong to sampled frames")
        if self.bones and not self.armature_object:
            raise ValueError("Bone QA requires armature_object")
        if not (
            self.channels
            or self.couplings
            or self.bones
            or self.objects
            or self.targets
            or self.volumes
            or self.layers
        ):
            raise ValueError("Request at least one diagnostic")
        groups: list[list[str] | list[int]] = [
            self.frames,
            self.detail_frames,
            self.bones,
            self.objects,
            self.couplings,
            [c.name for c in self.channels],
            [t.metric for t in self.thresholds],
        ]
        for values in groups:
            if len(values) != len(set(values)):
                raise ValueError("Motion query names and frames must be unique")
        if self.layers and self.layers.worst_limit:
            raise ValueError(
                "Motion aggregation requires layers.worst_limit=0; use "
                "layer.inspect for locations at a reported worst frame"
            )
        return self


class MotionMetric(Model):
    name: str
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    p05: float | None
    p50: float | None
    p95: float | None
    minimum_frame: int | None
    maximum_frame: int | None
    violation_count: int = 0


class MotionViolation(Model):
    frame: int
    metric: str
    value: float | None
    reason: str


class MotionFrame(Model):
    frame: int
    values: dict[str, float | None]


class MotionSampleResult(Model):
    sampled_frames: list[int]
    reference_frame: int
    metrics: list[MotionMetric]
    details: list[MotionFrame]
    violations: list[MotionViolation]
    violation_count: int
    restored: bool
    evaluated_vertex_samples: int
    processing_seconds: float
    limitations: list[str]
