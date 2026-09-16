# Deforming volumes and layer diagnostics

These operations construct and measure general flexible structures: sleeves,
padding, actuators, covers and layered surfaces. They do not simulate tissue,
collisions, friction or automatic contraction.

## Construct a live swept volume

Use [`curve.create`](curves.md) for one coherent, batched construction request.
A native Curve carries the path, variable point radius and tilt; its owned
Geometry Nodes group evaluates the profile and attachments. POLY, BEZIER and
NURBS paths, circle or custom asymmetric profiles, caps, intermediate controls,
object/bone frames and triangular surface attachments already share one contract.
No separate volume guide or low-level graph authoring is needed.

For example, with two existing bones:

```json
{
  "curves": [{
    "name": "FlexibleInsert",
    "role": "padding",
    "splines": [{
      "type": "BEZIER",
      "points": [
        {"co": [0, 0, 0], "radius": 0.4},
        {"co": [0.2, 1, 0], "radius": 1},
        {"co": [0, 2, 0], "radius": 0.4}
      ]
    }],
    "settings": {
      "resolution": 12,
      "profile": {"kind": "circle", "radius": 0.2, "resolution": 16, "caps": true}
    },
    "bindings": [
      {"spline": 0, "point": 0, "target": {"kind": "bone", "object": "Rig", "bone": "Start"}},
      {"spline": 0, "point": 2, "target": {"kind": "bone", "object": "Rig", "bone": "End"}}
    ]
  }]
}
```

Radius multipliers make narrow ends and a wider middle. Moving attachments changes
the evaluated guide and mesh. Unbound control points retain their authored
positions; moving two endpoints does not infer a new middle control, compensate
volume, or change radius automatically. This is an explicit modeling choice.
Custom profile dimensions and rotation stay in the existing curve contract.

## Inspect or freeze the result

`blender.volume.inspect` accepts 1–16 `objects`, each with `object_name` and an
optional `reference` object. It reads evaluated Mesh objects or managed profiled
Curves, including current modifier results. It returns compact geometry summaries:

- Closed, consistently wound triangle-sum volume magnitude, surface area,
  world bounds and **surface-area-weighted centroid** (not volume centroid).
- `reference_volume`, `reference_kind`, `volume_ratio` and percentage change.
- For curves: current evaluated path length, optional reference length/ratio,
  authored control radius extrema, circle radius extrema in world units, and
  attachment count/maximum error. Optional arc-length samples report interpolated
  radius; extrema of authored controls are not certified evaluated extrema.
- Current modifier type order and ordered evaluated topology hash. Inspect detailed
  relationships using `curve.inspect`, `armature.inspect`, `surface_deform.inspect`
  and `modifier.inspect`.

For a mesh without an explicit reference, the denominator is its **authored mesh
geometry in the current object world transform**. This ignores shape keys and
modifiers and is not an implicit rest evaluation of its whole stack. A curve has
no implicit reference. Use `volume.snapshot` for a fixed evaluated reference.
An explicitly named reference object is evaluated in its current state, so it is
only fixed if its geometry, transforms and dependencies remain fixed.

`volume_ratio = evaluated_volume / reference_volume`; percentage change is
`100 * (ratio - 1)`. Missing/open/zero reference volume gives null ratios.
Open or inconsistently wound surfaces have null volume but still provide area and
bounds. This tetrahedral signed-sum magnitude is **not** union volume, a
self-intersection check, or proof of outward orientation. Oppositely oriented
closed components may cancel. Mirroring/reversed winding does not change the
absolute volume of a single closed component.

`blender.volume.snapshot` copies 1–16 current evaluated sources into new ordinary
native Mesh objects with requested unique names, collections, role and tags.
Sources and their owned graphs are preserved. The copies are frozen, with no
live guide link, parent, animation, modifiers or automatic binding. Native
material slots, UV/deform layers and vertex-group names are retained by the mesh
copy. Current world placement is retained. Creation rolls back if any item fails.
Surface Deform can reject nonplanar swept polygons as invalid. Prepare the new
mesh with the existing `modifier.create` (`type: "triangulate"`) and
`modifier.apply` before binding, when needed. This preserves the source curve and
uses the established topology-edit lifecycle. Triangulation does not cure
degenerate or overlapping geometry and is not a deformation-topology design tool.
Captured path length is small saved provenance on the output object; it does not
make the source a dependency. Use these meshes with existing topology edits,
Armature weights, Surface Deform and explicit relative shape correctives.

```json
{"objects": [{"source": "FlexibleInsert", "name": "InsertReference"}]}
```

## Represent layers with existing native relationships

Collections, object-set filters, roles/tags and native object names identify a
family. Pass the selected names to bounded query batches; no separate tissue
database is required. A driver may use Armature deformation; an intermediate and
outer mesh may use a chain of owned Surface Deform bindings. Bind in an explicit
rest/current state and inspect validity. Modifier order matters: bind against the
intended input surface, then add suitable downstream smoothing/subdivision.
Surface Deform follows target mesh deformation, not target object transforms.
See [transfer and corrective contracts](correctives.md).

## Separation, contact and directional distance

`blender.layer.inspect` accepts up to eight `queries`:

```json
{
  "queries": [{
    "mode": "current", "source": "Outer", "target": "Inner", "sample_count": 1024
  }],
  "contact_distance": 0.001,
  "minimum_separation": 0.02,
  "ray_direction": "opposite_source_normal",
  "ray_limit": 1,
  "worst_limit": 8
}
```

All distances are in world units. The source is sampled at vertex positions; a
BVH accelerates queries against triangulated evaluated target faces. Shared mesh
selectors operate on **current evaluated source topology**. A region can use a
world/object/bone rest frame; a named saved reference can retain that selected
region. Vertex, edge and face selections resolve to source vertices. Sampling
uses evenly spaced ranks among sorted selected vertex indices, including both
ends when requesting at least two points. It is deterministic, **not uniform by
surface area**, and cannot certify unsampled face interiors or thin intersections.

| Metric | Definition and limit |
| --- | --- |
| `separation` | Nearest Euclidean distance from sampled source vertex to target surface. Nonnegative; not physical thickness. |
| `contact_samples` | Number with separation `<= contact_distance`. Proximity threshold, not a collision solution. |
| `below_minimum_samples` | Number with separation `< minimum_separation`. |
| `oriented_normal_gap` | Dot product of source-minus-nearest-point with that target triangle's oriented unit normal. |
| `negative_side_samples` | Number with oriented gap `< -normal_epsilon`. A local normal-side penetration proxy, not global inside/outside classification. |
| `negative_side_depth_max` | Largest negative oriented gap magnitude, clamped to zero. Winding, folds and nearest-face ambiguity affect it. |
| `normal_ray_distance` | Distance to the first hit along the sampled source's area-weighted vertex normal, or its opposite. Misses are counted; no miss is converted to zero. Not general shell thickness. |

Each distribution contains count, minimum, mean, p05, median/p50, p95, p99 and
maximum. Worst locations prioritize negative normal-side gap, then smallest
separation and vertex index; they are not a ranking of largest sliding. Return
at most 32 rows. Ray direction/limit are explicit; rays do not backface-cull.
Parallel layers with aligned normals make nearest and normal-ray distances
coincide. Oblique normals, finite boundaries and folded geometry can separate
them. Reversing target winding reverses the normal-side interpretation.

## Track relative tangential movement

`blender.layer.capture_reference` captures 1–8 named relations using the same
source/target/selector/sample-count fields. `replace: true` explicitly recaptures
an existing name; there is no silent rebind.

```json
{"references": [{"name": "CoverMotion", "source": "Outer", "target": "Inner", "sample_count": 1024}]}
```

For each sampled source vertex, capture the nearest target triangle's vertex
triple, barycentric coordinates and source-to-target offset in an orthonormal
triangle frame. At a later pose:

1. Advect that barycentric point with the same three target vertices.
2. Rebuild frame X from vertex 0 to 1, Z from the oriented triangle normal,
   and Y as Z cross X.
3. Project current source-minus-advected-target offset into that frame.
4. Subtract the captured frame coordinates.

`layer.inspect` with `{"mode":"reference","name":"CoverMotion"}` reports the
magnitude of the tangent XY delta, signed normal change, and signed tangent XY
components in bounded detail rows. This tracks a material triangle location;
it is **not** the displacement to the current nearest point, geodesic slip,
friction, or simulated sliding. Nearest separation remains an independent current
query. Common rigid motion cancels; common scale can change world-unit gaps.
Large deformation or changing n-gon triangulation may make the captured affine
triangle a poor surface correspondence even when connectivity remains unchanged.

The scene owns bounded JSON reference metadata and native object pointers, so
renaming objects is supported and state persists in `.blend` files. Captured
source indices stay fixed across poses. Ordered authored/evaluated connectivity
hashes must match: edits, missing objects, stale owned bindings or degenerate
tracked triangles return `valid: false` with a reason. Pose/coordinate changes do
not invalidate connectivity. Empty `queries` lists saved reference names.
`blender.layer.remove_reference` removes 1–32 references and pointer properties.
It does not alter geometry or native deformation relationships.

## Pose sweeps, limits and errors

`deformation.sweep` adds `volumes` and optional `layers` (the `layer.inspect`
argument object) to each evaluated pose. These requests are shared across poses;
current selectors follow each pose, saved references retain captured indices.
`objects` may be empty when volume/layer queries are supplied. Existing bound-mesh
strain/regions, explicit per-pose key values and target comparisons remain
available. Every pose/key/mode is restored, including failures; no drivers or
activation rules are created. Volume/layer summaries are limited to 128 per
sweep, layer worst details to 256, with a shared one-million evaluated vertex/pose
budget. Section samples are omitted in sweeps.

| Bound | Limit |
| --- | --- |
| Volume queries or snapshots | 16 |
| Layer pairs / capture batch | 8 |
| Samples per layer pair | 1–8192 (default 1024) |
| Worst rows per pair | 0–32 (default 8) |
| Evaluated surface | 250,000 vertices / 500,000 triangles |
| Unique evaluated vertices per call / vertex-pose work per sweep | 1,000,000 |
| Curve detail samples | 0 or 2–32 per spline, at most 256 total |
| Saved references / total saved samples / encoded metadata | 32 / 131,072 / 24 MB |

Existing curve-generation, mesh-selector and native dependency limits still apply,
including 256 visible/evaluated dependency objects. Source surfaces and BVHs are
cached within a call; caches are released after inspection. No full point/face
arrays are returned. Named references store bounded samples in the scene but
inspection returns summaries only.

Argument validation returns bounded field errors. Invalid geometry or budgets
return `layer_geometry_invalid`; malformed reference data uses
`layer_reference_invalid`. Existing dependency, selector and binding errors retain
their own codes. Repair the named geometry/reference or reduce the requested
batch/detail. Current queries fail on invalid geometry; named reference queries
report invalidity explicitly instead of treating stale data as measurements.

## Validation

Unit tests exercise schema/output budgets, sampling and signed delta math. Native
background fixtures cover attached and curved volumes, analytic cube volume/area,
known plane gaps, oblique ray distances, winding effects, contact/negative-side
proxies, controlled sliding and rigid-motion invariance, regional queries, native
transfer chains, corrective volume repair, payload preservation, topology guards,
transaction rollback and failure restoration. Packaged MCP tests save, terminate,
start a new background host, open, re-pose and compare volumes, attachment errors,
binding validity and tracked layer diagnostics.
