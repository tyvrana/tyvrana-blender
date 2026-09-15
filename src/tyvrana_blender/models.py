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
    samples: int = Field(default=16, ge=1, le=512)
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


class RenderArguments(Model):
    width: int = Field(default=512, ge=64, le=1024)
    height: int = Field(default=512, ge=64, le=1024)
    format: Literal["png"] = "png"
    cycles: CyclesRenderOptions | None = None
    wireframe: WireframeRenderOptions | None = None
    uv_checker: UVCheckerRenderOptions | None = None
    surface: SurfaceRenderOptions | None = None
    show_result: bool = False

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


class RenderResult(Model):
    width: int
    height: int
    format: Literal["png"] = "png"
