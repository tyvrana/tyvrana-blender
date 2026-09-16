"Staged native groups and bounded barycentric rest-surface weight transfer."

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from . import deformation_geometry as geo
from . import retopo_geometry, rig, weights
from .weight_transfer_models import (
    GroupsConfigureArguments,
    GroupsResult,
    GroupState,
    WeightsTransferArguments,
    WeightsTransferResult,
)


def group_definitions(obj: Any) -> list[tuple[str, bool]]:
    return [(g.name, g.lock_weight) for g in obj.vertex_groups]


def publish(obj: Any, data: Any, definitions: list[tuple[str, bool]]) -> None:
    # Clearing group definitions mutates current mesh weights. A disposable empty
    # mesh keeps both original and staged deform data intact during publication.
    holder = bpy.data.meshes.new("Group publication")
    try:
        obj.data = holder
        obj.vertex_groups.clear()
        for name, locked in definitions:
            obj.vertex_groups.new(name=name).lock_weight = locked
        obj.data = data
    finally:
        if holder.users == 0:
            bpy.data.meshes.remove(holder)


@contextmanager
def staged_groups(obj: Any) -> Iterator[Any]:
    original = obj.data
    definitions = group_definitions(obj)
    candidate = obj.copy()
    candidate.data = original.copy()
    data = candidate.data
    published = False
    try:
        yield candidate
        publish(obj, data, group_definitions(candidate))
        published = True
        bpy.context.view_layer.update()
    except BaseException:
        publish(obj, original, definitions)
        raise
    finally:
        bpy.data.objects.remove(candidate, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
        if published and original.users == 0:
            name = original.name
            bpy.data.meshes.remove(original)
            data.name = name


def referenced(obj: Any, name: str) -> bool:
    if any(
        m.type == "ARMATURE" and m.object and name in m.object.data.bones
        for m in obj.modifiers
    ):
        return True
    if obj.data.shape_keys and any(
        k.vertex_group == name for k in obj.data.shape_keys.key_blocks
    ):
        return True
    if any(
        p.type == "STRING"
        and "vertex_group" in p.identifier
        and getattr(m, p.identifier) == name
        for m in obj.modifiers
        for p in m.bl_rna.properties
    ):
        return True
    return any(
        getattr(c, "target", None) == obj and getattr(c, "subtarget", None) == name
        for other in bpy.data.objects
        for c in other.constraints
    )


def configure(args: GroupsConfigureArguments) -> GroupsResult:
    obj = geo.context(args.object_name, edit=True)
    if len(obj.vertex_groups) > 256:
        geo.fail("Vertex group count exceeds 256")
    changed = []
    with staged_groups(obj) as candidate:
        for definition in args.groups:
            geo.native_name(definition.name)
            if definition.rename:
                geo.native_name(definition.rename)
            group = candidate.vertex_groups.get(definition.name)
            if definition.create:
                if group is not None:
                    geo.fail("Group already exists; edit it explicitly")
                if len(candidate.vertex_groups) >= 256:
                    geo.fail("Vertex group count exceeds 256")
                group = candidate.vertex_groups.new(name=definition.name)
            elif group is None:
                geo.fail("Group is missing; set create=true")
            if group.lock_weight and (
                definition.layers or definition.remove or definition.rename
            ):
                geo.fail("Unlock the group before editing its weights or identity")
            if (definition.remove or definition.rename) and referenced(
                obj, definition.name
            ):
                geo.fail("Group is referenced by deformation; preserve its identity")
            if definition.remove:
                candidate.vertex_groups.remove(group)
                changed.append(definition.name)
                continue
            for layer in definition.layers:
                chosen = sorted(geo.selection(obj, layer.selector))
                group.add(
                    chosen, layer.weight, "REPLACE"
                ) if layer.weight else group.remove(chosen)
            if definition.rename:
                if (
                    definition.rename != group.name
                    and definition.rename in candidate.vertex_groups
                ):
                    geo.fail("New group name already exists")
                group.name = definition.rename
            if definition.locked is not None:
                group.lock_weight = definition.locked
            changed.append(group.name)
    summaries = []
    for group in list(obj.vertex_groups)[:32]:
        count = sum(
            any(g.group == group.index and g.weight > 0 for g in v.groups)
            for v in obj.data.vertices
        )
        summaries.append(
            GroupState(
                name=group.name, locked=group.lock_weight, weighted_vertices=count
            )
        )
    return GroupsResult(
        object_name=obj.name,
        changed=changed,
        groups=summaries,
        total=len(obj.vertex_groups),
    )


def transfer(args: WeightsTransferArguments) -> WeightsTransferResult:
    start = time.perf_counter()
    source = geo.context(args.source)
    target = geo.context(args.target, edit=True)
    if source.data.shape_keys or target.data.shape_keys:
        geo.fail(
            "Transfer rest weights before corrective keys; shape-dependent "
            "remapping requires explicit recapture"
        )
    if source.data.animation_data or source.animation_data:
        geo.fail("Weight transfer reads static authored rest surfaces")
    if len(source.vertex_groups) > 256 or len(target.vertex_groups) > 256:
        geo.fail("Vertex group count exceeds 256")
    names = {g.source: g.target for g in args.groups}
    for name in names.values():
        geo.native_name(name)
    if len(set(names.values()) | {g.name for g in target.vertex_groups}) > 256:
        geo.fail("Result would exceed 256 vertex groups")
    if any(n not in source.vertex_groups for n in names):
        geo.fail("Source group is missing")
    if any(
        g.target in target.vertex_groups and target.vertex_groups[g.target].lock_weight
        for g in args.groups
    ):
        geo.fail("Preserve locked target groups; explicitly unlock before transfer")
    # If transferring deform groups, include the complete bone group set so
    # normalization cannot silently combine with untouched deform influences.
    if rig.KEY in target:
        arm = rig.binding_modifier(target).object
        deform = {b.name for b in arm.data.bones if b.use_deform}
        if deform & set(names.values()) and not deform <= set(names.values()):
            geo.fail("Transfer all deform groups together to preserve normalization")
    source.data.calc_loop_triangles()
    triangles = [tuple(t.vertices) for t in source.data.loop_triangles]
    if not triangles or len(triangles) > 200000:
        geo.fail("Transfer source requires 1..200000 authored triangles")
    transform = retopo_geometry.matrix(source)
    points = [
        retopo_geometry.checked_point(transform @ v.co) for v in source.data.vertices
    ]
    if args.mirror:
        axis = "xyz".index(args.mirror.axis)
        for p in points:
            p[axis] = 2 * args.mirror.origin[axis] - p[axis]
    tree = BVHTree.FromPolygons(points, triangles, all_triangles=True)
    groups = {source.vertex_groups[n].index: n for n in names}
    rows = [
        {groups[g.group]: g.weight for g in v.groups if g.group in groups}
        for v in source.data.vertices
    ]
    chosen = sorted(geo.selection(target, args.selector))
    matrix = retopo_geometry.matrix(target)
    result = {}
    distances = []
    for i in chosen:
        location, _, face, distance = tree.find_nearest(
            matrix @ target.data.vertices[i].co, args.max_distance
        )
        if face is None:
            geo.fail(
                "Target vertex exceeds max_distance; revise rest alignment or distance"
            )
        ia, ib, ic = triangles[face]
        a, b, c = points[ia], points[ib], points[ic]
        v0 = b - a
        v1 = c - a
        v2 = location - a
        d00 = v0.dot(v0)
        d01 = v0.dot(v1)
        d11 = v1.dot(v1)
        denom = d00 * d11 - d01 * d01
        if denom <= 1e-20:
            geo.fail("Nearest source triangle is degenerate; repair source topology")
        v = (d11 * v2.dot(v0) - d01 * v2.dot(v1)) / denom
        w = (d00 * v2.dot(v1) - d01 * v2.dot(v0)) / denom
        factors = [max(0, 1 - v - w), max(0, v), max(0, w)]
        total = sum(factors)
        factors = [f / total for f in factors]
        mixed = {
            out: sum(
                rows[j].get(n, 0) * f
                for j, f in zip([ia, ib, ic], factors, strict=True)
            )
            for n, out in names.items()
        }
        if args.normalize:
            mixed = weights.normalized(mixed, args.max_influences)
            if not mixed:
                geo.fail(
                    "Source correspondence has no transferable weight; repair coverage"
                )
        result[i] = mixed
        distances.append(distance)
    with staged_groups(target) as candidate:
        for name in names.values():
            group = candidate.vertex_groups.get(name) or candidate.vertex_groups.new(
                name=name
            )
            group.remove(chosen)
        for i, row in result.items():
            for name, value in row.items():
                if value > 0:
                    candidate.vertex_groups[name].add([i], value, "REPLACE")
    return WeightsTransferResult(
        source=source.name,
        target=target.name,
        selected_vertices=len(chosen),
        groups=len(names),
        max_distance=max(distances),
        mean_distance=sum(distances) / len(distances),
        normalized=args.normalize,
        processing_seconds=time.perf_counter() - start,
    )
