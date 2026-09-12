import logging

import pytest
from tyvrana_protocol import (
    ArtifactDescriptor,
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

from tyvrana_blender.camera_models import (
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraSetActiveArguments,
    CameraSummary,
)
from tyvrana_blender.light_models import (
    LightConfigureArguments,
    LightCreateArguments,
    LightInspectResult,
    LightSummary,
    light_state,
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

    def light_inspect(self) -> LightInspectResult:
        self.calls.append("light_inspect")
        return LightInspectResult(lights=[])

    def light_create(self, arguments: LightCreateArguments) -> LightSummary:
        self.calls.append("light_create")
        return light_summary(arguments.name or "Light")

    def light_configure(self, arguments: LightConfigureArguments) -> LightSummary:
        self.calls.append("light_configure")
        return light_summary(arguments.name)

    def camera_inspect(self) -> CameraInspectResult:
        self.calls.append("camera_inspect")
        return CameraInspectResult(active_camera=None, cameras=[])

    def camera_create(self, arguments: CameraCreateArguments) -> CameraSummary:
        self.calls.append("camera_create")
        return camera_summary(arguments.name or "Camera")

    def camera_configure(self, arguments: CameraConfigureArguments) -> CameraSummary:
        self.calls.append("camera_configure")
        return camera_summary(arguments.name)

    def camera_set_active(self, arguments: CameraSetActiveArguments) -> CameraSummary:
        self.calls.append("camera_set_active")
        return camera_summary(arguments.name)

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


def light_summary(name: str) -> LightSummary:
    return LightSummary.model_validate(
        {
            **light_state("point", {}, set()).model_dump(mode="json"),
            "name": name,
            "location": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
            "visible": True,
            "hide_viewport": False,
            "hide_render": False,
            "parent": None,
        }
    )


def camera_summary(name: str) -> CameraSummary:
    return CameraSummary(
        name=name,
        active=True,
        projection="perspective",
        location=[0, 0, 0],
        rotation=[0, 0, 0],
        scale=[1, 1, 1],
        lens_mm=50,
        ortho_scale=None,
        clip_start=0.1,
        clip_end=1000,
        shift_x=0,
        shift_y=0,
        sensor_width_mm=36,
        sensor_height_mm=24,
        sensor_fit="auto",
    )


@pytest.mark.parametrize(
    ("operation", "arguments", "method"),
    [
        ("blender.light.inspect", {}, "light_inspect"),
        ("blender.light.create", {"type": "point"}, "light_create"),
        ("blender.light.configure", {"name": "Light", "energy": 20}, "light_configure"),
        ("blender.camera.inspect", {}, "camera_inspect"),
        ("blender.camera.create", {}, "camera_create"),
        (
            "blender.camera.configure",
            {"name": "Camera", "lens_mm": 80},
            "camera_configure",
        ),
        ("blender.camera.set_active", {"name": "Camera"}, "camera_set_active"),
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
