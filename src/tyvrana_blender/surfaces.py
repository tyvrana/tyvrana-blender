"""Native surface-network meshes, constrained tessellation, QA and atomic revision."""

import hashlib
import json
import math
import time
import uuid
from collections import Counter
from types import SimpleNamespace
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.geometry import delaunay_2d_cdt  # type: ignore[import-not-found]

from . import organization
from .bindings import RESOURCE_KEY
from .errors import OperationError
from .loft import signature
from .surface_geometry import Face, Geometry, Point, fail, generate, topology
from .surface_models import (
    SurfaceConfigureArguments,
    SurfaceCreateArguments,
    SurfaceNetworkInspectArguments,
    SurfaceRegion,
    SurfaceResult,
    SurfaceRevision,
    SurfaceSpec,
    SurfaceSummary,
)

KEY = "tyvrana_surface"
PATCH_ATTRIBUTE = "tyvrana_surface_patch"
REGION_ATTRIBUTE = "tyvrana_surface_region"


def triangulate(
    points: list[Point], edges: list[tuple[int, int]]
) -> tuple[list[Point], list[Face], list[list[int]]]:
    result = delaunay_2d_cdt([Vector(p) for p in points], edges, [], 1, 1e-8, True)
    return (
        [list(p) for p in result[0]],
        [list(f) for f in result[2]],
        [list(ids) for ids in result[3]],
    )


def topology_hash(mesh: Any) -> str:
    data = [len(mesh.vertices), [list(f.vertices) for f in mesh.polygons]]
    return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()


def content_hash(mesh: Any) -> str:
    parts = [signature(mesh)]
    for name in (PATCH_ATTRIBUTE, REGION_ATTRIBUTE):
        attribute = mesh.attributes.get(name)
        parts.append(str([v.value for v in attribute.data]) if attribute else "missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def metadata(obj: Any) -> dict[str, Any]:
    try:
        value = obj[KEY]
        if not isinstance(value, str) or len(value) > 262144 or obj.type != "MESH":
            raise ValueError
        meta: dict[str, Any] = json.loads(value)
        SurfaceSpec.model_validate(meta["spec"])
        if not isinstance(meta["revision"], int) or not isinstance(
            meta["regions"], dict
        ):
            raise ValueError
        return meta
    except (KeyError, ValueError, TypeError) as exc:
        raise OperationError(
            "surface_state_invalid", "Managed surface metadata is missing or damaged"
        ) from exc


def fingerprint(obj: Any) -> dict[str, Any] | None:
    if KEY not in obj:
        return None
    try:
        meta = metadata(obj)
        return {
            "revision": meta["revision"],
            "topology_revision": meta["topology_revision"],
            "spec": hashlib.sha256(
                json.dumps(meta["spec"], sort_keys=True).encode()
            ).hexdigest(),
            "content": content_hash(obj.data),
        }
    except OperationError:
        return {"invalid_surface": True}


def native_checks(mesh: Any) -> dict[str, int]:
    points = [list(v.co) for v in mesh.vertices]
    if any(not math.isfinite(v) or abs(v) > 1e8 for p in points for v in p):
        fail("degenerate", "Generated coordinates exceed supported finite scene scale")
    faces = [list(p.vertices) for p in mesh.polygons]
    stats = topology(points, faces, closed=True)
    if stats["max_vertex_valence"] > 16:
        fail(
            "limit_exceeded",
            "Vertex valence exceeds 16; revise patch/refinement layout",
        )
    if len({tuple(sorted(f)) for f in faces}) != len(faces):
        fail("degenerate", "Generated faces are duplicated")
    mesh.calc_loop_triangles()
    triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    vectors = [v.co.copy() for v in mesh.vertices]
    bounds = [max(p[k] for p in points) - min(p[k] for p in points) for k in range(3)]
    epsilon = max(1e-8, math.hypot(*bounds) * 1e-8)
    for a, b, c in triangles:
        if (vectors[b] - vectors[a]).cross(vectors[c] - vectors[a]).length < epsilon**2:
            fail("degenerate", "Native precision produced a zero-area triangle")
    # Reuse bounded hierarchical traversal; never materialize all BVH overlap pairs.
    from . import geometry_qa, layer_geometry
    from .geometry_qa_models import GeometryInspectArguments, GeometryQuery

    source = layer_geometry.Surface(
        SimpleNamespace(name=mesh.name),
        vectors,
        triangles,
        "",
        "",
        None,
        None,
        [t.polygon_index for t in mesh.loop_triangles],
    )
    surface = geometry_qa.Surface(source)
    budget = geometry_qa.Budget(100000)
    args = GeometryInspectArguments(
        objects=[GeometryQuery(object_name=mesh.name, self_intersection=True)],
        tolerance=epsilon,
        worst_limit=0,
    )
    try:
        _, contacts, _, _ = geometry_qa.proximity(surface, surface, args, budget)
    except OperationError as exc:
        raise OperationError("surface_limit_exceeded", str(exc)) from exc
    if contacts:
        fail(
            "self_intersection",
            "Generated shell intersects itself; reduce "
            "thickness/features or revise patch layout",
        )
    stats["self_intersection_tests"] = budget.tests
    return stats


def build(spec: SurfaceSpec) -> tuple[Any, Geometry, dict[str, int]]:
    geometry = generate(spec, triangulate)
    mesh = bpy.data.meshes.new(spec.name + ".Surface")
    try:
        mesh.from_pydata(geometry.vertices, [], geometry.faces)
        mesh.update()
        for polygon in mesh.polygons:
            polygon.use_smooth = spec.smooth
        patch_ids = {p.id: i + 1 for i, p in enumerate(spec.patches)}
        region_ids = {name: i + 1 for i, name in enumerate(geometry.regions)}
        for attribute, values, ids in (
            (PATCH_ATTRIBUTE, geometry.patch_ids, patch_ids),
            (REGION_ATTRIBUTE, geometry.region_ids, region_ids),
        ):
            layer = mesh.attributes.new(attribute, "INT", "FACE")
            layer.data.foreach_set("value", [ids[value] for value in values])
        stats = native_checks(mesh)
        return mesh, geometry, stats
    except BaseException:
        bpy.data.meshes.remove(mesh)
        raise


def state(
    spec: SurfaceSpec,
    mesh: Any,
    geometry: Geometry,
    stats: dict[str, int],
    *,
    previous: dict[str, Any] | None = None,
    changed: bool = False,
) -> str:
    regions = {
        name: {"kind": kind, "index": i + 1}
        for i, (name, kind) in enumerate(geometry.regions.items())
    }
    value = {
        "id": previous["id"] if previous else uuid.uuid4().hex,
        "revision": previous["revision"] + 1 if previous else 1,
        "topology_revision": previous["topology_revision"] + int(changed)
        if previous
        else 1,
        "topology_changed": changed,
        "spec": spec.model_dump(mode="json"),
        "signature": content_hash(mesh),
        "topology_hash": topology_hash(mesh),
        "regions": regions,
        "stats": stats,
        "thickness_min": geometry.thickness_min,
        "thickness_max": geometry.thickness_max,
        "junction_count": geometry.junction_count,
    }
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def summary(
    obj: Any, arguments: SurfaceNetworkInspectArguments | None = None
) -> SurfaceSummary:
    meta = metadata(obj)
    mesh = obj.data
    valid = content_hash(mesh) == meta["signature"]
    counts = (
        Counter(v.value for v in mesh.attributes[REGION_ATTRIBUTE].data)
        if REGION_ATTRIBUTE in mesh.attributes
        else Counter()
    )
    all_regions = [
        SurfaceRegion(id=name, kind=value["kind"], face_count=counts[value["index"]])
        for name, value in meta["regions"].items()
    ]
    filtered = [
        r
        for r in all_regions
        if not arguments or not arguments.region_ids or r.id in arguments.region_ids
    ]
    limit = arguments.region_limit if arguments else 12
    coords = [list(v.co) for v in mesh.vertices]
    spec = SurfaceSpec.model_validate(meta["spec"])
    return SurfaceSummary(
        name=obj.name,
        surface_id=meta["id"],
        revision=meta["revision"],
        topology_revision=meta["topology_revision"],
        topology_changed=meta["topology_changed"],
        valid=valid,
        topology=spec.topology,
        patch_count=len(spec.patches),
        opening_count=len(spec.openings),
        junction_count=meta["junction_count"],
        vertex_count=len(coords),
        face_count=len(mesh.polygons),
        triangle_count=sum(len(p.vertices) == 3 for p in mesh.polygons),
        quad_count=sum(len(p.vertices) == 4 for p in mesh.polygons),
        **{
            k: meta["stats"][k]
            for k in (
                "edge_count",
                "boundary_edges",
                "non_manifold_edges",
                "components",
                "max_vertex_valence",
            )
        },
        bounds_min=[min(p[k] for p in coords) if coords else 0 for k in range(3)],
        bounds_max=[max(p[k] for p in coords) if coords else 0 for k in range(3)],
        thickness_min=meta["thickness_min"],
        thickness_max=meta["thickness_max"],
        regions=filtered[:limit],
        region_count=len(filtered),
        regions_truncated=len(filtered) > limit,
        issues=[]
        if valid
        else [
            "Generated base mesh or region data changed; stored construction QA "
            "is stale. Use mesh tools for downstream refinement."
        ],
        constraints=spec.model_copy(update={"name": obj.name})
        if arguments and arguments.include_constraints
        else None,
    )


def create(arguments: SurfaceCreateArguments) -> SurfaceResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    collections = [organization.collection_named(n) for n in arguments.collections]
    for collection in collections:
        organization.collection_editable(collection)
    for spec in arguments.surfaces:
        organization.named_available(spec.name, bpy.data.objects)
    prepared = []
    objects = []
    try:
        for spec in arguments.surfaces:
            mesh, geometry, stats = build(spec)
            prepared.append((spec, mesh, geometry, stats))
        if sum(len(mesh.vertices) for _, mesh, _, _ in prepared) > 131072:
            fail("limit_exceeded", "Surface batch exceeds 131072 generated vertices")
        for spec, mesh, geometry, stats in prepared:
            obj = bpy.data.objects.new(spec.name, mesh)
            objects.append(obj)
            obj[KEY] = state(spec, mesh, geometry, stats)
            obj[RESOURCE_KEY] = uuid.uuid4().hex
            for collection in collections:
                collection.objects.link(obj)
        bpy.context.view_layer.update()
        return SurfaceResult(
            surfaces=[summary(o) for o in objects],
            processing_seconds=time.perf_counter() - start,
        )
    except BaseException:
        for obj in reversed(objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for _, mesh, _, _ in prepared:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise


def revised(spec: SurfaceSpec, edit: SurfaceRevision) -> SurfaceSpec:
    data = spec.model_dump(mode="json")
    for field in ("nodes", "curves", "patches", "openings", "features"):
        existing = {x["id"]: x for x in data[field]}
        for item in getattr(edit, field):
            if item.id not in existing:
                fail("handle_unknown", f"Unknown {field} handle: {item.id}")
            values = item.model_dump(mode="json", exclude_unset=True)
            if field in ("openings", "features"):
                if (
                    values.get("patch", existing[item.id]["patch"])
                    != existing[item.id]["patch"]
                ):
                    fail(
                        "revision_invalid",
                        "Revision cannot move a feature/opening to a different patch",
                    )
                values = item.model_dump(mode="json")
            existing[item.id].update(values)
        data[field] = list(existing.values())
    if edit.thickness is not None:
        data["thickness"] = edit.thickness
    try:
        return SurfaceSpec.model_validate(data)
    except ValueError as exc:
        raise OperationError("surface_revision_invalid", str(exc)) from exc


def configure(arguments: SurfaceConfigureArguments) -> SurfaceResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    prepared = []
    committed = []
    try:
        for edit in arguments.surfaces:
            obj = organization.object_named(edit.name)
            organization.object_editable(obj)
            organization.editable(obj.data)
            meta = metadata(obj)
            if meta["revision"] != edit.expected_revision:
                fail(
                    "revision_conflict",
                    "Expected revision is stale; inspect the surface",
                )
            if content_hash(obj.data) != meta["signature"]:
                fail(
                    "stale_geometry",
                    "Generated base mesh changed; use mesh tools to preserve edits",
                )
            if obj.data.users != 1 or obj.data.shape_keys or obj.data.animation_data:
                fail(
                    "revision_invalid",
                    "Revision needs exclusive mesh without shape keys/data animation",
                )
            spec = revised(SurfaceSpec.model_validate(meta["spec"]), edit)
            mesh, geometry, stats = build(spec)
            changed = topology_hash(mesh) != meta["topology_hash"]
            prepared.append((obj, mesh, spec, geometry, stats, meta, changed))
            if changed and edit.topology_policy != "rebuild":
                fail(
                    "topology_change_required",
                    "Revision changes tessellation; explicitly permit rebuild after "
                    "reviewing dependencies",
                )
            managed_attributes = {
                "position",
                ".edge_verts",
                ".corner_vert",
                ".corner_edge",
                ".select_vert",
                ".select_edge",
                ".select_poly",
                "sharp_face",
                PATCH_ATTRIBUTE,
                REGION_ATTRIBUTE,
            }
            if changed and (
                obj.vertex_groups
                or obj.modifiers
                or obj.data.uv_layers
                or any(
                    p.material_index or p.use_smooth != spec.smooth
                    for p in obj.data.polygons
                )
                or any(a.name not in managed_attributes for a in obj.data.attributes)
            ):
                fail(
                    "revision_invalid",
                    "Rebuild cannot discard modifiers, groups or custom attributes",
                )
        if sum(len(m.vertices) for _, m, *_ in prepared) > 131072:
            fail("limit_exceeded", "Surface batch exceeds 131072 generated vertices")
        for obj, mesh, spec, geometry, stats, meta, changed in prepared:
            old = obj.data
            backup = old.copy() if not changed else old
            committed.append((obj, old, backup, str(obj[KEY]), changed))
            if changed:
                for material in old.materials:
                    mesh.materials.append(material)
                obj.data = mesh
            else:
                for target, source in zip(old.vertices, mesh.vertices, strict=True):
                    target.co = source.co
                for name in (PATCH_ATTRIBUTE, REGION_ATTRIBUTE):
                    for target, source in zip(
                        old.attributes[name].data,
                        mesh.attributes[name].data,
                        strict=True,
                    ):
                        target.value = source.value
                old.update()
            obj[KEY] = state(
                spec, obj.data, geometry, stats, previous=meta, changed=changed
            )
        bpy.context.view_layer.update()
        result = SurfaceResult(
            surfaces=[summary(o) for o, *_ in prepared],
            processing_seconds=time.perf_counter() - start,
        )
    except BaseException:
        for obj, old, backup, raw, changed in reversed(committed):
            if changed:
                obj.data = old
            else:
                for target, source in zip(old.vertices, backup.vertices, strict=True):
                    target.co = source.co
                for name in (PATCH_ATTRIBUTE, REGION_ATTRIBUTE):
                    for target, source in zip(
                        old.attributes[name].data,
                        backup.attributes[name].data,
                        strict=True,
                    ):
                        target.value = source.value
                old.update()
                bpy.data.meshes.remove(backup)
            obj[KEY] = raw
        for _, mesh, *_ in prepared:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise
    for _, old, backup, _, changed in committed:
        bpy.data.meshes.remove(old if changed else backup)
    for _, mesh, *_ in prepared:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return result


def inspect(arguments: SurfaceNetworkInspectArguments) -> SurfaceResult:
    start = time.perf_counter()
    organization.idle()
    objects = [organization.object_named(n) for n in arguments.names]
    return SurfaceResult(
        surfaces=[summary(o, arguments) for o in objects],
        processing_seconds=time.perf_counter() - start,
    )
