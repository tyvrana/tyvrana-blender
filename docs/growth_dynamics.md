# Root-bound secondary motion

`growth.dynamics.bake` runs Blender's bundled Hair Dynamics XPBD rod solver on
Tyvrana's deformed growth paths. This native solver is experimental in Blender
5.2. Settings explicitly control mass, stretchiness, bendiness, root bendiness,
linear/angular damping, gravity, friction, surface collision and up to eight
external mesh colliders. Smaller bendiness means greater bending stiffness.
Native curve radii participate in the solve. Existing curve interpolation and
instance/deformable-template output use the resulting paths.

This strategy fits flexible strands and narrow structures. It does not simulate
broad sheet torsion, strand self-collision or every possible growth material.
Mesh colliders use the native surface/edge contact solver. A successful solve is
not collision certification: inspect `growth.sample` and `geometry.inspect` on
representative frames and final template geometry. Subframe playback interpolates
cached positions and can cross colliders between samples.

## Observable bounded jobs

Bake returns a job ID. `growth.dynamics.status` reports completed frames, elapsed
time and a terminal result or error; the latest eight records last until reload.
`growth.dynamics.cancel` stops between native frame evaluations, discards staging
and restores frame/subframe/ranges. One native frame evaluation cannot be
interrupted. Source mutations are blocked through Tyvrana while a job runs.
Keep source inputs unchanged by other means too.

A bake covers 2–128 consecutive integer frames. Defaults bound each frame to
20,000 points, the complete cache to one million points, estimated solver work to
20 million point × frame × substep × constraint-step units, and elapsed execution
to 60 seconds. Explicit maxima are 50,000 points/frame, four million cached points,
100 million work units and 600 seconds. Each collision mesh is limited to 100,000
evaluated polygons. Solver work is an estimate, not a promise about engine memory
or execution time. The time budget is checked between frames.

## Durable playback and revision

A completed job atomically publishes an owned native position cache and geometry
node playback. Native simulation assets are transient. Cached data and playback
survive save/reopen without callbacks or external cache paths. `replace=true`
retains the old cache until successful publication; cancellation/failure preserves
it. `growth.dynamics.clear` removes exclusively owned cache resources and restores
the authored path. Clear before changing the growth recipe or removing the system.

Roots are projected back to the current attached surface path during playback,
including at subframes. Inspection separately reports `maximum_solver_root_error`
before that correction and the final attachment error. This matters because
native collision constraints can displace a pinned root slightly. Reproducibility
is tested for the same Blender build and unchanged fixture inputs, not promised
across solver/build/platform changes.

`growth.dynamics.inspect` reports settings, range, payload size/hash, displacement
and stale state. Typed geometry/motion edits conservatively invalidate all dynamics
caches and disable playback; presentation/render/material/light changes and frame
scrubbing do not. Changes outside Tyvrana require explicit clear/rebake and fresh
QA. Outside the baked range the authored path is used and inspection reports that
fact. Use frame samples for cached motion; pose sweeps require clearing the cache.
