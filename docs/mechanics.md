# Geometry-derived mechanics evidence

These read-only operations supply geometric evidence to an external authoring
agent. They do not infer anatomical identity, choose a physical joint model or
solve full-scene collision physics. Distances use world Blender units. Inputs use
the shared bounded mesh selectors, named authored loft/surface features and typed
point sources (including landmarks and evaluated bone endpoints).

## Joint fitting

`blender.geometry.fit` accepts up to eight named fits. Each uses up to eight mesh
regions and 64 typed points. Deterministic sampling defaults to 2048 points and is
capped at 8192 per fit. Geometry evaluation retains the shared limits of 250,000
vertices per mesh and 1,000,000 per operation. Complete evaluated source geometry
is fingerprinted; sampled coordinates are not returned.

Methods:

- `sphere`: normalized algebraic least squares for center/radius. A sphere alone
  does not determine an axis. Supply directed landmarks through `axis` when needed.
- `circle`: best-fit plane followed by a projected circular least-squares fit.
- `cylinder`: derive an axis from selected surface normals, or use an explicit
  evidence direction; fit the radial circle. Its center is on the axis at the
  support centroid's axial station, not an inferred mechanical pivot along that axis.
- `plane`: principal plane normal and support centroid.
- `landmarks`: support centroid plus a directed axis, optionally a secondary
  direction. This is frame construction, not a residual-based surface fit.

`axis` and `secondary` each take typed `start` and `end` point sources. Axis sign is
deterministic when evidence does not specify a sign. Secondary evidence determines
roll; otherwise a deterministic perpendicular basis is labeled `conventional_roll`.
A convention is not evidence of physical orientation.

A fitted `frame` has `head`, `tail` and `x_reference`. Copy these values directly
into an existing `blender.armature.create` bone with `space: "world"`; no client
roll or XYZ calculation is necessary. `frame_length` controls the tail distance.

Each result includes support/sample counts, RMS and maximum geometric residual,
conditioning, method, source identities, selection/geometry SHA-256 fingerprints
and reasons. Rank loss, coincident/collinear evidence, ambiguous normals, a collinear
secondary direction, excessive residual or poor conditioning yields `UNCERTAIN`
without a fabricated frame. Quality is geometric, not a statistical confidence
probability. An axis-free sphere may have a valid center with no frame.

Store `source_sha256` with a derived result and pass it as `expected_sha256` on a
repeat query. Changed geometry, transforms, point positions or orientation evidence
returns `STALE` with no usable frame. Refit and revalidate dependencies explicitly.
These stateless queries create no hidden persistent fitting resources.

## Contact envelopes

`blender.contact.inspect` accepts up to eight directed source/target regional
interfaces. Each supplies `minimum_gap <= 0` and `maximum_gap >= 0`. Supported
signed gaps within that range are `PERMITTED_CONTACT`; gaps above it are
`SEPARATED`; gaps below it are `INVALID_PENETRATION`. Unqualified sidedness is
`UNCERTAIN`. A known invalid sample takes precedence over uncertainty, which takes
precedence over separation and permitted contact. There is no contact exemption.

Two explicit evidence models are available:

- `closed_solid`: a closed, consistently oriented target uses generalized solid
  winding to classify inside/outside. Penetration is sampled interior distance to
  the target surface; exterior distance is positive. Open/nonmanifold or
  self-intersecting targets are not accepted as solids.
- `oriented_patch`: an open or concave interface requires the caller's authored
  permitted normal side (`allowed_side`). Nearest support must lie inside the
  patch, farther from its boundary than the gap or `boundary_margin`, with aligned
  offset and compatible competing normals. Rim projections and ambiguous support
  report uncertainty. Reflected object transforms retain authored side semantics.

The open-patch penetration value is a supported local unilateral distance. It is
not intersection volume or a global minimum translation distance. Curved seating
can therefore be evaluated without treating every broad normal-side overlap as
penetration, while unsupported regions cannot silently pass.

Results report counts, gap extrema, minimum sampled separation, maximum sampled
penetration, vertex-count contact fraction, bounded worst findings and provenance.
Counts/fractions are vertex proxies, not contact area. Findings are directed;
reverse source/target explicitly when reciprocal coverage is required. Thin or
unsampled intersections can be missed. No volumetric coverage guarantee is made.

Source samples default to 256, maximum 2048. Target support is limited to 32,768
triangles. Work includes exact self-contact qualification, solid winding and local
boundary/support tests; `max_tests` defaults to 200,000 and caps at 2,000,000.
Exhausting a budget is an explicit operation failure, never a partial pass.
Worst detail defaults to four, capped at sixteen. Expected fingerprints detect
changed evaluated source/target geometry and region/envelope definitions.

## Scoped motion sampling

The canonical `blender.motion.sample` discovers requested owners and their native
parent, object-constraint, modifier, driver and bounded node dependencies. Optional
`scope` adds up to 64 explicit owners. At most 256 dependency objects participate;
unrelated static objects do not consume that limit. A scene inventory above 256
no longer causes failure by itself.

Blender still evaluates its dependency graph globally when changing frames. The
restoration snapshot additionally captures off-scope animated owners, bounded at
1024 objects, and retains the existing 8192 native channel/resource guard. Arbitrary
frame-change callbacks are rejected because their side effects cannot be restored.
This is bounded mechanics inspection, not a guarantee of constant native scene
execution time. Original frame/subframe and supported native transform, pose,
shape and constraint state are restored in `finally`, including failures.

Add the same contact envelopes through `contacts`. `contact_max_tests` is shared
across the entire call; it is not renewed each frame. Existing selected geometry,
metric and 128-sample limits remain. Contact aggregates report classification
counts, worst classification/frame, extrema and uncertain/failed frame counts.
Hard evaluation failures fail the operation with state restored; they are not
hidden in a successful aggregate. Existing joint, coupling, rigid geometry,
closure and length measurements remain in the same result.

Default output contains aggregate metrics, bounded violations and no transform
or per-frame table. `detail_frames` remains an explicit bounded opt-in.
`scoped_object_count` and `restoration_object_count` expose the work boundary.
There is one synchronous bounded operation through the existing dispatch path,
with no client per-frame loop or new job subsystem.

Fingerprints represent evaluated geometry at a specific state. A static
`expected_sha256` should not be attached to intentionally moving contact geometry
across frames; it would correctly identify the changed evaluated evidence.
