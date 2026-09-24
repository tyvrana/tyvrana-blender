"""Guard one advertised mutation between strong observations on the main thread."""

from collections.abc import Generator
from typing import TYPE_CHECKING, Any, cast

from tyvrana_protocol import OperationRequest

from .attestation_jobs import guarded_steps
from .errors import OperationError
from .mutation_models import MutationArguments, MutationJobStatus, MutationResult
from .native_jobs import NativeJobs

if TYPE_CHECKING:
    from .operations import SceneBackend
    from .restore_models import RestoreArguments, RestoreJobStatus

_jobs: NativeJobs[MutationJobStatus] = NativeJobs()
busy = _jobs.busy
status = _jobs.status
tick = _jobs.tick
cancel = _jobs.cancel
shutdown = _jobs.shutdown


def steps(
    backend: "SceneBackend", arguments: MutationArguments, request: OperationRequest
) -> Generator[dict[str, Any], None, MutationResult]:
    from .operations import REGISTRY

    spec = REGISTRY.get(arguments.operation)
    if (
        spec is None
        or spec.contract.effect != "mutating"
        or spec.contract.execution != "synchronous"
        or spec.contract.input_artifacts != "none"
        or spec.contract.output_artifacts != "none"
    ):
        raise OperationError(
            "mutation_contract_unsupported",
            "Guarded publication requires a synchronous advertised mutation "
            "without artifact transfers",
        )
    parsed = spec.parse(arguments.arguments)
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
    result, artifacts = spec.invoke(backend, parsed, nested)
    if artifacts:
        raise OperationError("mutation_contract_invalid", "Unexpected output artifact")
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
    backend: "SceneBackend", arguments: MutationArguments, request: OperationRequest
) -> MutationJobStatus:
    return _jobs.start(
        MutationJobStatus(job_id=arguments.mutation_id, state="queued"),
        steps(backend, arguments, request),
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
