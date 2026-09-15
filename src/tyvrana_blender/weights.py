"""Native regional weight computation; one existing binding transaction publishes it."""

import struct
import time
from typing import Any

from mathutils import Vector  # type: ignore[import-not-found]

from . import mesh, modifiers, rig
from .mesh_models import MeshElementSelector
from .mesh_selectors import SelectionError, select
from .rig_models import ArmatureBindArguments, ExplicitWeights, Influence, VertexWeights
from .weight_models import (
    ConstantWeights,
    GradientWeights,
    WeightGroupSummary,
    WeightSample,
    WeightsAssignArguments,
    WeightsAssignment,
    WeightsInspectArguments,
    WeightsSummary,
)


def selected(bm: Any, selector: MeshElementSelector) -> set[int]:
    try:
        found = {v.index for v in select(bm, selector)}
    except SelectionError as exc:
        rig.fail(str(exc))
    if not found:
        rig.fail("Weight selection is empty; revise its bounds or indices")
    return found


def context(name: str) -> tuple[Any, Any, list[dict[str, float]]]:
    rig.idle()
    obj = modifiers.object_mesh(name)
    mod = rig.binding_modifier(obj)
    armature = rig.armature(mod.object.name)
    rig.binding_summary(obj)  # Bounds and invalid native values, before allocation.
    names = {b.name for b in armature.data.bones if b.use_deform}
    indices = {g.index: g.name for g in obj.vertex_groups if g.name in names}
    rows = [
        {
            indices[g.group]: g.weight
            for g in v.groups
            if g.group in indices and g.weight > 0
        }
        for v in obj.data.vertices
    ]
    return obj, armature, rows


def normalized(row: dict[str, float], maximum: int = 4) -> dict[str, float]:
    values = sorted(
        ((n, w) for n, w in row.items() if w > 1e-8), key=lambda v: (-v[1], v[0])
    )[:maximum]
    total = sum(w for _, w in values)
    return {n: w / total for n, w in values} if total else {}


def inspect(args: WeightsInspectArguments) -> WeightsSummary:
    obj, armature, rows = context(args.object_name)
    names = args.bone_names or [b.name for b in armature.data.bones if b.use_deform]
    if any(
        n not in armature.data.bones or not armature.data.bones[n].use_deform
        for n in names
    ):
        rig.fail("Weight inspection requires existing deform bones")
    with mesh.snapshot(obj) as bm:
        indices = sorted(selected(bm, args.selector))
    unweighted = [i for i in indices if not rows[i]]
    non_normal = [i for i in indices if abs(sum(rows[i].values()) - 1) > 1e-5]
    multiple = [i for i in indices if len(rows[i]) > 1]
    matching = {
        "all": indices,
        "unweighted": unweighted,
        "non_normalized": non_normal,
        "multiple": multiple,
    }[args.filter]
    groups = []
    for name in names:
        values = [rows[i].get(name, 0) for i in indices]
        group = obj.vertex_groups.get(name)
        groups.append(
            WeightGroupSummary(
                bone=name,
                locked=bool(group and group.lock_weight),
                weighted_vertices=sum(v > 0 for v in values),
                dominant_vertices=sum(
                    bool(rows[i])
                    and max(rows[i], key=lambda n: (rows[i][n], n)) == name
                    for i in indices
                ),
                minimum=min(values),
                maximum=max(values),
                mean=sum(values) / len(values),
            )
        )
    return WeightsSummary(
        binding=rig.binding_summary(obj),
        selected_vertex_count=len(indices),
        matching_vertex_count=len(matching),
        unweighted_vertex_count=len(unweighted),
        non_normalized_vertex_count=len(non_normal),
        multiple_influence_vertex_count=len(multiple),
        groups=groups,
        samples=[
            WeightSample(
                vertex=i,
                position_local=list(obj.data.vertices[i].co),
                weight_sum=sum(rows[i].values()),
                influences=[
                    Influence(bone=n, weight=w)
                    for n, w in sorted(rows[i].items(), key=lambda x: (-x[1], x[0]))[:4]
                ],
                influences_truncated=len(rows[i]) > 4,
            )
            for i in matching[: args.sample_limit]
        ],
    )


def assign(args: WeightsAssignArguments) -> WeightsAssignment:
    start = time.perf_counter()
    obj, armature, rows = context(args.object_name)
    rig.armature(armature.name, edit=True)
    names = {b.name for b in armature.data.bones if b.use_deform}
    if any(g.lock_weight for g in obj.vertex_groups if g.name in names):
        rig.fail("Unlock deform groups explicitly before regional weight editing")
    if (
        len(rows) * (len(args.layers) + len(names) * (args.smooth_iterations + 1))
        > 4_000_000
        or len(obj.data.edges) * 2 * args.max_influences * args.smooth_iterations
        > 4_000_000
    ):
        rig.fail("Regional weighting exceeds 4000000 vertex/layer/influence work units")
    before = [dict(row) for row in rows]
    affected: set[int] = set()
    counts = []
    with mesh.snapshot(obj) as bm:
        fixed = (
            selected(bm, args.fixed_selector)
            if args.fixed_selector is not None
            else set()
        )
        for layer in args.layers:
            indices = selected(bm, layer.selector)
            counts.append(len(indices))
            affected.update(indices)
            profile = layer.weights
            if isinstance(profile, ConstantWeights):
                a = normalized(
                    {i.bone: i.weight for i in profile.influences}, args.max_influences
                )
                b = a
            elif isinstance(profile, GradientWeights):
                a = normalized({i.bone: i.weight for i in profile.start_influences})
                b = normalized({i.bone: i.weight for i in profile.end_influences})
            else:
                for i in indices:
                    rows[i] = normalized(rows[i], args.max_influences)
                continue
            if any(n not in names for n in a.keys() | b.keys()):
                rig.fail("Weight profile references a missing or non-deforming bone")
            for i in indices:
                if isinstance(profile, GradientWeights):
                    axis = Vector(profile.end) - Vector(profile.start)
                    t = min(
                        1.0,
                        max(
                            0.0,
                            (bm.verts[i].co - Vector(profile.start)).dot(axis)
                            / axis.length_squared,
                        ),
                    )
                    if profile.interpolation == "smoothstep":
                        t = t * t * (3 - 2 * t)
                    rows[i] = normalized(
                        {
                            n: a.get(n, 0) * (1 - t) + b.get(n, 0) * t
                            for n in a.keys() | b.keys()
                        },
                        args.max_influences,
                    )
                else:
                    rows[i] = dict(a)
        # Synchronous adjacency averaging; unselected/fixed rows provide context.
        for _ in range(args.smooth_iterations):
            updated = list(rows)
            for i in affected - fixed:
                neighbors = [
                    edge.other_vert(bm.verts[i]).index
                    for edge in bm.verts[i].link_edges
                ]
                if not neighbors:
                    continue
                mixed = {n: w * (1 - args.smooth_factor) for n, w in rows[i].items()}
                for j in neighbors:
                    for n, w in rows[j].items():
                        mixed[n] = mixed.get(n, 0) + args.smooth_factor * w / len(
                            neighbors
                        )
                updated[i] = normalized(mixed, args.max_influences)
            rows = updated
    if not args.allow_unweighted and any(not row for row in rows):
        rig.fail(
            "Weights leave unweighted vertices; revise regions or explicitly allow it"
        )
    if any(len(row) > 4 or abs(sum(row.values()) - 1) > 1e-5 for row in rows if row):
        rig.fail(
            "Unselected weights are not normalized or exceed four influences; "
            "include them in a normalize layer"
        )
    bound = rig.binding_summary(obj)

    def stored(row: dict[str, float]) -> list[tuple[str, bytes]]:
        return sorted((n, struct.pack("<f", w)) for n, w in row.items())

    changed = sum(stored(a) != stored(b) for a, b in zip(before, rows, strict=True))
    result = bound
    if changed:
        # The full matrix is constructed locally once, never returned or sent over MCP.
        explicit = ExplicitWeights(
            method="explicit",
            vertices=[
                VertexWeights(
                    vertex=i,
                    influences=[Influence(bone=n, weight=w) for n, w in row.items()],
                )
                for i, row in enumerate(rows)
                if row
            ],
        )
        result = rig.bind(
            ArmatureBindArguments(
                object_name=obj.name,
                armature_object=armature.name,
                weights=explicit,
                modifier_index=bound.modifier_index,
                preserve_volume=bound.preserve_volume,
                allow_unweighted=args.allow_unweighted,
            ),
            prepared_rows=[list(row.items()) for row in rows],
        )
    return WeightsAssignment(
        binding=result,
        selected_vertex_count=len(affected),
        changed_vertex_count=changed,
        layer_vertex_counts=counts,
        fixed_vertex_count=len(affected & fixed),
        smooth_iterations=args.smooth_iterations,
        processing_seconds=time.perf_counter() - start,
    )
