# Deformation transfer and relative corrections

Structural articulation, primary surface deformation, corrective shapes and
animation controls are distinct layers. These tools author and inspect the middle
two layers. The caller decides the desired shape and activation values; Blender
computes geometry mechanics. No anatomical classifier or animation driver language
is embedded in the adapter.

## Relative shape keys

`blender.shape_keys.edit` atomically creates, edits and configures 1–16 keys:

```json
{
  "object_name": "Cover",
  "keys": [
    {
      "name": "Support", "create": true, "value": 0.5,
      "correction": {
        "mode": "region", "delta": [0, 0, 0.02],
        "selector": {
          "mode": "neighborhood", "domain": "vertex", "vertex": 24, "steps": 3
        },
        "falloff": {"center": [0, 0, 0], "radii": [0.4, 0.6, 0.4]}
      }
    }
  ]
}
```

Basis is ensured when the first key is created and is protected from editing.
New keys explicitly start at value zero unless a value is supplied. All meshes
must be exclusive, editable, local and have 1–100,000 vertices. Storage is bounded
by 64 total keys and two million key coordinates, including Basis. Shape animation,
drivers, absolute timed keys, pinned-key display and linked/shared edits are refused.
Names must fit 63 UTF-8 bytes. Existing keys need `create: false` (the default).

Native relative keys store coordinate snapshots. The effective delta subtracts
`relative_to` (Basis by default). Changing that reference deliberately changes the
delta; it does not silently retarget an existing shape. Cycles are refused. Existing
references follow native key renames. Optional `minimum`, `maximum`, `value`, `mute`,
`vertex_group` and `rename` patch settings; omitted fields remain unchanged. Ranges
must increase and contain the current/requested value. Empty `vertex_group` clears
the mask. Native masks multiply influence using existing group weights.

Correction definitions:

- `sparse`: up to 65,536 unique `{vertex, delta}` entries per key.
- `region`: one delta over a shared mesh selector; optional smooth ellipsoidal
  falloff uses authored object-local center/radii. Edge/face selectors contribute
  their vertices. Loop/ring, connected, neighborhood, valence and rest-frame
  region semantics are shared with the modeling tools.
- `captured_target`: inverse-mapped displacement from an explicit captured target,
  described below. One captured key per request.

Sparse/region vectors use `space: local` (default) or `world`; world vectors are
converted through the object's linear transform. `operation: replace` replaces
selected relative deltas, preserving unselected vertices. `add` adds to existing
key coordinates. Zero replacement clears a selected region. Native keys remain
dense internally; sparse requests avoid transmitting unchanged coordinates.
An optional `expected_topology_sha256` protects an authored index snapshot.

`shape_keys.inspect` returns Basis/reference, values/ranges, mask/mute/lock/driver
state and computed delta magnitudes. Pages default to 16, maximum 32 keys. Details
require `detail_key` and positive `detail_limit` (maximum 256); detail pagination
counts nonzero deltas above the returned `delta_epsilon` (1e−7 local units). No
coordinates are returned by default. Deltas are relative to each reference key,
before value/mask influence. Stored topology mismatch fails visibly.

`shape_keys.remove` stages removal of up to 32 unique keys. Surviving relative
references and locked/animated data are protected. Removing Basis requires every
key and explicit `remove_basis: true`. Geometry, UVs, materials, groups and modifier
stacks are otherwise retained. Ordinary mesh topology editing and destructive
modifier application continue to refuse meshes containing keys.

## Capture a posed correction without baking the pose twice

1. Pose the structure using `armature.pose` and inspect the primary defect.
2. Call `deformation.capture_target` with source `object_name` and a new `name`.
3. Edit that target with typed mesh operations to describe the desired surface.
4. Create a key using `correction: {"mode":"captured_target","target":"Desired"}`.
5. Compare the evaluated result with `deformation.compare`; sweep neighboring poses.

Capture creates an ordinary mesh with the current evaluated coordinates, world
transform, UV/material/group data and no keys or modifiers. It stores a native
source reference, ordered topology and evaluated-baseline/transform fingerprints.
Connectivity or source evaluation changes make the target stale. Returning to the
same baseline permits reuse; arbitrary reordered vertices are not registered.

The supported inversion stack is a bare mesh or one enabled, owned, **linear**
Armature (`preserve_volume: false`). Dual-quaternion volume preservation,
constructive modifiers, Surface Deform, smoothing and arbitrary nonlinear stacks
are refused for capture. They may still evaluate ordinary authored shape keys.
Capture never applies or removes modifiers and never changes the source pose.

The adapter subtracts the current evaluated baseline from the desired target,
then uses Blender's native displacement mapping to convert only that correction
to original object space. It checks forward mapping, refuses inverse amplification
over 1,000×, stages a Basis-relative key and evaluates it at value one. The selected
world-space target error must meet `tolerance` (default 1e−5, maximum 0.01) before
commit. A failed mapping or residual check preserves the original keys/datablock.

Captured keys must be unmuted, unmasked, Basis-relative and currently zero-valued;
a requested final value is applied after verification. Other existing key values
form part of the captured baseline. Region capture changes only selected indices;
it does not certify untouched regions against an independently edited target.
Reuse `deformation.compare` for a whole-surface check. This is a bounded native
inversion workflow, not a general pose-space solver.

## Target comparison and pose sweeps

`deformation.compare` accepts 1–8 `{object_name, target, selector}` pairs. Both
current evaluated meshes must retain their authored ordered topology, and the pair
must have identical connectivity/order. Matching vertex counts alone is insufficient.
The caller remains responsible for corresponding index meaning; there is no automatic
registration or transfer of corrective deltas between unrelated topologies.

Results contain selected count, world-space RMS/min/p05/median/p95/p99/max distance
and default four, maximum 16 worst indices. The request is capped at one million
evaluated vertex samples. No pose or key values are changed.

`deformation.sweep` poses now accept `shape_values` (up to 64 named channels) and
`targets` (up to eight comparison pairs). Every named key channel is zeroed for the
rest baseline and before each pose, then that pose's declared values are applied.
Other key channels retain their current values. All original key values, pose
channels, rotation modes and rest/pose position are restored on success and failure.
Targets must compare inspected objects; their work counts toward the sweep's existing
one-million-sample budget. Existing regional edge/area/angle strain and closed-volume
proxies remain available. No automatic corrective activation or driver is created.

A correction can improve one pose while harming another. Compare rest, mild,
intended, neighboring and opposite poses; inspect both shape error and strain.
A volume proxy does not certify winding inversion, self-intersection or contact.

## Native groups and rest-surface weight transfer

`vertex_groups.configure` batches up to 32 create/edit/rename/lock/remove patches,
with up to 16 constant-weight selector layers per group. Zero weight removes
membership. Up to 256 native groups and 100,000 vertices are supported. Unselected
weights and unrelated groups/attributes/keys survive staged publication. Locked
groups must be unlocked first. Armature-, shape-mask-, modifier- or constraint-
referenced groups cannot be renamed or removed. Results include at most 32 group
summaries and the total count. These are ordinary native groups, not a second
weight representation; existing `weights.assign` supplies deform gradients and
adjacency smoothing.

`weights.transfer` maps 1–128 explicitly named source/target groups through nearest
**authored rest-surface triangles**, with a required maximum correspondence distance.
It computes barycentric interpolation locally and optionally normalizes/prunes to
four influences. A shared selector preserves unselected target weights. Optional
`mirror` reflects source world-rest points around a named axis/plane origin; explicit
group mapping controls left/right naming. Same-object mirroring reads one source
snapshot before writing. Source and target topology may differ.

Bounds: 100,000 vertices per mesh, 200,000 source triangles and 256 groups. Missing
coverage, excessive distance or locked targets abort before publication. When
transferring deform groups to an owned binding, map the complete deform group set.
Initialize an owned target binding with existing `armature.bind` first. General
mask groups can transfer without an armature. Transfer precedes corrective keys;
shape-key meshes and posed/evaluated registration are outside this operation.
Inspect influences and motion afterward: normalized weights alone do not prove
suitable deformation or anatomical correspondence.

## Surface Deform lifecycle

`surface_deform.bind` atomically binds up to eight unmodified driven meshes to one
driver, with explicit `bind_state: current`. This captures their current relation;
it does not secretly switch to rest. Each driven mesh owns one native Surface
Deform modifier. Optional falloff (2–16), strength (0–1) and an existing native
mask group are supported. Add downstream subdivision after binding.

Native transfer follows driver mesh deformation, including shape keys and
armature poses. Subsequent driver object-level transforms are ignored; it is
not a parenting relationship. Move related objects together for assembly placement.

The driver requires convex nondegenerate faces, no coincident vertices and no
edge shared by more than two faces. Bounds are 10,000 evaluated driver vertices,
20,000 driver faces, 100,000 authored vertices per driven mesh and five million
summed driven-vertex × driver-face work units. Native binding failure removes all
new modifiers. Dependency cycles and unsafe evaluation are rejected.

`surface_deform.inspect` returns up to 16 relations with driver/driven, native
bound state, validity/reason, modifier order/settings and topology fingerprints.
Native `is_bound` alone is insufficient: authored/evaluated topology, identity,
ordering and enabled state are checked. Renaming the stored driver currently
invalidates the identity record; explicitly rebind. Relevant evaluated inspection,
render preflight and attached-guide evaluation reject invalid owned bindings.

`surface_deform.unbind` prevalidates and removes owned modifiers/records, including
stale bindings, preserving authored geometry, current driver shape and other
modifiers. It does not bake the result or reset the driver. Bind again explicitly
to capture another relationship. Native binding data and keys survive save/reopen;
use fresh-process re-evaluation to verify persistence.

## Corrective Smooth and deferred deformers

The existing modifier family now supports `type: corrective_smooth`, with factor
0–1, iterations 1–100, scale (0,3], simple/length-weighted smoothing, pinned boundaries
and an optional native group mask. It uses original coordinates and must precede
constructive modifiers such as Mirror/Subdivision. Bound-coordinate rest states
remain unsupported. Modifier movement/configuration respects this ordering guard.

Corrective Smooth can reduce local distortion while losing volume. It does not
replace authored shapes or promise shape preservation. Mesh Deform's volumetric
cage binding, Lattice data authoring, absolute timed keys, general drivers and
nonmatching-topology corrective transfer remain outside this foundation. Surface
Deform covers the tested driver/outer-layer relationship; other deformers should
be added when a workflow establishes their distinct need.
