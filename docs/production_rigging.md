# Production rigging and revisions

`constraint.configure` authors a bounded ordered batch of owned copy-transform,
copy-rotation, copy-location, location-limit, damped-track, child-of, floor and IK
constraints. Endpoints identify objects or pose bones. Full replacements preserve
stack position; failed batches restore the previous native settings. Inspect before
editing externally modified constraints. `constraint.inspect` returns bounded
validity and evaluated transforms; `constraint.remove` removes owned entries,
including relationships whose original targets were deleted. Other externally
modified settings remain protected.

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
leaves authored bones and controls intact. Inspection derives the selected mode
from evaluated influences, including timeline playback.

## Animated matching and spaces

The three matching/switching operations accept optional
`keying={"action_name":"Shot","anchor_frame":9}` at the current integer frame
(for example frame 10). This extends the controls' owned active action, or creates
the named action, in the same transaction as matching. It samples destination
controls at the explicit earlier anchor and inserts CONSTANT anchor/switch keys.
Existing keys remain; the interval from the anchor to the switch is deliberately
revised. No action detachment or manual key reconstruction is needed. The original
action, assignments and channels remain available until evaluated pose validation
passes; failures restore them. Use existing action editing for subsequent spline
timing adjustments.

Keyed space switches retain up to 16 fixed-target Child Of branches on one control.
They key branch influences and compensate local transforms, preserving world pose
within the requested tolerance. Earlier target identities and inverse matrices
remain unchanged. Subsequent switches reuse existing target branches. Inspection
exposes their influences and native validity through `constraint.inspect`.

Destinations must be unlocked, use XYZ rotation for keys, and have no drivers;
match their source controls when driven. Evaluated sources may be animated or
constrained. Keyed spaces require an exclusive owned Child Of stack with exactly
one fully active branch. Blended influences, NLA, foreign actions, nonrepresentable
scale/shear, and unrelated destination constraints are protected. These operations
do not promise pose-preserving interpolation between sampled frames. General
constraint construction/removal still protects animated structures; rest revision
uses the explicit preservation contract below.

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
