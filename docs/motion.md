# Timeline, actions, coupling and motion QA

The motion interface authors native Blender animation without editor selection,
playback controls or arbitrary code. All operations work in Object Mode in a
background host. Discover their complete typed contracts through the operation
catalog; detailed animation schemas are not part of always-on client instructions.

## Scalar channels

Actions, couplings and motion queries share four channel kinds:

| Kind | Address | Meaning |
| --- | --- | --- |
| `transform` | `object_name`, optional `bone`, `property`, `axis` | `location`, `rotation`, or `scale`; axis `x`, `y`, or `z` |
| `shape` | `object_name`, `key` | Existing relative non-Basis shape-key value |
| `constraint` | `object_name`, optional `bone`, `constraint` | Existing named constraint's influence |
| `property` | `object_name`, `property` | Owned finite scalar created by `motion.set_properties` |

Rotations require existing XYZ mode and use radians. Object/bone action and driver
targets address **raw local channels**. Transform driver sources and motion channel
queries use **evaluated local transforms**, including constraints. Bone transforms
are relative to their native rest frame. These are principal Euler components,
not multi-turn angles, swing/twist decomposition, world coordinates or a universal
pose-space distance. Scalar sources use their native values. Location uses native
scene units and scale is dimensionless.

Initialize the structure with the existing armature operations. Constrained joints
accept rotation channels only; translation and scale cannot move their fixed centers.
Their principal-angle bounds still apply. Shape values, control properties and
constraint influence have their own writable ranges. Linked/override data, shared
armature data, shared shape meshes, locked/muted shape keys and read-only channels
are protected. Unknown external drivers/constraints can block relationship creation
when their dependencies cannot be verified; the operation preserves those resources.

`motion.set_properties` creates or updates up to 64 named float controls per request
and per object. Names use ASCII letters, digits and underscore, start with a letter,
and contain at most 48 characters. Values and explicit minimum/maximum are finite.
Storage uses reserved native custom properties; there is no arbitrary property
path interface. Keyed/driven controls must be changed through their action/coupling.

## Coupling

`coupling.configure` creates a batch of named `source → mapping → target`
relationships. Use `replace=true` to update existing owned relationships, including
an explicit change of source or destination. One scalar has at most one driver.
Active keyed destinations conflict; detach the action or remove its channel first.
An unassigned action remains retained, and assigning it later checks for drivers.

Two mappings are supported:

- `linear`: `scale * source + offset`, with an optional output `clamp` containing
  `minimum` and `maximum`.
- `remap`: ordered `input_min..input_max` maps to `output_min..output_max`.
  Descending output implements inversion. `clamp=true` is the default and limits
  the output to the interval between the two output endpoints.

Targets with a bounded native range require an explicit matching output clamp.
Clamping the relationship and constraining the joint are distinct: the raw driven
value may differ from the evaluated constrained angle. Inspection reports both.

Example: make one joint follow another at half its local rotation:

```json
{
  "couplings": [{
    "name": "Follower",
    "source": {"kind": "transform", "object_name": "Mechanism", "bone": "Input", "property": "rotation", "axis": "x"},
    "target": {"kind": "transform", "object_name": "Mechanism", "bone": "Output", "property": "rotation", "axis": "x"},
    "mapping": {"kind": "linear", "scale": 0.5, "clamp": {"minimum": -1.5, "maximum": 1.5}}
  }]
}
```

For pose-dependent correction, target an existing relative shape key and remap
an angle interval to 0..1. This activates the previously authored correction; it
does not construct the desired shape, infer anatomy or invert arbitrary modifier
stacks. `deformation.capture_target`, `shape_keys.edit` and `deformation.compare`
retain their separate [corrective semantics](correctives.md).

### Driver security and dependencies

Tyvrana generates the entire native simple expression from validated numeric
parameters. Transform variables use native `TRANSFORMS`/`LOCAL_SPACE`; other scalar
variables use `SINGLE_PROP` with paths constructed internally from supported data.
There is no expression string, supplied RNA path, `self`, driver namespace function,
Python callback, handler or polling loop. The generated expression evaluates with
Python script auto-run disabled. Opening files retains the existing scripts-disabled
file lifecycle.

Direct self-dependencies, cycles through other owned channels and transform parent
chains are rejected before mutation. All components of one transform share a
conservative dependency node. This deliberately refuses some combinations that a
more specialized native dependency analysis might permit. Unsupported external
driver/constraint dependencies are rejected rather than guessed. Native invalid
evaluation is checked after creation and rolls back the batch.

`coupling.inspect` returns filtered/paged compact summaries: typed source/target,
mapping, current input, mapped output, raw target, evaluated target, absolute
mapping error, absolute constrained error, saturation, validity and upstream owned
relationships. It does not dump native FCurves. Object pointers survive rename and
save/reopen. Missing data or externally edited paths/drivers are invalid; there is
no silent rebinding. Bone/key renames that make the owned declaration stale require
explicit repair. `coupling.remove` protects externally altered drivers; a missing
source or destination can be cleaned without affecting a new object of the same name.

### Metrics are not driver properties

Curve length, volume ratios, layer separation and other computed Tyvrana diagnostics
are **not persistent coupling sources**. There is no hidden application-side update
loop. Native shape values can drive a frozen volume snapshot's correction, while
live curve attachments follow native structural motion. This does not add an
implicit shortening/bulging model or animate private guide-profile metadata. See
[volume and layer diagnostics](volumes-and-layers.md).

## Timeline

`timeline.inspect` returns current integer frame and subframe, scene start/end,
preview range/enabled state, FPS and FPS base. Effective FPS is `fps / fps_base`.

`timeline.configure` patches these fields and evaluates through native `frame_set`.
Omitted fields remain unchanged; explicit null is rejected. A frame lies within
Blender's integer limit ±1048574; scene and preview ranges additionally respect
native RNA bounds. A subframe lies in `[0,1)`. FPS is 1..240 and FPS base 0.1..10.
Setting a frame does not start playback, create an action or bake simulation.

## Actions

`action.edit` operates on a named owned native action. `create=true` requires an
unused name; otherwise an existing owned action is required. A single request
edits multiple channels and keys without setting a scene frame for every key.

Each channel accepts integer-frame `keys`, `remove_frames`, `replace_keys` or
`remove_channel`. Key insertion replaces a key at that exact frame. Other keys
and other channels remain. Removing an absent frame is a repairable error; use
`remove_channel` when removing the last key. Changes are staged on a copied action
and published to its existing declared users only after validation.

Each key specifies the outgoing interpolation:

- `CONSTANT`: hold until the next key.
- `LINEAR`: straight interpolation to the next key.
- `BEZIER`: native Bézier interpolation with explicitly set `AUTO_CLAMPED` handles.

Custom native handles, sampled curves, modifiers and unsupported interpolation are
protected. Extrapolation is `CONSTANT` or `LINEAR`; omission preserves an existing
channel, and new channels default to `CONSTANT`. Bounded scalar targets require
constant extrapolation. Subframe evaluation is available through timeline controls;
key authoring and motion sampling currently use integer frames.

The native action has one keyframe layer/strip and one slot per owner ID. Shape keys
have their own Key owner, distinct from the mesh Object owner. Multiple objects and
an armature can share one declared clip. Unassigned actions use a native fake user
so they persist in saved files.

`action.assign` assigns all declared owner slots using REPLACE blending, influence1
and HOLD action extrapolation. Replacing another active action requires explicit
`replace=true`; the previous action is retained. `detach=true` detaches only matching
assignments and retains current values and action data. Assignment is transactional.
NLA tracks/tweak mode and external slot users are protected. This is an active-clip
foundation, not an NLA or production control-rig framework.

`action.inspect` provides counts, frame range and a bounded channel page with
interpolation, extrapolation, assignment, current/evaluated value and driver conflict.
Detailed keys require `key_limit`; handles and complete FCurves are not returned.
`action.remove` requires no remaining native users: detach first. It never deletes
unrelated actions or purges orphan resources.

## Motion QA

`motion.sample` evaluates existing scene animation in one bounded request. Combine
an inclusive uniform `range` (`start`, `end`, `step`) with explicit `frames`; samples
are deduplicated and sorted. A step need not land on the end; add that end explicitly
when required. No action is temporarily assigned. `reference_frame` supplies the
**evaluated geometry baseline**, defaulting to the first sample, rather than an
implicit rest pose.

Request only relevant diagnostics:

- Named scalar `channels` and `couplings`: source/output, mapping error, constrained
  error, validity and clamp saturation.
- `armature_object` plus `bones`: requested/evaluated local rotations, limit excess,
  evaluated violations, at-limit flags, world head/tail coordinates and world reach
  measured from the armature object's origin.
- `objects`: existing deformation edge/area/angle/volume diagnostics versus baseline.
- `targets`: existing corrective target RMS and maximum deviation.
- `volumes`: closed-volume/path/reference ratios and guide attachment errors.
- `contacts`: explicit regional contact envelopes with classification counts, gap
  extrema and worst frames; see [mechanics evidence](mechanics.md).
- `layers`: existing separation, contact, signed-normal proxy and saved sliding QA.
  Set `worst_limit=0`; inspect a reported worst frame separately for spatial details.

Results default to per-metric min/max/mean/p05/p50/p95 and extrema frames. These
percentiles aggregate each requested per-frame scalar; they are not pooled vertex
statistics. Explicit `thresholds` refer to metric names returned by an initial compact
query. Unknown metric names fail. Missing requested diagnostics, invalid relationships
and evaluated joint-limit violations are surfaced. Total violation count is exact;
returned violations are bounded in metric/frame order. Detailed frame values require
`detail_frames` and remain bounded. The response does not contain100 full mesh reports.

Requested owners and their native dependencies form a bounded mechanics scope.
Use `scope` to add explicit owners; unrelated static scene objects do not consume
the dependency-object limit. See [scope and restoration limits](mechanics.md#scoped-motion-sampling).

Sampling restores the original frame/subframe, transform modes/channels, pose-position
state, shape/control values and constraint influences on success or failure. Action
assignments are never modified. Native drivers and constraints continue to govern
evaluated values. Existing `deformation.sweep` remains the explicit hypothetical-pose
operation and retains its animation-protection rules; it is not a second action API.

Uniform/explicit samples do not guarantee continuous extrema, detect all collisions
or validate simulation caches. Existing volume/contact/sliding proxy limitations
remain. No anatomical, artistic or physical-simulation acceptance is inferred.

## Bounds and repair

| Resource | Limit |
| --- | ---: |
| Couplings per batch / scene |64 /256 |
| Owned scalar controls per batch / object |64 /64 |
| Action channels / keys per channel / total keys |128 /512 /8192 |
| Action key changes per edit / detailed returned keys |8192 /1024 |
| Sampled frames / detailed frames |128 /8 |
| Requested scalar channels / couplings / bones |64 each |
| Meshes / corrective pairs / volumes / layer queries |8 each |
| Evaluated vertex samples per motion request |2,000,000 |
| Aggregate metrics / detail scalar values |1024 /2048 |
| Returned violations / serialized motion result |64 /384KiB |
| Mechanics dependency objects / saved channel resources |256 /8192 |
| Restoration objects, including off-scope animated owners |1024 |
| Contact envelopes / shared contact tests |8 /2,000,000 |

Errors identify absent channels, unsupported spaces/modes, invalid ranges, cycles,
key/driver conflicts, external ownership and work/result budgets. Reduce the named
scope or repair the referenced resource, then retry once. Ordinary errors do not
return tracebacks. Unexpected native failures remain visible in diagnostic logs.

Implementation uses Blender's [slotted action API](https://developer.blender.org/docs/release_notes/5.0/python_api/),
[native driver variables and simple expressions](https://docs.blender.org/manual/en/5.2/animation/drivers/drivers_panel.html),
and [scene frame evaluation](https://docs.blender.org/api/5.2/bpy.types.Scene.html).


`motion.sample.measurements` reuses the existing typed distance/angle queries
from `measurement.inspect` (up to 32). Points resolve in world space at each
sampled frame; distances use Blender units and angles use degrees. Aggregate
`measurement.<name>.value`, absolute comparison `error`, and `valid` replace
per-frame calls and manual endpoint subtraction. Failed explicit comparisons
contribute violations, with the same timeline restoration and bounded result
policy as other motion diagnostics. This verifies sampled mechanism closure;
it does not implement an additional solver or continuous motion certificate.
