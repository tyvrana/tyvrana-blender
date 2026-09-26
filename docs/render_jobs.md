# Still-render jobs

`blender.render.image` submits a render and returns a job identity promptly.
Rendering uses the connected Blender process and its live scene/resource IDs.
Interactive hosts invoke Blender's native asynchronous render job; headless test hosts
execute frames in their main thread. No second Blender, scene serialization, project
copy, or image-buffer copying is involved. Status includes `host_pid`,
`host_background`, scene/document identity, `execution="connected_host"` and
`cancellation="frame_boundary"`. Individual requests retain normal client deadlines.

## Short and long workflows

A short diagnostic render can take one call:

```json
{"width":256,"height":256,"cycles":{"samples":8}}
```

Submission waits at most `wait_seconds` (default 5, range 0–5). If the render
succeeds within that interval, the response includes its output artifact and MCP
image. Otherwise it returns current job metadata. Always inspect `state`.
Native engine initialization can still dominate tiny renders.

For long work, set `wait_seconds: 0`. Then call `blender.render.status`:

```json
{"job_id":"<returned job_id>","after_revision":2,"wait_seconds":20}
```

Use the returned revision on the next wait. A changed revision or terminal state
returns immediately; otherwise an asyncio event waits up to 20 seconds. A timeout
returns unchanged metadata with updated elapsed time. Omit `after_revision` to
observe the current revision and then wait. Omit `job_id` to recover the active
or most recent job after losing a submit reply. No retained job returns
`render_job_not_found`. Prefer bounded waits over repeated instant status calls.
Clients with shorter deadlines should choose a smaller wait bound.

On success, call `blender.render.result` with `job_id` for an image if submission
did not already deliver one. Status never attaches image bytes. Cancelling a
status wait or disconnecting a client does not cancel the render.

## States and observations

| State | Meaning |
| --- | --- |
| `queued` | Admitted; waiting for host preflight/native job start. |
| `running` | The host invoked native rendering. |
| `succeeded` | Validated output produced; optional output published. |
| `failed` | Preparation, native rendering, encoding, storage or output failed. |
| `cancel_requested` | Cancellation accepted; active native frame/cleanup still pending. |
| `cancelled` | Work stopped and temporary job output removed. |

`blender.render.cancel` returns promptly with `cancel_requested`. It prevents
remaining frames, waits for any active native frame to finish, discards output and
restores settings before reporting `cancelled`. Blender's Python API does not expose
a safe native render-job stop operation; cancellation does not send process signals,
inject input, kill the host or claim immediate GPU preemption. Queued cancellation
prevents rendering. Repeated/terminal cancellation is idempotent; a completion already
committed wins. A stalled native engine can delay cleanup indefinitely.

Metadata includes job ID, revision, UTC timestamps, elapsed time, engine, frame,
requested dimensions, requested Cycles samples when applicable, native render
duration on success, output availability/size/SHA-256 and a bounded error. Queued
metadata may not yet know the engine/frame. Native duration excludes output encoding. Adaptive sampling or a scene time limit can finish
before the requested maximum samples. No sample count, percentage or ETA is
reported: these are not dependable cross-engine observations. No UI text is read.

Invalid request schemas fail immediately. Host-specific validation and execution
failures normally reach `failed` after submission. Errors include `no_camera`,
`invalid_context`, `render_engine_unavailable`, `render_device_unavailable`,
`file_destination_invalid`, `file_exists`, `render_budget_exceeded`,
`render_failed`, and artifact/output errors. Normal results
contain concise repair guidance; detailed diagnostics remain in application logs.
Unknown jobs and unavailable results have separate errors.

## Rendering and state preservation

Rendering has the `transient` effect: it produces an observation, not an authored
document mutation or a semantic validation. Bound and unbound documents follow the
same render-job and artifact-transfer path. It does not enter guarded mutation
publication, advance the semantic revision, refresh bindings, accept milestones or
replace the trusted document head. Stale/unverified bindings do not prevent visual
inspection; authored edits, checkpoints and acceptance still require their normal
binding/content guards. A successful image alone never certifies project freshness.

Temporary native changes are exclusive to the active job and restored before a
terminal result, including failure and frame-boundary cancellation. Native Render
Result/slots, explicit output files/review packets and an explicitly requested
result display are the documented persistent outputs; authored scene content is
preserved. Viewport capture remains a separate interactive framebuffer operation,
while render jobs support both interactive and background hosts.

Native file open/new are document lifecycle transitions, not guarded edits to the
current document. They preserve Core's trusted heads and historical records; a new
loaded session is not implicitly verified. After the advertised registration wait,
reattach matching saved content with `project.attest` before further guarded edits.
Changed content cannot use reattachment to replace the trusted head. File saving
remains a guarded mutation on a bound document. Neither rendering nor file loading
implicitly accepts or repairs semantic state.

One canonical request configures both short and long rendering. Current camera,
frame, scene color management, transparency, lighting and supported engine are
retained. Built-in Cycles, Eevee and Workbench are supported; custom engines and
Python-dependent autoexecution are not. Optional Cycles controls select device,
1–4096 samples and denoising. Existing wireframe, UV checker and surface diagnostic
options remain available. Output formats and resource admission are described below. Resolution percentage becomes 100; borders, cropping, multiview and
sequencer output are disabled. Compositor processing remains; File Output nodes
are muted to prevent unintended external writes.

The connected host reads its actual live image buffers, dependencies, GPU preferences
and evaluated scene. External resources must remain available. Temporary resolution,
engine, pass, color and diagnostic overrides are restored after completion/failure;
the original timeline frame/subframe is restored. Object Mode is required. Rendering
does not save or reopen the project. Native Render Result/render slots are updated.
Native dirty flags alone are not a complete proof of unchanged data.

GPU device selection uses the connected host configuration. CPU/headless tests do
not establish desktop GPU feasibility. `show_result: true` fits native Render Result
in a suitable editor; native render invocation can also open Blender's configured
render display. Neither behavior silently changes the authoritative process.

## Ownership, concurrency and persistence

The adapter networking process owns jobs; core routes their typed operations and
owns received transfer artifacts. There is one active job and no work queue. The
`queued` state is preparation, not a promise of a scheduler. Another render returns
`adapter_busy`. During an active render only render job operations and extension identity are
available. Other inspections can evaluate geometry or alter sample time and are
therefore blocked along with edits, bake, persistence and reload. Native active
bake/render jobs prevent submission. Stop/wait for work before
changing project or extension generation.

The newest 16 metadata records and four successful result files are retained.
An active job is never evicted. Older result eviction preserves its success
record with `result_available: false`. Results are at most 128 MiB each; transfer
copies have a separate four-file spool limit. Each explicit retrieval uses
a fresh correlated binary transfer with byte-size and SHA-256 validation. Core automatically releases small inline image outputs. Larger images, EXR and
ZIP outputs are retained, with `retained_artifact_ids` returned to the client.
`artifact_delivery: "reference"` retains any output without inline image data.
`tyvrana_export_artifact` atomically saves a retained output on the Core host,
verifies its integrity and releases it by default. Explicit release is available
when no export is needed. The adapter source remains available for retries after
interrupted transfer. Status never duplicates binary transport.

Client/core disconnection does not delete jobs in a surviving adapter worker;
reconnect and inspect by ID or use the latest-job fallback. File open, extension
reload, worker shutdown and host termination discard job records and temporary
results. Core reports adapter loss instead of serving cached RUNNING state; a new
host/generation cannot recover the old job. Parent EOF causes the worker to delete its spool; there is no render child. An OS kill of the entire process tree can
prevent cleanup code; no crash-persistent job database is promised.

To keep an output after host shutdown, supply:

```json
{"width":512,"height":512,"output":{"filepath":"/absolute/output/still.png","overwrite":false}}
```

The parent directory must exist; absolute and Blender-relative output paths are
accepted, symlinks rejected. Validated output is staged in the destination
filesystem and atomically published. `overwrite` defaults false. The returned
`saved_filepath`, size and SHA identify this explicit persistent output, not an
artifact transport path. It is not automatically added to the `.blend` as an image
resource or semantic output relationship. Temporary artifacts and job metadata are
not saved with the project. Submission is conservatively classified `mutating`
because optional persistent output and interactive preview have lasting effects.

## Production formats and budgets

`format` is `png`, `exr` or `exr_multilayer`. PNG accepts `bit_depth` 8 (default)
or 16; EXR requires explicit 16 (half) or 32 (float). `color_mode` is RGB or RGBA.
EXR uses ZIP compression and preserves scene-linear HDR values. Multilayer outputs
support up to 16 selected native `passes` and eight named COLOR/VALUE `aovs`.
Author custom material values with `shader.node.create` / `output_aov` and normal
typed shader links. Pass availability depends on the selected engine; missing
requested output channels fail validation rather than silently succeeding.
Results include native channel names/types and color-management metadata. Optional
`color` overrides view transform, look, exposure and gamma using native validation.
PNG is display-referred; EXR is scene-linear regardless of display metadata.

| Budget | Default | Maximum |
| --- | ---: | ---: |
| Per-image pixels | 4,194,304 | 67,108,864 |
| Total sequence pixels | 16,777,216 | 268,435,456 |
| Estimated output buffers | 512 MiB | 2 GiB |
| Final artifact bytes | 64 MiB | 128 MiB |
| Frame-boundary deadline seconds | 600 | 7200 |

Each dimension is 64–16384. Requests exceeding default admission must explicitly
supply a larger allowed `budget`. Buffer admission uses a conservative 16 bytes per
pixel per combined/pass/AOV output; it is not a bound on all engine allocations.
The time budget is checked before/after native frames. Explicit Cycles settings also
receive the remaining native `time_limit`; synchronization/encoding may exceed it.
It is not a hard wall-clock or GPU preemption guarantee. Output/deadline failures
discard temporary results and restore the host before admitting new work.

`frames` is an optional unique list of up to 64 explicit integers. A sequence runs
in the connected host and returns an `application/zip` artifact containing frame
files and a JSON manifest with frame numbers, filenames and integrity hashes.
`frame_count` and `completed_frames` report progress; completed-frame events can be
consumed through revision waits. No percentage/ETA is inferred for an active frame.
`show_result` is restricted to stills. Persistent `output.filepath` must end in
`.png`, `.exr` or `.zip`, matching the request.

## Saved delivery audit

`file.audit` returns current native document identity, totals and worst-N dependencies.
It checks used images (packed, external, dirty/generated), linked library paths,
native external paths, actions and owned growth caches. Required files support minimum
size and optional SHA-256; required action names detect omissions. Default inventory
bound is 4096 resources (maximum16384), detail16 (maximum64), hash budget128MiB.

Missing/unsaved/unknown dependencies make `ready=false`. Sequence coverage, unknown
native simulation bakes and external physics cache coverage remain explicitly
unverified; a directory's existence does not certify a bake. Linked files are not
recursively opened. This is a persistence/presence audit, not appearance, motion or
continuous-cache acceptance. It never packs assets, saves data or rewrites paths.
