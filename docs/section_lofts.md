# Editable section lofts

Use `loft.create` for a batch of structural or organic components whose shape needs
independent cross-section dimensions. Use `curve.create` for native Bezier/NURBS
curves, ordinary bevel profiles and evaluated curve attachments.

Each component has two to 64 sections: center, positive/negative X radii,
positive/negative Z radii and twist around its local tangent. A linear or
Catmull–Rom centerline interpolates centers; dimensions and twist interpolate
linearly. The initial X reference is projected perpendicular to the tangent and
transported between sections to reduce unintended roll. Collinear/collapsed frames
are rejected with repair guidance. Closed caps are optional. Signed end depths
add concentric support rings: positive depth recesses an end, negative depth makes
it convex. Named radial features provide smooth compact processes, ridges and
grooves using normalized longitudinal position, section angle and finite widths.
Their strength is displacement in local units; a groove cannot collapse a section.
These are
ordinary native meshes with quads between section rings, suitable for modifiers,
weights, binding, sculpting and downstream topology work; no anatomical meaning
or physical validity is inferred.

A batch admits 64 components, 1024 authored sections and 131072 generated vertices.
Each ring has 4–64 sides and each span 1–16 subdivisions. Input parameters remain
on the object, with a stable component UUID. Inspection returns counts, local
bounds and base-mesh integrity; full section details are opt-in and bounded.

Sections may have stable names. `loft.configure` accepts selected `section_edits`
by name or full sections, plus selected component settings and an optional
`expected_revision`. Omitted values persist. It keeps section identities/count,
sides, subdivisions and caps fixed. Object/mesh identity, ordered connectivity,
weights, UVs, materials, modifiers and collection membership are preserved. Shared
mesh data, shape keys, data animation and externally modified base coordinates or
connectivity are rejected. Failed batches restore all authored coordinates and
metadata. Topology-changing or freeform refinement should use the mesh tools;
parametric regeneration cannot overwrite those edits. Self-intersection and
surface/deformation fitness require geometry QA and visual inspection.

End support-ring count also stays fixed. Feature/end-bearing construction receives
closed-surface checks before publication. Cap regions `start_cap` and `end_cap`
and named section/feature regions can be used by geometry-point measurements and
placement. Region projection is a surface estimate, not an inferred physical center.
Use [structural assemblies](structural_assemblies.md) for repeated varied families,
independent mirrored members and batched landmark-based placement/fitting.
