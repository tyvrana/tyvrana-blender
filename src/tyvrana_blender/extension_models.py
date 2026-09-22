"""Typed lifecycle control for this extension, never arbitrary modules or code."""

from typing import Annotated, Literal

from pydantic import Field

from .mesh_models import Arguments
from .models import Model


class ExtensionInspectArguments(Arguments):
    pass


class ExtensionReloadArguments(Arguments):
    expected_build: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ExtensionReloadResult(Model):
    reload_id: str
    status: Literal["scheduled"]
    previous_build: str
    new_build: str
    previous_adapter_id: str


class ExtensionState(Model):
    host_session_id: str | None = None
    document_session_id: str | None = None

    host_pid: int
    background: bool
    window_count: int
    application_version: str
    project_path: str | None
    project_id: str | None
    build: str
    implementation_build: str | None
    generation: int
    status: Literal[
        "idle", "scheduled", "reconnecting", "completed", "rolled_back", "failed"
    ]
    reload_id: str | None
    staged_build: str | None
    error: str | None
    adapter_id: str | None
    connection_state: str
    operation_count: int
    worker_pid: int | None
    runtime_timer_count: int
    lifecycle_timer_count: int
    handler_count: int
    registered_class_count: int
    artifact_count: int
