# Semantic materials and bounded shader graphs

Use `blender.material.author` for common Principled surfaces and
`blender.shader.author` for explicit procedural graphs. Both return the same
compact material summary as `blender.material.inspect`. Existing constant
Principled and individual node/link operations remain available.

## Semantic authoring

Create a textured material and assign it to two existing object slots:

```json
{
  "name": "PaintedSurface",
  "parameters": {"roughness": 0.4, "coat_weight": 0.3},
  "coordinates": {"source": "uv", "uv_map": "UVMap", "scale": [2, 2, 2]},
  "textures": [
    {"channel": "base_color", "image": "Albedo"},
    {"channel": "roughness", "image": "Roughness"},
    {"channel": "normal", "image": "Normal"}
  ],
  "assignments": [{"object_name": "Panel"}, {"object_name": "Frame", "slot_index": 1}]
}
```

Images must already exist, for example through `image.create_from_artifact` or
`image.create_generated`. The operation does not transport image bytes in JSON.
Slot indices are zero-based and cannot leave gaps. Other slots and face-material
indices are preserved. Extending slots on shared geometry isolates that object's
geometry using the existing assignment behavior.

`mode` defaults to `create`. `update` merges supplied parameters, settings and
texture channels into the owned recipe. Omitted channels remain; use
`remove_textures` to remove them. A supplied texture binding replaces that complete
channel binding. `coordinates`, `variation` and `tangent` likewise replace their
respective components; parameters/settings are field patches. Use
`remove_variation` and `remove_tangent` for explicit removal. Null is not an
instruction to remove a branch.

```json
{
  "name": "PaintedSurface", "mode": "update", "affect_shared": true,
  "parameters": {"roughness": 0.25, "coat_roughness": 0.1},
  "remove_textures": ["roughness"]
}
```

Shared material changes require `affect_shared: true`, including when several
objects share the same geometry/material slot. Otherwise use `material.copy`
with `source` and a new `name`, then update/assign the copy. Copies have independent
root graphs and shared image resources; there is no variant inheritance.
`material.assign_batch` accepts a `material_name` and up to 64 `assignments`.
`material.remove` accepts `name`, refuses resources with users (including a fake
user), and preserves image and node-group datablocks.

### Surface parameters

Parameters use Blender 5.2 Principled semantics:

- Base color, metallic, roughness, IOR, alpha and diffuse roughness.
- Subsurface weight, radius, scale and signed anisotropy. Set
  `subsurface_method` to `random_walk`, `random_walk_skin` or `burley` separately.
- Specular IOR level/tint, anisotropy and anisotropy rotation.
- Transmission weight, coat weight/roughness/IOR/tint, sheen weight/roughness/tint.
- Emission color/strength, Thin Wall, thin-film thickness/IOR.

Colors are linear RGB triples. Distances use scene units; thin-film thickness is
in nanometers. Strengths/weights and roughness use bounded semantic ranges in the
advertised schema. Thin Wall changes subsurface behavior; it ignores radius and
scale. The engine and geometry still determine the evaluated appearance. Native
thin film is a general angle-dependent effect, not a precomputed color texture.
A `tangent` component selects a named UV map or a radial X/Y/Z direction.

`variation` builds a normalized 3D fBM Noise → Color Ramp branch for `base_color`
or `roughness`, with `scale`, `detail`, `low`, `high` and optional coordinates.
Roughness ramp colors should be grayscale. Variation defaults to generated
coordinates. A channel cannot simultaneously have a semantic texture and
variation; use a declarative Mix branch to combine them.

### Textures and coordinates

Supported channels: `base_color`, `roughness`, `metallic`, `normal`, `bump`,
`displacement`, `alpha`, `emission_color`, `anisotropy`, `anisotropy_rotation`,
`subsurface_weight`, `transmission_weight`, `coat_weight`, `sheen_weight`.
These weight channels also serve as masks for the corresponding Principled layer.

Color/emission default to `sRGB`; other channels default to `Non-Color`.
The image's existing color space must match the requested space. The operation
never silently changes a shared image. Explicit `sRGB`, `Non-Color` and
`Linear Rec.709` are supported; a non-default semantic interpretation produces a
warning. Use existing image configuration to intentionally change an image for
all users. Single file and generated images are supported; animated/tiled texture
resources require a later extension of this subset.

Each binding supports linear/closest/cubic interpolation, flat/box/sphere/tube
projection, repeat/extend/clip/mirror extension and optional coordinate override.
Coordinates select UV, generated or object space plus location, rotation in
radians and scale. Object coordinates can reference an existing object. Named UV
maps must exist on assigned meshes; an empty UV name selects the render UV map.
`image_output` selects `color` (default, including grayscale alpha maps) or `alpha`
for ordinary channel bindings. Normal/bump/displacement read the image color.

Normal maps use separate tangent/object/world space, strength and OpenGL/DirectX
convention. Tangent normals require flat UV coordinates and the same named UV
basis. Bump uses strength, distance and inversion. When both are present, the
normal-map output feeds Bump's Normal input. Displacement uses its own scale,
midlevel and object/world space, connected to Material Output Displacement.
`settings.displacement` selects `bump`, `displacement` or `both`. True displacement
requires sufficient evaluated geometry and engine support. These operations do
not add subdivision or change mesh geometry. Surface method (`dithered`/`blended`)
and thickness model (`sphere`/`slab`) are explicit material settings.

## Declarative graphs

A request has up to 64 nodes, 128 links and 16 distinct images in the final graph.
Nodes use stable request-local IDs; their types, parameters and socket aliases are
explicit in the advertised schema. There are no arbitrary Blender properties,
Python expressions or nested groups. Exactly one active all-engine output and a
connected surface are required. Cycles, invalid links and unavailable sockets are
rejected before publication.

```json
{
  "name": "ProceduralSurface",
  "nodes": [
    {"id": "p", "type": "principled", "parameters": {"roughness": 0.35}},
    {"id": "out", "type": "output"},
    {"id": "coords", "type": "texture_coordinate"},
    {"id": "map", "type": "mapping", "scale": [2, 2, 2]},
    {"id": "noise", "type": "noise", "scale": 4, "detail": 3},
    {"id": "ramp", "type": "color_ramp", "stops": [
      {"position": 0.25, "color": [0.01, 0.03, 0.12]},
      {"position": 0.75, "color": [0.4, 0.15, 0.02]}
    ]}
  ],
  "links": [
    {"source": {"node": "coords", "socket": "generated"}, "target": {"node": "map", "socket": "vector"}},
    {"source": {"node": "map", "socket": "vector"}, "target": {"node": "noise", "socket": "vector"}},
    {"source": {"node": "noise", "socket": "factor"}, "target": {"node": "ramp", "socket": "factor"}},
    {"source": {"node": "ramp", "socket": "color"}, "target": {"node": "p", "socket": "base_color"}},
    {"source": {"node": "p", "socket": "bsdf"}, "target": {"node": "out", "socket": "surface"}}
  ]
}
```

Supported families are Principled, Material Output, Image Texture, Texture
Coordinate, UV Map, Mapping, normalized 3D fBM Noise, Color Ramp (2–8 stops), color
Mix, scalar Math, Vector Math, Normal Map, Bump, Displacement, Layer Weight,
Fresnel and Tangent. Math/Mix operations are explicit bounded enums. Dynamic
sockets are validated after configuring the node; for example, Normalize does
not expose Vector Math's second vector. Voronoi, RGB Curves, Hue/Saturation,
Separate/Combine Color, custom BSDF families and node groups are not part of this
subset. Principled's native layered surface and the supported color/mask branches
cover the common workflow without group ownership or inheritance machinery.

`mode: "patch"` upserts supplied nodes and replaces supplied target connections.
Existing node IDs cannot change type. Omitted nodes/properties/links remain.
`remove_nodes` deletes named nodes; `disconnect` removes links at named input
endpoints. Disconnect a linked input explicitly before setting its default.
Patches require the final graph to fit the supported subset; they may target a
supported unowned graph. They mark the resulting graph as declarative.

```json
{
  "name": "ProceduralSurface", "mode": "patch",
  "nodes": [{"id": "noise", "type": "noise", "scale": 10}]
}
```

`mode: "replace"` requires the current `expected_fingerprint`. Replacing an
unowned graph additionally requires `replace_unowned: true`. Ordinary semantic
updates never overwrite an unrelated custom graph. Low-level edits to an owned
graph are allowed, but a changed fingerprint prevents later semantic recipe
replay; use an explicit graph patch/replacement to resolve that state.

## Transaction and inspection contract

Changes are validated on an independent material/root-tree stage. Publication
remaps the existing resource's users to the completed stage, preserving its name
and shared relationship. Native datablock pointer identity changes. The original
is retained until assignments and result validation succeed; failures restore
users, names, slots and face indices and remove the stage. Animated, linked,
overridden/read-only materials, protected users and externally referenced root
node trees are rejected. No shared image is mutated or deleted by this lifecycle.

The compact `graph` summary contains ownership (`semantic`, `declarative`,
`modified`, `unowned`), a fingerprint and completeness flag, node/link/image counts,
surface/displacement connection state, Principled defaults, linked input names,
subsurface/displacement methods, texture channels, features and bounded warnings.
Linked socket defaults are not evaluated shader values. Semantic texture
recognition follows verified recipes; other graphs recognize direct image-to-
Principled connections only. Detailed node/socket/link pages remain opt-in through
`shader.inspect`, including supported node modes/operators, ramp stops and normal
conventions. Ramp details cap at eight stops with an explicit count/truncation flag.
Material catalog filters and pagination remain unchanged.

Fingerprints include stored graph values, supported node properties, links,
material settings and image identities/interpretation, and survive save/reopen.
They exclude layout/selection, image pixels and arbitrary unsupported node
internals. Unknown nodes, animation or non-finite external state make the
fingerprint incomplete. They are change checks, not rendered appearance hashes.

Construction validation does not replace rendering. Verify texture coordinates,
layering, displacement and highlights through `render.image`, and compare images
in the intended engine. Cycles and Eevee differ, especially for subsurface,
anisotropy, transmission and thin film. Render failures remain visible through the
existing render operation; graph success alone is not shader evaluation acceptance.
