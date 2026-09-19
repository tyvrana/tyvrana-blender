"""Native XPBD on a transient rooted path; atomic durable sampled playback."""

import hashlib
import json
import math
import time
from array import array
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy  # type: ignore[import-not-found]

from . import growth, growth_nodes, organization
from .dynamics_models import (
    DynamicsBakeArguments,
    DynamicsCache,
    DynamicsJobStatus,
    DynamicsObjectArguments,
)

KEY = "_tyvrana_growth_dynamics"
CACHE = "Secondary Cache"
_active: str | None = None
_jobs: dict[str, DynamicsJobStatus] = {}
_steps: Generator[int, None, DynamicsCache] | None = None
_started = 0.0


def busy() -> bool:
    return _active is not None


def asset_groups() -> list[Any]:
    path = (
        Path(bpy.utils.resource_path("LOCAL"))
        / "datafiles/assets/nodes/geometry_nodes_dynamics_assets.blend"
    )
    if not path.is_file():
        growth.fail(
            "Native Hair Dynamics assets are unavailable in this Blender installation"
        )
    before = set(bpy.data.node_groups)
    with bpy.data.libraries.load(str(path)) as (_, loaded):
        loaded.node_groups = ["Hair Dynamics", "Collider"]
    return [g for g in bpy.data.node_groups if g not in before]


def raw(obj: Any) -> dict[str, Any] | None:
    value = obj.get(KEY)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 32768:
        growth.fail("Dynamics metadata is invalid; preserve external edits")
    data = json.loads(value)
    if not isinstance(data, dict):
        growth.fail("Dynamics metadata is invalid")
    return data


def cache_object(group: Any) -> Any:
    node = group.nodes.get(CACHE)
    return node.inputs["Object"].default_value if node else None


def inspect(args: DynamicsObjectArguments) -> DynamicsCache:
    obj, _, _, group = growth.owned(args.object_name)
    meta = raw(obj)
    if meta is None:
        return DynamicsCache(object_name=obj.name, cached=False, valid=True)
    result = DynamicsCache.model_validate(meta["result"])
    cache = cache_object(group)
    issues = []
    if cache is None or cache.get(KEY + "_owner") != obj or cache.data.users != 1:
        issues.append("Owned cache resource is missing or shared")
    elif points_hash(cache.data) != result.cache_sha256:
        issues.append("Cached positions changed externally; clear and rebake")
    if meta.get("stale_reason"):
        issues.append(meta["stale_reason"])
    frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
    if not meta["start"] <= frame <= meta["end"]:
        issues.append("Current frame is outside the cache; authored growth is used")
    return result.model_copy(update={"valid": not issues, "issues": issues})


def points_hash(mesh: Any) -> str:
    values = array("f", [0]) * (3 * len(mesh.vertices))
    mesh.vertices.foreach_get("co", values)
    return hashlib.sha256(values.tobytes()).hexdigest()


def clear(args: DynamicsObjectArguments) -> DynamicsCache:
    organization.idle(mutate=True)
    obj, _, _, group = growth.owned(args.object_name)
    meta = raw(obj)
    if meta is None:
        return DynamicsCache(object_name=obj.name, cached=False, valid=True)
    cache = cache_object(group)
    if group.users != 1:
        growth.fail("Cache graph has external users; detach them before clearing")
    if cache is None or cache.get(KEY + "_owner") != obj or cache.data.users != 1:
        growth.fail("Cache ownership is damaged; preserve external resources")
    users = bpy.data.user_map(subset={cache, cache.data})
    if (users.get(cache, set()) | users.get(cache.data, set())) - {
        cache,
        group,
        bpy.context.scene,
        *cache.users_collection,
    }:
        growth.fail("Cache has external users; detach them before clearing")
    source = group.nodes[meta["source"]].outputs["Curves"]
    for name, socket in meta["sinks"]:
        group.links.new(source, group.nodes[name].inputs[socket])
    for name in meta["nodes"]:
        group.nodes.remove(group.nodes[name])
    mesh = cache.data
    bpy.data.objects.remove(cache, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    del obj[KEY]
    group[growth.KEY + "_signature"] = growth_nodes.signature(group)
    bpy.context.view_layer.update()
    return DynamicsCache(object_name=obj.name, cached=False, valid=True)


def playback(
    group: Any, cache: Any, start: int, end: int, count: int
) -> dict[str, Any]:
    # Build all nodes disconnected, publish their sole output link last.
    g = growth_nodes.GrowthGraph.__new__(growth_nodes.GrowthGraph)
    g.group = group
    before = set(group.nodes)
    source = next(
        n for n in group.nodes if n.bl_idname == "GeometryNodeDeformCurvesOnSurface"
    )
    sinks = [
        (link.to_node.name, link.to_socket.identifier)
        for link in group.links
        if link.from_node == source
    ]
    info = g.info(cache, CACHE, "ORIGINAL")
    frame = g.node("GeometryNodeInputSceneTime").outputs["Frame"]
    fraction = g.math("SUBTRACT", frame, start)
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
        offset = g.math("MINIMUM", g.math("ADD", lower, step), end - start)
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
    setpos = g.node("GeometryNodeSetPosition")
    g.wire(source.outputs["Curves"], setpos.inputs["Geometry"])
    g.wire(mixed, setpos.inputs["Position"])
    roots = g.node("GeometryNodeCurveEndpointSelection")
    roots.inputs["Start Size"].default_value = 1
    roots.inputs["End Size"].default_value = 0
    not_root = g.node("FunctionNodeBooleanMath")
    not_root.operation = "NOT"
    g.wire(roots.outputs["Selection"], not_root.inputs[0])
    g.wire(not_root.outputs[0], setpos.inputs["Selection"])
    within = g.math(
        "MULTIPLY",
        g.math("GREATER_THAN", frame, start - 0.0001),
        g.math("LESS_THAN", frame, end + 0.0001),
    )
    enabled = g.node("ShaderNodeValue", "Secondary Enabled")
    enabled.outputs[0].default_value = 1
    switch = g.node("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    g.wire(g.math("MULTIPLY", within, enabled.outputs[0]), switch.inputs["Switch"])
    g.wire(source.outputs["Curves"], switch.inputs["False"])
    g.wire(setpos.outputs["Geometry"], switch.inputs["True"])
    for name, socket in sinks:
        group.links.new(switch.outputs[0], group.nodes[name].inputs[socket])
    return dict(
        source=source.name,
        sinks=sinks,
        nodes=[n.name for n in group.nodes if n not in before],
        start=start,
        end=end,
        enabled=enabled.name,
    )


def simulation(args: DynamicsBakeArguments) -> Generator[int, None, DynamicsCache]:
    started = time.perf_counter()
    obj, meta, _, group = growth.owned(args.object_name)
    if group.users != 1:
        growth.fail("Dynamics require an exclusively owned growth graph")
    previous = raw(obj)
    original_group = group
    old_cache = cache_object(group) if previous else None
    original_metadata = obj.get(KEY)
    original_signature = group.get(growth.KEY + "_signature")
    with growth.evaluated_path(obj, group) as evaluated:
        count = len(evaluated.points)
    frames = args.frame_end - args.frame_start + 1
    if (
        count > args.max_points
        or count * frames > args.max_cached_points
        or count * frames * args.settings.substeps * args.settings.constraint_steps
        > args.max_solver_work
    ):
        growth.fail("Dynamics budget exceeded; reduce density, frames or solver steps")
    targets = [
        obj.data.surface,
        *(organization.object_named(n) for n in args.settings.colliders),
    ]
    from .retopo_geometry import graph

    for target in targets:
        if target == obj or target.type != "MESH":
            growth.fail("Colliders must be independent meshes")
        evaluated = target.evaluated_get(graph(target))
        if len(evaluated.data.polygons) > 100000:
            growth.fail("Collider exceeds 100000 polygons; use an authored proxy")
    warnings = growth.validity(obj, meta)
    if warnings:
        growth.fail(warnings[0])
    scene = bpy.context.scene
    saved_frame = (
        scene.frame_current,
        scene.frame_subframe,
        scene.frame_start,
        scene.frame_end,
    )
    groups: list[Any] = []
    clone = None
    clone_data = None
    copied = None
    working = None
    cache = None
    cache_data = None
    committed = False
    points = array("f")
    try:
        scene.frame_start, scene.frame_end = args.frame_start, args.frame_end
        scene.frame_set(args.frame_start)
        working = group.copy()
        if previous:
            old_source = working.nodes[previous["source"]].outputs["Curves"]
            for name, socket in previous["sinks"]:
                working.links.new(old_source, working.nodes[name].inputs[socket])
            for name in previous["nodes"]:
                working.nodes.remove(working.nodes[name])
        group = working
        groups = asset_groups()
        hair = next(g for g in groups if g.name.startswith("Hair Dynamics"))
        collider_asset = next(g for g in groups if g.name.startswith("Collider"))
        copied = group.copy()
        clone = obj.copy()
        clone_data = obj.data.copy()
        clone.data = clone_data
        clone.name = "Secondary simulation staging"
        clone.modifiers[0].node_group = copied
        scene.collection.objects.link(clone)
        clone.hide_render = True
        clone.hide_set(True)
        # Its evaluated Hair Curves output is required; never convert to a mesh.
        source = next(
            n
            for n in copied.nodes
            if n.bl_idname == "GeometryNodeDeformCurvesOnSurface"
        )
        output = next(n for n in copied.nodes if n.type == "GROUP_OUTPUT")
        node = copied.nodes.new("GeometryNodeGroup")
        node.node_tree = hair
        node.inputs["Mode"].default_value = "Physics (Experimental)"
        settings = args.settings
        for socket, value in [
            ("Substeps", settings.substeps),
            ("Constraint Steps", settings.constraint_steps),
            ("Mass", settings.mass),
            ("Stretchiness", settings.stretchiness),
            ("Bendiness", settings.bendiness),
            ("Root Bendiness", settings.root_bendiness),
            ("Linear", settings.linear_damping),
            ("Angular", settings.angular_damping),
            ("Surface Collision", settings.surface_collision),
            ("Surface Friction", settings.friction),
            ("Edge Contacts", settings.edge_contacts),
        ]:
            node.inputs[socket].default_value = value
        next(
            s for s in node.inputs if s.identifier == "Socket_24"
        ).default_value = settings.gravity
        g = growth_nodes.GrowthGraph.__new__(growth_nodes.GrowthGraph)
        g.group = copied
        radius = g.node("GeometryNodeSetCurveRadius")
        g.wire(source.outputs["Curves"], radius.inputs["Curve"])
        factor = g.node("GeometryNodeSplineParameter").outputs["Factor"]
        g.wire(
            g.math(
                "MULTIPLY",
                g.attr("growth_radius"),
                g.math(
                    "ADD",
                    1,
                    g.math(
                        "MULTIPLY", factor, g.math("SUBTRACT", g.attr("growth_tip"), 1)
                    ),
                ),
            ),
            radius.inputs["Radius"],
        )
        copied.links.new(radius.outputs["Curve"], node.inputs["Hair"])
        copied.links.new(node.outputs["Hair"], output.inputs["Geometry"])
        if settings.colliders:
            g = growth_nodes.GrowthGraph.__new__(growth_nodes.GrowthGraph)
            g.group = copied
            join = g.node("GeometryNodeJoinGeometry")
            for name in settings.colliders:
                target = organization.object_named(name)
                if target.type != "MESH" or target == obj:
                    growth.fail("Colliders must be independent mesh objects")
                info = g.info(target, "Collider " + name)
                g.wire(info.outputs["Geometry"], join.inputs["Geometry"])
            c = g.node("GeometryNodeGroup")
            c.node_tree = collider_asset
            c.inputs["Deforming"].default_value = True
            c.inputs["Edge Contacts"].default_value = settings.edge_contacts
            c.inputs["Margin"].default_value = settings.margin
            c.inputs["Friction"].default_value = settings.friction
            g.wire(join.outputs["Geometry"], c.inputs["Geometry"])
            g.wire(c.outputs["Collider"], node.inputs["Effectors"])
        maximum_root_error = maximum_displacement = 0.0
        count = 0
        for frame in range(args.frame_start, args.frame_end + 1):
            if time.perf_counter() - started > args.max_seconds:
                growth.fail(
                    "Dynamics time budget exceeded; previous authored state retained"
                )
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            # Full canonical path carries stable ordering and attached root coordinates.
            with growth.evaluated_path(obj, group) as base:
                base_points = [p.position.copy() for p in base.points]
                roots = [c.first_point_index for c in base.curves]
            evaluated = clone.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
            count = len(base_points)
            frames = args.frame_end - args.frame_start + 1
            if (
                count > args.max_points
                or count * frames > args.max_cached_points
                or count * frames * settings.substeps * settings.constraint_steps
                > args.max_solver_work
            ):
                growth.fail(
                    "Dynamics point/cache/solver budget exceeded; "
                    "reduce density or frames"
                )
            if len(evaluated.points) != count:
                growth.fail("Simulation changed path topology; cache was not published")
            current = [p.position.copy() for p in evaluated.points]
            if any(
                not math.isfinite(v) or abs(v) > 1000000
                for point in current
                for v in point
            ):
                growth.fail(
                    "Native solver produced unsafe positions; reduce forces/time step"
                )
            for i in roots:
                maximum_root_error = max(
                    maximum_root_error, (current[i] - base_points[i]).length
                )
                current[i] = base_points[
                    i
                ]  # Exact attachment at cached integer frames.
            maximum_displacement = max(
                maximum_displacement,
                max((a - b).length for a, b in zip(current, base_points, strict=True)),
            )
            points.extend(v for p in current for v in p)
            yield frame - args.frame_start + 1
        cache_data = bpy.data.meshes.new("Secondary cached positions")
        cache_data.vertices.add(len(points) // 3)
        cache_data.vertices.foreach_set("co", points)
        cache_data.update()
        cache = bpy.data.objects.new("Secondary cached positions", cache_data)
        scene.collection.objects.link(cache)
        cache[KEY + "_owner"] = obj
        cache.hide_render = True
        cache.hide_set(True)
        result = DynamicsCache(
            object_name=obj.name,
            cached=True,
            valid=True,
            settings=settings,
            frame_start=args.frame_start,
            frame_end=args.frame_end,
            points_per_frame=count,
            cache_bytes=len(points) * 4,
            cache_sha256=hashlib.sha256(points.tobytes()).hexdigest(),
            maximum_root_error=0,
            maximum_solver_root_error=maximum_root_error,
            maximum_displacement=maximum_displacement,
            processing_seconds=time.perf_counter() - started,
        )
        state = playback(group, cache, args.frame_start, args.frame_end, count)
        state["result"] = result.model_dump(mode="json")
        group[growth.KEY + "_signature"] = growth_nodes.signature(group)
        obj.modifiers[growth_nodes.MODIFIER].node_group = group
        obj[KEY] = json.dumps(state)
        bpy.context.view_layer.update()
        growth.owned(obj.name)
        committed = True
        bpy.data.node_groups.remove(original_group)
        if old_cache:
            old_mesh = old_cache.data
            bpy.data.objects.remove(old_cache, do_unlink=True)
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
        return result
    finally:
        if clone:
            bpy.data.objects.remove(clone, do_unlink=True)
        if clone_data and clone_data.users == 0:
            bpy.data.hair_curves.remove(clone_data)
        if copied and copied.users == 0:
            bpy.data.node_groups.remove(copied)
        # Native asset groups have dependencies; remove only the imported closure.
        for g in groups:
            bpy.data.node_groups.remove(g, do_unlink=True)
        if not committed:
            obj.modifiers[growth_nodes.MODIFIER].node_group = original_group
            if original_metadata is None:
                if KEY in obj:
                    del obj[KEY]
            else:
                obj[KEY] = original_metadata
            original_group[growth.KEY + "_signature"] = original_signature
            if working and working.users == 0:
                bpy.data.node_groups.remove(working)
            if cache:
                bpy.data.objects.remove(cache, do_unlink=True)
            if cache_data and cache_data.users == 0:
                bpy.data.meshes.remove(cache_data)
        scene.frame_start, scene.frame_end = saved_frame[2:]
        scene.frame_set(saved_frame[0], subframe=saved_frame[1])


def start(args: DynamicsBakeArguments) -> DynamicsJobStatus:
    global _active, _steps, _started
    organization.idle(mutate=True)
    if busy():
        growth.fail("A dynamics job is already active; inspect or cancel it")
    obj, _, _, _ = growth.owned(args.object_name)
    if raw(obj):
        if not args.replace:
            growth.fail("Cache exists; inspect or use replace=true")
        _, _, _, current_group = growth.owned(obj.name)
        cache = cache_object(current_group)
        if cache is None or cache.get(KEY + "_owner") != obj or cache.data.users != 1:
            growth.fail("Owned cache is missing/shared; inspect before replacing")
        users = bpy.data.user_map(subset={cache, cache.data, current_group})
        allowed = {
            obj,
            cache,
            current_group,
            bpy.context.scene,
            *cache.users_collection,
        }
        if any(
            users.get(item, set()) - allowed
            for item in (cache, cache.data, current_group)
        ):
            growth.fail("Cache has external users; detach them before replacing")
    while len(_jobs) >= 8:
        del _jobs[next(iter(_jobs))]
    identifier = uuid4().hex
    job = DynamicsJobStatus(
        job_id=identifier,
        object_name=obj.name,
        state="queued",
        frame_count=args.frame_end - args.frame_start + 1,
    )
    _jobs[identifier] = job
    _active, _steps, _started = identifier, simulation(args), time.perf_counter()
    return job.model_copy(deep=True)


def status(identifier: str) -> DynamicsJobStatus:
    if identifier not in _jobs:
        growth.fail("Dynamics job is absent; eight records persist until host/reload")
    return _jobs[identifier].model_copy(deep=True)


def tick() -> None:
    global _active, _steps
    if _active is None or _steps is None:
        return
    job = _jobs[_active]
    try:
        job = job.model_copy(
            update={"completed_frames": next(_steps), "state": "running"}
        )
    except StopIteration as done:
        job = job.model_copy(update={"state": "completed", "result": done.value})
    except Exception as exc:
        job = job.model_copy(update={"state": "failed", "error": str(exc)[:500]})
    finally:
        job = job.model_copy(update={"elapsed_seconds": time.perf_counter() - _started})
        _jobs[job.job_id] = job
        if job.state in {"completed", "failed"}:
            _steps.close()
            _steps, _active = None, None


def cancel(identifier: str) -> DynamicsJobStatus:
    global _active, _steps
    job = status(identifier)
    if identifier == _active:
        assert _steps is not None
        _steps.close()
        _steps, _active = None, None
        job = job.model_copy(
            update={
                "state": "cancelled",
                "elapsed_seconds": time.perf_counter() - _started,
            }
        )
        _jobs[identifier] = job
    return job


def shutdown() -> None:
    if _active:
        cancel(_active)


def affects_cache(operation: str, arguments: dict[str, Any] | None = None) -> bool:
    # Conservative native cache invalidation after geometry/motion source edits.
    domain = operation.removeprefix("blender.").split(".")[0]
    if domain == "timeline":
        if not set(arguments or {}) & {"fps", "fps_base"}:
            return False
        domain = "motion"
    if domain not in {
        "mesh",
        "loft",
        "curve",
        "growth",
        "armature",
        "constraint",
        "control_rig",
        "action",
        "coupling",
        "motion",
        "object",
        "object_set",
        "weights",
        "shape_keys",
        "modifier",
        "surface_deform",
        "sculpt",
        "remesh",
        "retopo",
        "uv",
        "volume",
    }:
        return False
    return True


def invalidate(operation: str, arguments: dict[str, Any] | None = None) -> None:
    if operation.startswith(
        ("blender.growth.dynamics.", "blender.growth.layers.")
    ) or not affects_cache(operation, arguments):
        return
    for obj in bpy.context.scene.objects:
        if KEY not in obj:
            continue
        meta = raw(obj)
        assert meta is not None
        group = obj.modifiers[growth_nodes.MODIFIER].node_group
        group.nodes[meta["enabled"]].outputs[0].default_value = 0
        meta["stale_reason"] = (
            "Geometry/motion changed after baking; clear or rebake dynamics"
        )
        obj[KEY] = json.dumps(meta)
        group[growth.KEY + "_signature"] = growth_nodes.signature(group)
