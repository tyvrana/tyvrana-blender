"""Blender operation arguments and small result summaries."""

from ipaddress import ip_address
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

type Vector = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
type ObjectName = Annotated[str, Field(min_length=1, pattern=r"\S")]
type Primitive = Literal[
    "cube", "plane", "uv_sphere", "ico_sphere", "cylinder", "cone", "torus"
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ConnectionConfig(Model):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if not ip_address(value).is_loopback:
            raise ValueError("Core host must be a numeric loopback IP address")
        return value

    @property
    def uri(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"ws://{host}:{self.port}"


class InspectArguments(Model):
    names: list[ObjectName] | None = Field(default=None, min_length=1, max_length=64)
    prefix: str = Field(default="", max_length=128)
    offset: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=32, ge=1, le=128)

    @field_validator("names")
    @classmethod
    def unique_names(cls, names: list[str] | None) -> list[str] | None:
        if names is not None and len(set(names)) != len(names):
            raise ValueError("Names must be unique")
        return names


class SceneInspectArguments(InspectArguments):
    types: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None, min_length=1, max_length=32
    )


class PageInfo(Model):
    total_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    next_offset: int | None


class CreateArguments(Model):
    primitive: Primitive
    name: ObjectName | None = None
    location: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: Vector = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: Vector = Field(default_factory=lambda: [1.0, 1.0, 1.0])


class TransformArguments(Model):
    name: ObjectName
    location: Vector | None = None
    rotation: Vector | None = None
    scale: Vector | None = None

    @field_validator("location", "rotation", "scale", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an unchanged transform; null is not a vector")
        return value


class DeleteArguments(Model):
    name: ObjectName


class ObjectSummary(Model):
    name: str
    type: str
    location: Vector
    rotation: Vector
    scale: Vector
    dimensions: Vector
    visible: bool
    hide_viewport: bool
    hide_render: bool
    selected: bool
    parent: str | None


class SceneSummary(Model):
    name: str
    filepath: str | None
    active_object: str | None
    selected_objects: list[str]
    selected_object_count: int
    selected_objects_truncated: bool
    object_count: int
    objects: list[ObjectSummary]
    page: PageInfo


class DeleteResult(Model):
    deleted: str


class CyclesRenderOptions(Model):
    device: Literal["cpu", "gpu"] = "cpu"
    samples: int = Field(default=16, ge=1, le=4096)
    denoise: bool = False


class WireframeRenderOptions(Model):
    objects: list[ObjectName] = Field(min_length=1, max_length=16)
    thickness: FiniteFloat = Field(default=0.001, ge=0.000001, le=1)
    surface_offset: FiniteFloat = Field(default=0, ge=-1, le=1)

    @field_validator("objects")
    @classmethod
    def distinct_objects(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any("\x00" in name for name in value):
            raise ValueError("Provide distinct object names without NUL characters")
        return value


class UVCheckerRenderOptions(Model):
    objects: list[ObjectName] = Field(min_length=1, max_length=16)
    uv_map: ObjectName
    exclude_objects: list[ObjectName] = Field(default_factory=list, max_length=64)
    grid_scale: FiniteFloat = Field(default=1, ge=0.125, le=16)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        names = self.objects + self.exclude_objects
        if len(set(names)) != len(names):
            raise ValueError("Checker and excluded objects must be distinct")
        return self


class SurfaceRenderOptions(Model):
    objects: list[ObjectName] = Field(min_length=1, max_length=16)
    exclude_objects: list[ObjectName] = Field(default_factory=list, max_length=64)
    uv_map: ObjectName = "UVMap"
    normal_image: ObjectName | None = None
    preserve_materials: bool = False

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.preserve_materials and self.normal_image is not None:
            raise ValueError(
                "Existing material isolation cannot override a normal image"
            )
        if len(set(self.objects + self.exclude_objects)) != len(
            self.objects + self.exclude_objects
        ):
            raise ValueError("Surface and excluded objects must be distinct")
        return self


class RenderOutput(Model):
    filepath: str = Field(min_length=1, max_length=4096)
    overwrite: bool = False


class RenderBudget(Model):
    max_pixels: int = Field(default=4194304, ge=4096, le=67108864)
    max_total_pixels: int = Field(default=16777216, ge=4096, le=268435456)
    max_buffer_bytes: int = Field(default=536870912, ge=65536, le=2147483648)
    max_artifact_bytes: int = Field(default=67108864, ge=1024, le=134217728)
    max_seconds: float = Field(
        default=600,
        ge=1,
        le=7200,
        description=(
            "Deadline checked at native frame boundaries; Cycles also receives "
            "a remaining-time limit. Active frames drain before cleanup. "
            "Not a hard wall-clock or native-job preemption guarantee."
        ),
    )


class RenderColorOptions(Model):
    view_transform: ObjectName | None = None
    look: ObjectName | None = None
    exposure: FiniteFloat | None = Field(default=None, ge=-32, le=32)
    gamma: FiniteFloat | None = Field(default=None, ge=0.1, le=5)


class RenderAOV(Model):
    name: ObjectName
    type: Literal["COLOR", "VALUE"] = "COLOR"


RenderPass = Literal[
    "z",
    "normal",
    "position",
    "vector",
    "uv",
    "mist",
    "object_index",
    "material_index",
    "shadow",
    "ambient_occlusion",
    "emit",
    "environment",
    "diffuse_direct",
    "diffuse_indirect",
    "diffuse_color",
    "glossy_direct",
    "glossy_indirect",
    "glossy_color",
    "transmission_direct",
    "transmission_indirect",
    "transmission_color",
]


class InspectionView(Model):
    name: str = Field(min_length=1, max_length=64)
    orientation: Literal[
        "left", "right", "top", "bottom", "front", "rear", "oblique", "reverse_oblique"
    ]
    objects: list[ObjectName] = Field(default_factory=list, max_length=128)


class InspectionRenderOptions(Model):
    objects: list[ObjectName] = Field(default_factory=list, max_length=128)
    views: list[InspectionView] = Field(
        default_factory=lambda: [
            InspectionView(name=n, orientation=n)
            for n in ("left", "right", "top", "bottom", "front", "rear", "oblique")
        ],
        min_length=1,
        max_length=12,
    )
    columns: int = Field(default=3, ge=1, le=4)
    margin: FiniteFloat = Field(default=0.1, ge=0.01, le=1)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({v.name for v in self.views}) != len(self.views):
            raise ValueError("Inspection view names must be unique")
        for names in [self.objects, *(v.objects for v in self.views)]:
            if len(set(names)) != len(names):
                raise ValueError("Inspection object names must be unique")
        return self


class InspectionTile(Model):
    name: str
    orientation: str
    column: int
    row: int
    object_count: int


class RenderArguments(Model):
    width: int = Field(default=512, ge=64, le=16384)
    height: int = Field(default=512, ge=64, le=16384)
    format: Literal["png", "exr", "exr_multilayer"] = "png"
    bit_depth: Literal[8, 16, 32] = 8
    color_mode: Literal["RGB", "RGBA"] = "RGBA"
    color: RenderColorOptions | None = None
    passes: list[RenderPass] = Field(default_factory=list, max_length=16)
    aovs: list[RenderAOV] = Field(default_factory=list, max_length=8)
    frames: list[Annotated[int, Field(ge=-1048574, le=1048574)]] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Explicit frame sequence produces one ZIP with frames "
        "and metadata.",
    )
    budget: RenderBudget = Field(default_factory=RenderBudget)
    cycles: CyclesRenderOptions | None = None
    wireframe: WireframeRenderOptions | None = None
    uv_checker: UVCheckerRenderOptions | None = None
    surface: SurfaceRenderOptions | None = None
    inspection: InspectionRenderOptions | None = Field(
        default=None,
        description="Auto-framed Workbench multiview contact sheet. No persistent "
        "cameras/lights; native state restored. Width/height are per tile; PNG8 "
        "only. Empty object list selects scene meshes.",
    )
    show_result: bool = False
    wait_seconds: FiniteFloat = Field(default=5, ge=0, le=5)
    output: RenderOutput | None = None

    @field_validator("cycles", "wireframe", "uv_checker", "surface", mode="before")
    @classmethod
    def non_null_cycles(cls, value: object) -> object:
        if value is None:
            raise ValueError(
                "Omit render options to use scene settings; null is invalid"
            )
        return value

    @model_validator(mode="after")
    def diagnostic_options(self) -> Self:
        pixels = self.width * self.height
        if self.inspection is not None:
            if (
                self.frames
                or self.cycles
                or self.surface
                or self.wireframe
                or self.uv_checker
                or self.passes
                or self.aovs
                or self.show_result
            ):
                raise ValueError(
                    "Inspection contact sheets cannot combine with other render modes"
                )
            if (
                self.format != "png"
                or self.bit_depth != 8
                or max(self.width, self.height) > 1024
            ):
                raise ValueError("Inspection tiles require PNG8 and dimensions64..1024")
            columns = min(self.inspection.columns, len(self.inspection.views))
            rows = (len(self.inspection.views) + columns - 1) // columns
            pixels *= columns * rows
        if (
            pixels > self.budget.max_pixels
            or pixels * len(self.frames or [0]) > self.budget.max_total_pixels
        ):
            raise ValueError(
                "Render exceeds pixel budget; reduce dimensions/frames or "
                "explicitly raise budget"
            )
        # Conservative four-float channels per pass/AOV, before engine-specific memory.
        if (
            pixels * 16 * (1 + len(self.passes) + len(self.aovs))
            > self.budget.max_buffer_bytes
        ):
            raise ValueError(
                "Estimated output buffers exceed max_buffer_bytes; "
                "reduce passes/resolution"
            )
        if (
            self.format == "png"
            and self.bit_depth not in (8, 16)
            or self.format != "png"
            and self.bit_depth not in (16, 32)
        ):
            raise ValueError(
                "PNG requires bit_depth 8/16; EXR requires explicit bit_depth 16/32"
            )
        if (self.passes or self.aovs) and self.format != "exr_multilayer":
            raise ValueError("Selected passes/AOVs require exr_multilayer")
        if len(set(self.passes)) != len(self.passes) or len(
            {a.name for a in self.aovs}
        ) != len(self.aovs):
            raise ValueError("Passes and AOV names must be unique")
        if self.frames and len(set(self.frames)) != len(self.frames):
            raise ValueError("Sequence frames must be unique")
        if self.frames and self.show_result:
            raise ValueError(
                "show_result is for stills; retrieve sequence frames as artifacts"
            )
        if self.surface is not None and (
            self.wireframe is not None or self.uv_checker is not None
        ):
            raise ValueError("Use one surface diagnostic per render")
        if self.uv_checker is not None and (
            self.wireframe is not None or self.cycles is not None
        ):
            raise ValueError(
                "UV checker uses Eevee and cannot combine with wireframe or Cycles"
            )
        return self


class RenderColorMetadata(Model):
    display_device: str
    view_transform: str
    look: str
    exposure: float
    gamma: float
    output_encoding: Literal["display_referred", "scene_linear"]


class RenderResult(Model):
    width: int
    height: int
    format: Literal["png", "exr", "exr_multilayer"] = "png"
    bit_depth: Literal[8, 16, 32] = 8
    color_mode: Literal["RGB", "RGBA"] = "RGBA"
    color_management: RenderColorMetadata | None = None
    output_channels: list[str] = Field(default_factory=list, max_length=256)
    inspection_tiles: list[InspectionTile] = Field(default_factory=list, max_length=12)
