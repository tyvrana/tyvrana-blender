"""Shared typed dispatch mechanics without loading the operation catalog."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from tyvrana_protocol import (
    ArtifactDescriptor,
    JsonValue,
    OperationContract,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
)

from .errors import OperationError

if TYPE_CHECKING:
    from .operations import SceneBackend
logger = logging.getLogger(__name__)
type Response = OperationSuccess | OperationFailure


def failure(request: OperationRequest, error: ProtocolError) -> OperationFailure:
    return OperationFailure(
        type="operation.failure", request_id=request.request_id, error=error
    )


@dataclass(frozen=True, slots=True)
class GuardedJob:
    """Native lifecycle with a pre-publication guard and atomic cancellation."""

    status: str
    cancel: str


@dataclass(frozen=True, slots=True)
class OperationSpec:
    contract: OperationContract
    parse: Callable[[JsonValue], BaseModel]
    invoke: Callable[
        [SceneBackend, BaseModel, OperationRequest],
        tuple[BaseModel, tuple[ArtifactDescriptor, ...]],
    ]

    guarded_job: GuardedJob | None = None


def argument_schema(validator: TypeAdapter[Any]) -> dict[str, Any]:
    """Discriminated unions require their tag before branch defaults are applied."""
    schema = validator.json_schema()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            discriminator = value.get("discriminator")
            if isinstance(discriminator, dict):
                tag = discriminator.get("propertyName")
                if isinstance(tag, str):
                    required = value.setdefault("required", [])
                    if tag not in required:
                        required.append(tag)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


def _operation[A: BaseModel, R: BaseModel](
    name: str,
    arguments: type[A] | TypeAdapter[A],
    result_model: type[R] | TypeAdapter[R],
    handler: Callable[
        [SceneBackend, A, OperationRequest], R | tuple[R, ArtifactDescriptor | None]
    ],
    description: str,
    *,
    effect: Literal["read_only", "mutating", "transient", "lifecycle"],
    execution: Literal["synchronous", "job_start", "job_status", "lifecycle"],
    guarded_job: GuardedJob | None = None,
    requires_interactive: bool = False,
    tags: tuple[str, ...] = (),
    input_artifacts: Literal["none", "required"] = "none",
    output_artifacts: Literal["none", "optional", "required"] = "none",
) -> OperationSpec:
    validator = TypeAdapter(arguments) if isinstance(arguments, type) else arguments
    result_validator = (
        TypeAdapter(result_model) if isinstance(result_model, type) else result_model
    )
    contract = OperationContract(
        name=name,
        description=description,
        category=name.split(".")[1],
        tags=tags,
        arguments_schema=argument_schema(validator),
        result_schema=result_validator.json_schema(mode="serialization"),
        effect=effect,
        execution=execution,
        requires_interactive=requires_interactive,
        input_artifacts=input_artifacts,
        output_artifacts=output_artifacts,
    )

    def invoke(
        backend: SceneBackend, parsed: BaseModel, request: OperationRequest
    ) -> tuple[BaseModel, tuple[ArtifactDescriptor, ...]]:
        value = handler(backend, cast(A, parsed), request)
        if isinstance(value, tuple):
            result, artifact = value
            artifacts = (artifact,) if artifact is not None else ()
        else:
            result, artifacts = value, ()
        return result_validator.validate_python(result), artifacts

    return OperationSpec(contract, validator.validate_python, invoke, guarded_job)


def _safe_text(value: str, limit: int) -> str:
    return value.encode("utf-8", "backslashreplace").decode("utf-8")[:limit]


def execute(
    backend: SceneBackend,
    request: OperationRequest,
    registry: Mapping[str, OperationSpec],
) -> Response:
    if not backend.operation_allowed(request.operation):
        return failure(
            request,
            ProtocolError(
                code="adapter_busy",
                message=(
                    "A native bake/dynamics job owns temporary scene resources; "
                    "inspect its status first"
                ),
            ),
        )
    spec = registry.get(request.operation)
    if spec is None:
        return failure(
            request,
            ProtocolError(
                code="operation_unsupported", message="Operation is not advertised"
            ),
        )
    try:
        arguments = spec.parse(request.arguments)
    except ValidationError as exc:
        return failure(request, argument_error(request.operation, exc))
    try:
        if spec.contract.input_artifacts == "none" and request.artifacts:
            raise OperationError(
                "invalid_arguments", "This operation accepts no input artifacts"
            )
        result, artifacts = spec.invoke(backend, arguments, request)
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


def argument_error(operation: str, exc: ValidationError) -> ProtocolError:
    details: list[JsonValue] = []
    for item in exc.errors(include_context=False, include_url=False)[:8]:
        received = item.get("input")
        preview = (
            str(received)[:120]
            if isinstance(received, (str, int, float, bool)) or received is None
            else f"{type(received).__name__} with {len(received)} items"
            if isinstance(received, (list, dict))
            else type(received).__name__
        )
        details.append(
            {
                "field": _safe_text(".".join(map(str, item["loc"])), 500),
                "message": _safe_text(item["msg"], 500),
                "reason": item["type"],
                "received": _safe_text(preview, 120),
            }
        )
    return ProtocolError(
        code="invalid_arguments",
        message=(
            f"Invalid arguments for {operation} "
            f"({exc.error_count()} errors; up to 8 shown)"
        ),
        details=details,
    )
