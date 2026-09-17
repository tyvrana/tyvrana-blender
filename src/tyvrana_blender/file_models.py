"""Explicit native project persistence, separate from artifact transport."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator

from .mesh_models import Arguments
from .models import Model


class FileInspectArguments(Arguments):
    pass


class FileSaveArguments(Arguments):
    filepath: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    overwrite: bool = False

    @field_validator("filepath")
    @classmethod
    def blend_path(cls, value: str | None) -> str | None:
        if value is not None and (
            "\0" in value
            or not Path(value).is_absolute()
            or value.startswith("//")
            or Path(value).suffix != ".blend"
        ):
            raise ValueError("Use an absolute native path ending in .blend")
        return value


class FileOpenArguments(Arguments):
    filepath: Annotated[str, Field(min_length=1, max_length=4096)]
    discard_current: Literal[True]
    load_ui: bool = False

    @field_validator("discard_current", mode="before")
    @classmethod
    def explicit_replacement(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Opening a project requires discard_current: true")
        return value

    @field_validator("filepath")
    @classmethod
    def blend_path(cls, value: str) -> str:
        FileSaveArguments.blend_path(value)
        return value


class FileState(Model):
    project_id: str | None = None
    filepath: str | None
    is_saved: bool
    is_dirty: bool
    exists: bool
    byte_size: int | None
