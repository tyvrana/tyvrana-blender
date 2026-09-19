"""Atomic broad-template offset caches in the native connected growth graph."""

import hashlib
import json
import math
import time
from array import array
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import growth, growth_dynamics, growth_nodes, organization
from .growth_layers_models import (
    LayerCache,
    LayerCorrectArguments,
    LayerObjectArguments,
)

KEY = "_tyvrana_growth_layers"
CACHE = "Layer Offset Cache"


def raw(obj: Any) -> dict[str, Any] | None:
    value = obj.get(KEY)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 32768:
        growth.fail("Layer cache metadata is invalid; preserve external edits")
    result = json.loads(value)
    if not isinstance(result, dict):
        growth.fail("Layer cache metadata is invalid")
    return result


def cache_object(group: Any) -> Any:
    node = group.nodes.get(CACHE)
    return node.inputs["Object"].default_value if node else None


def inspect(args: LayerObjectArguments) -> LayerCache:
    obj, _, _, group = growth.owned(args.object_name)
    meta = raw(obj)
    if meta is None:
        return LayerCache(object_name=obj.name, cached=False, valid=True)
    result = LayerCache.model_validate(meta["result"])
    cache = cache_object(group)
    issues = []
    if cache is None or cache.get(KEY + "_owner") != obj or cache.data.users != 1:
        issues.append("Layer cache is missing or shared")
    elif growth_dynamics.points_hash(cache.data) != result.cache_sha256:
        issues.append("Layer offsets changed externally")
    if meta.get("stale_reason"):
        issues.append(meta["stale_reason"])
    return result.model_copy(update={"valid": not issues, "issues": issues})


def owned_cache(obj: Any, group: Any) -> Any:
    cache = cache_object(group)
    if (
        group.users != 1
        or cache is None
        or cache.get(KEY + "_owner") != obj
        or cache.data.users != 1
    ):
        growth.fail(
            "Layer cache ownership is damaged/shared; preserve external resources"
        )
    users = bpy.data.user_map(subset={cache, cache.data})
    if (users.get(cache, set()) | users.get(cache.data, set())) - {
        cache,
        group,
        bpy.context.scene,
        *cache.users_collection,
    }:
        growth.fail("Layer cache has external users")
    return cache


def disconnect(group: Any, meta: dict[str, Any]) -> None:
    output = next(n for n in group.nodes if n.type == "GROUP_OUTPUT")
    source = group.nodes[meta["source"]].outputs[meta["socket"]]
    group.links.new(source, output.inputs["Geometry"])
    for name in meta["nodes"]:
        group.nodes.remove(group.nodes[name])


def remove_cache(cache: Any) -> None:
    data = cache.data
    bpy.data.objects.remove(cache, do_unlink=True)
    bpy.data.meshes.remove(data)


def clear(args: LayerObjectArguments) -> LayerCache:
    organization.idle(mutate=True)
    obj, _, _, group = growth.owned(args.object_name)
    meta = raw(obj)
    if meta:
        cache = owned_cache(obj, group)
        disconnect(group, meta)
        remove_cache(cache)
        del obj[KEY]
        group[growth.KEY + "_signature"] = growth_nodes.signature(group)
        bpy.context.view_layer.update()
    return LayerCache(object_name=obj.name, cached=False, valid=True)


def playback(
    group: Any, cache: Any, args: LayerCorrectArguments, count: int
) -> dict[str, Any]:
    g = growth_nodes.GrowthGraph.__new__(growth_nodes.GrowthGraph)
    g.group = group
    before = set(group.nodes)
    output = next(n for n in group.nodes if n.type == "GROUP_OUTPUT")
    link = output.inputs["Geometry"].links[0]
    source = link.from_socket
    meta = dict(source=link.from_node.name, socket=source.identifier)
    info = g.info(cache, CACHE, "ORIGINAL")
    frame = g.node("GeometryNodeInputSceneTime").outputs["Frame"]
    fraction = g.math(
        "MULTIPLY",
        g.math("SUBTRACT", frame, args.frame_start),
        (args.samples - 1) / (args.frame_end - args.frame_start),
    )
    lower = g.math("FLOOR", fraction, 0)
    blend = g.math("SUBTRACT", fraction, lower)
    index = g.node("GeometryNodeInputIndex").outputs["Index"]
    position = g.node("GeometryNodeInputPosition").outputs["Position"]
    samples = []
    for step in (0, 1):
        sample = g.node("GeometryNodeSampleIndex")
        sample.data_type = "FLOAT_VECTOR"
        sample.domain = "POINT"
        sample.clamp = True
        g.wire(info.outputs["Geometry"], sample.inputs["Geometry"])
        g.wire(position, sample.inputs["Value"])
        offset = g.math("MINIMUM", g.math("ADD", lower, step), args.samples - 1)
        g.wire(
            g.math("ADD", g.math("MULTIPLY", offset, count), index),
            sample.inputs["Index"],
        )
        samples.append(sample.outputs["Value"])
    mixed = g.vector(
        "ADD",
        samples[0],
        g.vector("SCALE", g.vector("SUBTRACT", samples[1], samples[0]), blend),
    )
    realize = g.node("GeometryNodeRealizeInstances")
    g.wire(source, realize.inputs["Geometry"])
    setpos = g.node("GeometryNodeSetPosition")
    g.wire(realize.outputs["Geometry"], setpos.inputs["Geometry"])
    g.wire(mixed, setpos.inputs["Offset"])
    within = g.math(
        "MULTIPLY",
        g.math("GREATER_THAN", frame, args.frame_start - 0.00001),
        g.math("LESS_THAN", frame, args.frame_end + 0.00001),
    )
    enabled = g.node("ShaderNodeValue", "Layer Correction Enabled")
    enabled.outputs[0].default_value = 1
    switch = g.node("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    g.wire(g.math("MULTIPLY", within, enabled.outputs[0]), switch.inputs["Switch"])
    g.wire(source, switch.inputs["False"])
    g.wire(setpos.outputs["Geometry"], switch.inputs["True"])
    g.wire(switch.outputs[0], output.inputs["Geometry"])
    meta.update(
        nodes=[n.name for n in group.nodes if n not in before], enabled=enabled.name
    )
    return meta


def layout(system: Any) -> str:
    rows = [
        (e.family_index, e.root_id, e.prototype.name, len(e.prototype.points))
        for e in sorted(system.elements, key=lambda e: e.family_index)
    ]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def apply_offsets(obj: Any, group: Any, system: Any) -> None:
    meta = raw(obj)
    if meta is None:
        return
    report = inspect(LayerObjectArguments(object_name=obj.name))
    if not report.valid:
        growth.fail("Layer cache is invalid: " + "; ".join(report.issues))
    args = report.settings
    assert args is not None
    frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
    if not args.frame_start <= frame <= args.frame_end:
        return
    if layout(system) != meta["layout"]:
        growth.fail("Layer cache element layout changed; clear/recompute")
    fraction = (
        (frame - args.frame_start)
        * (args.samples - 1)
        / (args.frame_end - args.frame_start)
    )
    lower = math.floor(fraction)
    blend = fraction - lower
    count = report.vertices_per_sample
    cache = cache_object(group).data.vertices
    offset = 0
    transform = obj.matrix_world.to_3x3()
    for element in sorted(system.elements, key=lambda e: e.family_index):
        element.offsets = [
            transform
            @ cache[lower * count + i].co.lerp(
                cache[min(lower + 1, args.samples - 1) * count + i].co, blend
            )
            for i in range(offset, offset + len(element.prototype.points))
        ]
        offset += len(element.prototype.points)


def correct(args: LayerCorrectArguments) -> LayerCache:
    from .geometry_elements import collect_growth
    from .growth_layer_solver import Solver

    organization.idle(mutate=True)
    obj, _, _, group = growth.owned(args.object_name)
    previous = raw(obj)
    if previous and not args.replace:
        growth.fail("Layer cache exists; inspect or use replace=true")
    if group.users != 1:
        growth.fail("Layer correction requires an exclusively owned growth graph")
    if args.object_name in args.colliders:
        growth.fail("A growth system cannot collide with itself as an external body")
    old_cache = owned_cache(obj, group) if previous else None
    original = group
    old_meta = obj.get(KEY)
    saved = bpy.context.scene.frame_current, bpy.context.scene.frame_subframe
    working = cache = data = None
    committed = False
    solver = Solver(args)
    points = array("f")
    digest = None
    count = element_count = 0
    try:
        for _pass_index in range(args.steps + 1):
            previous_lifts = dict(solver.lifts)
            points = array("f")
            for index in range(args.samples):
                frame = args.frame_start + index * (
                    args.frame_end - args.frame_start
                ) / (args.samples - 1)
                bpy.context.scene.frame_set(math.floor(frame), subframe=frame % 1)
                system = collect_growth(obj.name, apply_correction=False)
                count = system.equivalent_vertices
                element_count = len(system.elements)
                if (
                    count > args.max_vertices
                    or count * args.samples > args.max_cached_vertices
                ):
                    growth.fail("Layer vertex/cache budget exceeded; cache unchanged")
                current = layout(system)
                if digest is not None and current != digest:
                    growth.fail(
                        "Template topology/identity varies over the requested range"
                    )
                digest = current
                offsets, conflicts = solver.frame(system, obj.matrix_world, frame)
                if conflicts:
                    return LayerCache(
                        object_name=obj.name,
                        cached=bool(previous),
                        valid=False,
                        settings=args,
                        samples=index + 1,
                        elements=element_count,
                        conflicts=conflicts,
                        triangle_tests=solver.budget.tests,
                        ray_tests=solver.rays,
                        processing_seconds=time.perf_counter() - solver.started,
                        issues=[
                            "Correction infeasible within bounds; "
                            "previous state retained"
                        ],
                    )
                points.extend(v for p in offsets for v in p)
            if solver.lifts == previous_lifts:
                break
        else:
            growth.fail("Layer lift coordination did not converge within step bound")
        solver.bounded()
        working = group.copy()
        if previous:
            disconnect(working, previous)
        data = bpy.data.meshes.new("Layer offsets")
        data.vertices.add(len(points) // 3)
        data.vertices.foreach_set("co", points)
        data.update()
        cache = bpy.data.objects.new("Layer offsets", data)
        bpy.context.scene.collection.objects.link(cache)
        cache[KEY + "_owner"] = obj
        cache.hide_render = True
        cache.hide_set(True)
        result = LayerCache(
            object_name=obj.name,
            cached=True,
            valid=True,
            settings=args,
            samples=args.samples,
            coordination_passes=_pass_index + 1,
            elements=element_count,
            vertices_per_sample=count,
            maximum_displacement=solver.maximum,
            triangle_tests=solver.budget.tests,
            ray_tests=solver.rays,
            cache_bytes=len(points) * 4,
            cache_sha256=hashlib.sha256(points.tobytes()).hexdigest(),
            processing_seconds=time.perf_counter() - solver.started,
        )
        meta = playback(working, cache, args, count)
        meta.update(result=result.model_dump(mode="json"), layout=digest)
        working[growth.KEY + "_signature"] = growth_nodes.signature(working)
        obj.modifiers[growth_nodes.MODIFIER].node_group = working
        obj[KEY] = json.dumps(meta)
        bpy.context.view_layer.update()
        growth.owned(obj.name)
        committed = True
        bpy.data.node_groups.remove(original)
        if old_cache:
            remove_cache(old_cache)
        return result
    finally:
        if not committed:
            obj.modifiers[growth_nodes.MODIFIER].node_group = original
            if old_meta is None:
                if KEY in obj:
                    del obj[KEY]
            else:
                obj[KEY] = old_meta
            if working:
                bpy.data.node_groups.remove(working)
            if cache:
                bpy.data.objects.remove(cache, do_unlink=True)
            if data:
                bpy.data.meshes.remove(data)
        bpy.context.scene.frame_set(saved[0], subframe=saved[1])


def invalidate(operation: str, arguments: dict[str, Any] | None = None) -> None:
    if operation.startswith("blender.growth.layers."):
        return
    if not growth_dynamics.affects_cache(operation, arguments):
        return
    for obj in bpy.context.scene.objects:
        meta = raw(obj)
        if meta is None:
            continue
        group = obj.modifiers[growth_nodes.MODIFIER].node_group
        group.nodes[meta["enabled"]].outputs[0].default_value = 0
        meta["stale_reason"] = (
            "Geometry/motion changed; clear or recompute layer correction"
        )
        obj[KEY] = json.dumps(meta)
        group[growth.KEY + "_signature"] = growth_nodes.signature(group)
