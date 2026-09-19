# Surface-rooted growth

Use `blender.growth.*` for hair, fur, fibers, bristles, vegetation and other
surface-grown geometry. It stores native Hair Curves guides and generates dense
output through an owned Geometry Nodes graph. Use `blender.curve.*` for individually
named POLY/Bézier/NURBS paths, hoses, profiles and object/bone/triangle attachments.
Use `blender.surface_instances.*` for rigid distributions between explicit boundary
paths. These are distinct authoring choices, not interchangeable representations.

## Create and regroom

A local mesh with non-overlapping UVs is required. Existing materials and static
mesh templates are shared. For example, after creating a UV-mapped `Panel`:

```json
{
  "name": "Fibers",
  "surface": "Panel",
  "families": [{"name": "Fine", "length": 0.3, "radius": 0.002}],
  "regions": [{"name": "Patch", "family": "Fine", "guides": 128,
               "children": 4096, "flow": [1, 0, 0]}]
}
```

Send this to `blender.growth.create`. One call creates guides, sampled roots,
interpolation, native surface deformation and output. Counts describe each region:
`children: 0` outputs guides at their authored roots; otherwise that region outputs
exactly `children` interpolated curves. The guide geometry remains available for
editing in either case. Distribution is deterministic, weighted by authored triangle
area. `seed` and stable region slots determine positions. Overlapping regions are
allowed and generate independent layers; interpolation never crosses region groups.

Regions reuse face selectors: all, indices, box, normal and explicit rest-frame
region. They carry flow, counts and a default family. Flow is projected into each
root's tangent plane. A singular projection is rejected. Family `shape` is a short
normalized polyline: X follows projected flow, Z follows the surface normal. `length`
scales this shape; it is not necessarily its arc length. Families also supply radius,
tip-radius fraction, seeded length variation, roll in radians, offset, material,
template and ordering metadata. `layer` is an inspectable attribute, not an automatic
collision solver. Dimensions and offsets use surface-local units; QA distances are
world-space distances.

`blender.growth.configure` takes complete replacement family/region lists when those
fields are present, or a batch of `guides` edits identified by `root_id`. An edit
sets complete surface-local rest points, scales length, or reassigns a guide family.
Its first point must remain the bound root. Surviving roots retain IDs during ordinary
density/style edits. Manually edited guides preserve their authored shapes; family
shape/length/flow defaults regenerate unedited guides. A guide family override affects
that guide; generated children retain their region's output family. Region reassignment
is expressed through the region list. Changes to selection or seed require explicit
`rebind: true`. Mutation results include counts and compact change summaries.

## Continuous fields and ordered roots

A region's optional `field.controls` supplies UV positions, nonzero UV directions and
positive `length_scale` values. Inverse squared UV distance blends normalized directions
and length; exact control positions reproduce their values. The surface UV derivative
maps directions into the rest tangent plane. Cancelled or singular directions fail.
The field regenerates guides; dense child output uses native guide interpolation.
This is a rest-surface field, carried through native surface deformation afterward.

Use region `rows` with `guides: 0` and `children: 0` for ordered attachment systems.
Each named row has a short UV `path`, either `count` or `spacing` with `count: null`,
optional family/layer overrides, `order`, and `overlap` intent (`none` or
`over_previous`). `mirror: "u"` or `"v"` adds a reflected row around `mirror_center`
(default0.5); field directions are reflected too. Each root is checked against the
region's selected faces and UV binding. Surface, UV map and face domain are its explicit
support references. A row is its own native interpolation group.

Spacing uses surface-local arc length sampled at16 intervals per UV path segment;
it is approximate on curved/irregular mappings. `include_rows` returns measured rest
chord spacing. There are at most64 rows/system,16/region,32 UV points/path and2048
roots/side, within existing system budgets. Continuous-path coverage between quadrature
samples and collision-free layering are not certified by root placement.

Root IDs follow row name, side and sequence index through path, field, count and list
order changes. Count edits may reposition surviving roots; explicit guide shapes translate
with their revised root. Explicit rebinding creates fresh identities. Persisted attributes
carry row, sequence, mirror side, order, overlap and layer without per-root objects.
`growth.inspect` optionally returns row summaries and up to32 `field_samples` queries
(`region`, `uv`), including rest position, direction, normal, length factor and face.

## Attachment and persistence

Native Curves `surface`, `surface_uv_map` and per-curve UV coordinates drive Blender's
Deform Curves on Surface node. Creation enables the surface's native rest-position
attribute generation before evaluating shape keys and modifiers. This shared surface
setting remains enabled after removing a system because other hair users may need it;
failed staging restores its previous value. The surface's render and viewport modifier
settings must agree. Guides and roots live in the surface's local frame, parented with
identity local transforms. Armature, shape-key/corrective and object transformations
are evaluated natively. Keep the source's base mesh/rest data stable.

A system UUID scopes stable integer guide/child root IDs. Curve/point attributes hold
rest geometry, UV, region, family, direction, layer, variation and root pin weights.
There is no per-root object or large duplicated JSON point list. `.blend` save/open
preserves these resources and relationships, including in a fresh Blender process.

Base geometry, connectivity and UV fingerprints conservatively invalidate stale
attachments. Inspection reports aggregate validity and a repair message. Missing UVs
or a disabled rest-position source also fail validation. Rendering rejects invalid
systems. After deliberate source changes, use `growth.configure` with `rebind: true`
to regenerate rest data and allocate new root IDs. This is not a promise of arbitrary
topology-edit survival. Restore externally changed native bindings/transforms first.
Resource names referenced by the recipe must remain stable; restore external renames
before editing or removing the system.

## Shared and flexible templates

Set a family's `template` to an existing static mesh with 1–4096 vertices and local Z
in [0,1]. X is width, Y is thickness, and Z is root-to-tip distance. It can be hidden
as an authoring resource; its object transform is not used as template geometry.

- `mode: "instances"` shares geometry and aligns each instance to its guide root
  tangent and native minimum-twist normal, with Z scaled to guide arc length.
- `mode: "deform"` realizes bounded geometry and maps each template vertex's Z factor
  along the generated guide, using sampled tangent/normal frames for X/Y offsets.
  Curvature therefore changes the actual geometry instead of rotating a rigid panel.

`width`, `thickness` and family roll control orientation and size. Templates and
materials stay shared resources; family material overrides do not edit the template
or its original materials. Flexible output has an explicit evaluated-vertex cost.
Both modes use one growth object, one root carrier and one owned graph, independent
of output count. Native curve output renders as hair without mesh conversion.

## Compact QA

`blender.growth.inspect` returns counts, identities, regions/families, templates,
node count, output budgets and sampled attachment/orientation/clearance metrics.
No guide or point lists are returned by default. Use `guide_offset`, `guide_limit`
(up to 32), `include_points` and `include_recipe` for bounded detail. `qa_samples: 0`
skips spatial QA while retaining validation and evaluated counts.

Root QA compares actual evaluated roots with their UV-bound surface plus offset.
Orientation includes root tangent versus current surface normal, tangent versus the
configured rest flow transformed to world space, reversals, and adjacent native
frame-normal discontinuities. The flow statistic is a reference direction; it does
not claim to recover a deformed tangential material coordinate. Frame normals are
captured from the actual Geometry Nodes field used by template evaluation.

Clearance samples guide segments against the source and optional `clearance_objects`
using oriented nearest-surface BVHs. `root_exclusion` omits the near-root fraction of
guide length. Optional `template_samples` inspects actual template vertices, including
roots; this temporarily realizes geometry even for instance output. Signed distances
and penetrating/below-clearance counts are sampled diagnostics, not exact continuous
collision certification, especially for open, concave or inconsistently oriented
surfaces. No automatic biological or artistic verdict is produced.

`blender.growth.sample` accepts frames or typed poses with optional armature and
shape-key values. It returns compact QA per sample and restores prior scene, pose,
shape-key and frame state on both success and failure. At most 16 samples and 4096
sampled roots per sweep are allowed. Compose it with ordinary motion/deformation QA.

## Ownership, limits and cleanup

Only owned local single-user Hair Curves in Object Mode can be edited. The adapter
checks the native attributes, graph signature/ID references, owned carrier, modifier
stack and relationships. User-created Hair Curves, shared owned data, linked data,
unrelated modifiers and externally modified graphs are rejected. Configuration
stages replacement data/graph/carrier and swaps them on the same system object;
failures roll back without orphan resources. `blender.growth.remove` removes only
owned resources, protecting external users and leaving source/template/materials.
Generic object deletion is rejected for owned systems and root carriers.

Per system: 16 regions, 8 families, 10,000 authored guides, 50,000 output curves,
32 points per guide, 800,000 evaluated points, 2,000,000 equivalent template vertices,
350 nodes. Guide edit batches contain at most 256 IDs. Template samples are opt-in
(up to 4096). Budgets include family overrides and conservative interpolation point
counts. Avoid large realized geometry when shared instances satisfy the intent.

Native rod dynamics and cache lifecycle are documented separately. Rod clearance does
not certify broad-template or layered self-collision. Aerodynamic simulation, interactive
grooming brushes, automatic clumping and arbitrary Geometry Nodes authoring are deferred.
