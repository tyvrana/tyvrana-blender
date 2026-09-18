# Still-render jobs

`blender.render.image` submits a render and returns a job identity promptly.
Rendering runs in an owned background Blender process using a private snapshot of
the active scene. The connected Blender host remains available. There is no render
execution deadline; individual requests keep the normal core/client deadlines.

## Short and long workflows

A short diagnostic render can take one call:

```json
{"width":256,"height":256,"cycles":{"samples":8}}
```

Submission waits at most `wait_seconds` (default 5, range 0–5). If the render
succeeds within that interval, the response includes its output artifact and MCP
image. Otherwise it returns current job metadata. Always inspect `state`.
Process startup and scene snapshotting add overhead even for tiny renders.

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
| `queued` | Admitted; preparing the scene snapshot or starting the child. |
| `running` | The child entered native rendering. |
| `succeeded` | Validated output produced; optional output published. |
| `failed` | Preparation, native rendering, encoding, storage or output failed. |
| `cancel_requested` | Cancellation accepted; child exit/preparation cleanup pending. |
| `cancelled` | Work stopped and temporary job output removed. |

`blender.render.cancel` requests cancellation and returns promptly. Running work
receives SIGINT, followed by termination after 0.75 seconds and kill after a
further 0.5 seconds if necessary. The user host is never killed. Queued cancellation
prevents child execution; an already-running main-thread snapshot write finishes
before cleanup. Repeated cancellation is idempotent. Terminal cancellation returns
the existing terminal state. A completion committed before cancellation wins;
accepted cancellation before that point discards the result.

Metadata includes job ID, revision, UTC timestamps, elapsed time, engine, frame,
requested dimensions, requested Cycles samples when applicable, native render
duration on success, output availability/size/SHA-256 and a bounded error. Queued
metadata may not yet know the engine/frame. Native duration excludes process
startup and output encoding. Adaptive sampling or a scene time limit can finish
before the requested maximum samples. No sample count, percentage or ETA is
reported: these are not dependable cross-engine observations. No UI text is read.

Invalid request schemas fail immediately. Host-specific validation and execution
failures normally reach `failed` after submission. Errors include `no_camera`,
`invalid_context`, `render_engine_unavailable`, `render_device_unavailable`,
`file_destination_invalid`, `file_exists`, `render_snapshot_limit`,
`render_failed`, `render_process_failed`, and artifact/output errors. Normal results
contain concise repair guidance; detailed diagnostics remain in application logs.
Unknown jobs and unavailable results have separate errors.

## Rendering and state preservation

One canonical request configures both short and long rendering. Current camera,
frame, scene color management, transparency, lighting and supported engine are
retained. Built-in Cycles, Eevee and Workbench are supported; custom engines and
Python-dependent autoexecution are not. Optional Cycles controls select device,
1–4096 samples and denoising. Existing wireframe, UV checker and surface diagnostic
options remain available. Output formats and resource admission are described below. Resolution percentage becomes 100; borders, cropping, multiview and
sequencer output are disabled. Compositor processing remains; File Output nodes
are muted to prevent unintended external writes.

The parent scene is serialized without saving or changing the open project.
Live generated/dirty image buffers are copied privately so unsaved bake/paint data
are preserved, bounded at 128 images and 512 MiB across loaded used images. Scene
snapshot I/O happens on the host main thread and can briefly delay other host
operations; networking submission/status/cancellation remain responsive. External
linked resources and caches must remain accessible and stable. Unsaved simulation
caches, custom render engines and arbitrary Python drivers are not reproduced.
Object Mode is required. Snapshot storage scales with the native scene size.

Temporary overrides and diagnostic resources exist in the child, using the same
restoring renderer implementation on success/failure. Cancellation destroys that
isolated state. The original scene settings, images, geometry and materials stay
unchanged. GPU device selections are copied from the existing configuration to
the child's temporary preferences; preferences are never saved. Device availability
must be checked in the actual deployment environment. CPU headless testing does
not establish desktop GPU feasibility or performance.

`show_result: true` requires an interactive host and a suitable editor. On
completion it loads and packs the preview as `Tyvrana Render` and fits it in the
largest active-window 3D View or Image Editor. This deliberately changes that
editor. It does not populate the host's native Render Result or render slots.
The default false leaves editors and image resources unchanged.

## Ownership, concurrency and persistence

The adapter networking process owns jobs; core routes their typed operations and
owns received transfer artifacts. There is one active job and no work queue. The
`queued` state is preparation, not a promise of a scheduler. Another render returns
`adapter_busy`. During an active render, read-only inspection is allowed; bake,
file open/save, extension reload and other mutations are rejected. Native active
bake/render jobs also prevent snapshot preparation. Stop/wait for work before
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
host/generation cannot recover the old job. Parent EOF causes the worker to reap
its render child and delete its spool. An OS kill of the entire process tree can
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
| Child execution seconds | 600 | 7200 |

Each dimension is 64–16384. Requests exceeding default admission must explicitly
supply a larger allowed `budget`. Buffer admission uses a conservative 16 bytes per
pixel per combined/pass/AOV output; it is not a bound on all engine allocations.
The time budget covers child execution, not host snapshot preparation. Output
budget failures discard temporary results. Cancellation uses the same existing job
lifecycle for every format.

`frames` is an optional unique list of up to 64 explicit integers. A sequence runs
in one isolated child and returns an `application/zip` artifact containing frame
files and a JSON manifest with frame numbers, filenames and integrity hashes.
`frame_count` and `completed_frames` report progress; completed-frame events can be
consumed through revision waits. No percentage/ETA is inferred for an active frame.
`show_result` is restricted to stills. Persistent `output.filepath` must end in
`.png`, `.exr` or `.zip`, matching the request.
