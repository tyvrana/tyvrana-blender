"""Isolated native renderer tests; job submission is covered through real MCP."""

import importlib
from typing import Any

from tyvrana_protocol import OperationFailure, OperationSuccess


def execute(backend: Any, request: Any) -> Any:
    package = "bl_ext.user_default.tyvrana_blender"
    operations = importlib.import_module(package + ".operations")
    if request.operation != "blender.render.image":
        return operations.execute(backend, request)
    renderer = importlib.import_module(package + ".render")
    models = importlib.import_module(package + ".models")
    try:
        result, descriptor = renderer.render_image(
            models.RenderArguments.model_validate(request.arguments), backend.spool
        )
        return OperationSuccess(
            type="operation.success",
            request_id=request.request_id,
            result=result.model_dump(mode="json"),
            artifacts=(descriptor,),
        )
    except operations.OperationError as exc:
        return OperationFailure(
            type="operation.failure", request_id=request.request_id, error=exc.error
        )
