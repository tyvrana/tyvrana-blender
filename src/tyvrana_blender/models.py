"""Blender operation arguments and small result summaries."""

from ipaddress import ip_address
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

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
    pass


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
    object_count: int
    objects: list[ObjectSummary]


class DeleteResult(Model):
    deleted: str


class CyclesRenderOptions(Model):
    device: Literal["cpu", "gpu"] = "cpu"
    samples: int = Field(default=16, ge=1, le=512)
    denoise: bool = False


class RenderArguments(Model):
    width: int = Field(default=512, ge=64, le=1024)
    height: int = Field(default=512, ge=64, le=1024)
    format: Literal["png"] = "png"
    cycles: CyclesRenderOptions | None = None

    @field_validator("cycles", mode="before")
    @classmethod
    def non_null_cycles(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit cycles to use scene settings; null is invalid")
        return value


class RenderResult(Model):
    width: int
    height: int
    format: Literal["png"] = "png"
