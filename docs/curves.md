# Curves, profiles and attachments

Four operations author native Blender **Curve** objects: `blender.curve.create`,
`configure`, `inspect` and `remove`. Use them for editable routes, cables, swept
sections and structural or surface guides. Native geometry evaluation supplies
lengths, sample positions, frames and attachment motion. These operations do not
implement physics, a muscle solver, Hair Curves or grooming.

## Author a family in one request

```json
{
  "defaults": {
    "spline_type": "BEZIER",
    "resolution": 12,
    "profile": {"kind": "circle", "radius": 0.03, "resolution": 8}
  },
  "curves": [
    {"name": "Route A", "splines": [{"points": [
      {"co": [0, 0, 0]}, {"co": [0, 1, 0.4], "radius": 0.7}, {"co": [1, 2, 0]}
    ]}]},
    {"name": "Route B", "splines": [{"points": [
      {"co": [2, 0, 0]}, {"co": [2, 1, 0.2]}, {"co": [3, 2, 0]}
    ]}]}
  ],
  "sample_limit": 0
}
```

Each curve can override shared settings and use existing collection memberships,
role and tags. There is no additional guide-set database: native collections and
names scope a related family. Creation accepts 64 curves, eight splines and 1024
points per curve, 256 points per spline, and 4096 points/128 bindings per request.
Each curve has at most 32 bindings. Sweeps have a conservative 200,000-vertex
per-curve estimate and one-million-vertex batch estimate. Inspection rejects more
than 250,000 actual evaluated vertices per curve.

Supported spline types are POLY, BEZIER and rational NURBS. Bézier handles are
AUTO, VECTOR, FREE or ALIGNED; FREE/ALIGNED require both handle positions. Blender
may adjust ALIGNED handles to enforce alignment. NURBS weights are positive;
order is 2–6 and cannot exceed the point count. Cyclic splines need at least three
points and close implicitly; do not repeat the first point. Resolution controls
native tessellation, so lengths and arc-length samples are evaluated geometric
approximations at that resolution, not analytic integrals.

Coordinates and radius use Blender local units; rotations and tilt use radians.
Creation optionally converts world-space point/handle coordinates into the new
object's local space. Radius stays local. Object transforms require positive
uniform scale; use generic object transforms and parenting for placement.

## Edit without rebuilding an entire family

`configure` accepts a batch of named curves, each with either a full `splines`
list or contiguous `ranges` containing complete point definitions. Full lists
support adding/removing/reordering splines. Point/handle edits are always local.
Settings are patches: omit unchanged values; `material: null` clears assignment;
`profile: {"kind":"none"}` clears a sweep. Existing spline types stay explicit;
changing the shared type does not reinterpret existing splines. Replace the
spline's `type` to convert it deliberately.

Bindings are preserved when omitted. `bindings: []` detaches and restores authored
positions. A supplied list rebinds at current target frames. Changing a bound
coordinate or bound spline topology requires explicit rebinding. Unbound points
and bound radius/tilt can be edited without rebinding. Complete batches are
validated and staged; creation/configuration failures roll back staged objects,
curve data, graphs and owned anchors.

## Profiles and taper

A circular profile uses its radius multiplied by each path point's interpolated
radius. Point radii provide editable taper, including zero-radius ends. A custom
profile references an existing native Curve object with identity world transform,
a single local-XY spline and 3–128 evaluated vertices. Its handles must also lie
in XY. Caps require a cyclic profile. Attached or swept profiles, arbitrary
modifiers, animated/constrained profiles and linked profiles are rejected.

Custom profiles and materials stay shared: editing a section updates all users.
The profile's scale multiplies point radius. The guide owns its Curve data and
small evaluation graph, while existing materials and profile objects remain
independent resources. Separate native bevel-object/taper-object authoring is
not exposed; these tested sweep and point-radius semantics provide the current
canonical path. No general Geometry Nodes editing API is added.

## Attach to structure or a deforming surface

A binding identifies `spline`, `point`, a typed `target`, an `offset`, and `follow`:

| Target | Offset/frame meaning |
|---|---|
| `{"kind":"object","object":"Socket"}` | Object-local coordinates in its current evaluated transform |
| `{"kind":"bone","object":"Structure","bone":"Segment"}` | Evaluated pose-bone coordinates; Y follows the bone axis |
| `{"kind":"surface","object":"Surface","face":0,"barycentric":[0.5,0.25,0.25]}` | Evaluated triangle frame: X along vertex 0→1, Z triangle normal, Y=Z×X; origin is the barycentric point |

Surface weights refer to the three vertices of an **authored triangular polygon**,
are nonnegative and sum to one. Frame offsets use target-local units before its
object transform. `follow: "point"` drives one control point. `follow: "spline"`
snaps the selected point to the offset and moves the entire authored spline with
the target frame, preserving its bound local shape. Bindings cannot overlap.
Two point bindings can connect a guide's ends to separate bones; intervening
points remain authored. This is a geometric constraint, not elastic deformation.

Motion is native dependency-graph evaluation. No inspection call, per-frame Python
handler or external agent must update points. Bone bindings own small Empty anchors
with native Copy Transforms constraints. Object/profile pointers and bone subtargets
follow native rename and persist in `.blend` files. Save/reopen tests terminate the
first background process and evaluate again in a new process.

Surface bindings sample evaluated vertices and follow topology-preserving armature
or coordinate deformation. Authored and evaluated connectivity must match exactly.
Subdivision, remeshing or changed connectivity invalidates the binding; inspection
returns `valid: false`, a reason, and no geometry result for that curve. No silent
nearest-point rebinding occurs. Native graph output can still exist after such a
change: **inspection validity is the acceptance gate**. Restore compatible topology
or supply explicit replacement bindings. Topology-independent UV rebinding and
Hair Curves' rest-position/UV deformation remain separate future capabilities.

## Inspect compactly

Default inspection returns at most eight curve rows, lengths, bounds, evaluated
vertex/face counts, spline types/counts, radius/tilt ranges and binding errors.
Use names, prefix, collection and paging; maximum curve limit is 64. `spline`
filters detail. No control points or samples are returned by default.

- `point_offset`/`point_limit` return at most 64 **authored local-space** points.
- `samples` is zero or 2–64 per spline, uniformly spaced by evaluated arc length.
- `space` selects local/world sample positions, endpoints, lengths and bounds.
- Sample radius is local; tilt is radians. Tangent, normal and binormal are unit
  directions in the requested space, using Blender's minimum-twist frame and tilt.
- Open samples include both endpoints. Cyclic samples use the half-open factor
  range `[0,1)`; the summary end equals start. Sharp corners, cusps and reversals
  do not imply a smooth or temporally continuous frame.
- Binding intended/evaluated positions and error always use world units. Errors
  above 0.0001 mark the curve invalid. Summary radius/tilt ranges are authored;
  the sampled values are interpolated evaluations.

## Ownership and failure recovery

Edits reject shared/linked curve data, shape keys, animation, object constraints,
external modifiers, changed owned graphs and dependency cycles. Native topology
changes outside the typed editor are reported rather than silently overwritten.
Unknown Geometry Nodes graphs retain existing evaluation safeguards; owned curves
participate in mesh/deformation dependency preflight.

Use `curve.remove` for managed curves. It preflights child/external users and
removes only owned data, graphs and bone anchors; materials and reusable profiles
survive. Remove guides before removing their referenced profile. External users
of an owned helper or graph block cleanup. A native deletion exception reports
`curve_remove_failed` with exact `removed`/`remaining` names and whether cleanup
may be incomplete; deleted IDs are not claimed to have rolled back.

Native tests cover geometry, frames, transformed parents, posed bones, deforming
surfaces, topology invalidation, sharing, cycles, external edits and injected
failure recovery. Packaged MCP tests cover routes, structural spans, surface
roots, a 50-curve family, shared profiles and fresh-process persistence.


Authored guide routes may avoid a joint or obstacle over a bounded articulation
without an automatic wrapping solver. Bind endpoints to object/bone frames, keep
intermediate guide points in intentional clearance regions, and validate the
actual swept profile with `geometry.inspect` through motion. `motion.sample`
aggregates attachment error, path length and closed-surface volume. Existing
transform/shape couplings can express a bounded tissue or compliant-component
response; its measured volume behavior is a diagnostic, not a material simulation.
If an authored route loses clearance, revise guides or record a wrapping gap;
endpoint attachment alone does not prove contact safety.
