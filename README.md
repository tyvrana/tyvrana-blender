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
| `blender.material.inspect` | `{}` | Sorted material resources and assignments |
| `blender.material.create_principled` | Optional name and Principled fields | Created material summary |
| `blender.material.configure_principled` | Required name; partial Principled fields | Updated material summary |
| `blender.material.assign` | Object/material names; optional slot index | Assigned slot and ordered slots |
| `blender.light.inspect` | `{}` | Sorted light summaries |
| `blender.light.create` | Light type, optional name/transform and applicable settings | Created light summary |
| `blender.light.configure` | Name and partial applicable light settings | Updated light summary |
| `blender.camera.inspect` | `{}` | Active-camera name and sorted camera summaries |
| `blender.camera.create` | Optional name, projection, initial transform, optics, clipping, shifts, activation | Created camera summary |
| `blender.camera.configure` | Required `name`; optional projection, optics, clipping, shifts | Updated camera summary |
| `blender.camera.set_active` | Required `name` | Selected camera summary |
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
transparency across render engines and modes. Texture resources, arbitrary shader
graph editing, UVs, normal/bump/displacement nodes, baking, and face-material-index
editing are separate future capabilities. Material tests verify actual renders
for color, roughness highlights, metallic, emission, and slot reassignment.

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

Camera checks cover projection normalization, data/transform separation,
float32 limits, coherent partial updates, failure rollback, shared and linked
camera data, active selection, and camera deletion. Real background and UI MCP
tests compare decoded PNG subject bounds at different focal lengths, switch
active viewpoints, and verify orthographic configuration changes the render.
