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
packed images, shader graphs, whole-mesh UV controls, and PNG scene renders
delivered as MCP images.

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
| `blender.image.inspect` | `{}` | Sorted image resources |
| `blender.image.create_generated` | Width/height; optional name, generation and color settings | Created image summary |
| `blender.image.create_from_artifact` | Attached artifact ID; optional name, color space and alpha mode | Packed image summary |
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
| `blender.render.image` | Optional `width`, `height`, `format` | Render metadata and a typed PNG artifact |
| `blender.uv.inspect` | Object name | UV map summaries and active roles |
| `blender.uv.create_map` | Object name; optional map name and activation flags | Updated UV inspection |
| `blender.uv.set_active` | Object/map names; editing and render activation flags | Updated UV inspection |
| `blender.uv.unwrap` | Object name, method; optional map and applicable settings | Updated UV inspection |
| `blender.uv.pack_islands` | Object name; optional map, margin, rotation and scaling | Updated UV inspection |

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
| `smart_project` | `angle_limit` default about 1.15191734 radians, `island_margin` default 0, `area_weight` default 0, `scale_to_bounds` default false |
| `cube_project` | Object-local axes around the mesh bounds center; optional `cube_size`, otherwise fitted to mesh bounds; `scale_to_bounds` default false |
| `cylinder_project`, `sphere_project` | Fixed object-local Z pole/X orientation around the mesh bounds center; native pinched poles and existing seam-separated islands; `scale_to_bounds` default false |

All six methods run native production UV operators. Sphere/cylinder direction is
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

`blender.uv.pack_islands` requires `object_name`; optional `uv_map` chooses the map
without changing its active roles. `margin` defaults to 0.001 (`[0, 1]`), `rotate`
and `scale` default to true. It packs all islands to the origin unit tile using
fractional margins, native concave island shapes, and unrestricted rotation when
enabled. Overlapping islands remain separate. Pin flags remain set but packing
moves all islands, including pinned ones. Turning scaling off can prevent a large
layout from fitting the tile; inspect returned bounds.

Mutations require a visible, selectable, editable local mesh in the current view
layer, with no active render job. Linked data and library overrides are rejected.
If more than one object
uses the mesh, the target receives its own mesh copy before mutation; siblings
retain their data and UVs. Failures restore UV coordinates/map roles and discard a
failed shared-data copy. Empty meshes can have maps but cannot unwrap or pack.

Operations support Object Mode or single-object Edit Mode on the target. Other
modes and another object's or multi-object Edit Mode return `invalid_context`.
They restore mode, active object, object selection, mesh/UV selection, hidden
flags, and selection history. Operators run synchronously with no UV-editor or
viewport context. Errors include `object_not_mesh`, `uv_map_not_found`, and
`uv_unwrap_failed`, alongside normal argument/context errors.

This is a whole-mesh UV layer. Deliberate seam authoring requires a future mesh
selection/topology model; no fragile edge-index seam API is provided. Behavior is
checked against Blender's [UV operator source](https://github.com/blender/blender/blob/v5.2.1/source/blender/editors/uvedit/uvedit_unwrap_ops.cc)
and [BMesh selection API](https://docs.blender.org/api/5.2/bmesh.types.html).

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

Camera checks cover projection normalization, data/transform separation,
float32 limits, coherent partial updates, failure rollback, shared and linked
camera data, active selection, and camera deletion. Real background and UI MCP
tests compare decoded PNG subject bounds at different focal lengths, switch
active viewpoints, and verify orthographic configuration changes the render.
