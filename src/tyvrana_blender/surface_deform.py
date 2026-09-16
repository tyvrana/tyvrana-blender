"""Surface Deform bind lifecycle and honest persisted topology validity."""

import json
import time
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils.kdtree import KDTree  # type: ignore[import-not-found]

from . import deformation_geometry as geo
from . import mesh, modifiers, retopo_geometry
from .errors import OperationError
from .surface_deform_models import (
    SurfaceBindArguments,
    SurfaceBinding,
    SurfaceBindings,
    SurfaceInspectArguments,
)

KEY = "_tyvrana_surface_deform"


def metadata(obj: Any) -> dict[str, Any] | None:
    if KEY not in obj:
        return None
    try:
        value = json.loads(obj[KEY])
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        geo.fail(
            "Surface binding metadata is invalid; preserve and explicitly unbind it"
        )
    return None


_CHECKING: set[int] = set()


def state(obj: Any, *, evaluate: bool = True) -> SurfaceBinding:
    identity = int(obj.as_pointer())
    if identity in _CHECKING or len(_CHECKING) >= 64:
        geo.fail("Surface binding dependency cycle or depth exceeds 64")
    _CHECKING.add(identity)
    try:
        return _state(obj, evaluate=evaluate)
    finally:
        _CHECKING.remove(identity)


def _state(obj: Any, *, evaluate: bool = True) -> SurfaceBinding:
    meta = metadata(obj)
    if meta is None:
        return SurfaceBinding(
            driven=obj.name,
            driver=None,
            modifier=None,
            bound=False,
            valid=True,
            reason=None,
            modifier_index=None,
            falloff=None,
            strength=None,
            vertex_group=None,
            driver_topology=None,
            driven_topology=None,
        )
    mod = obj.modifiers.get(meta.get("modifier", ""))
    driver = (
        getattr(mod, "target", None) if mod and mod.type == "SURFACE_DEFORM" else None
    )
    reason = None
    if mod is None or mod.type != "SURFACE_DEFORM":
        reason = "Owned Surface Deform modifier was removed or replaced"
    elif driver is None:
        reason = "Driver object was removed"
    elif not mod.is_bound:
        reason = "Native modifier is unbound"
    elif geo.topology(obj.data) != meta.get("driven_authored"):
        reason = "Driven authored topology changed since bind"
    elif geo.topology(driver.data) != meta.get("driver_authored"):
        reason = "Driver authored topology changed since bind"
    elif list(obj.modifiers).index(mod) != meta.get("index"):
        reason = "Modifier ordering changed since bind"
    elif (
        driver.name != meta.get("driver")
        or mod.falloff != meta.get("falloff")
        or mod.vertex_group != meta.get("vertex_group")
    ):
        reason = "Binding settings or driver identity changed since bind"
    elif not mod.show_viewport or not mod.show_render:
        reason = "Binding modifier is disabled"
    if reason is None and evaluate:
        try:
            modifiers.budget(driver, modifiers.stack(driver), strict=True)
        except OperationError as exc:
            reason = "Driver evaluation is unsafe: " + str(exc)
    if reason is None and evaluate:
        graph = bpy.context.evaluated_depsgraph_get()
        with retopo_geometry.evaluated_mesh(driver, graph) as (data, _):
            if geo.topology(data) != meta.get("driver_evaluated"):
                reason = "Driver evaluated topology changed since bind"
    return SurfaceBinding(
        driven=obj.name,
        driver=driver.name if driver else None,
        modifier=mod.name if mod else None,
        bound=bool(mod and mod.type == "SURFACE_DEFORM" and mod.is_bound),
        valid=reason is None,
        reason=reason,
        modifier_index=list(obj.modifiers).index(mod) if mod else None,
        falloff=mod.falloff if driver else None,
        strength=mod.strength if driver else None,
        vertex_group=mod.vertex_group if driver else None,
        driver_topology=meta.get("driver_evaluated"),
        driven_topology=meta.get("driven_authored"),
    )


def validate(obj: Any) -> None:
    if KEY in obj:
        result = state(obj)
        if not result.valid:
            geo.fail(
                (result.reason or "Invalid binding")
                + "; explicitly unbind and bind compatible geometry"
            )


def inspect(args: SurfaceInspectArguments) -> SurfaceBindings:
    start = time.perf_counter()
    return SurfaceBindings(
        bindings=[state(modifiers.object_mesh(n)) for n in args.objects],
        processing_seconds=time.perf_counter() - start,
    )


def native_bind(obj: Any, mod: Any) -> set[str]:
    with bpy.context.temp_override(
        object=obj,
        active_object=obj,
        selected_objects=[obj],
        selected_editable_objects=[obj],
    ):
        return set(bpy.ops.object.surfacedeform_bind(modifier=mod.name))


def bind(args: SurfaceBindArguments) -> SurfaceBindings:
    start = time.perf_counter()
    driver = geo.context(args.driver)
    objects = [geo.context(n, edit=True) for n in args.driven]
    if any(
        KEY in obj or any(m.type == "SURFACE_DEFORM" for m in obj.modifiers)
        for obj in objects
    ):
        geo.fail(
            "Preserve existing surface bindings; unbind owned "
            "relations before rebinding"
        )
    if len(driver.data.vertices) > 10000:
        geo.fail("Surface driver exceeds 10000 authored vertices")
    graph = retopo_geometry.graph(driver)
    with retopo_geometry.evaluated_mesh(driver, graph) as (data, _):
        if len(data.vertices) > 10000 or len(data.polygons) > 20000:
            geo.fail("Surface driver exceeds 10000 evaluated vertices/20000 faces")
        if not data.polygons:
            geo.fail("Surface driver requires polygon faces")
        if len(data.polygons) * sum(len(o.data.vertices) for o in objects) > 5_000_000:
            geo.fail(
                "Surface binding exceeds 5000000 driven-vertex/driver-face work units"
            )
        signature = geo.topology(data)
        tree = KDTree(len(data.vertices))
        for v in data.vertices:
            tree.insert(v.co, v.index)
        tree.balance()
        if any(len(tree.find_range(v.co, 1e-8)) > 1 for v in data.vertices):
            geo.fail("Surface driver has duplicate vertices; merge them before binding")
    with mesh.snapshot(driver) as bm:
        if any(len(e.link_faces) > 2 for e in bm.edges):
            geo.fail("Surface driver has nonmanifold edges with more than two faces")
    for obj in objects:
        if obj.modifiers:
            geo.fail(
                "Bind an unmodified driven mesh; add downstream "
                "subdivision after binding"
            )
        if args.vertex_group and args.vertex_group not in obj.vertex_groups:
            geo.fail("Driven mask group does not exist")
        modifiers.check_dependencies(obj, None, {"target": driver})
    created = []
    try:
        for obj in objects:
            mod = obj.modifiers.new("Surface Deform", "SURFACE_DEFORM")
            created.append((obj, mod))
            mod.target = driver
            mod.falloff = args.falloff
            mod.strength = args.strength
            mod.vertex_group = args.vertex_group
            outcome = native_bind(obj, mod)
            if "FINISHED" not in outcome or not mod.is_bound:
                geo.fail(
                    "Native surface bind failed; driver needs convex nondegenerate "
                    "faces without duplicate vertices"
                )
            obj[KEY] = json.dumps(
                dict(
                    modifier=mod.name,
                    driver=driver.name,
                    index=0,
                    driver_authored=geo.topology(driver.data),
                    driver_evaluated=signature,
                    driven_authored=geo.topology(obj.data),
                    falloff=mod.falloff,
                    vertex_group=mod.vertex_group,
                )
            )
        bpy.context.view_layer.update()
        result = inspect(SurfaceInspectArguments(objects=args.driven))
        if any(not row.valid for row in result.bindings):
            geo.fail("Native surface binding did not validate")
    except BaseException:
        for obj, mod in reversed(created):
            obj.modifiers.remove(mod)
            if KEY in obj:
                del obj[KEY]
        bpy.context.view_layer.update()
        raise
    return result.model_copy(update={"processing_seconds": time.perf_counter() - start})


def unbind(args: SurfaceInspectArguments) -> SurfaceBindings:
    start = time.perf_counter()
    objects = [geo.context(n, edit=True) for n in args.objects]
    if len(set(args.objects)) != len(args.objects):
        geo.fail("Unbind each object once")
    # Prevalidate all native handles; removal only affects owned modifiers.
    handles = []
    for obj in objects:
        meta = metadata(obj)
        if meta is None:
            geo.fail("Object has no owned surface binding")
        mod = obj.modifiers.get(meta.get("modifier", ""))
        if mod is not None and mod.type != "SURFACE_DEFORM":
            geo.fail(
                "Preserve a replaced modifier; restore its owned "
                "Surface Deform identity"
            )
        handles.append((obj, mod))
    for obj, mod in handles:
        if mod is not None:
            obj.modifiers.remove(mod)
        del obj[KEY]
    bpy.context.view_layer.update()
    return inspect(args).model_copy(
        update={"processing_seconds": time.perf_counter() - start}
    )
