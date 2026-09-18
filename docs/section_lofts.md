# Editable section lofts

Use `loft.create` for a batch of structural or organic components whose shape needs
independent cross-section dimensions. Use `curve.create` for native Bezier/NURBS
curves, ordinary bevel profiles and evaluated curve attachments.

Each component has two to 64 sections: center, positive/negative X radii,
positive/negative Z radii and twist around its local tangent. A linear or
Catmull–Rom centerline interpolates centers; dimensions and twist interpolate
linearly. The initial X reference is projected perpendicular to the tangent and
transported between sections to reduce unintended roll. Collinear/collapsed frames
are rejected with repair guidance. Closed flat caps are optional. These are
ordinary native meshes with quads between section rings, suitable for modifiers,
weights, binding, sculpting and downstream topology work; no anatomical meaning
or physical validity is inferred.

A batch admits 64 components, 1024 authored sections and 131072 generated vertices.
Each ring has 4–64 sides and each span 1–16 subdivisions. Input parameters remain
on the object, with a stable component UUID. Inspection returns counts, local
bounds and base-mesh integrity; full section details are opt-in and bounded.

`loft.configure` replaces section specifications while keeping section count,
sides, subdivisions and caps fixed. Object/mesh identity, ordered connectivity,
weights, UVs, materials, modifiers and collection membership are preserved. Shared
mesh data, shape keys, data animation and externally modified base coordinates or
connectivity are rejected. Failed batches restore all authored coordinates and
metadata. Topology-changing or freeform refinement should use the mesh tools;
parametric regeneration cannot overwrite those edits. Self-intersection and
surface/deformation fitness require geometry QA and visual inspection.
