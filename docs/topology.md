# Articulated surface topology

Topology tools describe and edit native meshes. They do not decide an artistic
layout or classify whether a defect comes from topology, weights, joints or pose.
Use authored topology, influence inspection and evaluated motion together.

## Semantic selection

The shared `MeshElementSelector` works with mesh query/edit, regional weights,
Face Sets and retopology operations that accept the corresponding domain.
Selectors read the current authored snapshot, independently of UI selection.

| Mode | Required fields | Meaning |
| --- | --- | --- |
| `topology` | `domain: edge`, `path`, `seed` | `ring`: opposite edges across quads; `loop`: straight continuation through regular manifold quad vertices; `boundary_loop`: connected closed boundary. |
| `connected` | `domain`, `seed` | Vertex/edge connectivity; faces connect through shared edges. |
| `neighborhood` | `domain`, `vertex`, `steps` | Breadth-first edge distance, 0–32 steps; output edges/faces require all vertices inside. |
| `valence` | `domain: vertex` | Inclusive `minimum`/`maximum` (0–1024); `extraordinary` defaults true, selecting valence other than four. Boundaries excluded unless `include_boundary: true`. |
| `region` | `domain`, `region` | Frame-relative box; `inclusion` is `center` (default), `any_vertex`, or `all_vertices`. |

Graph traversal selects at most 100,000 elements. An ordinary loop stops at a
pole, boundary or non-quad neighborhood; it never guesses a continuation. Rings
reject triangles/ngons and non-manifold rails. Boundary-loop traversal rejects
branches. `mesh.query` returns at most 256 details with full matched count and
explicit truncation. Region tests use element centers or vertices, not geometric
polygon/box clipping.

A region contains `min`, `max` and an optional `frame`:

```json
{
  "frame": {"kind": "bone", "object": "Structure", "bone": "Segment"},
  "min": [-0.5, -0.4, -0.5],
  "max": [0.5, 0.4, 0.5]
}
```

A bone frame uses its native **rest** matrix: origin at the head and longitudinal
Y along head-to-tail. Object frames use `{"kind":"object","object":"Name"}`.
World frames default to identity and optionally accept `origin` and XYZ Euler
`rotation` in radians. Frame scale is retained; singular frames are rejected.
This describes slabs, half-regions and joint neighborhoods without biological
operation names or model-side matrix conversion.

## Compact cage inspection

`blender.mesh.inspect_topology` takes `object_name`, a shared `selector` (all
vertices by default), and `sample_limit` (default 8, maximum 64). Selected edges
or faces first contribute their vertices; metrics use the induced edges/faces
whose vertices are all inside. Valence counts incident edges in the complete
mesh, so a region boundary does not manufacture new poles.

Results contain counts, valence histogram, boundary/non-manifold edge counts,
degenerate faces, world-space edge-length and face-area distributions, quad
aspect (`longest edge² / area`), and bounded pole samples. Pole samples include
boundary flags; boundary valence other than four is not automatically a defect.
Distributions report min, p05, median, p95, p99 and max; empty distributions have
null values. The ordered-connectivity hash identifies the inspected snapshot,
not a persistent element identifier or shape hash.

## Coordinated loop insertion

`blender.mesh.insert_loops` refines explicit quad strips on an authored mesh:

```json
{
  "object_name": "Cover",
  "cuts": [
    {"edge": 12, "from_vertex": 8, "factors": [0.2, 0.5, 0.8]},
    {"edge": 46, "from_vertex": 25, "factors": [0.35, 0.65]}
  ]
}
```

All seed indices refer to one input snapshot. `from_vertex` orients every rail
through quad connectivity. Factors are increasing fractions along the original
rail, strictly inside (0,1), with minimum separation 0.00001. Each strip may have
up to 4096 transverse edges and 16 cuts; a request contains up to 16 strips.
Strips may share endpoint loops but must not overlap faces. A conservative
2-million-element staging budget includes vertices, edges, faces and corners;
modifier evaluation has its own existing budget. Repeated native cuts also have
a 20-million-element/cut work estimate: predicted staged elements multiplied by
total cuts. Reduce the batch if this guard is exceeded. Traversal is linear in
touched connectivity; repeated cuts rebuild lookup indices, so their work scales
with both mesh size and cut count.

The implementation uses native BMesh subdivision at the actual requested
fractions. It does not subdivide uniformly and then move interpolated data to
unrelated positions. All cuts are prevalidated and staged; a failure preserves
the original mesh and removes temporary data. Shared mesh users keep their
original datablock. The result reports counts and `indices_invalidated: true`.
Requery after mutation; no downstream index stability is promised.

For an explicit source-conforming target, existing
`blender.retopo.insert_loop` accepts `factors: [...]` with `from_vertex` instead
of a single `factor`. It projects all inserted vertices in the same transaction.
Its existing 128-rail, target-geometry and projection bounds remain. The two
operations share the native insertion implementation; source retopology retains
its stricter pre-UV/pre-skin safety policy.

Existing `retopo.slide`, `relax` and `project` handle directed redistribution and
source correspondence. `rotate_edge` provides explicit flow/pole rerouting;
`delete_elements`, `fill_boundary`, `bridge_loops` and `stitch` construct declared
replacement patches. Query, inspect and validate each deliberate layout. There
is no automatic pole relocation or patch optimizer.

## Preservation and refusal

| Data/dependency | `mesh.insert_loops` behavior |
| --- | --- |
| UVs and corner data | Native interpolation at cut fractions; UV layer identities and active flags retained. UV quality still needs inspection. |
| Seams, material slots, face materials, smooth/sharp flags | Native subdivision inheritance; existing slots retained. |
| Vertex groups/deform weights | Native interpolation, including owned Armature bindings. This preserves numeric influence data, not deformation quality. Reinspect weights and motion. |
| Custom attributes | Native BMesh custom-data behavior; loss of a named attribute schema causes rollback. Interpolation does not preserve application-specific meanings such as stored element indices. |
| Shape keys, custom normals, mesh animation | Refused. |
| Modifiers | Owned Armature, Mirror, Subdivision and Shrinkwrap remain ordered/unapplied, with evaluation budgets. Unknown modifiers are refused. |
| External deformation bindings / vertex parenting | Refused; explicit remapping would be required. |
| Surface curve attachments and index references | Connectivity changes invalidate their snapshot bindings. Curve inspection reports invalidity; explicitly rebind. Native graph output alone is not validity evidence. |
| Object/bone curve bindings | Retain their frame relationship; no automatic surface rebinding. |

Retopology operations still refuse target UV maps, vertex groups, shape keys and
unsupported attributes. Their vetted empty/Mirror/Shrinkwrap/Mirror→Shrinkwrap
stacks may now end with one bounded Subdivision modifier. Authored and evaluated
correspondence remain separate: subdivision can change the silhouette even when
authored vertices project exactly onto a source.

## Multiple-pose deformation QA

`blender.deformation.sweep` accepts an owned `armature_object`, up to eight bound
`objects`, 1–16 named `poses`, and up to eight named frame `regions`:

```json
{
  "armature_object": "Structure",
  "objects": ["Cover"],
  "poses": [
    {"name": "rest"},
    {"name": "bend", "bones": [{"name": "Segment", "rotation": [0.7, 0, 0]}]},
    {"name": "combined", "bones": [{"name": "Segment", "rotation": [0.7, 0.3, 0.2]}]}
  ],
  "regions": [{
    "name": "joint",
    "frame": {"kind": "bone", "object": "Structure", "bone": "Segment"},
    "min": [-0.5, -0.4, -0.5], "max": [0.5, 0.4, 0.5]
  }],
  "sample_limit": 2
}
```

Every pose starts from reset channels; native joint limits remain active.
Requested bones' evaluated local rotations are returned. Original channel values,
rotation modes and pose/rest position are restored on success **and failure**.
Existing rig animation/control/lock guards still apply. Execution is synchronous;
no animation keys or persistent pose changes are authored.

Regions select evaluated rest vertices once, using frozen frame matrices. Every
edge/triangle endpoint must lie inside. These are evaluated indices and must not
be used as authored edit indices. Empty regions produce empty distributions.
The result is limited to 128 mesh/pose/region summaries and one million evaluated
vertex/pose samples; existing per-snapshot mesh/triangle bounds also apply.
To keep replies within transport bounds, summary count multiplied by
`1 + 2 * sample_limit + len(bone_names)` must not exceed 384.
`sample_limit` is 0–16, default 4. Contact probes remain in `deformation.inspect`.

Both current-pose inspection and sweeps report edge and triangle-area ratios,
absolute triangle corner-angle changes in radians, and triangles with area ratio
below 0.01. Degenerate rest triangles are counted separately. Bone regions use
weights at least 0.5. Closed, consistently wound meshes/regions have a volume
proxy; open regions report null. Worst edge samples retain rest/posed coordinates
and dominant inspected-bone evidence.

These metrics do not certify inversion or self-intersection. Rotating a normal
more than 90 degrees is not proof of an inverted face. Additional loops can
improve angle distortion and volume retention while increasing measured peak
stretch by resolving deformation more accurately. Compare controlled variants
across rest, mild, medium, near-limit and combined poses; there is no universal
quality threshold or automatic diagnosis.
