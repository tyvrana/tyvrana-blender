"""Generated image resources and their color interpretation metadata."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator
from tyvrana_protocol import ArtifactId

from .models import Model, ObjectName, PageInfo
from .numeric import Float32

type GeneratedType = Literal["blank", "uv_grid", "color_grid"]
type AlphaMode = Literal["straight", "premultiplied", "channel_packed", "none"]
type Dimension = Annotated[int, Field(ge=1, le=4096)]
type FillComponent = Annotated[Float32, Field(ge=0, le=1)]
type FillColor = Annotated[list[FillComponent], Field(min_length=4, max_length=4)]

ALPHA_MODES: dict[AlphaMode, str] = {
    "straight": "STRAIGHT",
    "premultiplied": "PREMUL",
    "channel_packed": "CHANNEL_PACKED",
    "none": "NONE",
}


class ImageSummary(Model):
    name: str
    source: str
    width: int
    height: int
    channels: int
    has_alpha: bool
    is_float: bool
    color_space: str | None
    alpha_mode: AlphaMode
    packed: bool
    users: int
    dirty: bool
    generated_type: GeneratedType | None


class ImageInspectResult(Model):
    page: PageInfo
    images: list[ImageSummary]


class ImageCreateArguments(Model):
    name: ObjectName | None = None
    width: Dimension
    height: Dimension
    generated_type: GeneratedType = "blank"
    color: FillColor = Field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    alpha: bool = True
    float_buffer: bool = False
    color_space: ObjectName | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an optional property; null is invalid")
        return value

    @model_validator(mode="after")
    def meaningful_fill(self) -> Self:
        if "color" in self.model_fields_set and self.generated_type != "blank":
            raise ValueError("color applies only to blank generated images")
        if not self.alpha and self.color[3] != 1:
            raise ValueError("Images without alpha require an opaque fill color")
        return self


class ImageConfigureArguments(Model):
    name: ObjectName
    color_space: ObjectName | None = None
    alpha_mode: AlphaMode | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an unchanged property; null is invalid")
        return value


class ImageArtifactSpec(Model):
    artifact_id: ArtifactId
    name: ObjectName | None = None
    color_space: ObjectName | None = None
    alpha_mode: AlphaMode | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit an optional property; null is invalid")
        return value


class ImageFromArtifactArguments(Model):
    images: list[ImageArtifactSpec] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        names = [item.name for item in self.images if item.name is not None]
        if len(names) != len(set(names)):
            raise ValueError("Explicit image names must be unique")
        return self


class ImageImportResult(Model):
    images: list[ImageSummary]


class ImagePreviewArguments(Model):
    names: list[ObjectName] = Field(min_length=1, max_length=16)
    tile_size: int = Field(default=512, ge=128, le=1024)
    columns: int = Field(default=3, ge=1, le=4)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.names)) != len(self.names):
            raise ValueError("Preview image names must be unique")
        return self


class ImagePreviewTile(Model):
    name: str
    width: int
    height: int
    color_space: str
    row: int
    column: int


class ImagePreviewResult(Model):
    width: int
    height: int
    tiles: list[ImagePreviewTile]
