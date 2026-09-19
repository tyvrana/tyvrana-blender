"""Bounded presence and persistence audit of native document dependencies."""

from typing import Literal

from pydantic import Field, field_validator

from .file_models import FileState
from .models import Model
from .reference_models import Name


class RequiredFile(Model):
    filepath: str = Field(min_length=1, max_length=4096)
    minimum_bytes: int = Field(default=1, ge=0, le=2147483648)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("filepath")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("Paths must not contain null bytes")
        return value


class FileAuditArguments(Model):
    required_files: list[RequiredFile] = Field(default_factory=list, max_length=64)
    required_actions: list[Name] = Field(default_factory=list, max_length=64)
    limit: int = Field(default=16, ge=1, le=64)
    max_resources: int = Field(default=4096, ge=1, le=16384)
    max_hash_bytes: int = Field(default=134217728, ge=1, le=2147483648)


class DeliveryDependency(Model):
    kind: Literal[
        "document", "image", "library", "external", "cache", "action", "required_file"
    ]
    name: str
    status: Literal["present", "packed", "embedded", "missing", "unsaved", "unverified"]
    filepath: str | None = None
    detail: str | None = None
    byte_size: int | None = None


class FileAuditResult(Model):
    document: FileState
    ready: bool
    dependency_count: int
    counts: dict[str, int]
    issues: int
    dependencies: list[DeliveryDependency] = Field(max_length=64)
    truncated: bool
    hashed_bytes: int
    processing_seconds: float
    limitations: list[str]
