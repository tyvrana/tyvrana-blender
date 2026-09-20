# Reference-driven constructive forms

Irregular form needs reference fidelity as well as valid geometry. Inspect the
source imagery, construct the major contours and sections, review closeups, refine
local features, and compare again. Dimensions and manifold topology alone do not
establish shape quality. Finish this loop before production topology and binding.

## Construction and refinement

`form.create` batches up to32 named editable forms in an incremental atomic job.
`form.configure` uses the same job lifecycle. Both return promptly with a job ID;
read `form.status` until completed before using the resulting geometry. Only job
status/cancellation and extension identity are available during preparation.
`form.cancel` discards uncommitted meshes and preserves prior forms. The latest
four job records persist until host reload. Each form combines named parts:

- `sections`: parallel registered planar foreground masks, ordered along their
  common oriented normal. Monotone cubic interpolation of signed contour fields
  joins observed sections; the first and last sections cap the volume.
  When every part mask is also declared in `surface_fit`, existing fitting masks
  in the same registered image stack fill missing internal section planes before
  volume sampling. This reuses supplied evidence without extending caps, mixing
  image frames, changing voxel resolution or requesting additional references.
  For a single whole-form stack with such gaps, two mutually perpendicular
  supplied section stacks can corroborate missing material between planes.
  Both must place it inside their observed volumes; a continuous taper vanishes
  on every primary section plane. Caps and supplied primary contours stay fixed.
- `silhouettes`: intersect registered orthographic foregrounds within explicit
  modeling bounds. This gives a visual hull; invisible concavities require section
  evidence or local refinement. At least two view directions are required.
- `loft` and `surface`: reuse the existing typed section-loft and patch-network
  specifications for shafts, branches, curved plates, openings and local features.
- `ellipsoid`: a positioned, rotated local feature for rounded additions, paired
  lobes, depressions, sockets or recesses.

Parts add, subtract or intersect material in declaration order. A positive `blend`
sets a local smooth Boolean radius. Vary section profiles, branch-root dimensions
and blend radii to shape transitions. These are approximate distance-field blends,
not a promise of exact tangent or curvature continuity. No arbitrary expressions,
code, topology arrays or external inference models are accepted.

Meshing uses Blender's bundled OpenVDB. `voxel_size` controls object-space sampling;
allow several samples across the smallest required opening, wall or groove.
`adaptivity` trades mesh density for approximation. `smoothing_passes` (default1,
0..8) applies a small separable volume filter before meshing to reduce sampling
ripples; each pass has standard deviation0.707 voxel. Verify thin features and
local dimensions after smoothing; zero preserves the unfiltered field. Bounds apply to voxel work,
input geometry, generated vertices and batched work. Reduce sampling density or
explicitly adjust bounded budgets when a request exceeds them; there is no silent
quality downgrade. A source or output that disappears at the chosen resolution
fails or must be caught by reference comparison and visual review.
Section sampling bounds use observed foreground extents, padded for pixel
uncertainty and swept across the section range; empty image margins do not consume
the volume budget.

Optional `surface_fit` refines the constructed surface against complementary
registered planar contours. It uses bounded local quadratic patches, with an
explicit neighborhood `radius`, total `max_distance` and up to6 iterations.
Smaller neighborhoods preserve narrow features; larger ones suppress sampling
waves but can round folds. Radius must resolve at least one construction voxel.
The fit changes vertex positions, not missing openings or connectivity. Select
appropriate section evidence for the starting surface, then compare the result.
For folds surrounded by flatter surfaces, `adaptive_neighborhood` narrows the
local radius where observation normals vary. It considers up to512 nearby contour
points instead of192 while retaining the same point, vertex-iteration and
displacement bounds. This costs more fitting time and can leave sparse regions
unsupported; it does not replace adequate reference coverage or close review.

Fitting accepts up to96 distinct mask references and262144 contour observations,
with a1048576 vertex-iteration work bound. Summaries report movement, vertices
that reached the movement limit, and unsupported regions with up to8 sample
positions. A coherent displacement field extends through unsupported vertices;
wholly unsupported fits fail. These diagnostics and source freshness are not visual
acceptance.
Fitting runs inside the atomic cancellable form job and retains source hashes.
In `form.configure`, omit `surface_fit` to retain it or pass null to remove it.

`form.configure` patches named parts, adds/removes local features and changes
sampling with an expected revision. Omitted parts remain. Object identities,
transforms and material slots persist; regenerated mesh connectivity may change.
Shared meshes, downstream vertex edits, modifiers, groups, UVs, shape keys,
custom attributes and face material assignments are protected from regeneration.
Use mesh tools after that transition. Failed batches restore prior objects/data.

For a local feature that smoothing or fitting rounds away, `sculpt.stroke` can use
`image_path` instead of object-space samples. Copy an inspection tile's `view`
framing and specify a bounded path of normalized `u`/`v` positions in that view
(rightward/downward from its upper-left corner). Brush radius remains in object
units. The adapter projects every sample onto the named object's current evaluated
surface before starting the stroke; a missed sample rejects the entire path before
sculpting. Other objects do not occlude this explicit target. Use an interactive
View3D host and the normal sculpt topology/modifier requirements. This framing is
not a geometry freshness assertion: review the current image, refine, then render
again to verify both the feature and its transitions.
Scene picking bounds surface objects and instancers rather than unrelated
reference-image empties, cameras or lights; its geometry and dependency guards
still apply in reference-heavy projects.

For nondestructive finishing across components, `modifier.create_batch` atomically
adds one supported typed modifier per object, up to64 objects. Settings remain
individual and the response contains only the created modifiers. A failure removes
all additions in that batch. Use `object_set.place` for batched dimensional fitting
or datum alignment; the adapter performs the placement math.
Corrective Smooth's `only_smooth` option performs surface fairing without detail
restoration and permits constructive modifiers before it. Inspect local features
after finishing; smoothing can erase intended shape.
The `datums` placement rule maps local origin and unit X/Y/Z axis points to four
measured points. It supports orthogonal scaled and mirrored frames, rejects shear
or collapsed axes, and retains landmark dependencies for explicit refresh.

`form.inspect` returns identities, revisions, bounds, part handles, work counts
and mesh/reference freshness. Full specifications are opt-in and bounded. Source
changes mark existing evidence stale; inspect and explicitly re-register/rebuild.

## Calibrated evidence

Import image bytes with `image.create_from_artifact`, place references and register
known scale, axes, origin and projection with `reference.register`. Use read-only
`image.preview` for up to16 packed/loaded images in one bounded artifact, preserving
color interpretation, file paths and calibration; tile metadata identifies sources.
A section is a
physical planar cut; an orthographic image is a projection. Display placement alone
is not calibration. Perspective photographs remain qualitative corroboration.

Masks explicitly select alpha or luminance above a threshold, optionally inverted.
The foreground must have a background margin. This is deterministic foreground
selection, not segmentation or interpretation of a photograph. Sources must be
unchanged packed RGBA images within4 megapixels; sampling uses at most512 pixels
on the long edge. Registration/source hashes remain attached to the generated form.

`reference.compare` compares selected evaluated meshes to registered section or
orthographic masks. Each query names its own object subset. It reports foreground
intersection-over-union, disagreement fraction, symmetric boundary mean/95th
percentile/maximum, worst locations and sampling uncertainty in scene units.
Planar occupancy requires closed manifold surfaces. The optional compact overlay
shows agreement in gray, missing material in green and excess material in magenta.

The reported sampling floor excludes source distortion and unprovided calibration
error. Pixel boundaries approximate continuous contours; a zero sampled deviation
is not exact geometric equality. Out-of-frame authored material is flagged, and
uncalibrated perspective evidence never receives invented metric accuracy. Use
measurements and geometry QA for dimensions, landmarks, connectivity and collision
checks; use independent closeup review for visible morphology.

## Families

`assembly.create` accepts form, loft and surface templates. Family `morphs` are
ordered normalized positions referencing related templates. Corresponding numeric
shape handles interpolate between keys; construction kind, semantic handles and
discrete settings must match. Indexed exceptions apply after interpolation, and
mirrored families inherit the varied source geometry. This varies shape independently
of placement, rotation and scale. Loft templates with matching section/ring settings
retain corresponding connectivity; volumetric form outputs can retessellate.

Use `members.shape.form_parts` for local form exceptions. `assembly.configure`
keeps membership and identities; topology changes require an explicit rebuild and
cannot discard downstream data. The default assembly vertex budget remains131072;
`max_vertices` permits an explicit bound up to1048576 for detailed assemblies.

## Review packets

`render.image` with `inspection` automatically frames cardinal, perspective and
regional views in consistent neutral Workbench shading. Optional per-view `focus`
frames normalized bounds within selected geometry without changing the mesh.
Add `inspection.packet`
with a native output directory for per-view PNGs, `00_overview.png` and a checksummed
`manifest.json`; the normal artifact response contains only a compact preview.

Packet views support a long edge up to2048 pixels. Set `budget.max_total_pixels`
for every native view plus the overview; budget rejections report the required
pixel total. `max_artifact_bytes` bounds aggregate
saved output. Up to24 views are supported. Per-view selections default to128
objects, with an explicit `max_objects` bound up to256. Unrelated scene objects
do not count. Wireframe views select up to32 meshes, sharing a default8192-edge
budget; explicitly allow up to65536 evaluated edges for denser regions.
For dense closeups, `focus` also filters temporary wire faces before the edge
budget. Whole-source evaluation limits still apply. Direct wire rendering accepts
an equivalent world-space `region`; no authored topology is cut or simplified.

A new directory is published only after all views succeed. Overwrite is opt-in
and requires an intact Tyvrana packet without unrelated or modified files. Failure
or cancellation preserves previous output. The manifest records view names,
projection/camera matrices, selections, resolution, bytes and hashes. Rendering
restores scene settings, cameras, visibility and temporary display geometry.

For lit Cycles diagnostics, inspect `render.devices` and explicitly select
`cycles.device="gpu"` when the configured host GPU is appropriate. A requested
unavailable GPU fails instead of falling back to CPU. Results report requested and
effective device, compute backend and enabled device names. The default explicit
Cycles option remains CPU; omitting Cycles options uses scene settings. Workbench
inspection does not use Cycles device options.
