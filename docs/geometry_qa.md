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
summaries and 256 detail findings. Per-sample evaluated geometry is bounded to one
million vertices and 250,000 triangles. `max_triangle_tests` defaults to 200,000,
up to two million per request. Exceeding a budget fails explicitly without a partial
clearance verdict. Results report counts, extrema, worst frame, bounded details,
work counters and native processing time. Frame/subframe are restored on failure
as well as success. Coverage between requested frames is unknown.
