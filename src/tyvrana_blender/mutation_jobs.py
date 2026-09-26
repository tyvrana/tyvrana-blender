"""Guard one advertised mutation between strong observations on the main thread."""

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic import ValidationError
from tyvrana_protocol import OperationRequest, ProtocolError

from .attestation_jobs import guarded_steps
from .errors import OperationError
from .incoming import input_path
from .mutation_models import MutationArguments, MutationJobStatus, MutationResult
from .native_jobs import NativeJobs, publication_guard
from .operation_dispatch import argument_error

if TYPE_CHECKING:
    from .operations import SceneBackend
    from .restore_models import RestoreArguments, RestoreJobStatus

_jobs: NativeJobs[MutationJobStatus] = NativeJobs()
busy = _jobs.busy
status = _jobs.status
tick = _jobs.tick
cancel = _jobs.cancel
shutdown = _jobs.shutdown
_controls: set[str] = set()


def allows(operation: str) -> bool:
    return operation in _controls


def steps(
    backend: "SceneBackend", arguments: MutationArguments, request: OperationRequest
) -> Generator[dict[str, Any], None, MutationResult]:
    from .operations import REGISTRY

    spec = REGISTRY.get(arguments.operation)
    if (
        spec is None
        or spec.contract.effect != "mutating"
        or (spec.contract.execution != "synchronous" and spec.guarded_job is None)
        or spec.contract.output_artifacts != "none"
    ):
        raise OperationError(
            "mutation_contract_unsupported",
            "Guarded publication requires a synchronous mutation or a qualified "
            "atomic job, without output artifacts",
        )
    try:
        parsed = spec.parse(arguments.arguments)
    except ValidationError as exc:
        error = argument_error(arguments.operation, exc)
        raise OperationError(error.code, error.message, error.details) from None
    if spec.contract.input_artifacts == "none" and request.artifacts:
        raise OperationError(
            "invalid_arguments", "This operation accepts no input artifacts"
        )
    before = yield from guarded_steps()
    if before.root.get("status") != "complete" or any(
        before.root.get(field) != getattr(arguments, field)
        for field in (
            "host_session_id",
            "document_session_id",
            "project_id",
            "format",
            "digest",
        )
    ):
        raise OperationError(
            "content_diverged", "Current document does not match the authorized head"
        )
    observed = {
        (r["resource_kind"], r["resource_id"]): r
        for r in cast(list[dict[str, Any]], before.root.get("resources", []))
    }
    if any(
        observed.get((r.resource_kind, r.resource_id), {}).get("state") != "present"
        for r in arguments.resources
    ):
        raise OperationError(
            "resource_attestation_required",
            "Bound resource coverage is incomplete; no mutation performed",
        )
    # No yield between the final precondition check and the typed invocation.
    # Both execute on the host main thread. No arbitrary code/property interface.
    nested = request.model_copy(
        update={"operation": arguments.operation, "arguments": arguments.arguments}
    )

    def revalidate() -> Generator[dict[str, Any], None, None]:
        current = yield from guarded_steps()
        if current.root.get("status") != "complete" or any(
            current.root.get(field) != before.root.get(field)
            for field in (
                "host_session_id",
                "document_session_id",
                "project_id",
                "format",
                "digest",
            )
        ):
            raise OperationError(
                "content_diverged",
                "Document changed while preparing the mutation; nothing published",
            )

    token = publication_guard.set(revalidate)
    try:
        result, artifacts = spec.invoke(backend, parsed, nested)
    finally:
        publication_guard.reset(token)
    if artifacts:
        raise OperationError("mutation_contract_invalid", "Unexpected output artifact")
    lifecycle = spec.guarded_job
    if lifecycle is not None:
        identifier = result.model_dump()["job_id"]
        status_spec, cancel_spec = (
            REGISTRY[lifecycle.status],
            REGISTRY[lifecycle.cancel],
        )
        job_args = status_spec.parse({"job_id": identifier})
        _controls.update((lifecycle.status, lifecycle.cancel))
        try:
            while result.model_dump()["state"] in {"queued", "running"}:
                yield {
                    "progress": {"operation": arguments.operation, "job_id": identifier}
                }
                result, _ = status_spec.invoke(backend, job_args, nested)
            terminal = result.model_dump(mode="json")
            if terminal["state"] != "completed":
                error = (
                    ProtocolError.model_validate(terminal["error"])
                    if terminal.get("error")
                    else ProtocolError(
                        code="operation_cancelled",
                        message="Native authoring job cancelled; nothing published",
                    )
                )
                raise OperationError(error.code, error.message, error.details)
        finally:
            # Closing the outer receipt must not leave unowned work able to publish.
            cancel_spec.invoke(
                backend, cancel_spec.parse({"job_id": identifier}), nested
            )
            _controls.clear()
    after = yield from guarded_steps()
    if after.root.get("status") != "complete" or any(
        after.root.get(field) != before.root.get(field)
        for field in ("host_session_id", "document_session_id", "project_id", "format")
    ):
        raise OperationError(
            "mutation_unqualified", "Mutation completion could not be strongly verified"
        )
    return MutationResult(
        dict(
            mutation_id=arguments.mutation_id,
            result=result.model_dump(mode="json"),
            before=before.root,
            after=after.root,
        )
    )


def start(
    backend: "SceneBackend",
    arguments: MutationArguments,
    request: OperationRequest,
    spool: Path | None = None,
) -> MutationJobStatus:
    if busy():
        raise OperationError("adapter_busy", "A guarded mutation is active")
    # The worker owns received bytes only until the admission response. Retain
    # hard links under a distinct correlation ID until the native job exits.
    retained = request.model_copy(update={"request_id": uuid4().hex})

    def owned() -> Generator[dict[str, Any], None, MutationResult]:
        paths: list[Path] = []
        try:
            if request.artifacts:
                if spool is None:
                    raise OperationError(
                        "invalid_context", "Input storage is unavailable"
                    )
                for descriptor in request.artifacts:
                    path = input_path(
                        spool, retained.request_id, descriptor.artifact_id
                    )
                    path.hardlink_to(
                        input_path(spool, request.request_id, descriptor.artifact_id)
                    )
                    paths.append(path)
            yield {}  # Enter ownership before returning queued, including cancellation.
            return (yield from steps(backend, arguments, retained))
        finally:
            for path in paths:
                path.unlink(missing_ok=True)

    work = owned()
    next(work)
    return _jobs.start(
        MutationJobStatus(job_id=arguments.mutation_id, state="queued"), work
    )


def restore_steps(
    backend: "SceneBackend", arguments: "RestoreArguments", request: OperationRequest
) -> Generator[dict[str, Any], None, MutationResult]:
    from . import files
    from .file_models import FileOpenArguments
    from .operations import REGISTRY

    spec = REGISTRY.get(arguments.operation)
    if spec is None or "document_open" not in spec.contract.tags:
        raise OperationError(
            "restore_contract_unsupported", "Expected typed document open"
        )
    parsed = spec.parse({"filepath": arguments.locator, "discard_current": True})
    if not isinstance(parsed, FileOpenArguments):
        raise OperationError(
            "restore_contract_unsupported", "Expected file open arguments"
        )
    before = yield from guarded_steps()
    if before.root.get("status") != "complete" or any(
        before.root.get(key) != value for key, value in arguments.current.items()
    ):
        raise OperationError(
            "restore_current_changed", "Discard authorization is stale"
        )
    # The final live guard, file identity check and typed load do not yield.
    # UI/other Tyvrana mutations cannot enter between authorization and discard.
    files.verify_restore_source(parsed.filepath, arguments.target["file_sha256"])
    nested = request.model_copy(
        update=dict(
            operation=arguments.operation, arguments=parsed.model_dump(mode="json")
        )
    )
    result, artifacts = spec.invoke(backend, parsed, nested)
    if artifacts:
        raise OperationError("restore_contract_invalid", "Unexpected output artifact")
    after = yield from guarded_steps()
    if after.root.get("status") != "complete" or any(
        after.root.get(key) != value for key, value in arguments.target.items()
    ):
        raise OperationError(
            "restore_post_mismatch", "Loaded content differs from the trusted target"
        )
    return MutationResult(
        dict(
            mutation_id=arguments.mutation_id,
            result=result.model_dump(mode="json"),
            before=before.root,
            after=after.root,
        )
    )


def start_restore(
    backend: "SceneBackend", arguments: "RestoreArguments", request: OperationRequest
) -> "RestoreJobStatus":
    from .restore_models import RestoreJobStatus

    return cast(
        RestoreJobStatus,
        _jobs.start(
            RestoreJobStatus(job_id=arguments.mutation_id, state="queued"),
            restore_steps(backend, arguments, request),
        ),
    )
