"""Main-thread authored-mesh editing with staged BMesh transactions."""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import islice
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]

from .mesh_models import (
    MAX_FACE_VERTICES,
    MAX_QUERY_RESULTS,
    BoundedIndices,
    EdgeDetail,
    EdgeQueryResult,
    ElementCounts,
    ElementSelection,
    FaceDetail,
    FaceQueryResult,
    ManifoldSummary,
    MeshBevelArguments,
    MeshDeleteArguments,
    MeshEditResult,
    MeshExtrudeArguments,
    MeshInsetArguments,
    MeshMergeArguments,
    MeshNormalsArguments,
    MeshQueryArguments,
    MeshQueryResult,
    MeshSeamArguments,
    MeshSelectionArguments,
    MeshShadingArguments,
    MeshSubdivideArguments,
    MeshSummary,
    MeshTransformArguments,
    VertexDetail,
    VertexQueryResult,
)
from .mesh_selectors import SelectionError, select
from .operations import OperationError
from .uv import mesh_users

# Includes vertices, edges, faces and corners, across one staged authored mesh.
MAX_WORK_ELEMENTS = 2_000_000
_DOMAINS = ("verts", "edges", "faces")


def refresh(bm: Any) -> None:
    bm.normal_update()
    for name in _DOMAINS:
        elements = getattr(bm, name)
        elements.ensure_lookup_table()
        elements.index_update()


def work_size(bm: Any) -> int:
    return (
        len(bm.verts)
        + len(bm.edges)
        + len(bm.faces)
        + sum(len(face.loops) for face in bm.faces)
    )


def check_budget(size: int) -> None:
    if size > MAX_WORK_ELEMENTS:
        raise OperationError(
            "invalid_context", "Mesh exceeds the bounded modeling capacity"
        )


@contextmanager
def snapshot(obj: Any) -> Iterator[Any]:
    mesh = obj.data
    if mesh.is_editmode:
        live = bmesh.from_edit_mesh(mesh)
        check_budget(work_size(live))
        bm = live.copy()
    else:
        check_budget(
            len(mesh.vertices) + len(mesh.edges) + len(mesh.polygons) + len(mesh.loops)
        )
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
        except BaseException:
            bm.free()
            raise
    try:
        refresh(bm)
        yield bm
    finally:
        bm.free()


def summary(obj: Any, bm: Any, mesh: Any | None = None) -> MeshSummary:
    mesh = obj.data if mesh is None else mesh
    minimum = (
        [min(float(v.co[i]) for v in bm.verts) for i in range(3)] if bm.verts else None
    )
    maximum = (
        [max(float(v.co[i]) for v in bm.verts) for i in range(3)] if bm.verts else None
    )
    manifold = sum(edge.is_manifold for edge in bm.edges)
    return MeshSummary(
        object_name=obj.name,
        mesh_name=mesh.name,
        mesh_users=mesh_users(mesh),
        vertex_count=len(bm.verts),
        edge_count=len(bm.edges),
        face_count=len(bm.faces),
        loop_count=sum(len(face.loops) for face in bm.faces),
        bounds_min=minimum,
        bounds_max=maximum,
        material_slot_count=len(mesh.materials),
        uv_map_count=len(mesh.uv_layers),
        has_shape_keys=mesh.shape_keys is not None,
        manifold_summary=ManifoldSummary(
            boundary_edge_count=sum(edge.is_boundary for edge in bm.edges),
            manifold_edge_count=manifold,
            non_manifold_edge_count=len(bm.edges) - manifold,
            loose_vertex_count=sum(not vertex.link_edges for vertex in bm.verts),
            loose_edge_count=sum(edge.is_wire for edge in bm.edges),
        ),
    )


def inspect(obj: Any) -> MeshSummary:
    with snapshot(obj) as bm:
        return summary(obj, bm)


def query(obj: Any, arguments: MeshQueryArguments) -> MeshQueryResult:
    with snapshot(obj) as bm:
        total = 0
        vertices: list[VertexDetail] = []
        edges: list[EdgeDetail] = []
        faces: list[FaceDetail] = []
        try:
            for element in select(bm, arguments.selector):
                total += 1
                if total > arguments.limit:
                    continue
                match arguments.selector.domain:
                    case "vertex":
                        vertices.append(
                            VertexDetail(index=element.index, co=list(element.co))
                        )
                    case "edge":
                        edges.append(
                            EdgeDetail(
                                index=element.index,
                                vertices=sorted(
                                    vertex.index for vertex in element.verts
                                ),
                                seam=bool(element.seam),
                                sharp=not element.smooth,
                                boundary=bool(element.is_boundary),
                                manifold=bool(element.is_manifold),
                            )
                        )
                    case "face":
                        indices = [
                            vertex.index
                            for vertex in islice(element.verts, MAX_FACE_VERTICES)
                        ]
                        faces.append(
                            FaceDetail(
                                index=element.index,
                                vertices=indices[:MAX_FACE_VERTICES],
                                vertex_count=len(element.verts),
                                vertices_truncated=len(element.verts)
                                > MAX_FACE_VERTICES,
                                center=list(element.calc_center_median()),
                                normal=list(element.normal),
                                area=float(element.calc_area()),
                                material_index=element.material_index,
                                smooth=bool(element.smooth),
                            )
                        )
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        common = {
            "object_name": obj.name,
            "matched_count": total,
            "truncated": total > arguments.limit,
        }
        match arguments.selector.domain:
            case "vertex":
                return VertexQueryResult(**common, elements=vertices)
            case "edge":
                return EdgeQueryResult(**common, elements=edges)
            case "face":
                return FaceQueryResult(**common, elements=faces)


def editable(obj: Any) -> None:
    mesh = obj.data
    if (
        bpy.context.mode != "OBJECT"
        or mesh.is_editmode
        or bpy.app.is_job_running("RENDER")
        or not bpy.context.scene.is_editable
        or not obj.is_editable
        or not mesh.is_editable
        or obj.library is not None
        or mesh.library is not None
        or obj.override_library is not None
        or mesh.override_library is not None
    ):
        raise OperationError(
            "invalid_context",
            "Mesh editing requires Object Mode and editable local data",
        )
    if mesh.shape_keys is not None:
        raise OperationError(
            "mesh_has_shape_keys",
            "Shape-key meshes require a dedicated editing workflow",
        )
    if mesh.has_custom_normals:
        raise OperationError(
            "invalid_context",
            "Preserve authored custom normals before direct mesh editing",
        )
    if obj.modifiers or mesh.animation_data is not None:
        raise OperationError(
            "invalid_context",
            "Direct mesh editing does not support modifiers or mesh animation",
        )
    if any(
        child.parent == obj and child.parent_type in {"VERTEX", "VERTEX_3"}
        for child in bpy.data.objects
    ):
        raise OperationError(
            "invalid_context",
            "Vertex-parented children require an explicit topology remapping workflow",
        )


class _Selection:
    def __init__(self, bm: Any) -> None:
        self.elements = {
            name: {
                element: (element.select, element.hide) for element in getattr(bm, name)
            }
            for name in _DOMAINS
        }
        self.history = list(bm.select_history)
        self.active = bm.faces.active
        self.uv_faces = {face: face.uv_select for face in bm.faces}
        self.uv_loops = {
            loop: (loop.uv_select_vert, loop.uv_select_edge)
            for face in bm.faces
            for loop in face.loops
        }
        self.uv_valid = bm.uv_select_sync_valid

    def restore(self, bm: Any) -> None:
        for name in _DOMAINS:
            for element in getattr(bm, name):
                element.hide = self.elements[name].get(element, (False, False))[1]
        for name in reversed(_DOMAINS):
            for element in getattr(bm, name):
                element.select = self.elements[name].get(element, (False, False))[0]
        for face in bm.faces:
            face.uv_select = self.uv_faces.get(face, False)
            for loop in face.loops:
                loop.uv_select_vert, loop.uv_select_edge = self.uv_loops.get(
                    loop, (False, False)
                )
        bm.uv_select_sync_valid = self.uv_valid
        bm.select_history.clear()
        for element in self.history:
            if element.is_valid and element.select:
                bm.select_history.add(element)
        bm.faces.active = (
            self.active if self.active is not None and self.active.is_valid else None
        )


def region_edges(faces: list[Any]) -> list[Any]:
    edges = sorted(
        {edge for face in faces for edge in face.edges}, key=lambda edge: edge.index
    )
    chosen = set(faces)
    if any(len(edge.link_faces) > 2 for edge in edges):
        raise OperationError(
            "invalid_arguments", "Face region touches non-manifold edges"
        )
    if not any(sum(face in chosen for face in edge.link_faces) == 1 for edge in edges):
        raise OperationError("invalid_arguments", "Face region needs a boundary")
    return edges


def selected_vertices(selected: list[Any], domain: str) -> list[Any]:
    vertices = (
        selected
        if domain == "vertex"
        else {vertex for item in selected for vertex in item.verts}
    )
    return sorted(vertices, key=lambda vertex: vertex.index)


def center(vertices: list[Any]) -> Any:
    return Vector(
        tuple(
            math.fsum(float(vertex.co[i]) for vertex in vertices) / len(vertices)
            for i in range(3)
        )
    )


def apply_transform(
    bm: Any, vertices: list[Any], arguments: MeshTransformArguments
) -> None:
    pivot = (
        center(vertices)
        if arguments.pivot == "median"
        else Vector((0, 0, 0))
        if arguments.pivot == "origin"
        else Vector(arguments.pivot)
    )
    translation = Vector(arguments.translation or (0, 0, 0))
    rotation = Euler(arguments.rotation or (0, 0, 0), "XYZ").to_matrix().to_4x4()
    scaling = Matrix.Diagonal((*(arguments.scale or (1, 1, 1)), 1))
    matrix = (
        Matrix.Translation(translation)
        @ Matrix.Translation(pivot)
        @ rotation
        @ scaling
        @ Matrix.Translation(-pivot)
    )
    bmesh.ops.transform(bm, verts=vertices, matrix=matrix)


def edit(
    obj: Any, arguments: MeshSelectionArguments | MeshNormalsArguments
) -> MeshEditResult:
    editable(obj)
    original = obj.data
    with snapshot(obj) as bm:
        try:
            selected = (
                list(bm.faces)
                if isinstance(arguments, MeshNormalsArguments)
                else list(select(bm, arguments.selector))
            )
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        if not selected:
            raise OperationError("mesh_selection_empty", "Selector matched no geometry")
        domain = (
            "face"
            if isinstance(arguments, MeshNormalsArguments)
            else arguments.selector.domain
        )
        before = {name: list(getattr(bm, name)) for name in _DOMAINS}
        selection = _Selection(bm)
        region: list[Any] | None = None
        transformed: int | None = None
        changed_edges: int | None = None
        changed_faces: int | None = None
        size = work_size(bm)
        candidate = None
        try:
            if isinstance(arguments, MeshTransformArguments):
                vertices = selected_vertices(selected, domain)
                transformed = len(vertices)
                apply_transform(bm, vertices, arguments)
            elif isinstance(arguments, MeshExtrudeArguments):
                edges = region_edges(selected)
                check_budget(size + sum(len(face.loops) for face in selected) * 12)
                result = bmesh.ops.extrude_face_region(
                    bm, geom=selected + edges, use_keep_orig=False
                )
                vertices = [
                    item
                    for item in result["geom"]
                    if isinstance(item, bmesh.types.BMVert)
                ]
                region = [
                    item
                    for item in result["geom"]
                    if isinstance(item, bmesh.types.BMFace)
                ]
                pivot = center(vertices)
                matrix = (
                    Matrix.Translation(Vector(arguments.offset))
                    @ Matrix.Translation(pivot)
                    @ Matrix.Diagonal((*arguments.scale, 1))
                    @ Matrix.Translation(-pivot)
                )
                bmesh.ops.transform(bm, verts=vertices, matrix=matrix)
                transformed = len(vertices)
            elif isinstance(arguments, MeshInsetArguments):
                region_edges(selected)
                check_budget(size + sum(len(face.loops) for face in selected) * 12)
                bmesh.ops.inset_region(
                    bm,
                    faces=selected,
                    thickness=arguments.thickness,
                    depth=arguments.depth,
                    use_boundary=True,
                    use_even_offset=arguments.even_offset,
                    use_interpolate=True,
                    use_relative_offset=False,
                    use_edge_rail=False,
                    use_outset=False,
                )
                region = selected
            elif isinstance(arguments, MeshBevelArguments):
                if any(
                    not edge.is_manifold or not edge.is_contiguous for edge in selected
                ):
                    raise OperationError(
                        "invalid_arguments",
                        "Bevel requires consistently wound two-face manifold edges",
                    )
                check_budget(size + len(selected) * (arguments.segments + 1) ** 2 * 64)
                bmesh.ops.bevel(
                    bm,
                    geom=selected,
                    offset=arguments.width,
                    offset_type="OFFSET",
                    segments=arguments.segments,
                    profile=arguments.profile,
                    affect="EDGES",
                    clamp_overlap=True,
                    material=-1,
                    loop_slide=True,
                    mark_seam=True,
                    mark_sharp=True,
                )
            elif isinstance(arguments, MeshSubdivideArguments):
                check_budget(size + len(selected) * (arguments.cuts + 1) ** 2 * 16)
                bmesh.ops.subdivide_edges(
                    bm,
                    edges=selected,
                    cuts=arguments.cuts,
                    smooth=arguments.smooth,
                    use_grid_fill=True,
                    use_single_edge=True,
                    quad_corner_type="STRAIGHT_CUT",
                )
            elif isinstance(arguments, MeshDeleteArguments):
                context = {
                    "vertex": "VERTS",
                    "edge": "EDGES_FACES",
                    "face": "FACES_ONLY",
                }[domain]
                if arguments.face_mode == "faces_and_unused":
                    context = "FACES"
                bmesh.ops.delete(bm, geom=selected, context=context)
            elif isinstance(arguments, MeshMergeArguments):
                if len(selected) < 2:
                    raise OperationError(
                        "invalid_arguments", "Merge requires at least two vertices"
                    )
                # Native point merge averages vertex custom data; loop UV data
                # remains per face corner rather than welding UV seams implicitly.
                bmesh.ops.pointmerge(bm, verts=selected, merge_co=center(selected))
            elif isinstance(arguments, MeshSeamArguments):
                changed_edges = sum(edge.seam != arguments.seam for edge in selected)
                for edge in selected:
                    edge.seam = arguments.seam
            elif isinstance(arguments, MeshShadingArguments):
                changed_faces = sum(
                    face.smooth != arguments.smooth for face in selected
                )
                for face in selected:
                    face.smooth = arguments.smooth
            else:
                assert isinstance(arguments, MeshNormalsArguments)
                bmesh.ops.recalc_face_normals(bm, faces=selected)
                if arguments.inside:
                    for face in bm.faces:
                        face.normal_flip()
            check_budget(work_size(bm))
            refresh(bm)
            if any(
                not math.isfinite(value) for vertex in bm.verts for value in vertex.co
            ):
                raise OperationError(
                    "invalid_arguments", "Edit produced non-finite vertex coordinates"
                )
            if any(
                not math.isfinite(face.calc_area())
                or any(not math.isfinite(value) for value in face.normal)
                for face in bm.faces
            ):
                raise OperationError(
                    "invalid_arguments", "Edit produced invalid derived geometry"
                )
            selection.restore(bm)
            surviving = {
                name: sum(element.is_valid for element in before[name])
                for name in _DOMAINS
            }
            created = ElementCounts(
                **{
                    key: len(getattr(bm, name)) - surviving[name]
                    for key, name in zip(
                        ("vertices", "edges", "faces"), _DOMAINS, strict=True
                    )
                }
            )
            removed = ElementCounts(
                **{
                    key: len(before[name]) - surviving[name]
                    for key, name in zip(
                        ("vertices", "edges", "faces"), _DOMAINS, strict=True
                    )
                }
            )
            region_result = None
            if region is not None:
                indices = sorted(face.index for face in region if face.is_valid)
                region_result = BoundedIndices(
                    indices=indices[:MAX_QUERY_RESULTS],
                    total=len(indices),
                    truncated=len(indices) > MAX_QUERY_RESULTS,
                )
            candidate = original.copy()
            bm.to_mesh(candidate)
            candidate.update()
            # Reject unexpected loss of named custom-data schemas before commit.
            optional_native = {
                "position",
                "sharp_face",
                "sharp_edge",
                "uv_seam",
                "material_index",
            }
            required = {
                (a.name, a.domain, a.data_type)
                for a in original.attributes
                if not a.name.startswith(".") and a.name not in optional_native
            }
            retained = {(a.name, a.domain, a.data_type) for a in candidate.attributes}
            if not required.issubset(retained):
                raise OperationError(
                    "invalid_context",
                    "BMesh cannot preserve this mesh's custom-data layout",
                )
            for layer in original.uv_layers:
                if layer.active:
                    candidate.uv_layers.active = candidate.uv_layers[layer.name]
                if layer.active_render:
                    candidate.uv_layers[layer.name].active_render = True
            result_summary = summary(obj, bm, candidate).model_copy(
                update={"mesh_users": 1}
            )
            response = MeshEditResult(
                object_name=obj.name,
                selected=ElementSelection(domain=domain, count=len(selected)),
                created=created,
                removed=removed,
                mesh=result_summary,
                region_faces=region_result,
                transformed_vertices=transformed,
                changed_edges=changed_edges,
                changed_faces=changed_faces,
            )
            # All operations and result validation finish before the single object
            # data-pointer commit. Other users retain the original datablock.
            obj.data = candidate
        except (RuntimeError, ValueError, OverflowError) as exc:
            if candidate is not None:
                if obj.data == candidate:
                    obj.data = original
                bpy.data.meshes.remove(candidate)
            raise OperationError(
                "operation_failed", "Blender could not complete the mesh edit"
            ) from exc
        except BaseException:
            if candidate is not None:
                if obj.data == candidate:
                    obj.data = original
                bpy.data.meshes.remove(candidate)
            raise
        if original.users == 0:
            bpy.data.meshes.remove(original)
        return response
