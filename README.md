# tyvrana-blender

Blender extension for the Tyvrana autonomous 3D creation system. It connects a
running Blender instance to [tyvrana-core](https://github.com/tyvrana/tyvrana-core)
and performs validated scene operations through the canonical
[tyvrana-protocol](https://github.com/tyvrana/tyvrana-protocol) contracts.

Tested with **Blender 5.2.1 LTS on Linux x64**, using its embedded Python 3.13.
The extension currently provides scene summaries and basic mesh object creation,
transformation, deletion, and PNG scene renders delivered as MCP images.

## Install and connect

Build the extension as described below, then install and enable it with Blender's
official extension command:

```sh
blender --command extension install-file --repo user_default --enable dist/tyvrana_blender-0.1.0.zip
```

When replacing an enabled installation with changed dependencies, first disable
Tyvrana in Preferences, save preferences, and exit Blender. Install from a fresh
Blender process so previously imported dependency modules are not reused.

Alternatively, use **Preferences → Get Extensions → Install from Disk**, select
the ZIP, and enable Tyvrana. The package installs in the user's extension area;
Blender's installation and embedded Python environment are not modified.

Start core from its own environment:

```sh
uv run --locked tyvrana-core serve
```

For an MCP client, launch `uv run --locked tyvrana-core mcp` from the core checkout.
Use core's `tyvrana_list_adapters` and `tyvrana_execute_operation` tools to discover
Blender and call the operations below.

The default connection is `ws://127.0.0.1:8765`. Tyvrana starts automatically when
enabled. Extension preferences expose the core host, port, connection status,
and **Apply and reconnect**. Hosts must be numeric loopback IP addresses (such
as `127.0.0.1` or `::1`); hostnames and remote addresses are rejected. Local
communication does not require internet access or changing Blender's online
access preference. The manifest declares network permission for this connection.

When core is unavailable, Blender remains responsive. Connection attempts use
an exponential delay from 0.25 seconds up to 8 seconds; a connection lasting at
least five seconds resets the backoff. Disable the extension to stop its work.
Unexpected worker failures are logged and shown in preferences; **Apply and
reconnect** starts a new worker after the cause is resolved.

## Operations

Names are exact and are advertised in sorted order:

| Operation | Arguments | Result |
| --- | --- | --- |
| `blender.scene.inspect` | `{}` | Scene summary |
| `blender.object.create_primitive` | Required `primitive`; optional `name`, `location`, `rotation`, `scale` | Created object summary |
| `blender.object.set_transform` | Required `name`; optional `location`, `rotation`, `scale` | Updated object summary |
| `blender.object.delete` | Required `name` | `{"deleted": "object name"}` |
| `blender.render.image` | Optional `width`, `height`, `format` | Render metadata and a typed PNG artifact |

Arguments must be objects with no unexpected fields. Vectors contain exactly
three finite numbers. Rotation uses XYZ Euler angles in radians; transforms are
local to the object's parent. Creation defaults to location/rotation `[0, 0, 0]`
and scale `[1, 1, 1]`. Transform fields may be omitted to retain their values;
explicit null vectors are rejected. Object names must be nonblank.

Supported primitives are `cube`, `plane`, `uv_sphere`, `ico_sphere`, `cylinder`,
`cone`, and `torus`. Blender resolves duplicate names; the returned summary
contains the actual resulting name. Creation selects the new object and requires
Object Mode. Delete also requires Object Mode and removes the named object
datablock, including its collection links. Names resolve in the current scene;
linked objects without library overrides cannot be mutated.

Example creation arguments:

```json
{
  "primitive": "cube",
  "name": "ExampleCube",
  "location": [2, 3, 4],
  "rotation": [0, 0, 0.5],
  "scale": [1, 2, 1]
}
```

An object summary contains:

```json
{
  "name": "ExampleCube",
  "type": "MESH",
  "location": [2, 3, 4],
  "rotation": [0, 0, 0.5],
  "scale": [1, 2, 1],
  "dimensions": [2, 4, 2],
  "visible": true,
  "hide_viewport": false,
  "hide_render": false,
  "selected": true,
  "parent": null
}
```

Visibility refers to the current view layer. Blender stores transforms with
finite precision, so returned floating-point values can differ slightly from the
input. A scene summary contains `name`, `filepath` (null when unsaved),
`active_object` (name or null), sorted `selected_objects`, `object_count`, and
`objects` sorted by name. It summarizes objects without dumping mesh geometry.

Failures use canonical protocol `OperationFailure` messages with these local
codes: `invalid_arguments`, `object_not_found`, `invalid_context`,
`operation_unsupported`, `adapter_busy`, `operation_failed`, `no_camera`,
`render_failed`, `artifact_too_large`, and `artifact_transfer_failed`. Validation errors
include field diagnostics; unexpected exceptions are logged and return a
sanitized message. Core maps these failures to MCP tool errors.

## Rendered images

`blender.render.image` renders the current scene with its current camera and
render engine. Arguments are strict and reject unknown fields:

```json
{"width": 512, "height": 512, "format": "png"}
```

All three fields are optional. Width and height default to 512 and each must be
an integer from 64 through 1024, inclusive. Only `"png"` is supported. No camera is
created automatically: a scene without one returns `no_camera`. An existing
render job returns `invalid_context`. Rendering works in background and UI modes.

The successful protocol response has this shape (IDs and hash shown schematically):

```text
{
  "type": "operation.success",
  "request_id": "<original request ID>",
  "result": {"width": 512, "height": 512, "format": "png"},
  "artifacts": [{
    "artifact_id": "<32 lowercase hexadecimal digits>",
    "name": "render.png",
    "media_type": "image/png",
    "byte_size": <exact PNG byte count>,
    "sha256": "<64 lowercase hexadecimal digits>"
  }]
}
```

Rendering uses `bpy.ops.render.render` synchronously on Blender's main thread,
then `Render Result.save_render()` writes an 8-bit RGBA PNG into an
extension-owned temporary directory. Resolution is used at 100%, with border,
crop, multiview, and sequencer output disabled for this single-camera image.
Compositor image processing is retained; File Output nodes, including nodes in
groups, are temporarily muted to prevent unrelated file writes. Modified settings
and mute flags are restored on success or failure. The usual Blender Render
Result remains in Blender; no extra image datablock is loaded for transport.

The networking subprocess receives the prepared descriptor through the private
extension pipe. It streams the file with canonical `artifact.begin` → `ready` →
binary chunks → `complete` → `accepted`, and sends operation success only after
core acknowledges verified bytes. The private spool is shared only within this
extension and its owned child process. No artifact filename or directory is sent
to core or an MCP client. Artifact bytes are never embedded in `JsonValue`.

The spool permits four prepared renders, each at most 16 MiB (64 MiB maximum
capacity); these constants are in `tyvrana_blender.artifacts`. Dimension limits
also constrain output generation before size validation. Chunks use the protocol's
65,536-byte payload limit. Acknowledgements have a 10-second deadline and network
writes have a 5-second deadline; core's overall operation deadline also applies.
Temporary files are deleted after delivery, rejection, cancellation, disconnect,
or extension shutdown. Rendering itself cannot be interrupted safely: if cancelled
while rendering, its result is discarded when native work returns. Cancellation
continues to be processed by the networking subprocess during rendering/transfer.

Through `tyvrana_execute_operation`, core returns the metadata in
`structuredContent` and an actual MCP image content block containing the PNG.
Core defaults to 4 MiB total raw inline image bytes per result; larger images
return `image_too_large`. Future production-resolution outputs will need resource
or file delivery semantics rather than unbounded inline images. Use a core build
and extension bundle pinned to the same current protocol revision.

## Execution and lifecycle

Blender owns all `bpy`/`mathutils` execution on its main thread. The official
[threading guidance](https://docs.blender.org/api/5.2/info_gotchas_threading.html)
warns against long-lived Python threads, so networking runs in a small subprocess
owned by the extension. It uses Blender's embedded interpreter and the extension's
bundled dependency paths. It never imports Blender API modules.

The worker is a WebSocket client. Controls use text messages; artifact chunks use
binary messages. Nonblocking local pipes carry newline-framed
canonical Tyvrana messages between it and Blender. A bounded command queue is
drained by a persistent `bpy.app.timers` callback, with a time budget and maximum
batch size. Main-thread assertions guard execution. The worker handles heartbeats
and cancellation while Blender performs an operation. No shell commands or
arbitrary Python execution operations are exposed.

There are at most 128 pending commands. Frames are limited to 1 MiB, and pipe IO
per tick is bounded. Each handler still runs synchronously: a long Blender API
call cannot be preempted by the timer budget.

Queued cancellations prevent execution when observed before dispatch. Once work
starts, it can finish changing the scene; cancellation does not roll it back or
interrupt Blender unsafely. The networking worker suppresses results after it
observes cancellation, and core ignores late responses. Disconnect clears queued
work and pending correlation state before reconnecting.

Registration contains application `blender`, the actual Blender version, a
process-lifetime instance ID, and the saved project path when present. File loads
cancel queued work and restart the connection; saving to a different path refreshes
registration. Disable and Blender's exit handler remove timers and handlers,
close pipes, and wait for the worker to exit. No application events are published
to core yet; connection-state messages are local to the worker and its parent.

Startup is deferred out of Blender's restricted `register()` context. In the UI,
Blender invokes the timer automatically. Background scripts that block Blender's
normal event processing must explicitly call the extension's `blender.pump()`
function on the main thread; the integration host demonstrates this pattern.

## Development and packaging

Requires uv, Python 3.12+, Git, and Blender 5.2.1. Create this repository's own
environment:

```sh
uv sync --locked --python 3.12
uv run --locked python tools/build_extension.py
```

The build tool stages extension files under `build/extension`, exports locked
runtime requirements, verifies wheel downloads against lockfile hashes, and builds
the protocol wheel from its pinned public Git commit. It bundles seven wheels,
including Pydantic's CPython 3.13 native dependency, using Blender's official
[wheel mechanism](https://docs.blender.org/manual/en/5.2/advanced/extensions/python_wheels.html).
Development dependencies are not bundled. Generated artifacts are ignored by Git.

The tool invokes these official commands and validates the resulting archive:

```sh
blender --command extension validate build/extension
blender --command extension build --source-dir build/extension --output-dir dist
blender --command extension validate dist/tyvrana_blender-0.1.0.zip
```

The source manifest supplies extension metadata and permissions; the staging step
adds the exact wheel filenames. Builds normalize file timestamps with
`SOURCE_DATE_EPOCH` (defaulting to the ZIP epoch) for reproducible archives.
The current distribution targets Linux x64 and CPython 3.13.

Runtime dependencies are Pydantic, `websockets`, and `tyvrana-protocol`. Development
tools are pytest, pytest-asyncio, Ruff, mypy, pip/Hatchling for packaging, and the
official MCP SDK for end-to-end tests. All are local to this repository's `.venv`.

The installed Blender distribution does not include a usable official `bpy` stub
bundle. Dynamic typing is isolated to the Blender API boundary, with strict mypy
for the rest of the adapter and actual Blender tests for native behavior.

## Tests

```sh
uv run --locked pytest
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv build --no-build-isolation
git diff --check
```

After building the extension, run native Blender and full-stack tests:

```sh
TYVRANA_CORE_EXECUTABLE=/path/to/core/.venv/bin/tyvrana-core uv run --locked pytest tests/integration -s
```

Integration tests require `xvfb-run` for isolated UI execution. They use temporary
Blender profiles and test scenes, install the ZIP through the official CLI, and
run real core and Blender processes. Coverage includes native operations,
register/unregister and exit cleanup, background pumping, UI timers, MCP
registration and scene mutations, and reconnection after core restarts. Normal
tests check validation, dispatch, cancellation races, real worker pipes/WebSockets,
backoff, artifact framing/acknowledgements, bounded spooling, cancellation during
transfer, and shutdown, with strict warnings and async/thread leak checks.
The render tests build a lit cube/plane/camera scene with contrasting materials,
verify actual PNG structure, dimensions, content variation, byte count and SHA-256,
and prove real background and UI renders reach the official MCP client as image
content. A separate real-render test cancels during binary transfer and verifies
cleanup and continued adapter operation. No image-analysis service is used.
