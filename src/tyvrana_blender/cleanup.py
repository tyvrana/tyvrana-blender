"""Staged BMesh repair; explicit scope, postconditions and whole-batch rollback."""

from types import SimpleNamespace
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

from . import geometry_qa, layer_geometry, loft, mesh, organization, surfaces
from .cleanup_models import CleanupArguments, CleanupResult, CleanupSummary
from .errors import OperationError
from .geometry_qa_models import GeometryInspectArguments, GeometryQuery


def intersections(data: Any) -> int:
    data.calc_loop_triangles()
    source = layer_geometry.Surface(
        SimpleNamespace(name=data.name),
        [v.co.copy() for v in data.vertices],
        [tuple(t.vertices) for t in data.loop_triangles],
        "",
        "",
        None,
        None,
        [t.polygon_index for t in data.loop_triangles],
    )
    surface = geometry_qa.Surface(source)
    args = GeometryInspectArguments(
        objects=[GeometryQuery(object_name=data.name, self_intersection=True)],
        tolerance=1e-7,
        worst_limit=0,
    )
    _, contacts, _, _ = geometry_qa.proximity(
        surface, surface, args, geometry_qa.Budget(200000)
    )
    return int(contacts)


def clean(bm: Any, args: CleanupArguments) -> None:
    if args.merge_distance > 0:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=args.merge_distance)
    mesh.refresh(bm)
    if args.remove_duplicates:
        seen = set()
        repeated = []
        for face in bm.faces:
            key = tuple(sorted(v.index for v in face.verts))
            if key in seen:
                repeated.append(face)
            seen.add(key)
        if repeated:
            bmesh.ops.delete(bm, geom=repeated, context="FACES_ONLY")
    if args.dissolve_degenerate:
        bmesh.ops.dissolve_degenerate(
            bm, edges=list(bm.edges), dist=max(args.merge_distance, 1e-9)
        )
    if args.remove_loose:
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
    if args.remove_islands_max_faces:
        remaining = set(bm.faces)
        islands = []
        while remaining:
            seed = remaining.pop()
            found = {seed}
            stack = [seed]
            while stack:
                for edge in stack.pop().edges:
                    for face in edge.link_faces:
                        if face in remaining:
                            remaining.remove(face)
                            found.add(face)
                            stack.append(face)
            islands.append(found)
        largest = max(islands, key=len, default=set())
        remove = [
            f
            for island in islands
            if island is not largest and len(island) <= args.remove_islands_max_faces
            for f in island
        ]
        if remove:
            bmesh.ops.delete(bm, geom=remove, context="FACES")
    if args.fill_holes_up_to:
        bmesh.ops.holes_fill(
            bm,
            edges=[e for e in bm.edges if e.is_boundary],
            sides=args.fill_holes_up_to,
        )
    if args.triangulate_ngons:
        bmesh.ops.triangulate(
            bm,
            faces=[f for f in bm.faces if len(f.verts) > 4],
            quad_method="BEAUTY",
            ngon_method="BEAUTY",
        )
    if args.recalculate_normals:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh.refresh(bm)


def commit(obj: Any, data: Any) -> None:
    obj.data = data


def cleanup(args: CleanupArguments) -> CleanupResult:
    organization.idle(mutate=not args.preview)
    objects = [organization.object_named(n) for n in args.names]
    if any(o.type != "MESH" or o.data.users != 1 for o in objects):
        raise OperationError(
            "cleanup_invalid", "Cleanup requires exclusive native mesh data"
        )
    if (
        sum(
            len(o.data.vertices)
            + len(o.data.edges)
            + len(o.data.polygons)
            + len(o.data.loops)
            for o in objects
        )
        > mesh.MAX_WORK_ELEMENTS
    ):
        raise OperationError(
            "cleanup_limit_exceeded", "Cleanup batch exceeds2M mesh elements"
        )
    staged = []
    committed = []
    rows = []
    try:
        for obj in objects:
            mesh.editable(obj)
            with mesh.snapshot(obj) as bm:
                before_vertices, before_faces = len(bm.verts), len(bm.faces)
                before = mesh.summary(obj, bm).geometry_sha256
                clean(bm, args)
                if not bm.faces:
                    raise OperationError(
                        "cleanup_postcondition_failed",
                        "Cleanup would remove every face",
                    )
                boundary = sum(e.is_boundary for e in bm.edges)
                nonmanifold = sum(
                    not e.is_manifold and not e.is_boundary for e in bm.edges
                )
                if nonmanifold or (args.require_closed and boundary):
                    raise OperationError(
                        "cleanup_postcondition_failed",
                        "Non-manifold edges or unclosed boundaries remain; "
                        "revise the responsible region explicitly",
                    )
                changed = before != mesh.summary(obj, bm).geometry_sha256
                data = obj.data.copy()
                staged.append((obj, data))
                bm.to_mesh(data)
                data.update()
                contacts = intersections(data) if args.check_self_intersection else None
                if contacts:
                    raise OperationError(
                        "cleanup_postcondition_failed",
                        "Self-intersections remain; revise construction or use "
                        "an explicitly selected remesh workflow",
                    )
                managed = loft.KEY in obj or surfaces.KEY in obj
                if changed and managed and not args.detach_construction:
                    raise OperationError(
                        "cleanup_construction_protected",
                        "Cleanup changes managed geometry; revise its semantic "
                        "inputs or explicitly detach_construction",
                    )
                rows.append(
                    CleanupSummary(
                        name=obj.name,
                        changed=changed,
                        vertices_before=before_vertices,
                        vertices_after=len(bm.verts),
                        faces_before=before_faces,
                        faces_after=len(bm.faces),
                        boundary_edges=boundary,
                        non_manifold_edges=nonmanifold,
                        max_valence=max(len(v.link_edges) for v in bm.verts),
                        self_contacts=contacts,
                        construction_detached=bool(
                            changed and managed and args.detach_construction
                        ),
                    )
                )
        if not args.preview:
            for (obj, data), row in zip(staged, rows, strict=True):
                if not row.changed:
                    continue
                committed.append((obj, obj.data, dict(obj.items())))
                commit(obj, data)
                if row.construction_detached:
                    for key in (loft.KEY, surfaces.KEY):
                        if key in obj:
                            del obj[key]
            bpy.context.view_layer.update()
        result = CleanupResult(
            objects=rows, preview=args.preview, committed=not args.preview
        )
    except BaseException:
        for obj, old, props in reversed(committed):
            obj.data = old
            for key in (loft.KEY, surfaces.KEY):
                if key in props:
                    obj[key] = props[key]
        for _, data in staged:
            if data.users == 0:
                bpy.data.meshes.remove(data)
        raise
    for _, old, _ in committed:
        bpy.data.meshes.remove(old)
    for _, data in staged:
        if data.users == 0:
            bpy.data.meshes.remove(data)
    return result
