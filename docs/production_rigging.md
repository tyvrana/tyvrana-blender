# Production rigging and revisions

`constraint.configure` authors a bounded ordered batch of owned copy-transform,
copy-rotation, copy-location, location-limit, damped-track, child-of, floor and IK
constraints. Endpoints identify objects or pose bones. Full replacements preserve
stack position; failed batches restore the previous native settings. Inspect before
editing externally modified constraints. `constraint.inspect` returns bounded
validity and evaluated transforms; `constraint.remove` removes owned entries.

IK supports explicit targets/poles, chain length (1–16), solver iterations,
orientation and stretch. `armature.configure_joints` also sets native IK axis
locks, angular limits, stiffness and stretch. Floor constraints provide a reusable
plane contact control, not automatic gait generation. Typed couplings and native
constraints share dependency validation, including parents and IK ancestors.
Unknown external constraints/drivers are rejected where dependencies cannot be
verified. No expressions or arbitrary native-property dictionaries are accepted.

`armature.match` copies sampled world transforms to independent targets in parent
order and verifies matrix error. `constraint.switch_space` updates an owned
child-of relationship and its inverse, optionally preserving the evaluated world
transform. Unrepresentable transforms fail and restore prior state.

`control_rig.configure` constructs an explicit two-segment FK/IK network over six
existing bones (matching-rest FK, IK and deform chains), plus independent target
and pole controls. Other architectures use the constraint and matching primitives.
`control_rig.switch` matches both directions and verifies each segment's full
matrix; `inspect` reports ownership, rest compatibility and output errors. `remove`
leaves authored bones and controls intact. Network switches currently operate on
unanimated, unlocked controls and do not insert keyframes. Use action authoring
separately. Constraint construction/removal also preserves existing animation by
requiring it to be detached before structural changes.

## Safe rest revisions

`armature.configure_rest` accepts `preview=true` to report affected owned bindings,
active actions, shape keys, captured targets and constraints without changing data.
The default dependency policy still requires an isolated neutral structure.
`dependency_policy="preserve"` keeps bone names, hierarchy, connectivity, deform
flags, weights and actions while revising frames/lengths. A commit requires the
current `expected_rest_sha256` obtained from inspection or preview. This is not
motion retargeting: existing poses and correctives must be revalidated.

Shared data, unsupported NLA, external references and unowned bindings fail before
publication. Rest data is staged and rolled back on validation failure. Captured
correction targets become stale; recapture in the intended pose. Shape-key edits
require the reported `acknowledge_rest_sha256` after a revision. Acknowledgement
permits editing and does not certify deformation quality. Existing ordered-topology
and inverse-deformation checks remain in effect.

`object_set.configure` permits collection membership and visibility changes on
owned objects while protecting domain-owned names and parenting. Inspection can
select visibility and membership fields. Core invalidates semantic validation
freshness after mutating operations, including conservative invalidation on preview.
