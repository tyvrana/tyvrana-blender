# tyvrana-blender

Blender extension for the Tyvrana autonomous 3D creation system. It connects a
running Blender instance to [tyvrana-core](https://github.com/tyvrana/tyvrana-core)
and performs validated scene operations through the canonical
[tyvrana-protocol](https://github.com/tyvrana/tyvrana-protocol) contracts.

Minimum supported host: **Blender 5.2.1 LTS**. Currently validated:
**Blender 5.2.1 LTS on Linux x64**, using its embedded Python 3.13.
The extension manifest and runtime guard reject older hosts. Newer versions pass
the minimum-version guard but require native and end-to-end testing before they
are declared validated. One current adapter implementation serves supported hosts.

The extension provides objects, cameras, lights, materials, generated and imported
packed images, shader graphs, semantic mesh modeling, whole-mesh UV controls,
modifier stacks, Multires sculpting, regional masks and Face Sets, native sculpt
filters, explicit voxel-remesh blockout, surface-conforming retopology, evaluated
surface picking, and PNG scene renders delivered as MCP images.

## Install and connect

Build the extension as described below, then install and enable it with Blender's
official extension command:

```sh
blender --command extension install-file --repo user_default --enable dist/tyvrana_blender-0.1.0.zip
```

The first installation command runs in a separate, short-lived Blender process.
It does not activate code in an already running process. For an enabled existing
installation, use the live-update workflow below instead of overwriting its files.
The extension lives in the user repository; Blender's installation and embedded
Python environment are not modified.

### Programmatic development updates

After editing and testing, build the validated archive and stage it separately:

```sh
uv run python tools/build_extension.py
uv run python tools/update_extension.py dist/tyvrana_blender-0.1.0.zip \
  --extension-dir "$HOME/.config/blender/5.2/extensions/user_default/tyvrana_blender"
```

Use the actual extension directory for your installation. The staging command
returns a SHA-256 `build` identity. It validates archive paths, size, Python syntax,
package identity, bootstrap files and dependency wheels. It leaves the active
installation untouched and publishes one candidate under a sibling update
transaction directory. A lock prevents overlapping staging/activation. This is
separate from live activation; staging alone does not change loaded Python code.

Call `blender.extension.reload` through core, supplying the returned identity:

```json
{"expected_build": "<64-character staged build SHA-256>"}
```

The response contains `status: "scheduled"`, a `reload_id`, `previous_build`,
`new_build` and `previous_adapter_id`. It is an acknowledgement, not completion.
The worker sends that response and waits for a WebSocket ping acknowledgement
before authorizing activation. Reload requires an idle request channel; other
operations are rejected while the generation transition is pending. Finish or
cancel artifact transfers before requesting it.

Discover the replacement adapter with `tyvrana_list_adapters`, then call
`blender.extension.inspect`. Completion requires the same `reload_id`, the expected
`build`, `status: "completed"` and `connection_state: "connected"`. The result also
reports `implementation_build`, captured when the backend module imports; it
must match `build` after activation. Packages retain their source revision and
source digest in `build_info.json`. Inspection also reports the generation counter,
advertised operation count, worker PID and owned
class/timer/handler/artifact counts. Each activation registers a fresh adapter
identity with the same core configuration. The previous connection is stopped
before the new worker starts. Repeated A → B → C updates use this same sequence.
No Blender UI, broad Reload Scripts action, file reopen or process restart is
required for normal implementation updates.

The fixed entry point, `lifecycle.py` and `deployment.py` own the transaction.
The controller stops the previous implementation, removes only its registered
classes, handlers and timer, closes its queue/pipes, waits for its worker and
cleans its artifact store. It removes only Tyvrana implementation modules and
package attributes from Python's import cache. Blender and shared wheel modules,
unrelated extensions, timers and handlers remain loaded. Candidate model/operation
imports are checked in Blender's bundled interpreter before unloading anything.
The complete installed directory is then exchanged with the candidate, and the
new implementation is imported and registered. The old directory remains available
until the new adapter reconnects. The package, manifest and installed code all
refer to the same active generation after completion.

Reload does not load/save the project or alter objects, geometry, materials,
modifiers, current frame, selections, cameras or render settings. Adapter host/port
preferences are retained across RNA class registration. Preferences are not saved
or reset. Tests cover unsaved image data, other preferences, unrelated handlers,
repeated native reloads and MCP rendering/file operations afterward.

Invalid packages, changed installed files and mismatched build identities fail
before activation. Missing response acknowledgement leaves the original runtime
active. Import/registration failure or failure to reconnect within 20 seconds
stops partial new registrations, restores the old directory and starts a clean
old implementation. `extension.inspect` reports `rolled_back` with the failure;
it never reports the failed candidate as completed. If rollback also fails, the
adapter remains disabled and logs the explicit error; the stable controller writes
`status.json` in the sibling transaction directory for external diagnosis. Blender
remains usable. Filesystem failure or a host crash is not a guarantee of automatic
live rollback. A stopped process's interrupted transaction can be recovered with:

```sh
uv run python tools/update_extension.py --recover \
  --extension-dir "$HOME/.config/blender/5.2/extensions/user_default/tyvrana_blender"
```

Recovery refuses to act while the transaction owner is alive. A surviving entry
point also attempts that recovery during the next normal extension enable.

The stable bootstrap and Blender-managed wheels must remain identical for a live
update. Replacing the controller currently executing a transaction or unloading
shared/native dependency modules is outside this operation's safety boundary;
such changes require a stopped-process installation. This is a specific limitation,
not the normal development update path. An older installed adapter without the
reload operation likewise has no endpoint through which to bootstrap it.

For that one-time transition, save through `blender.file.save`, verify
`blender.file.inspect`, shut down the host without UI automation and install the
archive with Blender's extension command (`--no-prefs` preserves an already enabled
installation's preferences). Relaunch through the configured desktop application
entry when it carries required GPU/environment policy. On Linux, use an application
launcher such as `gtk-launch blender` or `gio launch /path/to/blender.desktop`;
verify the desktop's discrete-GPU/offload selection rather than assuming a raw
binary launch reproduces it. Where the desktop requires it, run the application
launcher through its GPU dispatch service. Confirm the process's offload variables
and GPU presence using read-only system inspection. After reconnection, open the
saved project through `blender.file.open` with `discard_current: true` and verify
its state. No menus, Preferences clicks or Python Console are needed.

This separation follows Blender's extension lifecycle: its package manager stages
files and disables/clears the affected extension before re-enabling it, while a
command executed in another Blender process cannot invalidate this process's
Python imports. See the [5.2 extension command documentation](https://docs.blender.org/manual/en/5.2/advanced/command_line/extension_arguments.html),
[application timers](https://docs.blender.org/api/5.2/bpy.app.timers.html), and
[extension lifecycle source](https://projects.blender.org/blender/blender/src/tag/v5.2.1/scripts/addons_core/bl_pkg/bl_extension_ops.py).

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
| `blender.extension.inspect` | `{}` | Active/staged build, transition state and owned resources |
| `blender.extension.reload` | Required staged `expected_build` SHA-256 | Scheduled reload acknowledgement; verify completion after rediscovery |
| `blender.scene.inspect` | `{}` | Scene summary |
| `blender.scene.raycast` | World origin/direction or normalized camera image coordinates | Evaluated surface hit or miss |
| `blender.multires.inspect` | Object name | Base topology, levels and diagnostics |
| `blender.multires.create` | Object name; optional modifier name | Created zero-level Multires state |
| `blender.multires.subdivide` | Object name; optional level count/mode | Increased Multires levels |
| `blender.multires.configure` | Object name; partial existing display/sculpt/render levels | Updated Multires state |
| `blender.sculpt.inspect` | Object name | Sculpt context, resolution and scale summary |
| `blender.sculpt.stroke` | Object, brush, local surface samples, radius, strength | Native sculpt result with snapped targets and bounds |
| `blender.sculpt.mask.inspect` | Object name | Explicitly scoped native mask statistics |
| `blender.sculpt.mask.stroke` | Object, add/subtract mode, local samples, radius, strength | Native mask painting and updated summary |
| `blender.sculpt.mask.clear` | Object name | Clear visible protection |
| `blender.sculpt.mask.invert` | Object name | Invert visible protection |
| `blender.sculpt.face_sets.inspect` | Object name | Authored region IDs, counts, areas and bounds |
| `blender.sculpt.face_sets.assign` | Object, face selector; optional positive ID | Persistent region assignment |
| `blender.sculpt.face_sets.initialize` | Object and initialization mode | Deterministic visible region organization |
| `blender.sculpt.filter` | Object, type, strength; optional iterations/axes/orientation | Native masked refinement and geometry bounds |
| `blender.sculpt.voxel_remesh.inspect` | Object; proposed remesh settings | Blockers, data consequences, topology metrics and grid estimate |
| `blender.sculpt.voxel_remesh` | Object and voxel size; explicit native settings | Staged destructive blockout topology replacement |
| `blender.retopo.create_target` | Source; optional target name | Empty separate aligned target |
| `blender.retopo.inspect` | Explicit source/target | Authored quality and authored/evaluated surface correspondence |
| `blender.retopo.seed_patch` | Source/target, center, tangent, dimensions | Oriented projected quad grid |
| `blender.retopo.project` | Source/target, vertex selector | Surface-conforming authored positions |
| `blender.retopo.relax` | Source/target, vertex selector, bounded smoothing | Reprojected relaxation with optional boundary lock |
| `blender.retopo.extrude_boundary` | Source/target, boundary selector, offset | Projected quad strip |
| `blender.retopo.bridge_loops` | Source/target, two equal closed loops | Validated projected quad bands |
| `blender.retopo.insert_loop` | One transverse edge; optional factor/reference endpoint | One projected loop through a quad ring |
| `blender.retopo.slide` | Vertex/loop selector, explicit side, factor | Surface-conforming topology-preserving slide |
| `blender.retopo.subdivide` | Coherent edge selection, bounded cuts | Local projected quad density |
| `blender.retopo.collapse` | Edge or quad ring; explicit boundary permission | Controlled density reduction |
| `blender.retopo.rotate_edge` | Internal edge, winding-relative direction | Triangle/quad flow and pole redirection |
| `blender.retopo.stitch` | Equal open chains, explicit endpoints, weld cap | Weld matching seam boundaries |
| `blender.retopo.fill_boundary` | Closed boundary, explicit corner and span | Projected quad grid fill |
| `blender.object.create_primitive` | Required `primitive`; optional `name`, `location`, `rotation`, `scale` | Created object summary |
| `blender.object.set_transform` | Required `name`; optional `location`, `rotation`, `scale` | Updated object summary |
| `blender.object.delete` | Required `name` | `{"deleted": "object name"}` |
| `blender.material.inspect` | `{}` | Sorted material resources and assignments |
| `blender.material.create_principled` | Optional name and Principled fields | Created material summary |
| `blender.material.configure_principled` | Required name; partial Principled fields | Updated material summary |
| `blender.material.assign` | Object/material names; optional slot index | Assigned slot and ordered slots |
| `blender.image.inspect` | `{}` | Sorted image resources |
| `blender.image.create_generated` | Width/height; optional name, generation and color settings | Created image summary |
| `blender.image.create_from_artifact` | Attached artifact ID; optional name, color space and alpha mode | Packed image summary |
| `blender.file.inspect` | No arguments | Native filepath, saved/dirty flags and disk state |
| `blender.file.open` | Absolute `.blend` filepath; required `discard_current: true`; optional `load_ui` | Replace current project while retaining MCP response and registration |
| `blender.file.save` | Optional absolute `.blend` filepath; explicit overwrite permission | Save the current project and update registration metadata |
| `blender.image.configure` | Name; optional color space and alpha mode | Updated image summary |
| `blender.shader.inspect` | Material name | One shader graph summary |
| `blender.shader.node.create` | Material name, supported node type; optional name and typed settings | Created node summary |
| `blender.shader.node.configure` | Material/node names and partial typed settings | Updated node summary |
| `blender.shader.node.delete` | Material/node names | Material name and deleted node name |
| `blender.shader.connect` | Material and output/input endpoints; optional explicit replacement | Link summary |
| `blender.shader.disconnect` | Material and target input | Target and number of removed links |
| `blender.light.inspect` | `{}` | Sorted light summaries |
| `blender.light.create` | Light type, optional name/transform and applicable settings | Created light summary |
| `blender.light.configure` | Name and partial applicable light settings | Updated light summary |
| `blender.camera.inspect` | `{}` | Active-camera name and sorted camera summaries |
| `blender.camera.create` | Optional name, projection, initial transform, optics, clipping, shifts, activation | Created camera summary |
| `blender.camera.configure` | Required `name`; optional projection, optics, clipping, shifts | Updated camera summary |
| `blender.camera.set_active` | Required `name` | Selected camera summary |
| `blender.render.image` | Optional dimensions, format and Cycles render options | Render metadata and a typed PNG artifact |
| `blender.uv.inspect` | Object name | UV map summaries and active roles |
| `blender.uv.create_map` | Object name; optional map name and activation flags | Updated UV inspection |
| `blender.uv.set_active` | Object/map names; editing and render activation flags | Updated UV inspection |
| `blender.uv.unwrap` | Object name, method; optional map and applicable settings | Updated UV inspection |
| `blender.uv.pack_islands` | Objects/maps, relative density, unit-tile bounds, resolution and pixel padding | Joint packing result |
| `blender.uv.inspect_layout` | Objects, optional map, authored/evaluated geometry and resolution | Island quality, distortion, overlap, density, margins; optional PNG artifact |
| `blender.mesh.inspect` | Object name | Bounded authored mesh summary |
| `blender.mesh.inspect_evaluated` | Object name, optional UV map | Bounded authored/evaluated surface and tangent signatures |
| `blender.modifier.inspect` | Object name | Ordered object-owned modifier stack |
| `blender.modifier.create` | Object name, type, typed settings; optional name/common flags | Created modifier summary |
| `blender.modifier.configure` | Object/modifier names, existing type, partial settings/common flags | Updated modifier summary |
| `blender.modifier.move` | Object/modifier names, final index | Updated ordered stack |
| `blender.modifier.remove` | Object/modifier names | Removed name and remaining stack; authored data retained |
| `blender.modifier.apply` | Object/modifier names | Applied name, updated authored mesh and remaining stack |
| `blender.mesh.query` | Object name, selector; optional limit | Bounded vertex, edge or face details |
| `blender.mesh.transform` | Object name, selector; translation/rotation/scale and optional pivot; optional local falloff for translation | Regional geometry edit result |
| `blender.mesh.extrude_faces` | Object name, face selector, offset; optional cap scale | Region extrusion result |
| `blender.mesh.inset_faces` | Object name, face selector, thickness; optional depth/even offset | Region inset result |
| `blender.mesh.bevel_edges` | Object name, edge selector, width; optional segments/profile | Topology bevel result |
| `blender.mesh.subdivide_edges` | Object name, edge selector; optional cuts/smooth | Subdivision result |
| `blender.mesh.delete_elements` | Object name, selector; optional face deletion mode | Deleted element counts and summary |
| `blender.mesh.merge_vertices` | Object name, vertex selector; optional center mode | Merge result |
| `blender.mesh.mark_seam` | Object name, edge selector, seam boolean | Changed seam count and summary |
| `blender.mesh.set_shading` | Object name, face selector, smooth boolean | Changed face count and summary |
| `blender.mesh.recalculate_normals` | Object name; optional inside boolean | Whole-mesh normal repair result |

Arguments must be objects with no unexpected fields. Vectors contain exactly
three finite numbers. Rotation uses XYZ Euler angles in radians; object transforms
are local to the object's parent. Creation defaults to location/rotation `[0, 0, 0]`
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
codes: `invalid_arguments`, `object_not_found`, `object_not_camera`, `object_not_light`,
`unsupported_projection`, `invalid_context`,
`operation_unsupported`, `adapter_busy`, `operation_failed`, `no_camera`,
`render_failed`, `artifact_too_large`, and `artifact_transfer_failed`. Validation errors
include field diagnostics; unexpected exceptions are logged and return a
sanitized message. Core maps these failures to MCP tool errors.

## Lights

`blender.light.inspect` takes `{}` and returns `{"lights": [...]}`, sorted by
object name in the current scene. No lights returns `{"lights": []}`.
`blender.light.create` and `blender.light.configure` return the resulting
`LightSummary`. Every summary has these common fields:

```json
{
  "name": "Key",
  "type": "point",
  "location": [2, -3, 6],
  "rotation": [0, 0, 0],
  "scale": [1, 1, 1],
  "color": [1, 1, 1],
  "energy": 1000,
  "exposure": 0,
  "normalize": true,
  "use_shadow": true,
  "visible": true,
  "hide_viewport": false,
  "hide_render": false,
  "parent": null,
  "settings": {"radius": 0.25}
}
```

`type` is `point`, `sun`, `spot`, or `area`, matching Blender's four native light
types. `settings` contains only the corresponding properties:

| Type / shape | Exact `settings` fields |
| --- | --- |
| Point | `radius` |
| Sun | `angle` |
| Spot | `radius`, `spot_size`, `spot_blend` |
| Area: square or disk | `shape`, `size` |
| Area: rectangle or ellipse | `shape`, `size`, `size_y` |

Transforms and visibility follow object inspection: local authored XYZ location,
XYZ Euler radians, scale, current-view-layer visibility, viewport/render hide
flags, and parent name or null. Constraints, animation, and parenting still affect
evaluated rendering. These summaries do not expose node trees, temperature,
linking collections, or renderer-specific sampling settings. Existing advanced
settings continue to affect the final illumination; `color` reports the light's
stored RGB tint, not a computed shader or temperature color.

### Creating and configuring lights

`blender.light.create` requires `type`. Optional fields are `name`, `location`,
`rotation`, `scale`, the common settings below, and applicable type-specific
settings. Arguments are flat; the result groups type-specific data in `settings`.

| Common argument | Default / semantics |
| --- | --- |
| `color` | `[1, 1, 1]`; linear RGB tint, including HDR channels above 1 |
| `energy` | `10`; native power/strength value |
| `exposure` | `0`; multiplies intensity by `2 ** exposure` |
| `normalize` | `true`; normalizes intensity for consistent output as emitter size changes |
| `use_shadow` | `true`; enables shadow casting from this light |

With normalization enabled, energy is radiant power in watts for point/area
lights and irradiance in watts per square meter for sun lights. Spot energy
represents power before limiting emission to its cone. With normalization
disabled, changing emitter size also changes its total output. These are Blender's
radiometric settings, not electrical bulb wattages. Exposure and color further
scale the emitted light.

| Type-specific argument | Default / meaning |
| --- | --- |
| Point/spot `radius` | `0`; maps to `shadow_soft_size`, in scene units |
| Sun `angle` | Approximately `0.0091804322`; angular diameter in radians |
| Spot `spot_size` | Approximately `0.7853981853`; full cone angle in radians |
| Spot `spot_blend` | Approximately `0.150000006`; cone-edge softness |
| Area `shape` | `square`; supports `square`, `rectangle`, `disk`, `ellipse` |
| Area `size` | `0.25`; square side, disk diameter, or rectangle/ellipse X dimension |
| Area `size_y` | `0.25`; Y dimension, applicable only to rectangle/ellipse |

Creation defaults to location/rotation `[0, 0, 0]` and scale `[1, 1, 1]`. It creates
both a light datablock and object in the current editable collection, requires
Object Mode, and preserves object selection. An explicit name already used by
any object in the file is rejected with `invalid_arguments`; names Blender cannot
store exactly are also rejected. An omitted name uses Blender's normal unique
naming and returns the actual name. Existing lights are preserved.

```json
{
  "type": "area",
  "name": "Key",
  "location": [0, 0, 6],
  "rotation": [0, 0, 0],
  "energy": 1000,
  "shape": "rectangle",
  "size": 3,
  "size_y": 2
}
```

`blender.light.configure` requires `name` and accepts only the common and
applicable type-specific settings above. Omitted properties retain their values;
a name-only call validates and returns current state. It does not accept `type`,
transforms, visibility, or a nested `settings` argument. To replace a light's type,
create the desired light and delete the previous object explicitly.

```json
{"name": "Key", "energy": 1500, "color": [1, 0.8, 0.6]}
```

Irrelevant fields are rejected even if Blender stores an unused value internally:
for example `angle` on point, `radius` on sun/area, or `spot_size` on area.
`size_y` requires the resulting shape to be rectangle or ellipse. Changing area
shape to either of those may include `size_y` in the same request; otherwise its
stored Y dimension is retained. Square/disk summaries omit that unused dimension.

Use `blender.object.set_transform` to move or rotate a light, and
`blender.object.delete` to remove it. Sun rotation determines illumination
direction; its position does not affect the render. There is no active-light,
light-transform, light-delete, or type-conversion operation. Object deletion
unlinks the light object; Blender may retain its now-unused datablock.

### Light validation and rendering

All inputs are strict, finite, and checked at float32 precision before mutation.
Unknown fields, explicit nulls, numeric booleans/strings, blank names, overflow,
and nonzero values that would underflow to zero are rejected.

Let `F = 3.4028234663852886e38`, Blender's maximum finite float32 value:

| Property | Accepted hard range |
| --- | --- |
| Energy and creation transform components | `[-F, F]` |
| RGB channels, radius, area dimensions | `[0, F]` |
| Exposure | `[-32, 32]` |
| Sun angle | `[0, 3.1415927410125732]` radians |
| Spot angle | `[0.01745329238474369, 3.1415927410125732]` radians |
| Spot blend | `[0, 1]` |

Zero radius/dimensions and negative energy follow Blender's native storage
limits. Negative power is not physically based and should be used deliberately.
UI soft limits do not constrain this API. Returned values reflect stored
precision; the adapter checks that requested settings were retained.

Configuration validates the merged state before writing. Invalid combined state
returns `invalid_arguments` with no partial mutation. Shared light datablocks
are copied before changing the target object, preserving other objects' settings.
Linked/read-only data and mutation during a render job are rejected with
`invalid_context`. Unexpected failures restore changed properties or the original
shared datablock; failed creation removes its new object and data.

A non-light target returns `object_not_light`, and a missing object returns
`object_not_found`. Unexpected failures are logged and sanitized as
`operation_failed`. All light inspection and mutation uses the existing Blender
main-thread dispatcher; the networking subprocess does not access `bpy`.

`blender.render.image` renders the scene's configured lighting. Cycles and EEVEE
have different shadow/sampling implementations; this API does not promise
identical images or expose their advanced controls. The render integration tests
use deterministic Cycles CPU scenes and verify energy, color, light position,
sun rotation, and radius changes through actual pixels.

Semantics follow the official [Blender 5.2 Light API](https://docs.blender.org/api/5.2/bpy.types.Light.html),
[light manual](https://docs.blender.org/manual/en/5.2/render/lights/light_object.html),
and [Blender 5.2.1 RNA definitions](https://github.com/blender/blender/blob/v5.2.1/source/blender/makesrna/intern/rna_light.cc),
with native checks for property limits, defaults, shared data, and rollback.

## Materials

Materials are named reusable resources in the blend file. Configuring a named
material updates that resource for every user; it never silently duplicates the
material. Creation and assignment are separate. Object transforms remain
`blender.object.set_transform`; assigning slots does not edit face-material
indices.

`blender.material.inspect` takes `{}` and returns `{"materials": [...]}`, sorted
by material name, including unused and linked materials. Each material has:

```text
{
  name: string,
  surface: "principled" | "custom" | "none",
  principled: PrincipledSummary | null,
  assignments: [{object: string, slot: integer}, ...]
}
```

Assignments report effective object slots across the blend file, sorted by object
name and slot index. Unassigned materials have an empty list. These references
report authored slots, not evaluated Geometry Nodes assignments or per-face
usage. Assignment targets must exist in the current scene.

### Principled surface configuration

`blender.material.create_principled` accepts an optional `name` and any of the
fields below as flat arguments. It creates the native two-node graph, Principled
BSDF → Material Output Surface, and returns a material summary without assigning
it. Omitted properties retain Blender-native defaults. Explicit duplicate names,
including names used by linked materials, and names Blender cannot store exactly
are rejected. Omit `name` for native unique naming and use the returned name.

`blender.material.configure_principled` requires `name` and accepts a partial set
of the same fields. Omitted properties retain their values; a name-only call
validates and returns the material. Configuration neither rebuilds the graph nor
makes a shared material single-user. Named lookup rejects ambiguous names shared
by different libraries. Create another named material for a distinct resource.

| JSON field / `PrincipledSummary` field | Blender socket identifier | Native default |
| --- | --- | --- |
| `base_color` | Base Color | `[0.8, 0.8, 0.8]` |
| `metallic` | Metallic | `0` |
| `roughness` | Roughness | `0.5` |
| `ior` | IOR | `1.5` |
| `alpha` | Alpha | `1` |
| `subsurface_weight` | Subsurface Weight | `0` |
| `subsurface_radius` | Subsurface Radius | `[1, 0.2, 0.1]` |
| `subsurface_scale` | Subsurface Scale | `0.005` |
| `transmission_weight` | Transmission Weight | `0` |
| `coat_weight` | Coat Weight | `0` |
| `coat_roughness` | Coat Roughness | `0.03` |
| `emission_color` | Emission Color | `[1, 1, 1]` |
| `emission_strength` | Emission Strength | `0` |

The summary contains exactly these fields. Colors and radius are three-number
arrays; other fields are numbers. Defaults above are rounded for readability;
results expose actual float32 values. Socket lookup uses semantic identifiers,
so node names, display labels, and socket positions do not select the shader.

```json
{"name": "Ceramic", "base_color": [0.5, 0.5, 0.5], "roughness": 0.65}
```

Colors are scene-linear numeric shader RGB values, not CSS/sRGB strings. RGB
configuration preserves the color socket's unused fourth component. Transparency
uses the separate `alpha` socket. Radius components correspond to RGB scattering
distances, multiplied by `subsurface_scale` in Blender scene units. Emission uses
its color and strength; roughness controls reflection/transmission microfacets,
metallic blends dielectric and metallic shading, and IOR controls refraction and
reflection. The remaining weight fields blend the named layers.

All values must be strict finite numbers. Numeric strings/booleans, malformed
vectors, unknown fields, explicit nulls, float32 overflow, and nonzero values
that round to zero are rejected before mutation. Inputs are checked before
rounding to float32, then stored values are verified after writing. With
`F = 3.4028234663852886e38`, accepted Blender 5.2 socket **hard storage limits** are:

| Inputs | Hard range |
| --- | --- |
| Base/emission RGB components | `[0, F]` |
| Every supported scalar and subsurface-radius component | `[-F, F]` |

Blender's dynamic float/vector socket range callbacks distinguish storage limits
from node-declared slider ranges. Even factor sockets store values outside 0–1;
static RNA subtype metadata alone does not describe their dynamic hard range.
This API does not substitute UI soft limits for storage limits. Ordinary physical
use is narrower: weights, metallic, roughness, and alpha generally use 0–1; IOR is
normally at least 1; radius, scale, and emission strength are normally nonnegative.
Values outside physical ranges may be clamped or otherwise interpreted by the
render engine; their storage does not guarantee a distinct rendered effect.
See Blender's [dynamic socket range implementation](https://github.com/blender/blender/blob/v5.2.1/source/blender/makesrna/intern/rna_node_socket.cc)
and [Principled socket declarations](https://github.com/blender/blender/blob/v5.2.1/source/blender/nodes/shader/nodes/node_shader_bsdf_principled.cc).

### Recognized and custom graphs

A configurable surface has one active Material Output targeting `ALL`, resolved
to that same output for both Cycles and EEVEE. Its Surface is connected directly
by one valid, unmuted BSDF link to an unmuted Principled node. No Principled input
is linked, no other input of that output is linked, and the node tree has no
animation data. Disconnected nodes and inactive unused outputs may remain.
Unexposed native shader settings are preserved and can still affect shading.

Mixed shaders, reroutes, texture-driven inputs, volume/displacement links, muted
nodes, engine-specific/ambiguous outputs, animated node trees, Grease Pencil
materials, and non-finite shader state are `custom`, with `principled: null`.
Missing node trees, empty graphs, missing outputs, or an unconnected active
Surface are `none`. Inspection preserves all graphs. Typed configuration of
`custom` or `none` returns `unsupported_material_graph` without conversion.
Blender 5.2 materials use nodes inherently; deprecated `use_nodes` is not accessed.

Linked/read-only shader data and mutation during rendering return
`invalid_context`. Requested values are validated together before writes;
unexpected failures restore the changed supported values, and failed creation
removes the newly created datablock. Unused resources follow Blender's native
lifetime rules: an unassigned material without a fake user may not survive a
save/reload. This layer does not expose resource removal or persistence flags.

### Object material slots

`blender.material.assign` takes:

```json
{"object_name": "Body", "material_name": "Ceramic", "slot_index": 0}
```

`slot_index` is optional and defaults to 0. An existing index replaces that slot;
an index equal to the slot count appends one slot. Thus an object with no slots
gets slot 0. Negative indices, gaps, nulls, booleans, and non-integers are rejected.
Other slots retain their order and assignments. The result is:

```text
{
  object_name: string,
  assigned_slot: integer,
  material_name: string,
  slots: [string | null, ...]
}
```

The assigned slot uses Blender's `OBJECT` link mode, preserving other objects'
materials even when they share geometry. Appending to shared geometry first
copies the geometry datablock to preserve other objects' slot counts and index
interpretation. Existing material resources stay shared; no material is copied.
Failed assignments restore slot state, any original shared geometry reference,
and authored indices affected by Blender's slot-removal rollback.

Supported object types are `MESH`, `CURVE`, `SURFACE`, `FONT`, `CURVES`,
`POINTCLOUD`, and `VOLUME`, using their native material-slot API. Assignment does
not promise every slot or surface shader is meaningful for every geometry type;
for example a volume needs a volume shader. Metaball family shading and Grease
Pencil's distinct material model are outside this operation. Empty objects,
cameras, lights, and unsupported types return `object_not_material_capable`.
Assignment requires an editable object in Object Mode. Existing object slots can
reference linked materials or override linked geometry; extending read-only
geometry is rejected. Missing resources return `material_not_found`; missing
objects return `object_not_found`; invalid names/indices return
`invalid_arguments`. Unexpected failures are logged and sanitized as
`operation_failed`.

Alpha changes only the Principled Alpha socket. It does not configure material
surface-render methods, transparency/shadow settings, or guarantee identical
transparency across render engines and modes. Generated image resources and typed
shader graph controls are described below. Whole-mesh UV controls are a separate
layer; displacement authoring, baking, and face-material-index editing remain
future capabilities. Material tests
verify actual renders for color, roughness highlights, metallic, emission, and
slot reassignment.

## Images and shader graphs

Image datablocks are reusable named resources; their color-space interpretation
is shared by every Image Texture node using them. Graph edits affect the named
material and its users. They preserve existing trees and unknown nodes, without
converting custom materials, inserting nodes implicitly, or duplicating resources.
All operations run through the existing main-thread dispatcher.

### Image resources

`blender.image.inspect` accepts `{}` and returns `{"images": [ImageSummary, ...]}`,
sorted by name, including readable linked and file-backed images. It exposes no
image path or pixel payload. A summary has:

```text
{
  name: string, source: string,
  width: integer, height: integer, channels: integer,
  has_alpha: boolean, is_float: boolean,
  color_space: string | null,
  alpha_mode: "straight" | "premultiplied" | "channel_packed" | "none",
  packed: boolean, users: integer, dirty: boolean,
  generated_type: "blank" | "uv_grid" | "color_grid" | null
}
```

`source` uses lowercase native identifiers such as `generated`, `file`, `tiled`,
`sequence`, `movie`, and `viewer`. Unloaded/unavailable buffers can report zero
size/channels. `has_alpha` reports buffer color-mode alpha capability, not whether
any pixel is transparent. Blender can allocate four buffer channels even for an
RGB image; `channels` alone does not establish alpha. `packed` means embedded
image data exists, and `dirty` identifies unsaved pixel edits.
Render/compositor (`viewer`) buffers have no input image color space: inspection
reports `color_space: null`, and metadata edits are rejected with `invalid_context`.

`blender.image.create_generated` requires integer `width` and `height`. Optional
flat arguments are:

| Field | Default / meaning |
| --- | --- |
| `name` | Native unique naming; returns the actual name |
| `generated_type` | `blank`; also `uv_grid` and `color_grid` |
| `color` | `[0, 0, 0, 1]`, four normalized RGBA components; blank images only |
| `alpha` | `true`; allocate alpha-capable image data |
| `float_buffer` | `false`; otherwise use 32-bit float channels |
| `color_space` | Native image default under the current OCIO configuration |

Dimensions are 1–4096 on each axis. The largest float RGBA base buffer is 256 MiB;
a byte RGBA buffer is 64 MiB. Blender may allocate additional working/render
buffers. These are per-image allocation limits, not a total project memory quota.
Invalid sizes are rejected before allocation. Duplicate explicit names and names
Blender cannot store exactly are rejected; failed creation removes the new image.

Fill `color` deliberately uses normalized 0–1 components to avoid byte-buffer
clamping and unbounded HDR generation. It follows Blender's generated-color
**gamma-encoded RGB** semantics: float generation converts RGB to scene linear,
while byte generation quantizes to 8-bit channels. Alpha is a separate normalized
component. This differs from the scene-linear Principled shader RGB arguments.
An image created with `alpha: false` requires an opaque fill. Grid generation owns
its pattern; passing an irrelevant `color` with a grid is rejected.

`blender.image.configure` takes required `name`, optional `color_space`, and
optional `alpha_mode`. It changes interpretation metadata; it does not resize,
paint, regenerate, pack, or reload images explicitly. Omitted values remain
unchanged and nulls are rejected. Color-space names are checked against the
actual runtime OCIO enumeration, including custom configurations. Names are exact;
`sRGB` and `Non-Color` are examples, not a hardcoded list. Color space belongs to
the image, never to the texture node. Non-Color avoids color management for data
textures such as normal maps.

Blender's metadata callbacks may invalidate/reload buffers. Changes that could
reload a **dirty image** are rejected with `invalid_context` to preserve unsaved
pixel edits; save those edits first. Unchanged-value calls do not invoke setters.
Generated-image alpha-mode changes do not reload pixels. The modes map to native
`STRAIGHT`, `PREMUL`, `CHANNEL_PACKED`, and `NONE`; they control alpha interpretation
for image loading/saving. They do not add an alpha channel, change Principled
Alpha, or configure material transparency. Native generated images are unaffected
by alpha-mode pixel conversion.

Linked/read-only image mutation is rejected. Missing resources use
`image_not_found`; ambiguous names across libraries use `invalid_arguments`.
Unassigned resources follow native orphan lifetime; assign the image to a material
before saving if it must survive a save/reload. Image deletion, persistence flags,
URL downloading, and pixel painting are not exposed.

### Images from input artifacts

`blender.image.create_from_artifact` requires `artifact_id`. Optional fields are
`name`, `color_space`, and `alpha_mode` (the same four modes described above).
The artifact must also be attached to the operation through core's
`tyvrana_execute_operation.artifact_ids`. Nulls, unknown fields, duplicate explicit
image names, and unavailable OCIO color spaces are rejected.

Supported inputs are deliberately limited to **PNG** (`image/png`) and **JPEG**
(`image/jpeg`, 8-bit baseline/extended sequential or progressive DCT). Header
signatures must match the declared type. Dimensions must be 1–4096 on each axis;
headers and terminal markers are checked before Blender decodes the image.
PNG header integrity is checked, and JPEG header scanning is bounded. Blender
must then supply a valid decoded buffer with matching dimensions. Corrupt or
unsupported images fail without leaving a newly created image resource.

The worker receives and verifies each input in a private request-scoped file.
The handler loads it and immediately calls native `Image.pack()`. The resulting
summary truthfully reports `source: "file"`, `packed: true`, and
`generated_type: null`. Omitted names use Blender's clean unique `Image` naming.
Image and packed-file filepath properties are cleared after packing. Encoded
packed bytes survive buffer reload and saving/reopening an assigned image in a
`.blend` file. The original source file and adapter transfer file are no longer
dependencies. Image inspection never returns a filepath.

Failures include `artifact_not_attached`, `artifact_not_found`,
`unsupported_artifact_media_type`, and `artifact_decode_failed`. Image creation
requires the same editable Object Mode context as generated-image creation.

For an external texture workflow:

1. Call core's `tyvrana_import_artifact` with
   `{"path": "textures/checker.png", "name": "Checker"}`.
2. Use its opaque descriptor ID in both the attachment list and the image operation:

   ```json
   {
     "adapter_id": "<connected Blender instance>",
     "operation": "blender.image.create_from_artifact",
     "arguments": {"artifact_id": "00112233445566778899aabbccddeeff", "name": "Checker"},
     "artifact_ids": ["00112233445566778899aabbccddeeff"]
   }
   ```

3. Assign `Checker` to an Image Texture node, connect Texture Coordinate `UV`
   to its `Vector`, and connect its `Color` to the Principled `Base Color`.
4. Create/select and unwrap the object's UV map as needed, then render.
5. Release the core import with `tyvrana_release_artifact` when no more operations
   need it. The packed Blender image remains usable.

The local path exists exclusively at core's local ingestion boundary. Core copies
the file into its own storage; Blender receives only opaque descriptors and binary
bytes. No source path is an adapter argument or a cross-component transport.

### Inspecting shader trees

`blender.shader.inspect` takes `{"material_name": "Surface"}`. It inspects one
material, including custom and linked trees, without mutating it:

```text
ShaderGraphSummary {
  material_name: string, node_tree_present: boolean,
  nodes: NodeSummary[], links: LinkSummary[]
}
NodeSummary {
  node_name: string, node_type: string, label: string, muted: boolean,
  inputs: SocketSummary[], outputs: SocketSummary[], settings: NodeSettings | null
}
SocketSummary {
  name: string, identifier: string, socket_type: string,
  linked: boolean, enabled: boolean,
  default_value: boolean | integer | number | number[0..4] | null
}
LinkSummary {
  from_node: string, from_socket: string, to_node: string, to_socket: string,
  valid: boolean, muted: boolean
}
```

Nodes sort by name; links sort by their source/target names and socket identifiers.
Socket lists retain native order for readability, but indices are never public
endpoints. `node_type` is the actual `bl_idname`. Socket identifiers, rather than
display names/labels, identify connections. Enabled excludes unavailable inputs.
Only finite numeric scalars and vectors of at most four components are exposed
as defaults; strings, pointers, complex/large values, and non-finite values become
null. Unsupported node settings are null. Material Output settings report
`{active: boolean, target: string}`. Principled defaults remain visible in its
inputs. Unknown nodes and their links remain inspectable.

### Typed auxiliary nodes

`blender.shader.node.create` requires `material_name` and `node_type`, with optional
`name` and applicable flat settings below. It creates one unconnected node and
returns NodeSummary. Explicit duplicate/unstoreable names are rejected.

`blender.shader.node.configure` requires `material_name` and `node_name`, plus
applicable partial settings. It resolves the actual node type; callers cannot
change it. Omitted fields remain unchanged. Wrong-type fields, explicit nulls,
numeric coercion, non-finite values, float32 overflow/underflow, and malformed
vectors are rejected. Requested values are verified after writes and restored on
unexpected failure. Driven input defaults must be disconnected before configuring
them; the operation does not silently override a link.

| Public node type | Blender type | Configurable fields / native defaults |
| --- | --- | --- |
| `image_texture` | `ShaderNodeTexImage` | `image_name` (initially unassigned), `interpolation: linear`, `projection: flat`, `extension: repeat` |
| `texture_coordinate` | `ShaderNodeTexCoord` | `from_instancer: false` |
| `mapping` | `ShaderNodeMapping` | `vector_type: point`, `location: [0,0,0]`, `rotation: [0,0,0]`, `scale: [1,1,1]` |
| `normal_map` | `ShaderNodeNormalMap` | `strength: 1`, `space: tangent`, `uv_map: ""` |
| `bump` | `ShaderNodeBump` | `strength: 1`, `distance: 0.001`, `invert: false` |

Node settings summaries contain these current values. Texture Coordinate also
reports its existing read-only `object_name`, or null. Image assignment uses the
named image directly, including readable linked images; it never copies it.
There is no implicit image creation or image-unassignment through null.

Image interpolation supports `linear`, `closest`, `cubic`, and `smart`; the latter
has native engine/OSL restrictions and is not a universal quality guarantee.
Projection supports `flat`, `box`, `sphere`, `tube`; extension supports `repeat`,
`extend`, `clip`, `mirror`. Other native image-user/projection settings are retained.

Mapping types are `point`, `texture`, `vector`, and `normal`. Point mapping scales,
rotates, then translates coordinates; larger coordinate scale makes a repeated
texture denser. Texture mapping applies the inverse transform. Vector omits
translation; Normal uses inverse-transpose and normalization. Location is accepted
only for point/texture, and mode changes cannot disable an existing Location link.
Rotation is XYZ Euler radians. The scalar/vector socket hard limits are finite
float32 `[-3.4028234663852886e38, 3.4028234663852886e38]`, distinct from physical/UI
ranges; returned values reflect float32 storage.

Normal-map spaces are `tangent`, `object`, and `world`; historical Blender-space
variants are inspectable but not configurable. `uv_map` applies only to tangent
space; empty uses the native active UV choice. A tangent normal map's coordinates
must match that UV map, and its image should use Non-Color. Other native normal-map
settings are preserved. Bump strength/distance use native numeric socket units;
physical use normally uses nonnegative values. `invert` reverses the bump direction.
UV editing is provided by the separate operations below. Normal-map generation
and displacement authoring are not exposed.

### Explicit connections and deletion

`blender.shader.connect` accepts:

```json
{
  "material_name": "Surface",
  "from_node": "Texture", "from_socket": "Color",
  "to_node": "Principled BSDF", "to_socket": "Base Color",
  "replace_existing": false
}
```

Source resolves only among outputs and target only among inputs. Unknown or
ambiguous identifiers use `socket_not_found`. Disabled/unavailable/multi-input
sockets, cycles, and unsupported socket types are rejected. Numeric shader sockets
(`VALUE`, `INT`, `BOOLEAN`, `VECTOR`, `RGBA`) follow Blender's implicit conversions;
shader outputs can connect only to shader inputs. No conversion node is inserted.
Bundle/closure/menu and other socket families are outside this operation. Material
Output edits are limited to Surface; volume/displacement/thickness inputs remain
outside this layer.

An occupied input returns `socket_already_connected`, including an identical
requested link. Only `replace_existing: true` authorizes removing that input's
links. Replacement validates endpoints first, preserves unrelated links, verifies
the new link, and restores previous endpoints/mute state if it fails. The result
is LinkSummary. This operation does not select an active output or reconnect
anything beyond the requested endpoint.

`blender.shader.disconnect` accepts `material_name`, `to_node`, `to_socket`. It
removes only links entering that input and returns
`{material_name, to_node, to_socket, removed}`. An already disconnected valid input
returns `removed: 0`; bad resources/endpoints remain errors.

`blender.shader.node.delete` accepts `material_name`, `node_name` and returns
`{material_name, deleted}`. Only the five supported auxiliary types can be deleted.
All Principled and Material Output nodes are protected with `node_protected`,
including the active/surface-driving pair. Unsupported nodes use
`node_type_unsupported`; missing nodes use `node_not_found`. Blender removes the
deleted auxiliary node's own links naturally. No other nodes are rewritten.

Shader mutation requires editable material/tree data and a safe main-thread
context. Read-only data returns `invalid_context`; absent/non-shader trees return
`unsupported_material_graph`. Unexpected internal errors remain logged and
sanitized as `operation_failed`.

For an explicit UV texture workflow, create a generated image, a Principled
material, and the three auxiliary nodes; assign the image to the texture node and
connect these identifiers:

```text
Coordinates.UV → Mapping.Vector
Mapping.Vector → Texture.Vector
Texture.Color → Principled BSDF.Base Color
```

Assign the material separately with `blender.material.assign`, then render.
Texture Coordinate exposes native Generated, Normal, UV, Object, Camera, Window,
and Reflection outputs. This example uses the mesh's existing active render UVs;
it does not create or edit a UV map. Once Principled inputs are linked, the existing
constant-Principled configure operation rejects that material as custom; use the
explicit graph operations to manage those connections.

Behavior is checked against native Blender 5.2.1 and official
[image API](https://docs.blender.org/api/5.2/bpy.types.Image.html),
[image texture workflow](https://docs.blender.org/manual/en/5.2/render/shader_nodes/textures/image.html),
[mapping workflow](https://docs.blender.org/manual/en/5.2/render/shader_nodes/utilities/vector/mapping.html),
and [shader link validation](https://github.com/blender/blender/blob/v5.2.1/source/blender/nodes/shader/node_shader_tree.cc).

## Whole-mesh UV maps

UV maps belong to mesh datablocks. These operations target one named mesh object
and its complete authored mesh, including hidden/unselected faces. They do not
operate on evaluated modifier output or expose raw face/edge/vertex indices.
All return `UVInspectResult`:

```text
{
  object_name: string,
  active_map: string | null,
  active_render_map: string | null,
  mesh_users: integer,
  maps: [{
    name: string, active: boolean, active_render: boolean,
    loop_count: integer, uv_min: [u, v] | null, uv_max: [u, v] | null,
    out_of_unit_square_count: integer, pinned_count: integer
  }, ...]
}
```

Maps are sorted by name. Empty maps have null bounds. Collapsed maps have equal
minimum/maximum coordinates; no raw per-loop coordinates are returned. Unit-square
counts use a `1e-6` tolerance for native packing roundoff. Inspection reads live
BMesh UVs in Edit Mode and reports the number of **object users** of the mesh,
excluding non-object references and fake users. It does not mutate the UV maps.

`blender.uv.inspect` requires `object_name`.

`blender.uv.create_map` requires `object_name`; optional `name` uses native unique
`UVMap` naming. Explicit duplicate names are rejected. `set_active` defaults to
true. Omitted `set_render` preserves the current render map, selecting the new map
only if none exists; true explicitly selects it. The first map necessarily becomes
both editing and render-active, even if false flags were supplied. New maps use
Blender's initialized coordinates, copying the current map where one exists.

`blender.uv.set_active` requires `object_name` and map `name`. `set_active` and
`set_render` both default to true; at least one must be true. False preserves that
role's existing map. This selects editing and rendering roles independently,
without rewriting coordinates. Standard shader Texture Coordinate `UV` uses the
render-active map; an explicitly named UV/normal-map node can choose another map.

`blender.uv.unwrap` requires `object_name` and `method`. Optional `uv_map` selects
the map to modify; omission uses the editing-active map. A map must already exist.
The operation preserves the previously active editing/render map choices. Common
`correct_aspect` defaults to true and uses Blender's material-associated image
aspect correction. Set false to work without that correction.

| Method | Additional optional settings and behavior |
| --- | --- |
| `angle_based`, `conformal` | Existing seams/pins; `margin` default 0.001, `fill_holes` default false |
| `minimum_stretch` | Seam-aware SLIM solver; `margin` and `fill_holes` as above; `iterations` 1–100 (default 20), `no_flip` default true |
| `smart_project` | `angle_limit` default about 1.15191734 radians, `island_margin` default 0, `area_weight` default 0, `scale_to_bounds` default false |
| `cube_project` | Object-local axes around the mesh bounds center; optional `cube_size`, otherwise fitted to mesh bounds; `scale_to_bounds` default false |
| `cylinder_project`, `sphere_project` | Fixed object-local Z pole/X orientation around the mesh bounds center; native pinched poles and existing seam-separated islands; `scale_to_bounds` default false |

All seven methods run native production UV operators. Sphere/cylinder direction is
fixed to `ALIGN_TO_OBJECT`; viewport-directed projection is not exposed. Projection
can create overlapping islands or coordinates outside the unit square; inspect
and pack when appropriate. Angle/conformal methods need suitable topology and
existing seams; they do not invent cuts. Native solver warnings and the resulting
layout still require inspection and render verification. Pin flags are preserved;
seam-aware solvers respect pins, while projection replaces the full map layout.

Margins and `area_weight` use `[0, 1]`; smart angle uses `[0, 1.5707963705062866]`
radians. Cube size uses `[0, 3.4028234663852886e38]`; explicitly supplied zero has
Blender's native unit-size behavior. Numeric strings/booleans, non-finite numbers,
float32 overflow/underflow, nulls, and settings irrelevant to the chosen method
are rejected. Margin settings use native `FRACTION`, a fraction of final UV space.

`blender.uv.pack_islands` jointly packs 1–16 `objects`, each specifying
`object_name`, optional `uv_map`, and optional linear `density_weight` (default 1,
positive and at most 10). Optional `island_weights` entries use one existing
`face_index` per island and a multiplicative `weight`. The map must already be
unwrapped. Whole islands remain rigid; native triangulated world/UV areas normalize
relative density. `rotate` (default true) permits a 90-degree orientation change to
fit the requested aspect. Blender's native rectangle packer arranges the island
bounding boxes, preserving spacing without filling concave holes. This favors
predictable margins and rigid islands over maximum utilization.

`bounds_min`/`bounds_max` default to `[0,0]`/`[1,1]` and describe a nonempty rectangle
inside the unit tile. `resolution` defaults to 4096 (64–16384), and
`padding_pixels` defaults to 16 (1–256). Padding surrounds each rectangle, giving
at least twice that separation between islands and at least that border padding.
This is an explicit single-atlas contract, not implicit UDIM assignment. Pin flags
remain intact, but joint packing relocates all islands. Degenerate UV triangles,
empty meshes, duplicate targets/weight references, and impossible padding fail
before publishing UV edits. A batch rolls back all affected UV maps on failure.
Shared meshes are isolated, editing/render map roles are preserved, and topology,
seams, material assignments and object transforms are unchanged.

`blender.uv.inspect_layout` accepts 1–16 distinct object names in `objects`, an
optional common `uv_map` (otherwise each editing-active map), `evaluated` (default
false), and texture `resolution` (default 4096). It reports connected UV islands,
bounded face references, UV/world area, pixels per world unit, density percentiles,
triangle Jacobian singular-value ratios (1 is isotropic), maximum corner-angle
errors, signed flipped/degenerate faces, positive-area triangle overlap pairs
including cross-object overlaps, tile-centroid identifiers, unit-tile bounds,
and minimum island/border spacing in pixels. Overlaps are not automatically
classified as intentional. Touching boundaries are not positive-area overlap;
the numerical area tolerance is 1e-12 UV units squared. Density percentiles are
unweighted face samples; distortion percentiles are unweighted native triangles.

`layout_image: true` additionally returns a colored PNG through the ordinary
bounded artifact transport; `image_size` is 128–1024, default 1024. The image shows
native triangulation, island colors and a UV grid; it requires no UV editor or
filesystem-path transport. Inspection is limited to 12,000 faces, 24,000 triangles,
512 islands and two million overlap/margin candidate comparisons. Evaluated
references are inspection-only and cannot address authored edits. Results describe
the current viewport evaluation, including Mirror and Shrinkwrap; they do not
certify future subdivision, deformation, bake rays or export tangent conventions.

`blender.render.image` supports `uv_checker` with `objects`, an explicit `uv_map`,
optional `exclude_objects` and `grid_scale` (0.125–16, default 1). It renders a
native color grid on the actual object's modifier stack with Eevee. Temporary
material/mesh copies and grid images are removed; original material slots, object
visibility, layer material overrides and engine settings are restored even on
failure. Use `show_result` for native render display. Checker diagnostics cannot
combine with Cycles or wireframe options in the same render.

UV mutations require editable local mesh objects in the current view layer, with
no active render. Per-object hide/select locks are temporarily lifted and restored;
excluded collections and globally disabled objects are not exposed. Linked data
and library overrides are rejected. Shared-mesh siblings retain their original UVs.
Unwrap/map operations preserve single-object Edit Mode and selection; joint packing
and layout inspection require Object Mode and leave selection/active objects intact.
Errors include `object_not_mesh`, `uv_map_not_found`, `uv_unwrap_failed`,
`uv_analysis_failed` and normal argument/context limits.

Use `blender.mesh.mark_seam` with a typed edge selector before seam-aware unwrap.
This operation changes edge flags directly and safely supports unapplied modifiers,
shape keys and custom data without topology replacement; shared meshes are isolated.
Mirror configuration/inspection exposes `uv_flip_u`, `uv_flip_v`,
`uv_flip_per_tile`, `uv_flip_offset_u/v` and `uv_offset_u/v`. With per-tile flipping
disabled, native U flipping computes `1 - U + uv_flip_offset_u + uv_offset_u` for
the generated side. Offsets are bounded to [-10,10] for edits. Flags affect every
UV map on the object. Keep geometric Mirror settings separate from this UV policy,
and verify evaluated UVs before baking.

Behavior is checked against Blender's [UV operator source](https://github.com/blender/blender/blob/v5.2.1/source/blender/editors/uvedit/uvedit_unwrap_ops.cc),
[Mirror source](https://github.com/blender/blender/blob/v5.2.1/source/blender/blenkernel/intern/mesh_mirror.cc)
and [BMesh selection API](https://docs.blender.org/api/5.2/bmesh.types.html).

`blender.image.remove` deletes a named unused local input image, including an
image retained only by a fake user. Images referenced by materials or other
resources are rejected with `image_in_use`; render buffers and linked images
cannot be removed. External files are not deleted.

## Geometric normal baking

`blender.bake.inspect` analyzes explicit `targets`, each with `target`, `sources`
and `uv_map`. Optional paired `cage_extrusion` and `max_ray_distance` test a
particular projection; omitted values produce conservative initial suggestions.
Extrusion is in target-local units; ray distance and nearest distances are world
units. Positive finite ray distance is mandatory for execution. `use_cage`
defaults to true. Samples include vertices and native triangle centroids with a
bounded `sample_limit` (64–32768, default 8192). Results include nearest-distance,
normal-angle and directional-hit statistics, miss/backface counts, bounded face
references, and authored/evaluated UV and seam fingerprints. Recommendations
require inspection: thin parts, cavities and overhangs may need narrower settings.

`blender.bake.image` queues one new named normal image from the same targets,
with explicit ray settings on every target. It returns a `job_id` immediately;
poll `blender.bake.status` with that ID until `completed` or `failed`. Status
includes completed/total target counts, the final result or a visible error.
The latest four jobs remain available until extension reload. Native jobs need
the visible application's event loop. While a job owns temporary resources,
only bake status and extension inspection are allowed; other operations return
`adapter_busy`. This includes project changes, renders and extension reload.

It supports a single nonoverlapping
unit-tile atlas, `resolution` 64–4096 (default 4096), `margin` 0–64 (default 12),
`margin_type` extend/adjacent_faces, `device` cpu/gpu and `samples` 1–64. The current
supported type is `normal`, in `tangent` space with native OpenGL +X/+Y/+Z axes.
Existing images are never overwritten. No speculative bake-type aliases exist.

Baking freezes the current evaluated source and target meshes into a temporary
scene. Production modifiers remain unapplied, including Mirror UV transforms and
Shrinkwrap. Viewport/render modifier visibility and subdivision levels must
agree. A disposable target material owns the bake image node; native
selected-to-active runs separately for each explicit source set, accumulating
into one float Non-Color image. Original selection, scenes, visibility, materials,
UVs, geometry and render settings remain untouched. Temporary scenes, objects,
meshes and materials are removed even on failure. The successful image is a
retained resource; save it before closing the project.

Completed results report per-target native bake duration (including native job
completion dispatch), total execution duration,
configured device/backend, and image QA. `bake.inspect` accepts optional `image`
to repeat QA: target UV pixel coverage, alpha gaps, invalid normal-vector length,
negative tangent Z, tilt statistics and per-target counts. Pixel-center raster
boundaries can differ from native rasterization. Padding can cover tiny ray
misses; sampled ray tests, map inspection and rendered comparisons are necessary
alongside these metrics. Normal maps do not repair silhouettes or certify future
subdivision, rigging or export tangent conventions. GPU requests require an
enabled configured device; there is no silent CPU fallback or preference change.

`blender.image.save` persists a loaded RGBA Non-Color data image to an explicit
absolute or Blender-relative `.png` `filepath`. `bit_depth` is 8 or 16 (default
16), `overwrite` defaults false, and `pack` defaults true. Native encoding is
staged and decoded for numeric quantization verification before atomic file
publication. The map is referenced relative to the saved project where possible
and may be packed for portability. The response identifies the full-resolution
file's size, hash, bit depth and quantization error. It also returns a distinct
8-bit Non-Color PNG preview with a maximum dimension of 512, through bounded
artifact transport, with explicit preview dimensions. The preview never replaces
the production file and is not sufficient for texel-level QA; use `bake.inspect`
and close rendered surfaces for that. Filesystem destinations are native resource
persistence, not shared-path artifact transport. Bytes are never embedded in JSON.
Parent directories must exist; symlink destinations are rejected. Production PNG
files have a 128 MiB limit; diagnostic render artifacts retain their 16 MiB limit.
Oversize exports fail before publishing a file, with `artifact_too_large`.
Current output is intentionally PNG data, not a general image encoder.

`blender.render.image` accepts `surface` with `objects`, optional
`exclude_objects`, and optional `normal_image` plus its matching `uv_map`. This
produces a temporary uniform clay or tangent-normal diagnostic on the real
objects and modifier stacks. It uses EEVEE unless explicit Cycles options are
provided, and cannot combine with checker/wireframe diagnostics. Mesh resources,
material assignments, visibility and view-layer overrides are restored. Use the
same camera for isolated source, target without normals, and target with normals.
Set `surface.preserve_materials: true` to isolate the named objects using their
existing materials instead of an override. This cannot combine with `normal_image`.

`blender.mesh.set_shading` edits only selected face smooth/flat flags, preserving
ordered geometry, UVs and modifiers. Like seam edits, it isolates shared meshes
and supports unapplied modifier stacks. Plan the target shading basis before
baking: hard normal boundaries crossing continuous UVs can produce filtering
seams. Any shading-basis change requires rebaking the tangent map against that
same evaluated target; a normal map baked for flat faces cannot simply be reused
after changing them to smooth shading.

## Mesh inspection and modeling

`blender.mesh.inspect` includes triangle/quad/ngon counts and a `geometry_sha256`
fingerprint of ordered float32 local coordinates, edges and face vertex loops.
UVs, seams and materials are excluded, allowing topology-preservation checks
without complete mesh downloads. The fingerprint is snapshot-specific.

All mesh operations address an exact `object_name` in the current scene. They
inspect or edit the authored Mesh, before modifiers and evaluated deformation.
UI selection never supplies their geometry selection. Object location, rotation,
scale, parenting, active object, and object selection are preserved.

### Summary and query

`blender.mesh.inspect` takes `{"object_name": "Surface"}` and returns:

```text
MeshSummary {
  object_name: string, mesh_name: string, mesh_users: integer,
  vertex_count: integer, edge_count: integer, face_count: integer,
  loop_count: integer,
  bounds_min: [x, y, z] | null, bounds_max: [x, y, z] | null,
  material_slot_count: integer, uv_map_count: integer, has_shape_keys: boolean,
  manifold_summary: {
    boundary_edge_count: integer, manifold_edge_count: integer,
    non_manifold_edge_count: integer, loose_vertex_count: integer,
    loose_edge_count: integer
  }
}
```

Bounds enclose authored vertex coordinates in **object-local space**, and are
null only when there are no vertices. `mesh_users` counts objects referencing the
Mesh across the file, excluding fake users and other datablock references. Loops
are face corners. Material slots count the Mesh's ordered slots. A boundary edge
has exactly one face, a manifold edge exactly two, and a loose edge none.
`non_manifold_edge_count` includes boundary and loose edges; it is total edges
minus two-face edges. A loose vertex has no incident edges. These inexpensive
counts do not certify watertightness, consistent winding, or a valid solid.

`blender.mesh.query` requires `object_name` and `selector`; `limit` defaults to
64 and accepts integers 1–256. It returns exactly one domain-specific shape:

```text
{
  object_name: string, domain: "vertex" | "edge" | "face",
  matched_count: integer, truncated: boolean, elements: [...]
}
vertex element: {index, co: [x, y, z]}
edge element: {index, vertices: [index, index], seam, sharp, boundary, manifold}
face element: {
  index, vertices: [index, ...], vertex_count, vertices_truncated,
  center: [x, y, z], normal: [x, y, z], area, material_index
}
```

Results are ordered by current element index. Edge vertex indices are sorted;
face vertex indices follow polygon winding. Face centers are the arithmetic mean
of corner vertex coordinates, not area-weighted centroids. Coordinates, normals,
and area are object-local. `matched_count` counts every match even when output is
truncated. `truncated` means more matches exist than returned elements. A face's
vertex list is separately capped at 128, with its full `vertex_count` and
`vertices_truncated` flag. Empty queries return zero matches normally. Inspection
and queries can read a copy of a live Edit Mode BMesh without changing it.

### Explicit selectors

One discriminated selector union is shared by querying and editing:

| `mode` | `domain` | Additional required properties | Matches |
| --- | --- | --- | --- |
| `all` | `vertex`, `edge`, `face` | None | Every element in the domain |
| `indices` | `vertex`, `edge`, `face` | `indices`: 1–4096 unique nonnegative integers | Exact current indices, canonicalized into ascending order |
| `box` | `vertex`, `face` | `min`, `max`: three-number vectors | Vertex coordinate or face median center within inclusive bounds |
| `normal` | `face` | Nonzero `direction` vector and `min_dot` in `[-1, 1]` | Unit face normal dot normalized direction is at least the threshold |
| `boundary` | `edge` | None | Exactly one adjacent face |
| `seam` | `edge` | `value`: boolean | Authored seam flag equals the requested value |

All predicates use authored object-local geometry. Box minima must not exceed
maxima on any axis; boxes do not test polygon intersection. Normal direction is
normalized internally; degenerate faces with zero normals never match that
selector. No selection expression language, implicit UI state, nearest-point
selection, or boolean selector trees are accepted.

**Indices are snapshot-local references, not persistent IDs.** Topology editing
can delete or renumber elements. Reinspect/query after every topology edit before
reusing indices from an earlier result. Returned region face indices belong to
the completed edit's new snapshot. There is no fabricated revision or persistent
element registry. Other Blender users/tools can also invalidate previous results.

```json
{
  "object_name": "Surface",
  "selector": {
    "domain": "face", "mode": "normal",
    "direction": [0, 0, 1], "min_dot": 0.99
  },
  "limit": 16
}
```

### Geometry operations

Every edit requires `object_name`. Every edit except normal recalculation also
requires an explicit `selector`. A mutation matching nothing returns
`mesh_selection_empty`; it never falls back to all geometry.

| Operation suffix | Exact additional arguments and semantics |
| --- | --- |
| `transform` | Any selector domain. Optional `translation`, `rotation`, `scale` vectors; at least one is required. `pivot` defaults to `"median"`, or accepts `"origin"` or an explicit vector. |
| `extrude_faces` | Face selector; required nonzero `offset` vector; optional positive three-component `scale`, default `[1,1,1]`. Region extrusion with connected walls; scales the new outer cap about its vertex median, then translates it. |
| `inset_faces` | Face selector; required `thickness > 0`; `depth` defaults to 0 and may be negative; `even_offset` defaults to true. Insets a region with boundary edges, interpolating native corner data. |
| `bevel_edges` | Edge selector; required `width > 0`; integer `segments` 1–16, default 1; `profile` in `[0,1]`, default 0.5. Native OFFSET width: distance from an original edge along adjacent faces, in local units. Overlap is clamped. |
| `subdivide_edges` | Edge selector; integer `cuts` 1–32, default 1; `smooth` in `[0,1]`, default 0. Uses native grid fill and straight-cut quad corners, supporting single selected edges. |
| `delete_elements` | Any selector domain. Vertices remove incident edges/faces; edges remove adjacent faces while retaining vertices. Face deletion defaults to `face_mode: "faces_only"`, retaining edges/vertices. `"faces_and_unused"` also removes edges/vertices made unused by those faces. `face_mode` is valid only for a face selector. |
| `merge_vertices` | Vertex selector with at least two matches. `mode` is `"center"` (default). Welds selected vertices to their arithmetic mean; averages vertex custom data through native point-merge semantics. It does not perform distance-based deduplication or implicitly weld UV seams. |
| `mark_seam` | Edge selector and required boolean `seam`. Sets or clears seam flags; idempotent calls report zero changed edges. Use followed by `blender.uv.unwrap` with `angle_based` or `conformal`. |
| `set_shading` | Face selector and required boolean `smooth`. Sets native face shading; false restores flat shading. Reports `changed_faces`, including zero for idempotent calls. Face queries expose `smooth`. Preserves positions, connectivity, winding, edge sharpness, UVs, materials and supported attributes; this does not smooth geometry or clear sharp edges. Edits flags directly and isolates shared meshes; unapplied modifiers and shape keys are preserved. |
| `recalculate_normals` | No selector. `inside` defaults to false. Recalculates whole-mesh face winding with native connected-region logic; true reverses the result. Closed orientable shells support outside/inside interpretation; open, degenerate or non-manifold surfaces have no guaranteed outward direction. |

`mesh.transform` changes vertex coordinates while `object.set_transform` changes
the object transform. For edge/face selectors, each unique incident vertex is
transformed once. The mesh transform order is scale, XYZ Euler rotation in
radians, then translation. Scale and rotation act about the selected unique
vertices' arithmetic mean (`median`), the mesh origin, or the explicit local
pivot. Identity transforms are permitted if supplied explicitly. Zero/negative
scales follow Blender geometry semantics and can collapse or reflect geometry.
No cursor, viewport, global orientation, or UI pivot influences the result.
Coordinate transforms support unapplied modifiers and edit the authored cage.
Only native vertex coordinates are written to the staged Mesh copy; ordered
connectivity, UVs, seam/sharp flags, weights and other attributes are preserved
exactly. Modifiers remain in place and reevaluate the corrected cage. Inspect
the resulting evaluated surface and rebake normal maps when their basis changes.

For a smooth directional adjustment, translation also accepts optional
`falloff: {"center": [x,y,z], "radii": [rx,ry,rz]}`. Center and positive ellipsoid
semiaxes are object-local. The explicit selector remains a hard boundary: only
its unique vertices strictly inside the ellipsoid receive displacement. With
normalized ellipsoid distance `d`, the translation weight is
`(1-d)^2 * (1+2*d)` for `0 <= d < 1`, and zero outside. This uses Blender's
[smooth proportional falloff](https://github.com/blender/blender/blob/v5.2.1/source/blender/editors/transform/transform_generics.cc)
with an explicit local influence region. Equal radii give spherical influence.
It avoids coordinate queries and repeated small transforms when moving a soft
region. Weights are evaluated from the input positions, with no viewport,
cursor, connected-distance or sculpt-mask dependence. Existing mask values are
preserved but do not weight this mesh operation. Use selectors to protect geometry.

Falloff supports translation only; supplying rotation or scale with it is an
argument error. `transformed_vertices` counts selected vertices with positive
influence, while `selected.count` still describes all selector matches. A region
that reaches none of those vertices returns `mesh_selection_empty` without
replacing the mesh. The existing staged validation, topology/data preservation,
shared-mesh isolation and editing guards apply unchanged.

```json
{"operation":"blender.mesh.transform","arguments":{"object_name":"Surface","selector":{"domain":"vertex","mode":"all"},"translation":[0,0,0.2],"falloff":{"center":[0,0,1],"radii":[0.8,0.5,0.4]}}}
```

Extrusion/inset require a region with a boundary and reject incident edges having
more than two faces; selecting an entire closed shell is rejected. Adjacent
selected faces form regions, not independent face extrusions/insets. Extrusion
replaces the old connected cap instead of leaving an internal duplicate face.
Bevel requires consistently wound, two-face manifold edges. It uses native
material inheritance, loop slide, and seam/sharp propagation, without adding a
modifier or hardening custom normals. These operations do not guarantee freedom
from self-intersections, collapsed faces, or poor topology for arbitrary inputs.

Edits return:

```text
MeshEditResult {
  object_name,
  selected: {domain, count},
  created: {vertices, edges, faces},
  removed: {vertices, edges, faces},
  mesh: MeshSummary,
  region_faces?: {indices: [...], total, truncated},
  transformed_vertices?: integer,
  changed_edges?: integer
}
```

`selected.count` counts input matches before editing. Created/removed counts
track actual surviving BMesh elements, rather than assuming net count differences
represent creation/deletion. Extrusion returns new outer cap `region_faces`;
inset returns inner inset faces, useful for a following extrusion. Those lists
contain at most 256 indices and report their full total and truncation separately.
`transformed_vertices` appears for regional transforms and extrusion;
`changed_edges` appears for seam changes. Other optional fields are omitted.

### Mutation safety and data preservation

Mutations require Object Mode and editable local objects/Mesh data in an editable
scene, with no active render. Linked, read-only and library-override data remain
inspectable but cannot be mutated or silently localized. Shape-key meshes are
rejected by geometry mutations, including coordinate transforms and normals,
with `mesh_has_shape_keys`. Deliberate shape-key-aware geometry editing is deferred.
Authored custom split normals, mesh animation and vertex-parented children also
return `invalid_context` for geometry edits. Topology/winding edits additionally
reject object modifiers; coordinate-only transforms preserve unapplied stacks.
Seam and smooth-shading flag edits preserve these relationships without changing
coordinates or connectivity. No operation silently applies modifiers.

Edits resolve selection, operate on a detached BMesh, refresh index tables and
derived normals, and write to a temporary copy of the Mesh. The resulting summary
and custom-data layout are validated before a single object data-pointer change
commits the edit. An operation/writeback/result failure discards the candidate
and leaves the original mesh pointer and geometry intact. A sibling sharing the
original Mesh always keeps its original topology, materials, UVs and weights.
Even single-user edits replace the Mesh datablock: its identity/name may change,
and external Python references into the former datablock must be reacquired.
An original with no remaining users is removed; other users and fake users retain
it. This is operation-level staging, not a persistent undo/transaction service.
Cancellation after main-thread execution starts follows the existing dispatcher
contract and does not undo an already completed edit.

Mesh copies retain material slots and vertex group definitions. BMesh carries
vertex weights, supported custom attributes, face material/smooth flags, UV
layers, pins and seams through native interpolation rules. Active editing/render
UV roles are preserved. Tests cover constant weight/attribute interpolation and
material inheritance through transform, extrusion, inset, bevel and subdivision,
and unchanged bottom-face UVs through regional transform, extrusion and inset.
New faces/corners use Blender's derived values; topology editing is not a promise
of artistically correct new UVs. Reinspect and unwrap where needed. Unexpected
loss of a named custom-data schema rejects the staged edit. Surviving element
selection/visibility and UV selection flags are preserved; new elements start
unselected, and deleted elements cannot remain selected.

Arguments reject unknown fields, explicit nulls, numeric booleans/strings,
non-finite values, float32 overflow and nonzero float32 underflow. Indices are
range-checked against the entire current snapshot before any mutation. Queries
and edits have a 2,000,000-element work bound, counting vertices, edges, faces and
corners together; conservative operation-specific growth estimates can reject an
edit before its actual result would reach that limit. Post-edit finite geometry
and work limits are checked again before commit. These bounds limit work and
output, but cannot make synchronous native geometry calls interruptible.

Missing/non-mesh objects return `object_not_found`/`object_not_mesh`; malformed
selectors or unsupported region geometry return `invalid_arguments`. Capacity
and protected-data/context failures use `invalid_context`; unexpected native
failures are sanitized as `operation_failed`.

Hole fill, triangulation, individual face operations and shading controls
are deferred. Filling arbitrary boundary selections needs a
deliberate loop/hole contract; no ambiguous catch-all fill operation is exposed.
Semantics follow the official [BMesh API](https://docs.blender.org/api/5.2/bmesh.html),
[Blender 5.2.1 operation definitions](https://github.com/blender/blender/blob/v5.2.1/source/blender/bmesh/intern/bmesh_opdefines.cc),
[region extrusion implementation](https://github.com/blender/blender/blob/v5.2.1/source/blender/bmesh/operators/bmo_extrude.cc),
and [mesh conversion implementation](https://github.com/blender/blender/blob/v5.2.1/source/blender/bmesh/intern/bmesh_mesh_convert.cc),
with native and real-render verification.

## Modifier stacks and evaluated meshes

Modifiers belong to objects. Creating, configuring, reordering or removing one
does not replace or modify the authored Mesh, including when several objects
share it. This is the reversible base-form workflow; application is a separate,
explicit destructive operation. Direct `blender.mesh.*` topology edits retain
their existing modifier/shape-key guards.

`blender.modifier.inspect` takes `{"object_name": "Surface"}` and returns
`{"object_name": "Surface", "modifiers": [...]}`. Each entry contains:

```json
{
  "name": "Form",
  "index": 0,
  "type": "subdivision_surface",
  "supported": true,
  "enabled_viewport": true,
  "enabled_render": true,
  "show_in_editmode": true,
  "show_on_cage": false,
  "settings": {
    "mode": "catmull_clark",
    "levels": 1,
    "render_levels": 2,
    "uv_smooth": "preserve_boundaries",
    "boundary_smooth": "all",
    "use_creases": true,
    "show_only_control_edges": true
  }
}
```

Order is top to bottom, with zero-based indices. Existing unsupported types,
including Geometry Nodes, remain in this list with `supported: false`, their
lowercase native type identifier and `settings: null`. No arbitrary RNA dump is
returned. `supported` identifies a configured type, not a guarantee that every
native mode or dependency on an existing modifier is supported for mutation.
Inspection preserves incomplete/native state, including missing targets, disabled
axes and subdivision levels above the input safety cap.

Creation and configuration use a type-discriminated settings union. Configuration
requires `type` to match the existing modifier; it cannot convert types. Omitted
settings and common flags retain their native values. Explicit nulls, unknown
fields, blank/NUL-containing names, numeric booleans/strings, non-finite numbers, float32 overflow
and nonzero float32 underflow are rejected. Values are normalized to Blender's
float32 representation. Explicit duplicate names are rejected within the object;
omitting `name` allows native unique naming. Explicit names must fit Blender's
63-byte UTF-8 limit and contain no NUL.

The common writable flags are `enabled_viewport`, `enabled_render` and
`show_in_editmode`, beside `settings`. Cage display is inspection-only: its native
flag is returned for Mirror, Subdivision, Shrinkwrap and Solidify, otherwise null.
Actual cage eligibility also depends on enable state, targets and preceding stack
mapping support, so the flag alone does not promise an editable evaluated cage.

### Supported settings

| Type | Settings and native defaults |
| --- | --- |
| `mirror` | `axes: ["x"]`, `bisect_axes: []`, `bisect_flip_axes: []`, `clipping: false`, `merge: true`, `merge_threshold: 0.001`, optional `mirror_object` |
| `subdivision_surface` | `mode: "catmull_clark"`, `levels: 1`, `render_levels: 2`, `uv_smooth: "preserve_boundaries"`, `boundary_smooth: "all"`, `use_creases: true`, `show_only_control_edges: true` |
| `shrinkwrap` | Required creation `target`; `method: "nearest_surface"`, `mode: "on_surface"`, `offset: 0`; optional `projection` when method is `project` |
| `boolean` | Required creation `operand_object`; `operation: "difference"`, `solver: "exact"` |
| `solidify` | `thickness: 0.01`, `offset: -1`, `even_thickness: false`, `rim: true`, `rim_only: false`, `quality_normals: false` |

Mirror axis lists use unique lowercase `x`, `y`, `z`; at least one main axis must
be active. Bisect and flip flags are stored per axis and affect enabled axes.
The default symmetry frame is the object's local origin; a named `mirror_object`
provides a separate frame without moving either object. Configuration can clear
that reference with `"clear_mirror_object": true`, mutually exclusive with a new
name. Merge threshold is nonnegative. Existing UV mirroring/offset and vertex-group
mirroring settings remain untouched.

Subdivision `mode` is `catmull_clark` (round the form) or `simple` (subdivide without
rounding). Both viewport and render levels are integers **0–6**. Blender permits
0–11; the smaller range is an additional safety policy. `boundary_smooth` is `all`
or `preserve_corners`. `uv_smooth` is `none`, `preserve_corners`,
`preserve_corners_and_junctions`, `preserve_corners_junctions_and_concave`,
`preserve_boundaries` or `smooth_all`. Native quality, limit-surface and other
unexposed settings are preserved. Adaptive subdivision is outside this workflow.

Shrinkwrap supports `nearest_surface`, `project` and `target_normal_project`.
`mode` is `on_surface`, `inside`, `outside`, `outside_surface` or `above_surface`.
Offset is signed. The above-surface offset follows the target's interpolated
normal, which can differ from a flat face normal near corners. Nearest-vertex
projection remains inspectable but is not configurable in this initial subset.
Project settings are a nested partial object with `axes` (default empty, meaning
source-normal projection), `positive: true`, `negative: false`, `cull_face: "off"`
(`off`, `front`, `back`), `invert_cull: false` and `limit: 0` (unlimited).
At least one direction must remain enabled. Invert-cull affects the negative
direction's culling. Projection controls require the effective `project` method;
switching away preserves the latent native projection settings. Vertex-group and
auxiliary-target settings are preserved. Internal Shrinkwrap subdivision is
outside the bounded evaluation policy.

Boolean operates on another Mesh object, using `union`, `intersect` or `difference`.
Blender 5.2 solvers map to `float`, `exact`, `manifold`; `exact` is the default.
Float is unsuitable for overlapping coplanar faces. The Manifold solver is
restricted here to closed, consistently oriented authored inputs without other
modifiers on either input. General Boolean success does not guarantee a clean
manifold result for arbitrary geometry; inspect the result. Collection operands
are inspection-only. Operand objects are never hidden, deleted, moved or modified.
Existing material mode, self-intersection and tolerance settings remain untouched.

Solidify uses Blender's simple extrusion mode. Thickness and offset are signed
finite float32 values; offset is not restricted to its UI soft range of -1 to 1.
Thickness is local-space, so nonuniform object scale affects world-space thickness.
`rim_only` requires `rim`: it creates boundary walls and retains the original
surface while omitting the offset shell. Existing complex-mode modifiers are
inspectable but not configurable or applicable. Material offsets, clamp and
vertex-group controls are preserved.

### Create, configure, move and remove

```json
{"object_name": "Surface", "type": "mirror", "name": "Symmetry", "settings": {"axes": ["x"], "clipping": true}}
```

```json
{"object_name": "Surface", "type": "subdivision_surface", "name": "Form", "settings": {"levels": 2, "render_levels": 2}}
```

```json
{"object_name": "Surface", "modifier_name": "Form", "type": "subdivision_surface", "settings": {"levels": 1}, "enabled_render": true}
```

Creation appends to the stack. `blender.modifier.move` takes
`{"object_name": "Surface", "modifier_name": "Form", "index": 0}`; the index is
the **final** position and must be less than the stack size. Moving to the current
index succeeds without a change. Order can materially change evaluated geometry.
Creation and reordering reject pinned-last stacks rather than silently unpinning
or accepting a different order. Except for protected Multires stacks, explicit
move/remove can address unsupported
native modifiers while preserving other entries and unexposed settings.

`blender.modifier.remove` takes object and modifier names and returns
`object_name`, `removed` and the ordered remaining `modifiers`. It never applies
the modifier. Missing names return `modifier_not_found`. Configuration validates
the proposed complete supported state before writing; unexpected write/result
failures restore the changed fields.

### Authored and evaluated inspection

`blender.mesh.inspect` continues to describe the editable **authored** Mesh.
`blender.mesh.inspect_evaluated` takes `object_name` and optional `uv_map`, and returns:

- `object_name`, `source_mesh_name`, `evaluation: "viewport"`;
- `vertex_count`, `edge_count`, `face_count`, `loop_count`;
- object-local `bounds_min`/`bounds_max` (null for an empty mesh);
- `manifold_summary`, with the same edge/loose-element counts as authored inspection;
- `modifier_count` and `modifier_stack`, an ordered list of `{name, type}` entries.

This is the current dependency-graph viewport result, including viewport enable
state and viewport subdivision levels. Render levels can produce a different
rendered mesh. No evaluated element indices, coordinates dump, RNA objects or
temporary pointers are exposed.

`blender.mesh.inspect_evaluated` reports compact authored and evaluated surface
signatures. Optional `uv_map` adds UV and native tangent/bitangent-sign fingerprints
for triangle/quad meshes; without it, tangent generation is omitted. Reports include
smooth-face, sharp-edge, seam and crease counts, custom-normal presence, and
object-local ordered geometry, shading-flag and corner-normal fingerprints.
Tangents are calculated only on temporary mesh copies, with cleanup on failures.
Existing modeling/evaluation budgets apply.

The operation evaluates the current viewport dependency graph and reports modifier
visibility/subdivision-level differences from render settings. An empty difference
list is not a promise of equivalence for every host feature or renderer. Fingerprints
identify an exact local snapshot; they are not persistent mesh IDs or a perceptual
quality verdict. A changed geometry, UV or normal/tangent signature requires renewed
bake/render validation. In particular, a tangent normal map baked before changing
subdivision or smoothing must not be assumed valid afterward. Keep authored and
evaluated signatures separate when comparing unapplied modifiers.

 **Evaluated topology is inspection-only and cannot
be used as authored topology indices in direct mesh edits.** The evaluated object's
temporary `to_mesh()` output is always cleared in `finally`, including failures;
it is never attached to a real scene object.

### Explicit application and safety

`blender.modifier.apply` takes
`{"object_name": "Surface", "modifier_name": "Form"}` and returns `object_name`,
`applied`, the updated authored `mesh` summary and remaining ordered `modifiers`.
Only the named supported modifier is applied. Blender applies a nonfirst modifier
to the authored input as if it were first; it does **not** bake preceding modifiers.
Thus applying out of order can change the final form. Apply in stack order when
preserving the evaluated appearance is the goal.

Application uses Blender's native operator on a temporary object and independent
Mesh copy, with an explicit context override and `use_selected_objects: false`.
It disables the operator's optional extra UV merge. Result and data checks complete
before the target's Mesh pointer changes and its named modifier is removed. Other
modifier instances/settings/order are retained. Failures discard staging and
preserve the original object/data/stack. Active object, selection and mode are
preserved; temporary objects and unused Mesh copies are removed. Even single-user
application replaces the authored datablock; reacquire external Mesh references
and its returned name. Siblings keep the original shared Mesh unchanged.
Object-linked material assignments, underlying data slots and the active material
index are preserved through the staged application and Mesh replacement. Existing
slot order and effective assignments are validated before commit; new native slots
are carried with their link modes. Failed publication restores the original slot
state with the original Mesh.

Native interpolation determines new UVs, seams, face material indices, weights
and custom attributes. Representative Simple subdivision tests preserve both UV
roles, material slots, constant point attributes/weights and subdivided seam edges.
UV map names, existing material slots and group definitions are checked before
commit. This is not a promise that every custom data layer has identical values
after a topology-changing modifier; reinspect/unwrap as appropriate.

Mutations require Object Mode, a local editable scene/object and no running render.
Linked/override objects and animated object state are rejected. A local object may
hold modifiers over a linked Mesh, but **application requires local editable Mesh
data**. Shape-key meshes permit stack changes/evaluated inspection, but application
returns `mesh_has_shape_keys`. Authored custom normals, mesh animation, constraints,
and topology-dependent parenting also protect apply.
Remaining Armature modifiers are preserved rather than applied. These guards avoid
inventing remapping/rigging workflows. Edit Mode requests fail without changing UI
mode; stack inspection remains available.

Object references must exist unambiguously in the current scene, be compatible,
and not be self references. Names shared across linked libraries are rejected. Dependency checking follows parent links, native object/collection
modifier pointers and constraint targets, with a 256-object traversal limit.
Cycles, animated dependency chains and opaque Geometry Nodes dependencies are
rejected rather than assumed safe. Target objects are read-only inputs.

The work limit is **2,000,000 total vertices, edges, faces and corners**, with a
maximum stack size of 128. Preflight estimates account for straightforward
Subdivision growth and conservative Mirror/Solidify/Boolean factors, checking
both viewport and render configurations without evaluating the scene on each edit.
Unknown generators may remain in stack edits, but evaluated inspection/application
reject enabled unsupported topology generators because their growth is unbounded.
Evaluated output is checked before temporary extraction and again afterward;
application also validates finite coordinates before commit. Boolean intersections
are data-dependent, so their estimate is not a mathematical output bound. Blender
evaluates the current view-layer dependency graph before its result can be counted;
limits cannot prevent every native allocation, bound unrelated user-scene work or
interrupt a synchronous native operation.

Errors include `modifier_not_found`, `modifier_type_unsupported`,
`modifier_dependency_invalid`, `modifier_apply_failed`, `mesh_has_shape_keys`,
`object_not_found`, `object_not_mesh`, `invalid_arguments` and `invalid_context`.
Unexpected non-application failures follow the sanitized `operation_failed`
contract. No scene replacement, automatic apply, arbitrary RNA setter, script
execution or persistent undo/history layer is introduced.

The contract follows the official [modifier workflow](https://docs.blender.org/manual/en/5.2/modeling/modifiers/introduction.html),
[dependency graph API](https://docs.blender.org/api/5.2/bpy.types.Depsgraph.html),
[5.2.1 modifier RNA](https://github.com/blender/blender/blob/v5.2.1/source/blender/makesrna/intern/rna_modifier.cc)
and [native application implementation](https://github.com/blender/blender/blob/v5.2.1/source/blender/editors/object/object_modifier.cc).

## Surface picking

`blender.scene.raycast` casts against the **current evaluated viewport scene**.
It supports two disjoint argument shapes:

```json
{"mode": "world", "origin": [0, 0, 5], "direction": [0, 0, -1], "max_distance": 100}
```

```json
{"mode": "camera", "u": 0.5, "v": 0.5, "width": 512, "height": 512}
```

World origins/directions are in world coordinates. Direction must be nonzero and
is normalized internally; `max_distance` is a positive distance in Blender units,
not a multiple of the supplied direction. Its default is 1,000,000.

Camera mode uses normalized **uncropped image** coordinates: `u=0` is left,
`u=1` is right, `v=0` is top and `v=1` is bottom. Both coordinates are in `[0,1]`.
Optional `camera_name` selects a scene camera; omission uses the active camera.
Optional `width` and `height` must be supplied together (1–65,536 each); omission
uses the scene's render dimensions. **When picking from `blender.render.image`,
pass that render's width and height**, because rendering temporarily overrides
scene dimensions and then restores them. Pixel aspect remains scene state.

Blender's evaluated camera projection matrix supplies perspective/orthographic
projection, lens, sensor fit/size, image aspect and lens shift. The evaluated
camera transform supplies its position/orientation. Camera scale does not change
optics. Rays honor near/far clipping. For perspective rays, distance is measured
from the camera origin; for orthographic rays, from the corresponding point on
the camera's local `z=0` plane. `max_distance` further limits either ray.
Panoramic/custom projections return `unsupported_projection`; a missing active
camera returns `no_camera`. World mode does not require a camera.

The result is:

```text
{
  hit,
  object_name, object_type,
  location_world, normal_world,
  location_object, normal_object,
  distance,
  evaluated_face_index
}
```

A miss is normal: `hit=false` and every other field is `null`. Hit locations are
finite vectors; both normals are normalized. Object-local conversion uses the
actual hit transform, including instances and nonuniform scale. Names identify
the original scene object. **`evaluated_face_index` is inspection-only, may be
null, and must never be passed to an authored-mesh selector.** It is not a stable
face identifier. A hit does not change selection or the active camera.

This is a geometric surface pick, not image segmentation: alpha transparency,
compositor image warps, depth of field and render-only visibility/detail can make
a rendered pixel differ from the viewport's first geometric intersection. Match
viewport/render detail and visibility for visual targeting, use an unwarped image,
and inspect the returned object before sculpting. Picking supports Object/Sculpt
Mode outside rendering and bounds visible Mesh evaluation with the existing
geometry policy and a 256-object view-layer limit.

## Multiresolution

Multires is dedicated sculpt-displacement state, managed only through:

| Operation | Arguments | Result |
| --- | --- | --- |
| `blender.multires.inspect` | `object_name` | `MultiresSummary` |
| `blender.multires.create` | `object_name`; optional `name` | `MultiresSummary` |
| `blender.multires.subdivide` | `object_name`; optional `modifier_name`, `levels`, `mode` | `MultiresSummary` |
| `blender.multires.configure` | `object_name`; optional `modifier_name`, `viewport_level`, `sculpt_level`, `render_level` | `MultiresSummary` |

Inspection returns:

```text
{
  object_name, present, modifier_name,
  total_levels, viewport_level, sculpt_level, render_level,
  base_mesh: {
    vertex_count, edge_count, face_count,
    quad_face_count, non_quad_face_count, non_manifold_edge_count
  },
  diagnostics: [{code, severity, message}]
}
```

Without Multires, `present=false` and the modifier/level fields are null. Base
counts remain available. Diagnostics distinguish `warning` from `blocker` and
report non-quads, boundary/non-manifold edges, shape keys, unapplied scale,
other modifiers and stored levels beyond the safety policy. Inspection does not
reject a mesh merely for imperfect topology. Ambiguous multiple Multires
modifiers are outside this single-modifier workflow.

Creation adds one modifier with zero subdivision levels. The initial dedicated
workflow requires an otherwise empty stack: explicitly resolve any existing
Mirror, Subdivision Surface or other modifier before creating Multires. It never
applies, removes or reorders another modifier automatically. This conservative
contract is narrower than every stack Blender can represent and avoids sculpt
coordinates whose relation to the authored cage requires deformation mapping.

Subdivision adds `levels` levels, default one. Supported modes are
`catmull_clark` (default; smooth subdivision), `simple` (unsmoothed base edges),
and `linear` (linear interpolation of existing sculpt displacement). There are
at most **six total levels**, and a conservative growth estimate must also fit
the existing **2,000,000 vertices + edges + faces + corners** work bound. Sculpt
grid allocation is checked separately. A small maximum level is not permission
to subdivide an already large mesh. Empty/degenerate faces and edges shared by
more than two faces are rejected; ordinary triangles and open boundaries are
permitted with diagnostics.

Configuration only switches existing levels: each supplied level is between
zero and `total_levels`. It validates the complete patch before writing and
preserves omitted fields. It does not subdivide or delete higher detail. Edits
require Object Mode and local editable, unanimated object/Mesh data. Shape keys,
authored custom normals, external displacement files and Sculpt Base Mesh mode
require separate workflows and are protected.

Prefer clean production quads, sensible pole placement and manifold surfaces
where appropriate. Sculpt broad forms at low levels, add finer detail at higher
levels, and switch levels to inspect or refine the form. The base topology
remains authored Mesh data; higher detail lives in native Multires displacement.
Subdivision isolates a Mesh shared by other objects before native displacement
writes. Native UV maps, material slots/indices, seams, float attributes and
vertex-group weights are preserved in the supported tests. Level switching and
higher-level strokes preserve the authored topology and its coordinates.

Multires remains visible as `type: "multires", supported: false` in generic
modifier inspection, with settings managed by these dedicated operations.
Generic Multires removal, any reordering of a Multires stack, and modifier
application on a Multires object are blocked. Existing direct mesh topology and
coordinate edits remain blocked by their modifier guard. Never rebuild topology
underneath sculpted detail. Delete-higher-level, reshape, apply-base, baking and
Multires removal are deliberately not exposed yet.

Native subdivision is not a persistent transaction: an unexpected failure may
leave successfully added levels in place. `multires_failed` reports whether
mutation was possible; reinspect before retrying. User-authored displacement is
never deliberately discarded as a recovery shortcut.

## Controlled sculpt strokes

`blender.sculpt.inspect` takes `object_name` and returns:

```text
{
  object_name, object_mode, object_scale, scale_applied,
  multires: MultiresSummary,
  effective_sculpt_level, sculpt_vertex_count,
  symmetry: {x, y, z}, view3d_available,
  mask: SculptMaskSummary, hidden_geometry
}
```

`sculpt_vertex_count` counts native grid points at a Multires sculpt level,
including duplicated grid boundaries; otherwise it counts base vertices. It is
not the unique evaluated Mesh vertex count. `mask` follows the explicit statistics
scope below. `hidden_geometry` inspects authored visibility only, not private
higher-level hidden grid points.

`blender.sculpt.stroke` takes:

```json
{
  "object_name": "Surface",
  "brush": "draw",
  "samples": [{"location": [0, 0, 1], "pressure": 1}],
  "radius": 0.2,
  "strength": 0.4,
  "invert": false,
  "symmetry": {"x": false, "y": false, "z": false}
}
```

Required fields are `object_name`, `brush`, `samples`, `radius`, and `strength`.
There are 1–256 samples. Every location is **object-local**, normally taken from
`scene.raycast.location_object`. Before execution, all samples are snapped to
the nearest surface at the sculpt level only when the distance is no greater
than `radius`; an out-of-range sample rejects the whole request before a brush
runs. The result reports the actual snapped locations and maximum snap distance.
It does not expose evaluated vertex IDs or guessed affected-vertex counts.

Radius is in Blender object units, not viewport pixels. Unit local and inherited
scale (tolerance `1e-5`, without shear/reflection) is required so these are also
world Blender units. Unapplied scale returns `sculpt_unapplied_scale`; transforms
are never automatically applied. Radius is 0.0005–1,000,000, matching Blender's
minimum 0.001-unit brush **diameter**. Scene Simplify must be disabled for Multires
strokes so Object Mode sampling and Sculpt Mode detail agree.

Strength is Blender's `[0,1]` brush strength. Pressure defaults to one, is in
`[0,1]`, and uses the native Essentials strength-pressure response; radius pressure
is disabled. Zero strength/pressure can produce `changed=false`. The adapter uses
explicit native dabs: it does not interpolate extra samples or reinterpret brush
spacing as a distance along the path. Supply an appropriately sampled path;
repeated locations build repeated dabs for brushes supporting stationary input.
Work is bounded to 64 million sculpt-point × sample × mirror-pass combinations.

| Brush | Essentials asset | Behavior |
| --- | --- | --- |
| `draw` | Draw | Add volume along the sampled surface normal |
| `smooth` | Smooth | Average local geometry to reduce surface irregularity |
| `inflate` | Inflate/Deflate | Expand along local vertex normals |
| `clay` | Clay | Build local volume toward the brush plane |
| `crease` | Crease Polish | Cut and pinch a groove |
| `flatten` | Flatten/Contrast | Move the surface toward an averaged plane |

`invert=true` uses the native inverse behavior, including subtract/deflate,
Smooth's detail enhancement and Flatten's increased contrast. **Flatten requires
a moving path with distinct surface locations and tangential motion**; stationary
dabs cannot initialize its Plane behavior. The adapter derives its viewport
motion from those 3D samples, with the initial view aligned to the first sampled
surface normal. No public mouse-event dictionaries or arbitrary pixel radii are
used. Draw Sharp/Pinch remain outside this initial validated set. Grab and Clay
Strips need deliberate drag/directional contracts; Snake Hook, Pose, cloth and
paint brushes are not exposed.

Symmetry uses local mesh X/Y/Z axes. Omission deterministically disables all three
for that stroke; a partial symmetry object defaults omitted axes to false. Native
symmetry feathering is enabled, radial repetition and tiling are disabled, and
prior mesh symmetry is restored afterward. Existing masks and hidden geometry
are respected; automatic masking, gravity and axis locks do not silently alter
the requested stroke. Sculpt masks provide explicit regional protection.

Actual strokes require a normal interactive Blender window with a View3D region.
Background strokes return `invalid_context`; raycasting, Multires management and
inspection remain usable headlessly. The target must be a visible, selectable,
local single-user Mesh, in Object Mode or already the active Sculpt Mode object.
Finish other editing modes first. Other modifiers, animation, constraints,
shape keys, authored custom normals and Dyntopo are protected. Multires strokes
require enabled internal displacement at a positive sculpt level, with Sculpt
Base Mesh disabled. Ordinary meshes without Multires can also be sculpted.

The adapter temporarily uses a fresh local Essentials brush, an isolated scene
for paint settings, and a deterministic View3D orientation. The actual object and
Mesh are sculpted by `bpy.ops.sculpt.brush_stroke`; no direct vertex deformation
stands in for sculpting. It restores the original scene, active object, selection,
mode, viewport, brush/tool state and mesh symmetry. A private temporary library
copy avoids Blender reusing an already linked, possibly modified brush. Temporary
brush data/library files are removed. No asset library, startup preference or
workspace configuration is saved.

The result includes `object_name`, `brush`, `sample_count`, `radius`, `strength`,
`invert`, `symmetry`, `multires_level`, `snapped_locations`, `max_snap_distance`,
`bounds_before_min/max`, `bounds_after_min/max`, and `changed`. Bounds and change
detection compare evaluated local geometry at the sculpt level, independently of
the restored viewport display level.

**A native sculpt stroke is not transactional.** All inputs, resource limits and
surface samples are checked first, and UI restoration runs in `finally`. If an
unexpected error occurs during or after native execution, `sculpt_failed` reports
`mutation_possible: true`; inspect the geometry before retrying. This is not a
claim of rollback or a replacement for future undo/history support. Dyntopo,
voxel remeshing and retopology have different destructive topology/data semantics
and are not part of this layer.

## Sculpt masks and Face Sets

Masks protect sculpt geometry: **0 is fully editable, 1 is fully protected**, with
partial protection between those values. Masks, persistent Face Sets, mesh UI
selection, vertex groups and materials are separate concepts. Protect good detail
before broad refinement, then render and verify both regions.

| Operation | Arguments | Result |
| --- | --- | --- |
| `blender.sculpt.mask.inspect` | `object_name` | `SculptMaskSummary` |
| `blender.sculpt.mask.stroke` | Stroke contract below | Snapped targets and updated mask summary |
| `blender.sculpt.mask.clear` | `object_name` | Updated mask summary |
| `blender.sculpt.mask.invert` | `object_name` | Updated mask summary |
| `blender.sculpt.face_sets.inspect` | `object_name` | Authored base-face region summary |
| `blender.sculpt.face_sets.assign` | `object_name`, face-domain `selector`, optional `face_set_id` | Assigned ID/count, mesh isolation flag and updated summary |
| `blender.sculpt.face_sets.initialize` | `object_name`, `mode` | Updated region summary |

`SculptMaskSummary` has this shape:

```text
{
  object_name, multires_level,
  effective_values_available,
  statistics_scope: "base_mesh",
  base_mesh: {
    present, sample_count, min, max, mean,
    masked_fraction, fully_masked_fraction, unmasked_fraction
  }
}
```

Statistics describe Blender's authored `.sculpt_mask` FLOAT/POINT attribute,
including hidden vertices. A missing attribute means all base values are zero.
`masked_fraction` counts values greater than 0.001; `unmasked_fraction` counts
values at most 0.001; `fully_masked_fraction` counts values at least 0.999 and is a
subset of masked points. Empty meshes report zero counts/statistics.

**Multires masks live in native grid storage that Blender 5.2 does not expose
through RNA.** With stored Multires levels, `effective_values_available` is false:
base statistics and attribute presence cannot establish effective grid coverage.
Even an evaluated `.sculpt_mask` attribute is only interpolated base data. The
adapter does not read private C layouts or invent effective statistics. Native
mask painting, inversion, clearing, sculpt strokes and filters operate on the
actual grid masks. Native level changes preserve that storage, subject to
Blender's resolution-dependent interpolation. Inspect, render and verify at the
level used for the intended refinement.

Mask painting takes:

```json
{
  "object_name": "Surface",
  "mode": "add",
  "samples": [{"location": [0, 0, 1], "pressure": 1}],
  "radius": 0.4,
  "strength": 1,
  "symmetry": {"x": false, "y": false, "z": false}
}
```

`mode` is required: `add` increases protection; `subtract` decreases it. Targets,
radius, pressure, symmetry, sample limits, scale guards and surface snapping have
the same meaning as sculpt strokes. The fresh native Essentials Mask brush owns
falloff and accumulation. A single dab need not fully protect its entire radius.
The result reports `object_name`, `mode`, `sample_count`, `radius`, `strength`,
`symmetry`, `snapped_locations`, `max_snap_distance`, and `mask`; it does not claim
an affected-grid count. Zero pressure/strength can still initialize mask storage.

Clear uses native zero flood-fill and is idempotent. Invert uses native `1-mask`.
Both affect visible sculpt points and preserve hidden masks, geometry and Face
Sets; clear does not mean deleting hidden protection. Existing `sculpt.stroke`
and `sculpt.filter` respect native masks and hidden geometry. Mask mutations and
filters share the interactive context, single-user, scale, modifier, restoration
and failure guards of sculpt strokes. Read-only mask inspection works headlessly
outside Edit Mode; Multires statistics retain the limitation above.

Face Sets use Blender's native `.sculpt_face_set` INT/FACE attribute. Inspection
returns `object_name`, `authored`, `scope: "base_mesh"`, `unassigned_face_count`,
and `face_sets` sorted by ID. Each entry contains `id`, `face_count`,
`hidden_face_count`, local authored `area`, and local `bounds_min/max`. Missing
storage represents Blender's implicit default ID 1, with `authored: false`.
Stored ID 0 counts as unassigned. Negative IDs are rejected as requiring explicit
repair; visibility uses native hide state, not public negative IDs. Runtime
cursor-based active Face Set state is not exposed as persistent selection.

Assignment uses the existing authored face selector, including snapshot-local
indices or semantic bounds/normals. An omitted ID allocates `max(1, current IDs)+1`;
an explicit ID may create or reuse any integer from 1 through 2,147,483,647.
Empty selections, invalid indices and more than 256 resulting sets are rejected
before mutation. Hidden faces are assignable when explicitly selected; visibility
is preserved. Results contain `assigned_id`, `assigned_face_count`,
`mesh_isolated`, and `summary`. The complete Mesh is staged before publication,
preserving native Multires displacement/mask grids; shared siblings keep their
original Mesh. Face Set writes require Object Mode and local editable data under
the dedicated Multires guards. They do not require unit scale or an interactive
window. Coordinates, topology, UVs, materials and unrelated attributes remain
unchanged.

Initialization supports four deterministic authored-topology modes matching
Blender's region concepts: `loose_parts`, `materials`, `uv_seams`, and
`sharp_edges`. Materials uses material-slot index plus one. The other modes flood
face adjacency, stopping at seams or sharp edges where applicable. Components
are ordered by the lowest current face index; IDs start above any retained hidden
IDs. Hidden faces retain IDs and form component boundaries. Initialization
replaces visible region organization. Face Set identity persists through level
changes and sculpt detail while authored topology is unchanged; later topology
changes may rebuild/invalidate regions. There are no permanent cross-topology IDs.

Face Set-to-mask conversion is deferred. Blender's active-Face-Set mask expansion
is cursor-dependent and modal, with no deterministic ID-based EXEC operator or
public access to Multires masks. The adapter does not approximate those grids or
alter hidden state to simulate a bridge. Broad automasking and sculpt visibility
controls remain deferred.

## Native sculpt filters

`blender.sculpt.filter` executes Blender's native Sculpt Mesh Filter:

```json
{
  "object_name": "Surface",
  "type": "surface_smooth",
  "strength": 0.5,
  "iterations": 12,
  "axes": {"x": true, "y": true, "z": true},
  "orientation": "local"
}
```

`object_name`, `type`, and `strength` are required. Axes default to all enabled;
partial objects default omitted axes to true. At least one axis must be enabled.
`orientation` is `local` (default) or `world`, defining which displacement
components those axes constrain. View orientation is not exposed.

| Type | Strength | Iterations and native effect |
| --- | --- | --- |
| `smooth` | 0–1 | 1–100, default 1; neighbor averaging, potentially shrinking the form |
| `surface_smooth` | 0–1 | 1–100; surface smoothing with native shape-preservation/current-vertex factors fixed at 0.5 |
| `relax` | 0–1 | 1–100; tangential relaxation of vertex distribution with native boundary handling |
| `inflate` | -1–1 | Exactly 1; displacement along original surface normals in local units |
| `scale` | Greater than -1, at most 1 | Exactly 1; unmasked coordinates scale by `1 + strength` about the object origin |

Strength is native filter strength, not brush pressure or a radius. Refinement
iterations accumulate native passes. Inflate/scale use one pass to avoid ambiguous
repeated original-state displacement semantics. Zero strength can return
`changed: false`. Work is limited to 64 million sculpt points × iterations × four
conservative passes, in addition to the existing total geometry limits. Supported
filters work on ordinary meshes and internal Multires sculpt grids. They preserve
native masks and Face Sets and honor native protection and visibility.
Partially protected points receive Blender's reduced effect. Mask transitions
still affect how neighboring geometry is smoothed, so inspect the boundary.
Multires synchronization around hide boundaries can also move boundary points;
results match native Blender rather than imposing a separate boundary lock.
Fully protected interiors and their hidden state remain preserved.

Results contain `object_name`, `type`, `strength`, `iterations`, `axes`,
`orientation`, `multires_level`, `bounds_before_min/max`, `bounds_after_min/max`,
`changed`, and `mask`. Bounds/hash compare evaluated geometry at the sculpt level.
The same UI/tool/brush restoration and **nontransactional** mutation boundary as
strokes applies: a post-start failure reports `sculpt_failed` with
`mutation_possible: true`; reinspect and rerender before retrying.

Sphere, sharpen, random, detail enhancement, displacement erasure and Face Set
boundary relaxation are not exposed in this layer. Dyntopo and production
retopology remain separate workflows. Explicit voxel remeshing is documented below.

## Voxel-remesh blockout

Voxel remeshing is **destructive topology replacement for early organic blockout**:
rebuild stretched/uneven sculpt density, inspect the new mesh, then continue
sculpting. It is **not production retopology**, deformation-loop generation, or
animation-ready topology. Final organic assets still need deliberate production
retopology before final UVs, feather systems, and rigging. Small details, thin
parts, disconnected regions and gaps can disappear or merge at voxel resolution.

First call `blender.sculpt.voxel_remesh.inspect` with the intended settings:

```json
{
  "object_name": "Surface",
  "voxel_size": 0.05,
  "adaptivity": 0,
  "preserve_volume": true,
  "fix_poles": true,
  "preserve_attributes": true,
  "discard_uv_maps": false
}
```

Inspection is read-only. Only `object_name` is required; omitted voxel size uses
0.1, and the other defaults are shown above. It does not change the Mesh's saved
native remesh settings. Its result contains:

```text
{
  object_name, guidance,
  mesh: MeshSummary + {edge_length: Distribution, face_area: Distribution},
  settings: {voxel_size, adaptivity, preserve_volume, fix_poles,
             preserve_attributes, discard_uv_maps},
  effective_fix_poles,
  grid: {dimensions, cells, cell_limit, padding_per_axis,
         exceeds_limit, coordinate_limit_exceeded},
  shared_mesh,
  blockers: [{code, message}],
  destructive_effects: [{code, behavior, names, message}],
  preservable_data: [{code, behavior, names, message}]
}
```

`Distribution` contains `min`, `max`, `mean`, `variance`, and
`coefficient_of_variation` (standard deviation divided by mean). Empty samples
report zeros. These are authored local-space density measurements, not claims
about artistic quality. Effects distinguish `preserved`, `reprojected`,
`discarded`, and `rebuilt`; reprojection is approximate, not preserved element
correspondence. The returned `guidance` explains the blockout purpose, destruction
boundary, inspection/reinspection requirements and visual verification workflow.

`blender.sculpt.voxel_remesh` accepts the same properties but **requires an explicit
voxel_size**. It repeats all analysis/guards immediately before native execution;
a previous inspection never authorizes bypassing changed blockers. No broad data
loss override is provided. `preserve_attributes: false` requests the documented
attribute loss; UV maps still block unless `discard_uv_maps: true` explicitly
requests their removal. Weights, shape keys, Multires and the other protected
data continue to block execution.

Settings use Blender's stored float32 precision:

- `voxel_size`: approximately 0.0001 through 1000 Blender units, in object space.
  The exact lower bound is float32(0.0001). Unit local and inherited scale without
  shear is required; transforms are never applied automatically.
- `adaptivity`: 0–1, default 0. Positive values simplify low-detail regions and can
  introduce triangles/nonuniform density. They disable native pole fixing;
  `effective_fix_poles` reports this even when `fix_poles` is true.
- `preserve_volume`: default true; native offset/projection attempts to retain
  source form and detail. This is not an exact volume or silhouette guarantee.
- `fix_poles`: default true; native cleanup reduces poles when adaptivity is zero.
- `preserve_attributes`: default true; native reprojection described below.
- `discard_uv_maps`: default false. Set true only when deliberately discarding
  every UV map on the target, such as generated primitive UVs during blockout.
  Inspection lists the discarded map names; removal happens on the staged Mesh
  before remeshing. Other requested attributes are still reprojected. Shared
  siblings keep their UVs, and failure preserves the target's original maps.

The grid estimate uses authored bounds and the actual stored voxel size:
`n_axis = ceil(extent_axis / voxel_size) + 8`, allowing padding on both sides.
The product must not exceed **2,000,000 cells**, and no padded axis may exceed
**4096 cells**. Oversized axes are rejected before multiplication; their reported
dimensions/cells are null, not an overflowed or guessed number. Absolute local
coordinates divided by voxel size must not exceed **1,000,000**. This guards
native coordinate range/precision as well as density. The dense-box estimate is
conservative for Blender's sparse grid, not an exact memory or output-face count.

Input and staged output retain the existing **2,000,000 total geometry-element**
limit (vertices + edges + faces + corners). Remeshing additionally permits at most
**32 nonstructural attributes** and **8,000,000 stored attribute components** on
input/output. Post-operation checks cannot undo native allocation, so the grid
limit is enforced before Blender is called.

The initial blockers are:

| Code | Protected boundary |
| --- | --- |
| `object_mode_required`, `render_running` | Object Mode required; no active render |
| `linked_data` | Local editable scene, object and Mesh; no library overrides |
| `has_multires` | Any Multires modifier; no implicit apply/removal/detail loss |
| `has_shape_keys` | Shape-key data; execution retains `mesh_has_shape_keys` error semantics |
| `has_modifiers` | Any other modifier; resolve the stack explicitly first |
| `nonunit_scale` | Local/inherited scale or shear |
| `mesh_animation`, `deformation_relationship` | Animated Mesh/object, constraints, deformation parents/targets or vertex-parented children |
| `has_uv_maps` | Any UV map unless `discard_uv_maps: true`; no inference that generated maps are disposable |
| `has_vertex_groups` | Any group definition, including empty groups; no approximate weight transfer |
| `has_custom_normals` | Authored custom normals |
| `dyntopo`, `hidden_geometry` | Dyntopo or hidden authored geometry; no implicit reveal |
| `invalid_surface`, `non_manifold` | Empty/degenerate or open/nonmanifold surface, including loose geometry |
| `voxel_grid_limit`, `voxel_coordinate_limit` | Allocation or coordinate budget |
| `attribute_limit`, `unsupported_attribute`, `invalid_sculpt_regions` | Attribute capacity, unvalidated types, malformed masks/Face Sets |

Inspection outside Edit Mode reports blockers together. Edit Mode and input
inspection-capacity failures return errors instead of reporting stale mesh data.
Coordinates beyond any supported voxel range are rejected before area/normal
calculations. Otherwise blocked execution returns `voxel_remesh_blocked` with the
analysis report in error details. There is no native allocation on blocked calls.

Blender 5.2.1's actual data behavior is:

| Data | Preserve attributes enabled | Disabled |
| --- | --- | --- |
| Mesh material-slot references | Retained | Retained |
| Object-linked material slots, including empty overrides | Retained and checked | Retained and checked |
| Face material indices | Nearest-source-face sampling; regional layout approximate | All zero |
| Sculpt mask | Barycentric point sampling; coverage changes with resolution | Removed; fully editable |
| Face Sets | Nearest-source-face IDs; small sets can disappear | Removed; implicit default ID 1 |
| Point colors/numeric attributes | Barycentric surface sampling | Removed |
| Edge attributes, seam/sharp flags | Nearest-source-edge sampling; original edge paths are not preserved | Removed |
| Face attributes and smooth/flat shading | Nearest-source-face sampling | Attributes removed; all faces use the first source face's shading |
| Corner attributes/colors | Sampled to vertices, then expanded to corners; discontinuities lost | Removed |
| Computed normals, selection correspondence | Rebuilt/resampled on new topology | Rebuilt |
| UV maps | Blocked unless `discard_uv_maps: true`, then removed | Same explicit discard requirement |
| Groups/weights, custom normals | Blocked by Tyvrana | Blocked by Tyvrana |

The validated transferable types are FLOAT, INT, INT8, BOOLEAN, FLOAT_VECTOR,
FLOAT2, INT32_2D, FLOAT_COLOR and BYTE_COLOR on mesh domains. Other types are
blocked. Attribute names/types/domains and material references are checked before
publication. Native mask interpolation roundoff within 1e-6 of the valid range
is normalized to [0,1]; larger invalid values reject the staged result.

Native Blender can reproject UVs and weights, but UV corner discontinuities and
weight correspondence do not survive exactly. Tyvrana protects weights and
requires explicit UV discard; it does not claim to preserve UV seams or provide
custom attribute-transfer machinery. Native behavior is
verified against the [Blender 5.2.1 remesh operator](https://github.com/blender/blender/blob/v5.2.1/source/blender/editors/object/object_remesh.cc)
and [attribute reprojection implementation](https://github.com/blender/blender/blob/v5.2.1/source/blender/blenkernel/intern/mesh_remesh_voxel.cc),
not the operator's outdated blanket claim that all data layers are lost.

Execution runs native `object.voxel_remesh` on a temporary object and complete Mesh
copy, then checks geometry, data and resource limits before replacing the target's
Mesh. Shared siblings retain their original Mesh and settings. Failure before
publication preserves the original; staging objects and meshes are cleaned up.
Object identity, transforms, mode, selection, tool/view state and unrelated scene
resources are preserved. This Object Mode operation works in both background and
interactive Blender; it needs no fabricated sculpt viewport context.

The result contains `object_name`, `before`, `after`, `settings`,
`effective_fix_poles`, `grid`, `isolated_shared_mesh`, `indices_invalidated: true`,
`lost_or_rebuilt_data`, and `preserved_data`. **All old authored element indices
are invalid**, even if a new element happens to have the same number. Reinspect
and query after remeshing, raycast the new surface, continue controlled sculpting,
and rerender to verify form. High-detail Multires and destructive blockout remesh
remain separate branches; there is no automatic bake or detail-transfer bridge.
Dyntopo remains deferred. Separate-source retopology is described below.

## Surface-conforming retopology

Retopology reads an explicit **high-resolution source** and edits a separate
**low-poly authored target**. It does not reduce, duplicate, remesh or modify the
source. The target has independently authored topology; correspondence measures
spatial distance, never a source-to-target vertex map. Quad counts alone cannot
establish anatomical edge flow, pole placement or deformation quality. Those
require deliberate loop planning and visual/deformation review.

The construction workflow follows Blender's [Poly Build](https://docs.blender.org/manual/en/5.2/modeling/meshes/tools/poly_build.html),
[surface snapping](https://docs.blender.org/manual/en/5.2/editors/3dview/controls/snapping.html)
and [bridge loops](https://docs.blender.org/manual/en/5.2/modeling/meshes/editing/edge/bridge_edge_loops.html)
concepts, using native BMesh construction, edge extrusion, bridging and smoothing.
The finishing operations below add controlled loop density, sliding, pole-flow
redirection and boundary closure. No automatic quad-remeshing library is used.

### Source and target ownership

`blender.retopo.create_target` takes `source_object` and optional `name`
(default `Retopology`). It validates the evaluated source, creates an empty
independent Mesh object, and copies its current world alignment without parenting
or a persistent relationship. Native name collision suffixes are returned.
The result contains `source_object`, `target_object`, `target_mesh`, `matrix_world`,
empty authored `mesh: MeshSummary`, and workflow `guidance`. No geometry, UVs,
materials, sculpt data, modifiers or rigging are copied. Source world shear that
cannot be represented by an independent object transform is rejected.

Every other retopology operation requires both `source_object` and `target_object`.
They must be distinct Mesh objects in the current view layer. Operations require
Object Mode outside rendering and run on Blender's main thread.

The source is the **current viewport dependency-graph result**, including current
Subdivision, Multires detail, shape-key values and other bounded supported source
modifiers. Viewport detail can differ from render detail. Source UVs, weights,
materials, shape keys and sculpt attributes are read-only reference state, not
mutation blockers. Invalid/empty/degenerate evaluated surfaces, singular transforms,
unsupported/unbounded evaluation and a source that depends on the target are
rejected. Evaluated Mesh extraction always calls `to_mesh_clear()` in `finally`.
Each operation builds its own world-space BVH and releases it on success/failure;
there is no persistent surface cache or evaluated edit-index exposure.

Targets must be local editable data. Mutations reject linked/override data, shape
keys, object/Mesh animation, constraints and unsafe deformation/vertex-parenting
relationships, custom normals, hidden geometry, UV maps, vertex groups/weights and
unsupported attributes. Structural Mesh attributes, selection/hide flags, material
indices, sharp flags and seams are allowed. Material slots and native supported
selection/custom data are preserved. Inspection reports mutation `blockers` while
still providing useful geometry diagnostics when safe evaluation is possible.
Generic mesh-edit modifier guards remain unchanged.

Every target edit uses detached BMesh staging, validates the entire result, creates
and checks a replacement Mesh, and publishes it only on success. Shared target
Mesh users retain their original data; the edited object receives an isolated
replacement. Failure discards staged geometry and preserves the original target.
This is operation-local safety, not a persistent undo/history system.

### Inspection and correspondence

`blender.retopo.inspect` returns:

```text
{
  source_object, target_object, guidance,
  source: EvaluatedSurfaceSummary,
  target: RetopoQuality,
  target_modifiers: [ModifierSummary, ...],
  evaluated_target: EvaluatedSurfaceSummary,
  authored_correspondence: Correspondence,
  evaluated_correspondence: Correspondence,
  blockers: [code, ...]
}
```

`EvaluatedSurfaceSummary` contains object name, vertex/edge/face/triangle counts
and world bounds. Its triangle count describes evaluated tessellation, not the
number of triangular polygons. `RetopoQuality` contains authored `mesh: MeshSummary`
and:

- `quad_count` (four corners), `triangle_count` (three), `ngon_count` (more than four).
- Boundary edge, closed-loop, open-chain and branched-component counts;
  `non_manifold_edge_count` counts edges with **more than two** faces. Boundaries
  and loose edges are reported separately. `non_manifold_vertex_count` also
  detects disconnected face fans and invalid boundary incidence, including
  vertex-only pinches that edge counts cannot detect. The nested generic MeshSummary retains
  its broader native non-manifold classification. Inconsistent two-face winding,
  loose vertices/edges and degenerate faces are also counted.
- `valence`: edge-degree counts `valence_0` through `valence_5`, `valence_6_plus`
  and `max_valence`. Boundary valence naturally differs from interior quad flow.
- World-space `edge_length`, `face_area`, and `quad_aspect_ratio` distributions:
  min/max/mean, population variance and coefficient of variation. Empty samples
  yield zeros. Quad aspect is **longest edge squared / face area**, a measurement
  sensitive to both elongation and skew, not a deformation-quality score.
  `extreme_aspect_ratio_count` uses a documented threshold of **10**.
- `boundaries`: up to 64 components, each with snapshot-local `loop_index`,
  edge/vertex counts, `kind` (`closed`, `open`, `branched`), world perimeter,
  target-local bounds and bounded edge/vertex indices. Each index list includes
  `total`/`truncated`; at most 128 indices are returned. Component truncation is
  explicit. Branched boundaries are diagnostics, not valid construction selectors.

Each `Correspondence` contains separate `vertices` and `face_centers` summaries:
`sample_count`, `mean_distance`, `rms_distance`, `max_distance`, `p95_distance`.
Distances are unsigned world-space distances to the nearest evaluated source
triangle. P95 uses nearest rank. `face_normal_dot` is a distribution comparing
world geometric face normals with the nearest source triangle normal. Face-center
error can remain nonzero when all corners are projected exactly: a coarse flat
quad is a chord across a curved surface. Nearby wrong regions can have small
spatial distance; the metrics do not establish semantic correspondence.
Authored and evaluated target results are separate so live modifiers cannot hide
an off-surface authored cage.

### Construction, projection and relaxation

All five editing operations accept `surface_offset` in `[-1, 1]` (default zero)
and `max_projection_distance` in `(0, 1000]` (default one), in world scene units.
The distance cap applies to each input point's nearest-source search **before**
adding the offset. Offset is signed along the evaluated triangle's world geometric
normal; positive follows source winding. This differs from Shrinkwrap's native
smooth-normal/projection-line offset modes. It is not a collision guarantee.

| Operation | Additional arguments | Behavior |
| --- | --- | --- |
| `blender.retopo.seed_patch` | Required source-local `center`, source-local nonzero `tangent_direction`, world `width`/`height`; `u_segments`, `v_segments` default 1 | Snap center, project transformed tangent into its source tangent plane, derive orthogonal tangent, build and project an oriented quad grid |
| `blender.retopo.project` | Required vertex `selector`; `mode: "nearest_surface"` | Project selected authored vertices; unselected coordinates and element ordering remain unchanged |
| `blender.retopo.relax` | Vertex `selector`, nearest-surface mode; `iterations: 1..50` default 5, `factor: (0,1]` default 0.5, `preserve_boundary: true` | Native neighbor smoothing followed by reprojection after every iteration |
| `blender.retopo.extrude_boundary` | Edge `selector`; nonzero target-local `offset`; optional XYZ Euler `rotation` and positive `scale` | Extrude one boundary chain/loop, shape its new section, then project new vertices |
| `blender.retopo.bridge_loops` | Edge selectors `loop_a`, `loop_b`; `twist` default 0, `segments: 1..16` default 1 | Bridge two disjoint equal-count closed boundary loops, optionally subdivide connecting edges into bands and project new vertices |

Selectors use the existing explicit authored mesh selector union. Seed width and
height are approximately `0.0001..1000`; each segment count is `1..16`. A parallel
or degenerate tangent fails explicitly. Returned `patch` contains the snapped
source/world center, world normal and actual world tangent basis. The orientation
is never guessed from a hidden axis.

Boundary extrusion applies component scale, then XYZ Euler rotation in radians,
about the selected boundary's vertex centroid in target-local coordinates, followed
by translation and source projection. Defaults are unit scale and zero rotation;
`offset` remains required, nonzero and at most 1000 units long. Scale components
must be positive and at most 1000. This supports curved or tapering strip/loop
growth without first projecting an unshaped section onto an unintended surface.
Original vertices remain unchanged. An existing Mirror seam must remain in its
origin plane; incompatible shaping is rejected. Projection, coordinate budgets,
source winding, fold and topology validation still run before publishing the edit.
These controls guide the starting section; nearest-surface projection does not
promise an exact final rotation, width or anatomical correspondence.

Boundary preservation in relaxation locks vertices on the **actual Mesh boundary**,
not every edge of the selected region. Unselected vertices remain fixed. Turning
it off permits selected boundary vertices to move. Projection and relaxation
preserve topology and index ordering. Seed/extrude/bridge change topology and
invalidate snapshot references: inspect/query again after each successful edit.

Bridging uses native surrounding-face winding and nearest total connector-length
rotation in target-local coordinates. Equally close rotations are rejected as
ambiguous. `twist` is native integer loop offset, bounded to `[-127, 127]` and
strictly smaller in absolute value than the loop count; it does not bypass
validation. Shared or spatially intersecting loops, open/branched chains,
internal edges and unequal counts are rejected. Construction checks distinct
quad faces, manifoldness, winding, positive area, local convex corners, triangle
folds of at least 90 degrees across either quad diagonal, and agreement with
source normals. These are conservative local checks, **not** a general global
self-intersection solver or an automatic loop-flow planner.

Edit results contain names, `topology_changed`, `indices_invalidated`,
`mesh_isolated`, `selected_count`, `moved_count` for existing vertices moving more
than `1e-7` in target-local distance,
`created: {vertices, edges, faces}`, bounded `created_faces`, pre-offset
`projection` distance statistics, `before`/`after` RetopoQuality,
`correspondence_before`/`correspondence_after`, and `patch` (null except seed).
Relaxation projection statistics aggregate all moved samples over all iterations;
final correspondence measures the published cage. Original vertices stay fixed
for extrusion and bridging. A one-band bridge creates faces without new vertices,
so its projection sample count is zero; inspect its face-center error and add bands
when curvature requires them.

### Target modifier policy and budgets

Supported target stacks are empty, one Mirror, one Shrinkwrap, or **Mirror followed
by Shrinkwrap**. They remain object-owned, unapplied, and retain their settings.

- Mirror: exactly one local axis, target-origin plane, no external mirror object,
  no bisect, no Edit Mode clipping, merge threshold `0..0.1`. Authored geometry must
  remain on one side of the plane; an entire coplanar patch is rejected. Native
  merging is allowed. Clipping is an Edit Mode transform constraint and is not
  silently emulated by these staged Object Mode edits.
- During explicit projection, vertices within `1e-6` local units of the Mirror
  plane are fitted to the actual evaluated source/plane intersection and remain
  exactly on that plane. This also applies to newly extruded seam vertices and
  to boundary relaxation when `preserve_boundary` is false. Normal offsets remain
  in the plane. The intersection is rebuilt per operation; up to two million
  section-candidate comparisons are allowed. A missing or too-distant
  intersection fails without publishing staged geometry.
- Shrinkwrap: exact explicit source target, `NEAREST_SURFACEPOINT`, `ON_SURFACE` or
  `ABOVE_SURFACE`, no vertex group/auxiliary target/internal subdivision, offset
  `[-1,1]`. Explicit retopo projection changes authored positions and leaves this
  helper intact. Evaluated inspection shows the helper's actual result.

The existing **2,000,000 total geometry-element** evaluated work budget remains
in force, including conservative modifier growth estimates. View-layer/dependency
traversal is limited to 256 objects. Authored targets are limited to 100,000 total
vertices + edges + faces + loops, 32 attributes, and a conservative eight-million
byte attribute estimate (16 bytes per attribute-domain element). Vertex selections
are limited to 4096, boundary selections to 128 edges per loop, and relaxation to
100,000 selected-vertex iterations. Local/world coordinates must be finite and
within one million. Construction has a conservative preallocation estimate and
checks actual staged results before publication. Unsupported/unbounded dependency
stacks, including Geometry Nodes, are rejected instead of evaluated speculatively.

A minimal workflow (replace names/geometry with inspected scene values):

```json
{"operation":"blender.retopo.create_target","arguments":{"source_object":"Sculpt","name":"Cage"}}
{"operation":"blender.retopo.seed_patch","arguments":{"source_object":"Sculpt","target_object":"Cage","center":[0,0,1],"tangent_direction":[1,0,0],"width":0.6,"height":0.6,"u_segments":3,"v_segments":3}}
{"operation":"blender.retopo.inspect","arguments":{"source_object":"Sculpt","target_object":"Cage"}}
{"operation":"blender.retopo.relax","arguments":{"source_object":"Sculpt","target_object":"Cage","selector":{"mode":"all","domain":"vertex"},"preserve_boundary":true}}
```

Render before and after, examine the actual cage, query current boundaries, and
extend deliberately. Final deformation topology still needs anatomical loop-flow
and pole review before UVs, detail transfer and rigging.

### Loop flow and topology finishing

Finishing continues the same explicit read-only source/authored target model.
The tools follow Blender's [loop cutting](https://docs.blender.org/manual/en/5.2/modeling/meshes/editing/edge/loopcut_slide.html),
[vertex sliding](https://docs.blender.org/manual/en/5.2/modeling/meshes/editing/vertex/slide_vertices.html),
[collapse](https://docs.blender.org/manual/en/5.2/modeling/meshes/editing/mesh/delete.html)
and [Grid Fill](https://docs.blender.org/manual/en/5.2/modeling/meshes/editing/face/grid_fill.html)
workflows using native BMesh data operations. There are no viewport mouse gestures,
implicit anatomy templates, automatic density transitions or external remeshers.

Every operation requires `source_object` and `target_object`, uses the existing
world-space `surface_offset`/`max_projection_distance` contract, and stages changes
before publication. Projection applies only to newly created or deliberately
moved vertices; unrelated coordinates remain fixed. No finishing operation
implicitly smooths the cage. Use `retopo.relax` deliberately afterward.

| Operation | Additional arguments | Semantics |
| --- | --- | --- |
| `blender.retopo.insert_loop` | `edge`: exactly one selected edge; `factor` in `(0,1)`, default `0.5`; optional `from_vertex` | Traverse opposite edges through a complete quad ring and insert one loop. Non-midpoint factors require an explicit endpoint identifying factor zero. |
| `blender.retopo.slide` | `selector`: one vertex or a complete edge loop/chain; `toward_vertex`; required `factor` in `[0,1)` | Interpolate from current positions toward an explicitly identified topological neighbor/rail, then project. Zero is an exact coordinate no-op. |
| `blender.retopo.subdivide` | Edge `selector`; `cuts: 1..4`, default `1` | Native local subdivision; each touched quad must have all four or two opposite edges selected. New vertices project to the source. |
| `blender.retopo.collapse` | `selector`: exactly one edge; `mode: "edge"` (default) or `"ring"`; `allow_boundary: false` | Collapse one edge, or expand its complete transverse quad ring and collapse each disjoint edge at its midpoint. Project surviving merged vertices. |
| `blender.retopo.rotate_edge` | `edge`: exactly one internal edge; `direction: "clockwise"` (default) or `"counterclockwise"` | Native topological rotation between two triangles or two quads. Coordinates stay fixed; the replacement diagonal changes local valence. |
| `blender.retopo.stitch` | `chain_a`, `chain_b`: open boundary edge selectors; explicit endpoint indices `start_a`, `start_b`; world `max_weld_distance` in `(0,1]`, default `0.01` | Pair vertices along the explicit chain directions, weld to pair midpoints, and project the retained vertices. |
| `blender.retopo.fill_boundary` | Closed `boundary` edge selector; `corner_vertex`; `span: 1..63`; `mode: "grid"` | Fill an even boundary with a native rectangular quad grid, then project new interior vertices. |

**Ring insertion and subdivision.** Rings may be closed or cleanly terminate at
two boundaries. Traversal follows opposite edges of quad faces, never a guessed
continuation through a vertex pole. Triangles, ngons, shared ring endpoints,
branching, inconsistent orientation and invalid revisits are rejected. The
`from_vertex` endpoint defines factor zero; the opposite endpoint defines one.
This orientation propagates across the face ring. At the default midpoint no
orientation reference is needed. Multiple cuts remain in `subdivide`, while
`insert_loop` creates exactly one identifiable flow loop. Subdivision rejects
partial face patterns that would introduce arbitrary triangles or ngons. Callers
must select a coherent pattern; surrounding unselected faces are not automatically
expanded into a transition.

**Sliding.** For a single vertex, `toward_vertex` must be its connected neighbor.
For a loop, it must be one unselected neighboring vertex identifying exactly one
side; corresponding rails propagate through adjacent quads. Select a complete
closed loop or boundary-to-boundary chain. Interior vertices require valence four,
with clean valence-three boundary endpoints. Incomplete selections, poles,
ambiguous anchors and reversing rail orientation fail. Factor one is excluded to
prevent collapsing onto the adjacent rail. To move in the other direction, supply
a neighbor on that side. Direction is independent of incidental edge-index order.

**Reduction and poles.** Ring collapse preserves a quad neighborhood. Single-edge
collapse may deliberately turn adjacent quads into triangles; it reports that
change rather than promising quads. Any selected endpoint on an actual boundary
requires `allow_boundary: true`. Collapse and stitching at an existing Mirror
center seam are conservatively rejected even with that permission. Native rotation
supports quad pole redirection as well as triangle diagonal changes. Clockwise and
counterclockwise follow native face winding. Mixed triangle/quad pairs, invalid
combined neighborhoods, existing incompatible diagonals, foldovers and unsafe
results fail. Rotation can retain straight corners in nonzero-area quads; these
are useful intermediate topology but generally need deliberate relaxation.
It does not automatically move vertices to rescue an invalid rotation.

**Stitching and filling.** Stitch means welding matching seam boundaries; it does
not create a bridge strip. Chains must be disjoint, open, nonbranching and equal
in vertex count, with compatible opposite face winding. Every explicit pair must
satisfy the world-space weld cap. Reversing both start endpoints gives the same
pairing; reversing only one generally describes a different, invalid seam.
Existing closed-loop bridging remains the operation for filling a strip between
separated closed loops.

Grid Fill requires an entire unbranched closed boundary component. The explicit
corner starts the layout following surrounding face winding; `span` defines the
first side's edge count. For `N` boundary edges, the other dimension is
`N/2 - span`, which must be positive. Opposite sides therefore have matching
counts; the wrapper supplies those two sides to native `bmesh.ops.grid_fill`.
Open, odd, intersecting or unsuitable boundaries fail. New faces must have valid
winding, positive area, no foldover, and aspect at most 10. A filled grid is not
a certificate of good production flow. Existing boundary positions remain fixed;
inspect face-center error and deliberately relax when needed.

**Inspection and results.** `RetopoQuality` additionally contains `poles`,
`poles_total`, and `poles_truncated`. Up to 128 entries report `vertex_index`,
`valence`, `boundary`, target-local `position`, and `incident_face_count`.
Interior valence-four and boundary valence-three vertices are omitted. Interior
entries precede boundary entries; ordinary valence-two corners remain explicitly
marked as boundary structure, not automatically classified as defects. Valence
three/five may be deliberate flow transitions, and high valence is contextual.
Existing spacing, area, aspect, boundary and source-distance metrics remain
separate review dimensions.

Edit results retain before/after quality and correspondence and additionally
return nonnegative `created`/`removed` element counts, bounded `created_vertices`,
`created_edges`, and `flow_edges`. The last identifies the inserted loop or rotated
diagonal. `input_edges_before` records the finishing operation's selected/expanded
input edges when relevant. These are explicitly indices from the pre-edit
snapshot; created/flow indices refer to the result snapshot. Slide preserves
geometry ordering. Every other finishing operation invalidates previous indices,
including rotations that leave counts unchanged.

**Safety and work.** The existing source/evaluation, valuable-data and shared-Mesh
isolation policies apply unchanged. Selection/material/seam data use native
preservation/interpolation; final UVs, weights, shape keys, animation, custom
normals and unsafe deformation relationships remain protected. All operations
support the vetted Mirror, Shrinkwrap and Mirror → Shrinkwrap stacks without
applying helpers. Authored projection remains explicit; evaluated Shrinkwrap
cannot substitute for authored correspondence inspection. Existing Mirror seam
vertices may not move off their plane.

Finishing selections/rings/closed fill boundaries are limited to 128 edges;
collapse rings to 64. Subdivision and grid fill have conservative allocation
estimates before native construction. The existing 100,000-element target and
2,000,000-element evaluated limits remain in force. Results reject duplicate
faces/edges, disconnected vertex face fans, loose or zero-length geometry,
invalid edge incidence, inconsistent
winding and unsupported faces. Evaluated Mesh and per-operation BVH cleanup
remain scoped to the shared transaction. Local validity checks do not constitute
a general global self-intersection solver.

A professional sequence is: inspect → add/remove local density → redirect flow
→ close boundaries → relax/project → inspect quality/correspondence → render and
verify. Generic dissolve, arbitrary ngon filling, screen-space sliding, automatic
pole creation and automatic density-transition generation are deliberately absent.

## Project persistence

`blender.file.inspect` takes no arguments and reports `filepath`, `is_saved`,
`is_dirty`, `exists`, and `byte_size`. The dirty flag is Blender's native signal;
it is not a complete audit of scripted RNA changes. Save deliberate milestones
even when that flag is false.

`blender.file.open` loads an existing local regular `.blend` file in idle Object
Mode. Supply an absolute `filepath` and `discard_current: true` explicitly: opening
replaces the current project, including unsaved changes. Save valuable work first;
native dirty state is not a complete audit of scripted edits. `load_ui` defaults
to false, preserving the current workspace layout; true loads the saved layout.
Symlinks and missing/nonregular sources are rejected. Embedded Python execution
is always disabled; there is no script-execution option.

The requesting connection survives native load callbacks. Project registration
refreshes after the response and pending transfers drain, using the same instance
ID. Ordinary external file loads retain the extension's normal restart behavior.
Native open failures report `file_open_failed` and `possible_partial_load: true`;
inspect the resulting scene before continuing. No rollback of a loaded project
is promised. Native semantics follow [Blender's file-open operator](https://docs.blender.org/api/5.2/bpy.ops.wm.html#bpy.ops.wm.open_mainfile).

`blender.file.save` uses native `bpy.ops.wm.save_as_mainfile` in Object Mode.
Supply an absolute host-local `filepath` ending in `.blend` for the first save or
Save As. Omit it to save the current file. The destination parent must exist;
relative paths, final-component symlinks, and non-regular destinations fail.
Every existing destination, including the current file, requires
`overwrite: true`. Normal Blender backup preferences remain in effect.

The operation saves the current project, changes its active filepath, and reports
the resulting file state. It remaps relative external references using native
semantics; it does not pack or separately save external images/resources. A native
write failure reports `file_save_failed` with `possible_partial_write: true`;
filesystem writes are not advertised as an atomic transaction. Reinspect the file
and destination after such a failure.

Project paths describe local application state and save destinations, not binary
artifact transport. Render/image artifacts continue through the existing typed
binary channel. Save As refreshes registration after pending operations and input
transfers finish, preserving the save response and adapter instance identity.
A brief reconnect updates the advertised project path. No project loader or
arbitrary execution operation is exposed.

## Cameras

Camera objects hold transforms and reference camera data containing optical and
projection settings. Use `blender.object.set_transform` for a camera's location,
XYZ Euler rotation in radians, and scale. `blender.camera.configure` changes
camera data only; transform arguments are rejected. All camera operations run
through the same main-thread queue as object operations.

### Inspection

`blender.camera.inspect` takes `{}` and returns:

```json
{
  "active_camera": "Camera",
  "cameras": [{
    "name": "Camera",
    "active": true,
    "projection": "perspective",
    "location": [0, -8, 4.5],
    "rotation": [1.1583858728408813, 0, 0],
    "scale": [1, 1, 1],
    "lens_mm": 50,
    "ortho_scale": null,
    "clip_start": 0.10000000149011612,
    "clip_end": 1000,
    "shift_x": 0,
    "shift_y": 0,
    "sensor_width_mm": 36,
    "sensor_height_mm": 24,
    "sensor_fit": "auto"
  }]
}
```

Only objects in the current scene are listed, sorted by name. An empty scene
returns `{"active_camera": null, "cameras": []}`. Transforms describe authored
local object state, consistently with scene inspection; they are not evaluated
world matrices. Existing parenting, constraints, animation, sensor settings, and
render settings continue to affect Blender's evaluated rendering.

Inspection maps Blender's `PERSP`, `ORTHO`, `PANO`, and `CUSTOM` projections to
`perspective`, `orthographic`, `panoramic`, and `custom`. `lens_mm` is non-null
only for perspective; `ortho_scale` is non-null only for orthographic. Sensor
dimensions and `sensor_fit` (`auto`, `horizontal`, or `vertical`) are inspected
but are not writable through this initial API. Panoramic subtype, custom camera
shaders, depth of field, and other advanced camera data are not exposed.

### Creation and configuration

`blender.camera.create` accepts these optional fields:

| Field | Default / meaning |
| --- | --- |
| `name` | Blender's `Camera` name; duplicate names are resolved by Blender |
| `projection` | `"perspective"`; also supports `"orthographic"` |
| `location`, `rotation`, `scale` | `[0, 0, 0]`, `[0, 0, 0]`, `[1, 1, 1]` |
| `lens_mm` | 50; specify only with perspective projection |
| `ortho_scale` | 6; specify only with orthographic projection |
| `clip_start`, `clip_end` | 0.1 and 1000, in scene units |
| `shift_x`, `shift_y` | 0 and 0, Blender's dimensionless lens shifts |
| `make_active` | `false`; preserve an existing active camera. The first camera becomes active automatically when none exists. `true` explicitly replaces the active camera. |

Creation makes a new object and camera datablock in the current collection,
requires editable Object Mode, and preserves object selection. It returns the
actual resulting `CameraSummary`, including Blender's resolved name.

```json
{
  "name": "Overview",
  "projection": "orthographic",
  "ortho_scale": 8,
  "location": [0, -8, 4.5],
  "rotation": [1.1583858728408813, 0, 0],
  "make_active": true
}
```

`blender.camera.configure` requires `name` and accepts only these optional fields:
`projection`, `lens_mm`, `ortho_scale`, `clip_start`, `clip_end`, `shift_x`,
`shift_y`. Omitted properties retain their stored values. A name-only request
validates and returns the current supported camera state. Explicit nulls,
unexpected fields, and numeric strings/booleans are rejected.

```json
{"name": "Overview", "ortho_scale": 10, "clip_start": 0.25, "clip_end": 500}
```

The resulting projection determines which optical field is valid. To switch
projection, include the new projection and optionally its applicable optics:

```json
{"name": "Overview", "projection": "perspective", "lens_mm": 65}
```

Creation/configuration accept perspective and orthographic only. Existing
panoramic/custom cameras remain inspectable and selectable; configuring one
requires explicitly switching it to a supported projection. This does not
configure panoramic or custom-shader parameters.

### Validation and mutation

Camera inputs use Blender's finite float32 range, approximately
`[-3.4028234663852886e38, 3.4028234663852886e38]`, with these additional rules:

- `lens_mm` is at least 1 mm.
- `ortho_scale` is strictly positive. Zero is rejected as a degenerate view even
  though Blender permits storing it.
- Clipping distances are at least Blender's float32 `1e-6` minimum
  (`9.999999974752427e-7`), and `clip_end` must exceed `clip_start`.
- Shifts use the full finite float32 range; the UI's ±2 soft range is not a limit.
- Non-finite values, overflow, and values that underflow to zero are rejected.
  Coherence is checked at Blender's stored precision, including clipping planes
  that would round to the same value. Returned values reflect that precision.

Partial updates are merged with existing data and validated before mutation.
An invalid combined state returns `invalid_arguments` without changing the
camera. Unexpected failures restore changed data; failed creation removes its
new object/datablock. If multiple objects share camera data, configuration makes
the target's data independent first. It does not change other cameras' optics.
Linked/read-only camera data is rejected. Camera mutations are rejected while a
render job is active. Unsupported existing projections return
`unsupported_projection`; a non-camera target returns `object_not_camera` and
a missing target returns `object_not_found`.

### Active camera and rendering

`blender.camera.set_active` takes `{"name": "Overview"}` and returns that
camera's summary with `active: true`. The target must be a camera in the current
scene. It may be linked because selecting it does not mutate its camera data.
Selection changes `Scene.camera`, independently of viewport object selection.

`blender.render.image` uses this explicit active scene camera. It has no camera
selection argument. Deleting the active camera with `blender.object.delete`
clears active-camera state; other cameras are not selected automatically, and
rendering returns `no_camera` until a camera is selected or created.

Camera semantics and limits follow the official
[Blender 5.2 Camera API](https://docs.blender.org/api/5.2/bpy.types.Camera.html)
and [Scene.camera](https://docs.blender.org/api/5.2/bpy.types.Scene.html#bpy.types.Scene.camera),
with native Blender checks for stored precision and render behavior.

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

Set `show_result: true` to leave the completed Render Result visible and fitted
in the active window's largest 3D View or Image Editor. This deliberately changes
that editor to an Image Editor; scene geometry and render settings are preserved.
The default false leaves editors unchanged. The option requires a visible window
and a suitable editor, and rejects background mode before rendering. It is useful
for visible inspection checkpoints alongside the returned MCP image.

An optional `wireframe` object displays the actual evaluated edges of named Mesh
objects as cyan native wire geometry in this render:

```json
{"width": 768, "height": 576, "wireframe": {"objects": ["Cage"], "thickness": 0.001, "surface_offset": 0.002}}
```

One to sixteen distinct objects are allowed, with at most 8192 evaluated edges
combined. Object Mode and existing bounded modifier/dependency evaluation guards
apply. Thickness is 0.000001 to one world unit; the optional normal offset is in [-1, 1]
world units and defaults to zero. A display offset can expose coarse cage chords
otherwise obscured by a source surface; it does not change authored correspondence.
This is a normally occluded native render, not an X-ray overlay or a source-fit
measurement. Evaluated world-space copies, display material and modifiers exist
only during rendering. Original geometry, materials, modifiers and render visibility
are restored/preserved, including failure paths. No persistent cage helper is added.

An optional `cycles` object selects Cycles for this request without changing the
saved scene configuration:

```json
{"width": 768, "height": 768, "cycles": {"device": "cpu", "samples": 16, "denoise": false}}
```

Its fields default to CPU, 16 samples and no denoising. `device` accepts `cpu` or
`gpu`, `samples` is an integer from 1 through 512, and `denoise` is a boolean.
GPU rendering uses the user's existing device configuration; it does not install
or configure GPU backends. Denoising uses the existing native denoiser settings.
Omit `cycles` to preserve the current engine and sampling choices; explicit null
and unknown options are rejected. Per-layer sample overrides and sample subsets
are temporarily disabled when these options are supplied, so the requested
maximum sample count applies. Engine and all overridden settings are restored
on success or failure. Other scene settings remain effective. These controls
bound requested samples, not scene complexity or elapsed render time.

The native settings follow Blender 5.2.1's
[Cycles properties](https://github.com/blender/blender/blob/v5.2.1/intern/cycles/blender/addon/properties.py).

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

For operation inputs the worker reserves storage before `artifact.ready`, receives
the same canonical binary chunks used by render output, checks sequential offsets,
exact byte size and SHA-256, then replies `artifact.accepted`. Only after all
attachments are complete and their descriptors match does it forward the
`OperationRequest` to Blender. Binary input bytes never pass through the main-thread
timer queue. Main-thread file lookup uses only validated opaque IDs and private
request correlation; no path is added to the application protocol.

Input storage permits 128 MiB per artifact, 512 MiB total reserved/completed bytes,
128 entries, four active transfers, and eight attachments per request. Separate
requests can use the same core artifact concurrently, with independent files.
Transfers and accepted files waiting for an operation expire after 30 seconds
without request input activity. Request completion/failure, cancellation, abort,
disconnect, worker shutdown, extension disable and Blender exit clean the input
files. Core's original import remains reusable until explicitly released or core
shuts down. The adapter's separate 64 MiB render spool bound still applies.

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

Input tests cover worker-side quota/integrity checks, admission ordering, reuse,
concurrency, cancellation at each stage, and complete cleanup. Native image tests
decode and pack PNG/JPEG, reject malformed content, and retain pixels after buffer
reload and saving/reopening a packed image with all transfer files removed. Native
UV tests exercise map roles, all six methods, packing, pins, shared mesh isolation,
linked-data rejection, context/selection restoration and rollback. Background and
real UI-timer tests import an external checker through MCP, observe actual binary
frames, build the material and UV workflow, delete the original source and core
copy, and verify the packed texture in a real render.

Mesh tests cover semantic selectors, bounded queries, regional transforms, region
extrusion/inset, bevel, subdivision, deletion, merge, seams and normal repair.
Native checks exercise shared-data isolation, shape-key and linked-data guards,
material/UV/weight preservation, staged failure cleanup and context preservation.
Seam tests verify that angle-based and conformal unwrapping split adjacent faces
at authored cuts. Background and UI MCP tests compare real extrusion, bevel,
inset/extrusion and asymmetric regional-transform renders while verifying
unchanged object transforms, object count, cameras, lights and material resources.

Modifier tests cover ordered supported/unsupported inspection, all five configured
types, dependency cycles, native solver/method behavior, partial-write rollback,
shared meshes, shape keys, linked data, UI context and failure cleanup. Repeated
evaluated extraction checks include success, budget rejection and summary failure.
Real MCP renders demonstrate mirrored form completion, smoother subdivision,
shell thickness and Boolean cuts while preserving authored data and scene setup.
Separate native and full-stack apply tests verify destructive baking, preserved
remaining modifiers, sibling isolation and authored/evaluated consistency.

Camera checks cover projection normalization, data/transform separation,
float32 limits, coherent partial updates, failure rollback, shared and linked
camera data, active selection, and camera deletion. Real background and UI MCP
tests compare decoded PNG subject bounds at different focal lengths, switch
active viewpoints, and verify orthographic configuration changes the render.

Sculpt tests exercise all six native brushes in real UI mode, scene-unit radius,
pressure/inversion, per-stroke symmetry, mask preservation, user brush/tool/mode
restoration, failure reporting and Multires detail persistence across levels.
Raycast tests cover camera projection, shifts, aspect ratio, evaluated geometry,
clipping, world/local normals and misses. MCP tests render, pick a visible surface,
perform a native Multires stroke and rerender while checking unchanged authored
geometry, object transforms, cameras, lights and materials.

Regional sculpt tests cover real mask add/subtract, pressure/radius/symmetry,
invert/clear, partial and full protection, hidden geometry, Multires mask
persistence, and state restoration. Face Set checks cover native default IDs,
semantic assignment, deterministic initialization, shared Mesh isolation and
persistence through Multires changes. Filter tests measure geometry rather than
relying only on operator completion: noise reduction, tangential redistribution,
normal inflation, scaling, axis/orientation constraints, and preservation of a
masked region. Full MCP tests exercise the regional operations and compare renders
while preserving authored geometry and unrelated scene state.

Voxel-remesh tests cover native background/UI execution, strict allocation guards,
attribute reprojection/loss, protected production data, shared-mesh isolation,
staged failure cleanup and remesh-to-sculpt integration. Render tests compare
density distributions and bounds as well as actual image changes.

Retopology tests exercise separate empty target creation, explicit tangent grids,
selection-limited projection, boundary-preserving relaxation, native extrusion and
subdivided loop bridges. Native checks cover source production data/current
Multires, target data guards, shared Mesh isolation, staged failures, evaluated
resource/BVH cleanup, quality diagnostics and live Mirror/Shrinkwrap behavior.
MCP render tests inspect visible quad cages and bridge growth on curved sources.
