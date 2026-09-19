# Geometry QA

`blender.geometry.inspect` measures evaluated mesh surfaces, including explicit
sampled animation frames. It is a diagnostic tool, not continuous collision
certification. Use `pairs` for inter-object queries, `objects.self_intersection`
for self-contact, and `objects.reference` for same-ordered-topology comparison.

Distance uses triangle vertex/face, edge/edge and edge/face tests, with an AABB
hierarchy to reject distant candidates. Distances at or below `tolerance` count as
contact triangle pairs; counts are not penetrating volume or unique contact regions.
Pair exemptions identify face sets on BOTH surfaces and exclude matching pairs.
Self-contact excludes triangles sharing a vertex; adjacent folding requires
additional deformation checks. Bounded findings include source face IDs and closest
world-space points.

Containment tests one representative per connected component against a closed,
consistently oriented noncontact surface using winding. It assumes nonselfintersecting
surfaces and does not prove arbitrary solid union/intersection. Exempted pairs do
not receive containment verdicts. `null` means not evaluated, not clear.

Reference comparison requires identical ordered connectivity. Normals and areas
are compared in object-local space to exclude rigid object rotation. Normal reversal
alone does not prove local inversion. Collapsed triangle ratios and signed-volume
reversal supplement the diagnosis; signed volume is reported only for closed,
consistently oriented surfaces. Degenerate elements are reported independently.

Limits: 16 objects and 16 pairs; 64 explicit frames; at most 128 query/frame
summaries and 256 detail findings. Ordinary surface evaluation is bounded to one
million vertices per request and 250,000 triangles per sample. `max_triangle_tests` defaults to 200,000,
up to two million per request. Exceeding a budget fails explicitly without a partial
clearance verdict. Results report counts, extrema, worst frame, bounded details,
work counters and native processing time. Frame/subframe are restored on failure
as well as success. Coverage between requested frames is unknown.

## Local volume-orientation proxies

For a topology-matched `reference`, each vertex with a full-rank reference one-ring
receives a least-squares affine deformation gradient. Its determinant is a signed
local volume ratio, invariant under rigid rotation. Negative values and absolute
ratios below `collapsed_volume_ratio` are counted, with minimum and worst vertex IDs.
Planar or ill-conditioned neighborhoods are explicitly counted as unavailable.
This can expose localized inversion hidden by positive total volume. It remains a
surface-neighborhood proxy, not a volumetric-element/FEM inversion certificate.

## Shared instances and templates

`instances` queries name a source with native mesh instances or an owned growth
system containing mesh templates. They optionally check inter-element contacts and
up to eight ordinary polygon-surface `obstacles`. Shared prototypes and conservative
element bounds are inspected first; only candidate element vertices/triangles are
expanded. Owned growth reads Blender's evaluated curve samples, normals, tangents
and lengths, including broad-template bending. Full growth realization is unnecessary.
Mixed bare-curve/template growth must be separated for this surface query.

Summaries report element/prototype counts, equivalent and transformed vertices,
tested elements, candidate/contact pairs, body containment, minimum distance and
worst-N contacts. Contacts identify stable growth root IDs or native persistent
instance paths, prototypes, layers and source faces. Native identities depend on
unchanged generating topology. Degeneration totals cover tested elements; they do
not certify unexpanded elements or internal self-contact within one prototype.
Layer/overlap metadata never exempts physical penetration automatically.

Bounds: eight instance queries,50,000 elements/source,250,000 shared prototype vertices,
250,000 transformed candidate vertices/request by default (up to2,000,000),800,000
native path points/sample and2,000,000 path points/request. Triangle work shares the
existing request budget. Native dependency-graph allocation precedes these checks.

## Fractional and adaptive sampling

`frames` accepts fractional values. Alternatively, `adaptive` supplies `start`, `end`,
`initial_samples`, `max_samples`, `minimum_step`, `near_clearance` and `change_ratio`.
Every initial interval receives a midpoint. Wide intervals are visited first;
near-clearance, metric changes, sign changes and midpoint extrema trigger further
midpoints until step/sample limits. Output includes actual sample times, maximum gap,
remaining risky intervals and whether the sample budget stopped refinement.

This is deterministic sampled QA. A narrow event between all sample times can still
be missed; a zero risky-interval count is not continuous clearance certification.
The worst frame identifies minimum clearance, or minimum local Jacobian for reference
queries without clearance. A single batched request restores the original time on
success and failure.
