# Rest structures and constrained joints

An armature defines articulation/deformation data: segment endpoints, joint centers,
axes, hierarchy and motion limits. It does not create anatomical bone or physical
component geometry. When correctness depends on those structures, author and inspect
them separately with mesh, object-set or swept-curve operations at suitable fidelity.
Binding transfers motion to geometry; animation controls provide interfaces, IK,
drivers and timed motion. Armature validation checks its data and motion, not the
adequacy of the underlying domain structure.

## Operations

| Operation | Purpose |
|---|---|
| `blender.armature.create` | Create 1–128 rest segments, including frames, hierarchy and optional limits in one request. |
| `blender.armature.configure_rest` | Add or replace full rest definitions and rename existing bones in one staged edit. |
| `blender.armature.configure_joints` | Replace or remove typed limits on 1–128 existing joints without changing rest geometry. |
| `blender.armature.pose` | Apply a bounded batch of rest-relative channels, optionally resetting all bones first. |
| `blender.armature.inspect_structure` | Inspect selected rest geometry, frames, limits and evaluated poses, with whole-structure validation. |
| `blender.measurement.inspect` | Measure lengths, joint-center distances, endpoint reach and unsigned geometric angles using shared point sources. |

The existing `armature.inspect` remains useful for world-space bone summaries,
content hashes and owned mesh-binding information. Create, rest configuration,
joint configuration and pose accept `sample_limit` (default 16, maximum 128);
zero suppresses bone rows while retaining counts, content hashes and bounded
binding summaries. Both create and pose already support batching.

## Centers, frames and units

Each native bone represents an articulation segment. Its **head is the joint center**;
its tail is the other endpoint. A connected child's head must coincide with its
parent's tail within 1e-6 armature units. A disconnected child retains an explicit
offset from its parent while inheriting its motion.

Blender's right-handed rest frame has **Y pointing from head to tail**. Choose one
orientation representation per bone:

- `roll`: radians about native longitudinal Y (default zero).
- `x_reference`: a direction projected perpendicular to Y and normalized as X;
  Z is X cross Y. Zero or collinear references fail. The reference need not
  already be normalized or perpendicular.

The frame origin is the head; it is not an independently stored landmark or
helper control. Native frames survive save/reopen through bone rest matrices.
There is no independently oriented joint frame offset from a segment: arbitrary
oblique mechanical pivots require explicitly modeled structural pivot segments,
or a later independently framed joint capability. No helper bones are generated.

Creation and rest editing accept `space: "armature" | "world"` (default armature).
Raw head/tail vectors and `x_reference` use this space. Creation starts with an
identity object transform. Shared point sources (`world`, `object`, `landmark`,
`reference_pixel`, `bone`) resolve to world positions and then armature space,
regardless of request space. They are **snapshots at authoring time**, not live
attachments. Existing world/object-local landmarks remain the persistent datum
representation. World-space rest edits require positive uniform object scale
without shear; local rest edits retain native armature coordinates.

Inspection endpoints and axes use `world` (default), `armature` or `parent`.
For `parent`, rest geometry uses the parent rest frame and pose geometry uses the
parent evaluated pose frame; roots use armature space. World frame axes require
positive uniform object scale without shear. Native axes remain available in
armature space for other object transforms. Returned axes are unit vectors;
lengths and positions use the chosen coordinate frame.

Joint limits, roll and pose rotations are **radians**, consistent with existing
pose operations. The shared measurement API reports **unsigned geometric angles
in degrees** and distances in Blender units or meters as requested. A geometric
three-point angle is not a signed joint Euler coordinate.

## Limits and native evaluation

`limits` is a typed XYZ Euler range relative to the bone's rest frame. The adapter
owns one native `LIMIT_ROTATION` constraint using `LOCAL` space, XYZ order, full
influence and modern rotation behavior. Other constraint spaces are not exposed:
world/pose limits change meaning with the object or parent, and
`LOCAL_WITH_PARENT` includes rest-parent relationships instead of isolating this
joint's rest-relative channels.

```json
{
  "name": "Arm",
  "head": [0, 0, 0],
  "tail": [0, 2, 0],
  "x_reference": [1, 0, 0],
  "limits": {
    "x": {"minimum": -0.4, "maximum": 0.6},
    "y": {"minimum": 0, "maximum": 0},
    "z": {"minimum": 0, "maximum": 0}
  }
}
```

This defines an X hinge. A null/omitted axis is unconstrained; `[0,0]` locks an
axis. Enable two or three axes for bounded multi-axis articulation. At least one
axis must be enabled. `configure_joints` with `limits: null` removes only the
owned joint constraint. Limits are full replacements, not partial field patches.

Requested channels remain intact while Blender clamps evaluated motion. For an
X request of 0.9 in this example, inspection reports requested 0.9 and evaluated
0.6, with `changed_by_constraint: true`. Evaluation removes parent motion and
rest orientation before reporting rest-relative XYZ angles. Constraint effects
are not inferred from channel values. Fixed-center constrained bones require
zero translation; every bone in a constrained structure requires unit pose scale.

This contract deliberately stays on one principal Euler branch: absolute X/Z
must be at most `pi - 0.0001`, and absolute Y at most `pi/2 - 0.0001` radians,
for both constrained requests and enabled limits. It excludes multi-turn motion,
gimbal singularities, swing/twist cones and anatomical joint solvers. Independent
Euler intervals are not coupled motion. Driver relationships, mechanical linkage
closure, IK/FK controls and timed animation use the [motion](motion.md) and
[production control](production_rigging.md) families. Optional `ik` settings in
`configure_joints` replace native per-axis IK locks/limits/stiffness and stretch;
`inspect_structure` reports them with the `limits` fields.

For mirrored structures, supply mirrored endpoints and a deliberate reference
axis for each side. Frames remain right-handed. Do not infer rotation signs from
`.L`/`.R` names or use negative object scale as an implicit frame reflection.

## Rest edits and safety

`configure_rest` accepts up to 128 full bone definitions to add or replace;
omitted bones retain rest geometry and owned limits. A replacement definition
uses defaults for omitted fields, including no parent and no limits. Renames run
first, so supplied definitions and parents use final names. Rename targets must
be unused; swaps are not supported. The final hierarchy must contain 1–128 bones.

The operation validates the complete resolved hierarchy, stages a copied armature,
then publishes it. Failures restore original data and owned constraints, remove
staging resources, and preserve active object, selection and Object Mode.

The default dependency policy requires neutral requested channels and exclusive
local data. Reset with `armature.pose(reset=true)` before isolated editing.
`dependency_policy="preserve"` permits verified owned bindings, actions and
constraints while retaining names, hierarchy, connectivity, weights and deform
flags. Preview the affected dependencies, then supply `expected_rest_sha256` for
commit. Captured targets become stale and corrective editing needs acknowledgement;
revalidate motion. See [rest revision policies](production_rigging.md).

External references/children, NLA, library/shared data and unverified dependencies
remain protected. No automatic weight retargeting or deformation correction is
performed. Owned rotation limits can remain during revision; neutral requested
input can evaluate away from rest if the permitted interval excludes zero.

Limit configuration may operate on a posed or bound armature because it leaves
rest geometry unchanged. It can repair changed settings of an identifiable owned
rotation constraint. Missing/replaced constraints or corrupt ownership metadata
are reported; arbitrary constraints are never adopted. The operation snapshots
native settings and restores them if a later step fails. Pose, binding and
existing deformation inspection accept validated owned limits while continuing
to protect external constraint systems.

## Inspection, identity and persistence

`inspect_structure` supports `root` (inclusive subtree), `names`, prefix and
pagination. It returns 16 rows by default, at most 128. Select fields from `rest`,
`frame`, `limits`, `pose`; defaults are rest and limits. Each row identifies its
parent, connection, owned joint constraint, constraint count and bounded issues.
Whole-structure validity includes unsampled bones. This validates structural
geometry and supported constraint state; it does not certify physical realism,
collision freedom, a closed mechanism or mesh deformation quality.

Use the shared measurement API with points such as:

```json
{"kind":"bone","object":"Mechanism","bone":"Arm","endpoint":"tail","state":"evaluated"}
```

`endpoint` defaults to head; `state` defaults to evaluated and can be rest. This
works in distance and angle queries and as a source for later rest authoring.
Names are current native object/bone names. Successful renames return those names;
stale point queries fail instead of silently targeting another entity. Native
constraints and ownership metadata survive the supported rename and save/reopen
paths. No parallel UUID database is introduced.

Native rest matrices, head/tail geometry, hierarchy, owned constraint settings,
requested poses and ownership metadata persist in the `.blend` file. Integration
checks author four generic structures, inspect and pose them, save, close the
background process, open in a new process, inspect, reset and pose again, and
repeat endpoint/angle measurements. Other tests cover different joint frames,
mirrored structures, linked data, binding dependencies and injected rollback.

## Native references

- [Blender EditBone API](https://docs.blender.org/api/5.2/bpy.types.EditBone.html)
- [Blender Limit Rotation](https://docs.blender.org/manual/en/latest/animation/constraints/transform/limit_rotation.html)
- [Blender coordinate spaces](https://docs.blender.org/api/5.2/bpy.types.Object.html#bpy.types.Object.convert_space)

The package's native tests validate the declared behavior on Blender 5.2.1 LTS;
passing a newer host's minimum-version guard alone does not establish validation.
