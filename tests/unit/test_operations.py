import logging

import pytest
from tyvrana_protocol import (
    ArtifactDescriptor,
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

from tyvrana_blender.models import (
    CreateArguments,
    DeleteArguments,
    DeleteResult,
    ObjectSummary,
    RenderArguments,
    RenderResult,
    SceneSummary,
    TransformArguments,
)
from tyvrana_blender.operations import OperationError, execute


class Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def render(
        self, arguments: RenderArguments
    ) -> tuple[RenderResult, ArtifactDescriptor]:
        self.calls.append("render")
        return RenderResult(
            width=arguments.width, height=arguments.height
        ), ArtifactDescriptor(
            artifact_id="1" * 32, media_type="image/png", byte_size=1, sha256="0" * 64
        )

    def inspect(self) -> SceneSummary:
        self.calls.append("inspect")
        return SceneSummary(
            name="Scene",
            filepath=None,
            active_object=None,
            selected_objects=[],
            object_count=0,
            objects=[],
        )

    def create(self, arguments: CreateArguments) -> ObjectSummary:
        self.calls.append("create")
        return ObjectSummary(
            name=arguments.name or "Cube",
            type="MESH",
            location=arguments.location,
            rotation=arguments.rotation,
            scale=arguments.scale,
            dimensions=[2, 2, 2],
            visible=True,
            hide_viewport=False,
            hide_render=False,
            selected=True,
            parent=None,
        )

    def transform(self, arguments: TransformArguments) -> ObjectSummary:
        raise OperationError(
            "object_not_found", "Object is missing", {"name": arguments.name}
        )

    def delete(self, arguments: DeleteArguments) -> DeleteResult:
        self.calls.append("delete")
        return DeleteResult(deleted=arguments.name)


def call(
    backend: Backend, operation: str, arguments: JsonValue
) -> OperationSuccess | OperationFailure:
    return execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="request-a",
            operation=operation,
            arguments=arguments,
        ),
    )


@pytest.mark.parametrize(
    ("operation", "arguments", "method"),
    [
        ("blender.scene.inspect", {}, "inspect"),
        ("blender.render.image", {}, "render"),
        (
            "blender.object.create_primitive",
            {"primitive": "cube", "name": "Example"},
            "create",
        ),
        ("blender.object.delete", {"name": "Example"}, "delete"),
    ],
)
def test_dispatch_calls_only_the_selected_handler(
    operation: str, arguments: JsonValue, method: str
) -> None:
    backend = Backend()
    result = call(backend, operation, arguments)
    assert isinstance(result, OperationSuccess)
    assert result.request_id == "request-a"
    assert backend.calls == [method]


@pytest.mark.parametrize(
    "arguments",
    [None, [], {}, {"primitive": "duck"}, {"primitive": "cube", "location": [1, 2]}],
)
def test_invalid_arguments_do_not_call_blender(arguments: JsonValue) -> None:
    backend = Backend()
    result = call(backend, "blender.object.create_primitive", arguments)
    assert isinstance(result, OperationFailure)
    assert result.error.code == "invalid_arguments"
    assert result.error.details
    assert backend.calls == []


def test_known_errors_preserve_details_without_stack_traces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = call(Backend(), "blender.object.set_transform", {"name": "missing"})
    assert isinstance(result, OperationFailure)
    assert result.error.code == "object_not_found"
    assert result.error.details == {"name": "missing"}
    assert all(record.exc_info is None for record in caplog.records)


def test_unsupported_operation() -> None:
    result = call(Backend(), "blender.not_implemented", {})
    assert isinstance(result, OperationFailure)
    assert result.error.code == "operation_unsupported"


@pytest.mark.parametrize("bad_model", [True, False])
def test_internal_failures_are_logged_and_sanitized(
    bad_model: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend = Backend()

    def broken() -> SceneSummary:
        if bad_model:
            return SceneSummary.model_validate({"private": "internal diagnostic"})
        raise RuntimeError("internal diagnostic")

    monkeypatch.setattr(backend, "inspect", broken)
    result = call(backend, "blender.scene.inspect", {})
    assert isinstance(result, OperationFailure)
    assert result.error.code == "operation_failed"
    assert "internal diagnostic" not in result.model_dump_json()
    assert (
        len([record for record in caplog.records if record.levelno >= logging.ERROR])
        == 1
    )
