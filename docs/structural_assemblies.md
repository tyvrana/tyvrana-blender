# Structural families and assembly

`assembly.create` combines the existing loft and connected-surface generators.
Templates define sparse shape controls; families place independent members along
an authored polyline at equal arc-length intervals. Scale, Euler rotation and
feature strength interpolate between endpoints. Named member overrides handle
exceptions. A family may mirror another same-count family across a plane while
retaining independent geometry, identities and subsequent asymmetric edits.

One request admits 16 templates, 32 families, 64 mesh components, 131072 generated
vertices and 256 KiB of constraints. Each assembly has an empty root, family
empties and named mesh children. Construction metadata lives with native objects;
it does not replace the core semantic project model. Generated object names must
fit Blender's 63-byte limit without truncation or collision.

`assembly.configure` requires the expected assembly revision. Supply selected
templates, complete selected family rules, or sparse indexed member edits. Omitted
rules and exceptions persist. Membership counts and construction kinds remain
fixed. Named loft sections and surface nodes, openings, features and thickness
support local corrections. Changes with identical ordered connectivity preserve
mesh data; changed tessellation requires explicit `topology_policy="rebuild"` and
is rejected when it would discard protected downstream data. Externally edited
geometry is protected. Creation and revision stage the whole batch and roll back
on failure. Use assembly revision for managed members; editing them independently
invalidates the family construction.

Revision responses omit unchanged inventory: they return totals, all changed names
and at most 16 changed rows, with an explicit truncation flag. Read additional
inventory through filtered/paged `assembly.inspect` when needed.

Paths and placement targets use world coordinates. Keep organizational root and
family transforms at identity when revising; move members with placement rules.
Renamed/missing members produce explicit identity errors. Arrays are finite
authored structures, not live simulation, implicit joints or automatic topology.

## Placement and dimensional fitting

`object_set.place` accepts up to 64 distinct placements or stored-rule refreshes.

- `between`: align a local axis between two point sources and fit its bounding end
  planes. Optional dimensions fit the other local axes. This measures axial
  extent, not curved centerline length.
- `frame`: align a local axis/up direction and a local point or geometry-region
  anchor with a target point and world orientation.
- `fit`: deterministically fit selected local-axis extents without rewriting mesh
  coordinates; null dimensions remain unchanged.
- `mirror`: reflect an existing component's world transform from another object.
  This does not create a copy; mirrored assembly families do.

Points reuse measurement sources: world/object points, landmarks and named
geometry regions. Geometry points estimate a normalized local region-bounds
position and optionally project to nearby authored triangles; an optional offset
follows the projected face normal in local units. No anatomical or joint-center
meaning is inferred. `landmark.derive` can persist the same estimate with source
identity and geometry/transform freshness.

Rules persist but are static: refresh after upstream changes. Dependency ordering,
cycles, stale landmarks, collapsed axes, unsupported hierarchy and shear are
checked before commit. Failed batches restore matrices and rules. A no-op refresh
does not increment component placement revisions. `object_set.inspect` with the
`placement` field reads constraint freshness and fitted dimensions compactly.

## Inspection and cleanup

`assembly.inspect` returns totals and 16 member rows by default, at most 64. Filter
families and paginate; use `limit=0` for totals or opt into the sparse specification.
Rows contain resource/member IDs, construction revision, bounds, counts, region
handles and mirrored provenance. Region lists are capped at eight with their total
count. Construction validity does not establish evaluated or visual quality.

Existing `measurement.inspect` batches target/actual/tolerance comparisons; existing
`geometry.inspect` provides bounded topology, degeneration, clearance, contact,
containment and orientation diagnostics. Inspect relevant pairs and regions after
revision. Floating-point surface sampling is not exhaustive collision certification.

`mesh.cleanup` stages up to 32 exclusive meshes, bounded by two million total mesh
elements. Explicit operations include nearby-vertex merging, duplicate/degenerate
and loose removal, normal consistency, optional small-island removal, bounded hole
filling and ngon triangulation. Holes are preserved by default. Preview computes
the same proposed result without publishing. Remaining non-manifold geometry or
requested self-intersection/closedness failures reject the batch. Clean managed
meshes are no-ops. Actual repair of managed geometry requires explicit
`detach_construction`, preserving object/resource identity while invalidating
generator revision. It does not automatically resolve intersections, redesign
high-valence regions or produce deformation topology; revise responsible controls
or select an appropriate remesh workflow explicitly.

## Multiview diagnostics

Use the existing `render.image` operation with `inspection={}` for seven automatically
framed Workbench views in one PNG contact sheet. Select objects and up to 12 named
views, including per-view object subsets for regional closeups. Width and height
are per-tile dimensions, at most 1024; pixel/memory budgets bound the complete
sheet. Result metadata gives final dimensions and each tile's row/column/name.
Camera, visibility, render/display settings and helper data are restored on success
and failure. Diagnostic rendering works in background and interactive hosts.

This mode intentionally uses neutral Workbench shading for structural inspection.
Use normal material-aware rendering for appearance. The existing bounded render
job, cancellation, artifact and wait contracts remain canonical. Retrieve each
completed image once; use reference delivery/export for large artifact retention.

Mirrored families inherit the source member count when omitted. Family revisions
patch only supplied fields by ID; omitted layout, progression and exceptions
survive. Explicit replacement arrays replace that array; `members` edits patch
individual exceptions and their named shape handles.

Geometry QA accepts up to 128 objects or pairs in one request within its shared
128 query/frame, 256 finding, vertex, triangle and triangle-test budgets. Use a
small `worst_limit` for broad inspection and request detailed failures separately.
