# Reference-space construction

Use source observations to retain where a feature was seen, and registered
references to state how that evidence constrains construction geometry. The
adapter performs the coordinate conversion and solve. It does not identify
features, choose correspondences, infer projection assumptions or calibrate a
camera from a photograph.

## Coordinate spaces

- **Source:** continuous image-edge pixels, bottom-left origin, `[0,0]` through
  `[width,height]`. Pixel centers are half-integers. Coordinates follow encoded
  image orientation, not EXIF rotation. Moving a reference never rewrites pixels.
- **Reference local:** the image empty's display size, image aspect and offset
  map source pixels into its local XY plane. Parenting alone does not account
  for display-size changes.
- **World:** the reference object's native transform maps that plane into the scene.
- **Construction:** world axes by default, or an existing rigid, right-handed,
  unit-scale object frame. Results use Blender units. Registration calibration
  may use meters through the scene's explicit meters-per-unit value. Source
  origins and axis constraints are always expressed in construction Blender units.

## Register references

`blender.reference.register` accepts up to 16 declarations. Each contains a
reference name, explicit projection assumption, two source pixels with a known
distance, a source origin, signed horizontal/vertical construction axes, and an
optional construction frame and origin. The operation calibrates and aligns the
native image empties; separate per-reference transform arithmetic is unnecessary.
References must be editable, unparented and have no children. Source images must
be unchanged, packed static images. Existing image/reference creation is reused.

```json
{
  "registrations": [{
    "reference": "Front drawing",
    "projection": "orthographic",
    "calibration": {"a": [0, 0], "b": [200, 0], "distance": 2},
    "origin_pixel": [100, 100],
    "horizontal": "x",
    "vertical": "z"
  }]
}
```

An **orthographic** observation constrains two components and leaves depth free.
A **plane** declaration additionally asserts that the feature is on the registered
physical plane. This is a geometric assumption that needs supporting evidence.
Image-empty visibility switches and a scale ruler do not make a perspective photo
orthographic. `perspective` returns `unsupported_projection`; calibrated-camera
triangulation, lens distortion and photogrammetry are outside this contract.

Registration inspection returns its source hash, stable reference identity,
revision, frame and current/stale state. Re-register explicitly after changing its
basis. `reference.calibrate` remains a scalar display calibration primitive; it
does not establish a projection registration by itself.

## Persist observations

`blender.reference.observation.set` declares up to 128 observations per call.
Each has a stable caller-selected ID, reference, pixel, optional label and
`sigma_pixels` (independent one-standard-deviation image-coordinate error), and
visibility (`visible` or `occluded`). The native document stores source dimensions,
packed-byte SHA256, source label, stable reference identity and a monotonic revision.
The response contains only the count and IDs. A scene supports 2048 observations
within a 2 MiB serialized budget. Replacing or deleting evidence invalidates its
derived outputs; deleting and recreating an ID never restores an old revision.

Inspection paginates with `names` selecting IDs. `mapped_points=true` additionally
returns reference-local and world **display-plane** coordinates. These are not
inferred object depth. Source images and existing reference display support visual
checking; derived outputs reuse the existing native landmark glyphs. Provenance
links each output to the exact observed source pixels and residuals.

## Derive landmarks

`blender.landmark.derive` accepts 1–32 named outputs. An `observations` source names
1–8 persisted observations of the same feature. Optional known-axis coordinate
planes constrain missing components in the chosen construction frame. A
`reflection` source mirrors an existing current landmark across an explicit
construction-axis plane. Reflected sources must be outside the current batch;
there is no arbitrary expression or matrix interface.

The adapter solves a bounded weighted linear system. It reports rank and maximum
per-observation metric residual, rejecting solutions above the requested tolerance.
Rank below three is `underconstrained`; no arbitrary missing coordinate is supplied.
Occluded observations do not constrain the solve. Unregistered or stale evidence
reports `registration_required` or `stale_dependency`. A contradictory full-rank
solution reports `inconsistent`; it does not create geometry.

Only solved outputs create/update the existing managed landmark objects. Existing
outputs from unsuccessful re-solves become stale while retaining their last
coordinates. Invalid requests and unexpected native failures roll back the batch.
A successful operation response may contain unsolved points: always read counts
and statuses. Native coordinate precision remains part of the reported result.

The default result is compact: solved/underconstrained/inconsistent/stale counts,
max/mean residual, frame/reference IDs, solver seconds and the worst eight results
(configurable 0–32, with explicit truncation). It omits raw XYZ. Uncertainty is
available only when all contributing scalar observations supply errors; it assumes
independence and exact registration/calibration. Unknown calibration error is not
estimated. Reflection currently reports no propagated uncertainty.

## Persistence and invalidation

Source records, registrations and derived provenance are saved in the native
document. No transcript or external geometry database is needed. Provenance records
the derivation, constraints, observations and revisions, source hashes, registration
bases, construction frame, result, residuals and conditional uncertainty.

Changes to observations, packed sources, source dimensions, image aspect/offset,
reference display size/transform, registration, scene measurement scale, construction
frame or reflected source make affected derived points stale. Source pixels themselves
remain unchanged after reference movement. Re-derivation is explicit; stale data is
never silently accepted. Literal redefinition clears derivation provenance.

`landmark.inspect(detail="summary")` omits coordinates and reports the selected
page's construction statistics. `detail="provenance"` returns at most eight
provenance records with an explicit truncation flag; use bounded names/pages for
more. Ordinary point inspection reports `derived`, `stale`, `valid`, residual and
observation IDs. Measurement consumption rejects stale derived points.

The existing native resource fingerprint includes reference construction evidence
and derived freshness. Core's existing project verification/invalidation observes
these changes; this capability adds no competing project dependency graph. A
fingerprint or solved status establishes neither visual accuracy nor acceptance of
the source assumptions.

## Errors and scope

Typed errors distinguish `observation_outside_source`, `observation_not_found`,
`source_image_unavailable`, `unsupported_projection`, `invalid_construction_frame`,
`construction_limit_exceeded`, invalid/ambiguous resource identities and corrupt
construction storage. Underconstraint, excessive residual and stale prerequisites
are per-output solve statuses, not successful trusted geometry.

Use justified planar/orthographic evidence. Automatic camera estimation, nonlinear
distance constraints, lens correction, feature recognition, deforming frames and
an unrestricted constraint language are intentionally unsupported.

See [reference-driven constructive forms](constructive_forms.md) for calibrated contour/section
authoring, smooth structural fusion, family shape interpolation, reference comparison
and native review packets.
