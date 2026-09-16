"""Bounded render-job observations independent of Blender and transport."""

from typing import Literal

from pydantic import Field

from .models import Model, RenderResult

JobState = Literal[
    "queued", "running", "succeeded", "failed", "cancel_requested", "cancelled"
]
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class RenderJobArguments(Model):
    job_id: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")


class RenderStatusArguments(Model):
    job_id: str | None = Field(
        default=None, min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$"
    )
    after_revision: int | None = Field(default=None, ge=0)
    wait_seconds: float = Field(default=0, ge=0, le=20, allow_inf_nan=False)


class RenderJobError(Model):
    code: str = Field(max_length=80)
    message: str = Field(max_length=500)


class RenderJobStatus(RenderResult):
    job_id: str
    state: JobState
    revision: int = 0
    submitted_at: str
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float = 0
    render_seconds: float | None = None
    engine: str | None = None
    frame: int | None = None
    samples_requested: int | None = None
    result_available: bool = False
    byte_size: int | None = None
    sha256: str | None = None
    saved_filepath: str | None = None
    error: RenderJobError | None = None
