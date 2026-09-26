"""Canonical render-job contracts shared by host and networking dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tyvrana_protocol import OperationRequest

from .models import RenderArguments
from .operation_dispatch import Response, _operation
from .operation_dispatch import execute as dispatch
from .render_models import RenderJobArguments, RenderJobStatus, RenderStatusArguments

if TYPE_CHECKING:
    from .operations import SceneBackend
DECLARATIONS = (
    _operation(
        "blender.render.image",
        RenderArguments,
        RenderJobStatus,
        lambda b, a, q: b.render(a, q.request_id),
        "Render the connected host's live scene; interactive hosts use native async "
        "jobs. One active job; only render jobs/extension identity are available "
        "during work. inspection={} creates auto-framed Workbench multiview QA, "
        "without camera/light setup. Per-view selections, orthographic/perspective "
        "projection and bounded wireframes support close review. inspection.packet "
        "persists native up-to2048px views, overview and checksummed manifest in one "
        "directory, returning a compact preview; set total pixel budget explicitly. "
        "Otherwise use scene settings or temporary Cycles/diagnostic options. "
        "cycles.device=gpu requires an enabled available backend device; inspect "
        "render.devices. Unavailable GPU fails without CPU fallback; results report "
        "device/backend. PNG8/16, EXR half/full or multilayer passes/AOVs,64..16384px "
        "within pixel/buffer/artifact/time budgets. Up to64 frames return ZIP. "
        "wait_seconds0..5 returns an image on success or a job ID; use render.status "
        "event waits. Deadlines apply between frames; active frames drain before "
        "cleanup. Transient: authored document/settings/frame are restored on "
        "success, failure or cancellation; no semantic revision or acceptance is "
        "created. Inspection is allowed for stale/unverified bindings and does not "
        "verify them. Native Render Result and explicitly requested output may "
        "persist. Optional output persists atomically. "
        "Jobs survive disconnect, not reload/file-open; retain16 records/4 results. "
        "show_result requires an interactive still. Additional passes need multilayer; "
        "AOVs require shader nodes.",
        tags=("render", "production", "exr", "sequence", "artifact"),
        effect="transient",
        execution="job_start",
        output_artifacts="optional",
    ),
    _operation(
        "blender.render.status",
        RenderStatusArguments,
        RenderJobStatus,
        lambda b, a, q: b.render_status(a),
        "Compact render-job metadata only, never image bytes. Omit job_id to "
        "recover the active or most recent job after a lost submit reply. Bounded "
        "event wait 0..20s: return when revision differs from after_revision "
        "or job terminates; omitted revision observes current state then waits. "
        "Timeout returns unchanged status. Use 20s waits for long jobs. Running "
        "means native render entered; no sample percentage is claimed. Latest "
        "16 records retained until host/reload/file-open; result availability is "
        "separate. "
        "Unknown/evicted job: render_job_not_found; disconnected host has no "
        "queryable jobs. Cancelling this request only stops waiting.",
        tags=("render", "job", "progress"),
        effect="read_only",
        execution="job_status",
    ),
    _operation(
        "blender.render.cancel",
        RenderJobArguments,
        RenderJobStatus,
        lambda b, a, q: b.render_cancel(a),
        "Request frame-boundary cancellation; returns promptly with cancel_requested. "
        "A running native frame drains, remaining frames/output are discarded, "
        "then state becomes cancelled after host restoration. No unsafe native-job "
        "preemption, process signals or host termination. "
        "Repeated or terminal cancel is idempotent and preserves terminal "
        "state. Completion already committed wins; otherwise cancellation "
        "discards output. Unknown job: render_job_not_found.",
        tags=("render", "job", "cancellation"),
        effect="transient",
        execution="job_status",
    ),
    _operation(
        "blender.render.result",
        RenderJobArguments,
        RenderJobStatus,
        lambda b, a, q: b.render_result(a),
        "Retrieve succeeded PNG/EXR/sequence ZIP through binary artifact transfer. "
        "Only this operation and completed short submits deliver output bytes. "
        "Core releases inline images; export/release retained_artifact_ids. "
        "Retained source permits retries until evicted by four newer results "
        "or host/reload/file-open shutdown. Metadata retains success after eviction, "
        "with result_available=false. Unfinished/failed/cancelled/evicted "
        "output: render_result_unavailable. Explicit saved output persists.",
        tags=("render", "artifact", "production"),
        effect="read_only",
        execution="job_status",
        output_artifacts="required",
    ),
)
REGISTRY = {item.contract.name: item for item in DECLARATIONS}


def execute(backend: SceneBackend, request: OperationRequest) -> Response:
    return dispatch(backend, request, REGISTRY)
