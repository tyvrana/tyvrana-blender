"""Validated operation dispatch independent of Blender's Python module."""

import logging
from typing import Protocol

from pydantic import ValidationError
from tyvrana_protocol import (
    AdapterRegistration,
    ArtifactDescriptor,
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
)

from .camera_models import (
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraSetActiveArguments,
    CameraSummary,
)
from .models import (
    CreateArguments,
    DeleteArguments,
    DeleteResult,
    InspectArguments,
    Model,
    ObjectSummary,
    RenderArguments,
    RenderResult,
    SceneSummary,
    TransformArguments,
)

logger = logging.getLogger(__name__)
OPERATIONS = (
    "blender.camera.configure",
    "blender.camera.create",
    "blender.camera.inspect",
    "blender.camera.set_active",
    "blender.object.create_primitive",
    "blender.object.delete",
    "blender.object.set_transform",
    "blender.render.image",
    "blender.scene.inspect",
)
type Response = OperationSuccess | OperationFailure


def registration(instance_id: str, version: str, filepath: str) -> AdapterRegistration:
    return AdapterRegistration(
        type="adapter.register",
        instance_id=instance_id,
        application="blender",
        application_version=version,
        project_path=filepath or None,
        operations=OPERATIONS,
    )


class OperationError(Exception):
    def __init__(self, code: str, message: str, details: JsonValue = None) -> None:
        super().__init__(message)
        self.error = ProtocolError(code=code, message=message, details=details)


class SceneBackend(Protocol):
    def camera_inspect(self) -> CameraInspectResult: ...
    def camera_create(self, arguments: CameraCreateArguments) -> CameraSummary: ...
    def camera_configure(
        self, arguments: CameraConfigureArguments
    ) -> CameraSummary: ...
    def camera_set_active(
        self, arguments: CameraSetActiveArguments
    ) -> CameraSummary: ...
    def inspect(self) -> SceneSummary: ...
    def create(self, arguments: CreateArguments) -> ObjectSummary: ...
    def transform(self, arguments: TransformArguments) -> ObjectSummary: ...
    def delete(self, arguments: DeleteArguments) -> DeleteResult: ...
    def render(
        self, arguments: RenderArguments
    ) -> tuple[RenderResult, ArtifactDescriptor]: ...


def failure(request: OperationRequest, error: ProtocolError) -> OperationFailure:
    return OperationFailure(
        type="operation.failure", request_id=request.request_id, error=error
    )


def execute(backend: SceneBackend, request: OperationRequest) -> Response:
    try:
        arguments: (
            InspectArguments
            | CreateArguments
            | TransformArguments
            | DeleteArguments
            | RenderArguments
            | CameraCreateArguments
            | CameraConfigureArguments
            | CameraSetActiveArguments
        )
        match request.operation:
            case "blender.camera.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.camera.create":
                arguments = CameraCreateArguments.model_validate(request.arguments)
            case "blender.camera.configure":
                arguments = CameraConfigureArguments.model_validate(request.arguments)
            case "blender.camera.set_active":
                arguments = CameraSetActiveArguments.model_validate(request.arguments)
            case "blender.scene.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.object.create_primitive":
                arguments = CreateArguments.model_validate(request.arguments)
            case "blender.object.set_transform":
                arguments = TransformArguments.model_validate(request.arguments)
            case "blender.object.delete":
                arguments = DeleteArguments.model_validate(request.arguments)
            case "blender.render.image":
                arguments = RenderArguments.model_validate(request.arguments)
            case _:
                return failure(
                    request,
                    ProtocolError(
                        code="operation_unsupported",
                        message="Operation is not advertised",
                    ),
                )
    except ValidationError as exc:
        details: list[JsonValue] = [
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in exc.errors(
                include_input=False, include_context=False, include_url=False
            )
        ]
        return failure(
            request,
            ProtocolError(
                code="invalid_arguments",
                message="Invalid operation arguments",
                details=details,
            ),
        )
    try:
        result: Model
        artifacts: tuple[ArtifactDescriptor, ...] = ()
        if isinstance(arguments, InspectArguments):
            result = (
                backend.camera_inspect()
                if request.operation == "blender.camera.inspect"
                else backend.inspect()
            )
        elif isinstance(arguments, CameraCreateArguments):
            result = backend.camera_create(arguments)
        elif isinstance(arguments, CameraConfigureArguments):
            result = backend.camera_configure(arguments)
        elif isinstance(arguments, CameraSetActiveArguments):
            result = backend.camera_set_active(arguments)
        elif isinstance(arguments, CreateArguments):
            result = backend.create(arguments)
        elif isinstance(arguments, TransformArguments):
            result = backend.transform(arguments)
        elif isinstance(arguments, DeleteArguments):
            result = backend.delete(arguments)
        else:
            result, descriptor = backend.render(arguments)
            artifacts = (descriptor,)
        return OperationSuccess(
            type="operation.success",
            request_id=request.request_id,
            result=result.model_dump(mode="json"),
            artifacts=artifacts,
        )
    except OperationError as exc:
        logger.info("Operation %s failed: %s", request.operation, exc.error.code)
        return failure(request, exc.error)
    except Exception:
        logger.exception("Unexpected failure executing %s", request.operation)
        return failure(
            request,
            ProtocolError(
                code="operation_failed",
                message="Blender operation failed; see the application log",
            ),
        )
