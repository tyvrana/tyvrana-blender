# Mixed closed-chain couplings

A scalar coupling prescribes a mapping. A closed coupling instead solves selected
local channels to satisfy explicit geometric closures. Both belong to the existing
`blender.coupling.configure`, `inspect` and `remove` catalog interface. There is no
automatic anatomical inference or callback-driven simulation.

Configure a named entry in `coupling.configure.mechanisms`. Each variable reuses a
`TransformChannel`: `rotation` is a revolute XYZ Euler component in radians;
`location` is a prismatic component in native parent/bone-local Blender units.
`role: input` reads an existing channel, including an action or scalar coupling;
`role: solve` grants exclusive numerical ownership of an unkeyed, undriven channel.
Every variable has explicit minimum/maximum values. Scalar mappings can drive inputs
or downstream native relationships; they cannot also own solved channels.

Existing armature rest bones, parenting, fixed object-local attachment points and
native typed constraints supply rigid links, fixed anchors and local frames.
Constrained rotational bones retain their existing zero-translation/unit-scale
contract; a bounded carrier supplies prismatic motion through the existing location
constraint path. Scale is never a mechanism variable. Author fixed links without
external deformation and request existing length/deformation QA where relevant.
No duplicate joint or constraint representation is introduced.

Each named closure identifies two shared typed point sources (`a`, `b`), which must
coincide in world space. Sources may reference object-local attachment points, bone
endpoints, landmarks or fixed world anchors. Several closures can express one
coupled loop or multiple connected loops in the same solve. Coincident triples can
also constrain orientation by using multiple separated attachment points. Redundant
closure equations are permitted when the solved-variable Jacobian retains rank.

## Bounded numerical evaluation

`blender.coupling.solve` accepts a configured name. The default `apply: false`
returns a candidate with scene state restored. `apply: true` commits only a qualified
solution; failure restores the original state. The solver never creates keyframes,
assigns actions or installs arbitrary driver expressions.

The algorithm is deterministic, projected damped least squares with a bounded
backtracking search. Finite differences evaluate the native constrained geometry;
variables are normalized by their declared ranges. Steps and candidates stay within
bounds. No random restart, global search or competing-branch minimization occurs.

Results include attempted channel values, maximum and per-closure Euclidean
residuals, range/native-constraint residual, active limits, normalized Jacobian condition/rank,
iterations and evaluation count. Failed candidate values are diagnostics and were
not committed. Statuses distinguish:

- `SOLVED`: closure tolerance and numerical/continuity guards passed.
- `INFEASIBLE`: the requested input violates its declared range.
- `LIMIT_BLOCKED`: bounded local solving cannot close at an active limit.
- `UNDERCONSTRAINED`: fewer scalar closure equations than solved variables.
- `SINGULAR`: locally deficient rank or excessive condition number.
- `NO_CONVERGENCE`: bounded local search did not qualify a solution.
- `BRANCH_DISCONTINUITY`: channel-step or tangent-orientation continuity failed.

A local failure does not establish global mathematical infeasibility. Locally
rank-deficient evidence cannot distinguish every structural underconstraint from
a special singular configuration; it never returns a fabricated valid solution.
Native binary32 transforms limit useful tolerances, especially far from the origin.

| Bound | Maximum |
| --- | ---: |
| Mechanisms per configure / scene | 8 / 16 |
| Variables / solved variables | 16 / 12 |
| Closures / scalar closure equations | 12 / 36 |
| Iterations per solve | 64 |
| Native evaluations per single solve | 4096 |
| Native evaluations per motion call | 50000 |
| Returned mechanism worst samples | 16 |

Default tolerance is 0.00001 world Blender units; default maximum condition is
10000. Exhausting the evaluation budget fails explicitly and restores state.

## Range sampling and continuity

Pass `mechanism: "Name"` to canonical `blender.motion.sample`. Existing actions
supply input values at each ordered sample. The solver uses the previous qualified
configuration as its next seed. A variable's `continuity_limit` bounds change as a
fraction of its configured range (default 0.2). A change of orientation between the
previous and candidate Jacobian tangents also rejects a candidate. Poor conditioning
is rejected even when closure residual is small. Rejected samples retain the last
qualified continuation state; there is no silent reseeding onto another branch.

These are sampled local continuity guards, not a proof of branch identity between
arbitrarily separated times. Use a sufficient sample density and meaningful ranges.
Large intentional steps may correctly require a denser request.

The mechanism is solved before existing contact, joint, coupling, length and rigid
geometry diagnostics. Unsolved frames skip those downstream diagnostics, so contact
counts cover only qualified frames. The mechanism aggregate separately reports all
solved/failed/uncertain frames, maximum closure/range residual, conditioning,
singularities, branch rejections, total work and bounded worst samples. Default
output has no per-frame transform table. Explicit detailed frames remain bounded.
If mesh deformation comparison requires a reference state, that reference must
solve; otherwise the request fails without publishing an invalid baseline.

Sampling restores frame/subframe, supported native channels and unrelated animated
owners after success or failure. The existing 256 mechanics-dependency bound is
independent of unrelated static scene inventory; other motion limits remain.
No per-frame client call or manually authored intermediate XYZ table is required.

## Inspection and lifecycle

`coupling.inspect` pages scalar and closed relationships together. Closed summaries
provide definitions/types/ranges, current native values, active limits and closure/range/native-constraint residuals.
The last committed solution carries condition, rank, branch and status information
only while a fingerprint of the current variable/closure state still matches.
After external changes it is unqualified until solved again. Read-only inspection
does not secretly run a numerical solve. Native object pointers preserve references
across rename/save/reopen; removed owners fail explicitly rather than rebinding to
another object with the same name. Remove the coupling before deleting its resources.

The native and MCP qualification fixtures cover a 33-sample slider-crank, a rotated
offset linkage solving rotation and translation together, an inverse slider-driven
linkage solving two rotations, incompatible limits, near-toggle
conditioning, branch rejection, injected failures and a 324-object scene.
