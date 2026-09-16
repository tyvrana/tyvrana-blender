"""Explicit target-only retopology using native BMesh and evaluated projection."""

import math
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]
from mathutils.geometry import intersect_line_line  # type: ignore[import-not-found]

from . import mesh, modifiers
from . import retopo_geometry as geometry
from .errors import OperationError
from .mesh_models import ElementCounts
from .mesh_selectors import SelectionError, select
from .retopo_models import (
    MAX_BOUNDARY_EDGES,
    MAX_RELAX_WORK,
    MAX_SELECTED_VERTICES,
    MAX_TARGET_ELEMENTS,
    PatchFrame,
    ProjectionSettings,
    RetopoBridgeArguments,
    RetopoCollapseArguments,
    RetopoCreateArguments,
    RetopoCreateResult,
    RetopoEditArguments,
    RetopoEditResult,
    RetopoExtrudeArguments,
    RetopoFillArguments,
    RetopoInsertArguments,
    RetopoInspectArguments,
    RetopoProjectArguments,
    RetopoRelaxArguments,
    RetopoRotateArguments,
    RetopoSeedArguments,
    RetopoSlideArguments,
    RetopoSubdivideArguments,
    RetopoSummary,
)

SAFE_ATTRIBUTES = {
    "position",
    ".edge_verts",
    ".corner_vert",
    ".corner_edge",
    ".select_vert",
    ".select_edge",
    ".select_poly",
    ".hide_vert",
    ".hide_edge",
    ".hide_poly",
    "material_index",
    "sharp_face",
    "sharp_edge",
    "uv_seam",
}


def target_budget(data: Any) -> None:
    if modifiers.data_size(data) > MAX_TARGET_ELEMENTS:
        raise OperationError(
            "retopo_geometry_limit",
            "Authored target exceeds 100,000 total geometry elements",
        )
    if (
        len(data.attributes) > 32
        or sum(len(a.data) * 16 for a in data.attributes) > 8_000_000
    ):
        raise OperationError(
            "retopo_geometry_limit",
            "Target attribute storage exceeds inspection/staging capacity",
        )
    modifiers.check_geometry(data)
    for vertex in data.vertices:
        geometry.checked_point(vertex.co)


def modifier_blockers(source: Any, target: Any) -> list[str]:
    kinds = [m.type for m in target.modifiers]
    if kinds and kinds[-1] == "SUBSURF":
        kinds = kinds[:-1]
    if kinds not in ([], ["MIRROR"], ["SHRINKWRAP"], ["MIRROR", "SHRINKWRAP"]):
        return ["target_modifier_stack"]
    for mod in target.modifiers:
        if mod.type == "MIRROR":
            if (
                sum(mod.use_axis) != 1
                or mod.mirror_object
                or any(mod.use_bisect_axis)
                or mod.use_clip
                or not 0 <= mod.merge_threshold <= 0.1
            ):
                return ["target_mirror_configuration"]
        elif mod.type == "SHRINKWRAP" and (
            mod.target != source
            or mod.wrap_method != "NEAREST_SURFACEPOINT"
            or mod.wrap_mode not in {"ON_SURFACE", "ABOVE_SURFACE"}
            or mod.vertex_group
            or mod.auxiliary_target
            or mod.subsurf_levels
            or not -1 <= mod.offset <= 1
        ):
            return ["target_shrinkwrap_configuration"]
    return []


def blockers(source: Any, target: Any) -> list[str]:
    data = target.data
    result: list[str] = []
    conditions = {
        "target_read_only": not bpy.context.scene.is_editable
        or not target.is_editable
        or not data.is_editable
        or bool(
            target.library
            or data.library
            or target.override_library
            or data.override_library
        ),
        "mesh_has_shape_keys": data.shape_keys is not None,
        "target_animation": bool(target.animation_data or data.animation_data),
        "target_custom_normals": data.has_custom_normals,
        "target_uv_maps": bool(data.uv_layers),
        "target_vertex_groups": bool(target.vertex_groups),
        "target_attributes": any(
            a.name not in SAFE_ATTRIBUTES for a in data.attributes
        ),
        "target_hidden_geometry": any(
            e.hide for e in [*data.vertices, *data.edges, *data.polygons]
        ),
        "target_deformation_relationship": bool(target.constraints)
        or bool(target.parent and target.parent.type in {"ARMATURE", "LATTICE"})
        or any(
            o.parent == target and o.parent_type in {"VERTEX", "VERTEX_3"}
            for o in bpy.data.objects
        )
        or any(
            (m.type == "MESH_DEFORM" and m.object == target)
            or (m.type == "SURFACE_DEFORM" and m.target == target)
            for o in bpy.data.objects
            if o != target
            for m in o.modifiers
        ),
    }
    result.extend(code for code, condition in conditions.items() if condition)
    result.extend(modifier_blockers(source, target))
    return result


def mirror_half(target: Any, bm: Any) -> None:
    for mod in target.modifiers:
        if mod.type != "MIRROR" or not bm.verts:
            continue
        axis = list(mod.use_axis).index(True)
        values = [v.co[axis] for v in bm.verts]
        if min(values) < -1e-6 and max(values) > 1e-6:
            raise OperationError(
                "retopo_modifier_unsupported",
                "Mirror target must remain on one side of its object-origin plane",
            )
        if bm.faces and max(abs(v) for v in values) <= 1e-6:
            raise OperationError(
                "retopo_modifier_unsupported",
                "A target entirely in the mirror plane would overlap itself",
            )


def objects(arguments: RetopoInspectArguments) -> tuple[Any, Any]:
    source = modifiers.object_mesh(arguments.source_object)
    target = modifiers.object_mesh(arguments.target_object)
    if source == target:
        raise OperationError(
            "invalid_arguments", "Source and target must be separate objects"
        )
    target_budget(target.data)
    geometry.matrix(target)
    return source, target


def create_target(arguments: RetopoCreateArguments) -> RetopoCreateResult:
    source = modifiers.object_mesh(arguments.source_object)
    if not bpy.context.scene.is_editable:
        raise OperationError(
            "invalid_context", "Target creation requires an editable scene"
        )
    with geometry.surface(source) as (reference, _):
        transform = reference.matrix
        location, rotation, scale = transform.decompose()
        reconstructed = Matrix.LocRotScale(location, rotation, scale)
        if (
            max(
                abs(transform[i][j] - reconstructed[i][j])
                for i in range(4)
                for j in range(4)
            )
            > 1e-5
        ):
            raise OperationError(
                "invalid_context",
                "Cannot copy source world shear to an independent target transform",
            )
        data = bpy.data.meshes.new(arguments.name)
        target = None
        try:
            target = bpy.data.objects.new(arguments.name, data)
            bpy.context.scene.collection.objects.link(target)
            target.matrix_world = transform
            bpy.context.view_layer.update()
            with mesh.snapshot(target) as bm:
                result = RetopoCreateResult(
                    source_object=source.name,
                    target_object=target.name,
                    target_mesh=data.name,
                    matrix_world=[list(row) for row in target.matrix_world],
                    mesh=mesh.summary(target, bm),
                )
            return result
        except BaseException:
            if target is not None:
                bpy.data.objects.remove(target, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)
            raise


def inspect(arguments: RetopoInspectArguments) -> RetopoSummary:
    source, target = objects(arguments)
    with geometry.surface(source, target) as (reference, depsgraph):
        with mesh.snapshot(target) as bm:
            quality = geometry.quality(target, bm)
            authored = geometry.correspondence(reference, bm, geometry.matrix(target))
        with geometry.evaluated_mesh(target, depsgraph) as (data, transform):
            data.calc_loop_triangles()
            evaluated_summary = geometry.surface_summary(target, data, transform)
            bm = bmesh.new()
            try:
                bm.from_mesh(data)
                mesh.refresh(bm)
                evaluated = geometry.correspondence(reference, bm, transform)
            finally:
                bm.free()
        return RetopoSummary(
            source_object=source.name,
            target_object=target.name,
            source=reference.summary,
            target=quality,
            target_modifiers=modifiers.inspect(target).modifiers,
            evaluated_target=evaluated_summary,
            authored_correspondence=authored,
            evaluated_correspondence=evaluated,
            blockers=blockers(source, target),
        )


def selection(bm: Any, selector: Any, maximum: int) -> list[Any]:
    try:
        result = list(select(bm, selector))
    except SelectionError as exc:
        raise OperationError("invalid_arguments", str(exc)) from exc
    if not result or len(result) > maximum:
        raise OperationError(
            "retopo_selection_invalid",
            f"Select between 1 and {maximum} authored elements",
        )
    return result


def boundary(edges: list[Any], *, closed: bool) -> list[Any]:
    if any(not edge.is_boundary for edge in edges):
        raise OperationError(
            "retopo_boundary_invalid", "Select only one-face boundary edges"
        )
    components = geometry.boundary_components(edges)
    if (
        len(components) != 1
        or components[0][2] == "branched"
        or (closed and components[0][2] != "closed")
    ):
        raise OperationError(
            "retopo_boundary_invalid",
            "Select one connected nonbranching boundary, closed for bridging",
        )
    return components[0][1]


def project(
    reference: geometry.Surface,
    target: Any,
    vertices: list[Any],
    arguments: ProjectionSettings,
) -> list[float]:
    transform = geometry.matrix(target)
    inverse = transform.inverted()
    proposed = []
    distances = []
    mirror_axes = [
        list(mod.use_axis).index(True)
        for mod in target.modifiers
        if mod.type == "MIRROR"
    ]
    for vertex in vertices:
        axis = next((a for a in mirror_axes if abs(vertex.co[a]) <= 1e-6), None)
        if axis is None:
            location, normal, distance = reference.nearest(
                transform @ vertex.co, arguments.max_projection_distance
            )
        else:
            plane_normal = inverse.transposed().to_3x3() @ Vector(
                [float(i == axis) for i in range(3)]
            )
            plane_normal.normalize()
            location, normal, distance = reference.nearest_on_plane(
                transform @ vertex.co,
                transform.translation,
                plane_normal,
                arguments.max_projection_distance,
            )
            normal = normal - plane_normal * normal.dot(plane_normal)
            if normal.length <= 1e-8 and arguments.surface_offset:
                raise OperationError(
                    "retopo_projection_failed",
                    "No offset direction within the Mirror plane",
                )
            normal.normalize()
        coordinate = inverse @ (location + normal * arguments.surface_offset)
        if axis is not None:
            coordinate[axis] = 0
        proposed.append(geometry.checked_point(coordinate))
        distances.append(distance)
    for vertex, coordinate in zip(vertices, proposed, strict=True):
        vertex.co = coordinate
    return distances


def seed(
    reference: geometry.Surface, target: Any, bm: Any, arguments: RetopoSeedArguments
) -> tuple[list[Any], list[float], PatchFrame]:
    center, normal, _ = reference.nearest(
        reference.matrix @ Vector(arguments.center), arguments.max_projection_distance
    )
    hint = reference.matrix.to_3x3() @ Vector(arguments.tangent_direction)
    hint.normalize()
    tangent = hint - normal * hint.dot(normal)
    if tangent.length < 1e-6:
        raise OperationError(
            "retopo_orientation_invalid",
            "Tangent direction is parallel to the source normal",
        )
    tangent.normalize()
    bitangent = normal.cross(tangent).normalized()
    inverse = geometry.matrix(target).inverted()
    rows = []
    for j in range(arguments.v_segments + 1):
        rows.append(
            [
                bm.verts.new(
                    inverse
                    @ (
                        center
                        + tangent * arguments.width * (i / arguments.u_segments - 0.5)
                        + bitangent
                        * arguments.height
                        * (j / arguments.v_segments - 0.5)
                    )
                )
                for i in range(arguments.u_segments + 1)
            ]
        )
    vertices = [v for row in rows for v in row]
    distances = project(reference, target, vertices, arguments)
    faces = []
    for j in range(arguments.v_segments):
        for i in range(arguments.u_segments):
            faces.append(
                bm.faces.new(
                    (rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i])
                )
            )
    return (
        faces,
        distances,
        PatchFrame(
            center_source=list(reference.matrix.inverted() @ center),
            center_world=list(center),
            normal_world=list(normal),
            tangent_u_world=list(tangent),
            tangent_v_world=list(bitangent),
        ),
    )


def oriented_loop(vertices: list[Any]) -> list[Any]:
    a, b = vertices[:2]
    edge = next(e for e in a.link_edges if b in e.verts)
    if any(
        loop.vert == a and loop.link_loop_next.vert == b for loop in edge.link_loops
    ):
        return vertices
    return [vertices[0], *vertices[:0:-1]]


def crossing_boundaries(edges_a: list[Any], edges_b: list[Any]) -> bool:
    """Detect spatial segment crossings, including coincident parallel segments."""
    edges = edges_a + edges_b
    for i, first in enumerate(edges):
        a, b = (v.co for v in first.verts)
        u = b - a
        if u.length <= 1e-7:
            return True
        for second in edges[i + 1 :]:
            if set(first.verts) & set(second.verts):
                continue
            c, d = (v.co for v in second.verts)
            v = d - c
            if v.length <= 1e-7:
                return True
            tolerance = 1e-6 * max(1, u.length, v.length)
            points = intersect_line_line(a, b, c, d)
            if points is not None:
                p, q = points
                if (
                    (p - q).length <= tolerance
                    and -1e-6 <= (p - a).dot(u) / u.length_squared <= 1 + 1e-6
                    and -1e-6 <= (q - c).dot(v) / v.length_squared <= 1 + 1e-6
                ):
                    return True
            else:
                for point, origin, direction in (
                    (a, c, v),
                    (b, c, v),
                    (c, a, u),
                    (d, a, u),
                ):
                    t = max(
                        0,
                        min(
                            1,
                            (point - origin).dot(direction) / direction.length_squared,
                        ),
                    )
                    if (point - origin - direction * t).length <= tolerance:
                        return True
    return False


def bridge(bm: Any, arguments: RetopoBridgeArguments) -> int:
    edges_a = selection(bm, arguments.loop_a, MAX_BOUNDARY_EDGES)
    edges_b = selection(bm, arguments.loop_b, MAX_BOUNDARY_EDGES)
    a, b = boundary(edges_a, closed=True), boundary(edges_b, closed=True)
    if set(a) & set(b) or len(a) != len(b) or abs(arguments.twist) >= len(a):
        raise OperationError(
            "retopo_boundary_invalid",
            "Bridge disjoint equal-count loops; twist must be smaller than the count",
        )
    if crossing_boundaries(edges_a, edges_b):
        raise OperationError(
            "retopo_boundary_invalid",
            "Boundary loops intersect or contain zero-length edges",
        )
    a = oriented_loop(a)
    b = list(reversed(oriented_loop(b)))
    costs = sorted(
        math.fsum((v.co - b[(i + shift) % len(b)].co).length for i, v in enumerate(a))
        for shift in range(len(b))
    )
    if len(costs) > 1 and costs[1] - costs[0] <= max(1e-7, costs[0] * 1e-6):
        raise OperationError(
            "retopo_bridge_ambiguous",
            "Loop correspondence has multiple equally close rotations",
        )
    result = bmesh.ops.bridge_loops(
        bm,
        edges=edges_a + edges_b,
        use_pairs=True,
        use_cyclic=False,
        use_merge=False,
        twist_offset=arguments.twist,
    )
    if len(result["faces"]) != len(a):
        raise OperationError(
            "retopo_bridge_invalid",
            "Native bridge did not create the expected quad band",
        )
    if arguments.segments > 1:
        bmesh.ops.subdivide_edges(
            bm,
            edges=result["edges"],
            cuts=arguments.segments - 1,
            use_grid_fill=True,
            use_single_edge=False,
            quad_corner_type="STRAIGHT_CUT",
        )
    return len(edges_a) + len(edges_b)


def validate(
    bm: Any,
    reference: geometry.Surface,
    target: Any,
    affected: set[Any],
    created: list[Any],
    *,
    quad_only: bool = True,
    allow_straight: bool = False,
) -> None:
    mesh.refresh(bm)
    if mesh.work_size(bm) > MAX_TARGET_ELEMENTS:
        raise OperationError(
            "retopo_geometry_limit", "Result exceeds target geometry capacity"
        )
    if any(
        len(e.link_faces) > 2 or (e.is_manifold and not e.is_contiguous)
        for e in bm.edges
    ) or geometry.non_manifold_vertices(bm):
        raise OperationError(
            "retopo_topology_invalid",
            "Result has nonmanifold edges/vertex fans or inconsistent winding",
        )
    signatures = [frozenset(v.index for v in f.verts) for f in bm.faces]
    if len(set(signatures)) != len(signatures) or any(
        len(f.verts) not in ({4} if quad_only else {3, 4}) for f in created
    ):
        raise OperationError(
            "retopo_topology_invalid", "Construction requires distinct supported faces"
        )
    affected_indices = {f.index for f in affected}
    with geometry.world_mesh(bm, geometry.matrix(target)) as world:
        for face in world.faces:
            if face.calc_area() <= geometry.AREA_EPSILON or face.normal.length == 0:
                raise OperationError(
                    "retopo_topology_invalid", "Degenerate target face"
                )
            if face.index not in affected_indices:
                continue
            if len(face.verts) == 4:
                points = [v.co for v in face.verts]
                for diagonal in (0, 1):
                    a, b, c, d = points[diagonal:] + points[:diagonal]
                    first = (b - a).cross(c - a)
                    second = (c - a).cross(d - a)
                    if first.dot(second) <= 0 and not (
                        allow_straight
                        and (
                            first.length <= geometry.AREA_EPSILON
                            or second.length <= geometry.AREA_EPSILON
                        )
                    ):
                        raise OperationError(
                            "retopo_topology_invalid",
                            "Quad folds by at least 90 degrees across a diagonal",
                        )
                for loop in face.loops:
                    outgoing = loop.link_loop_next.vert.co - loop.vert.co
                    incoming = loop.link_loop_prev.vert.co - loop.vert.co
                    if outgoing.cross(incoming).dot(face.normal) < (
                        -geometry.AREA_EPSILON
                        if allow_straight
                        else geometry.AREA_EPSILON
                    ):
                        raise OperationError(
                            "retopo_topology_invalid", "Folded, concave or twisted quad"
                        )
            _, normal, _ = reference.nearest(face.calc_center_median())
            if face.normal.dot(normal) <= 0.05:
                raise OperationError(
                    "retopo_topology_invalid",
                    "Affected faces must follow the source winding and surface",
                )
    mirror_half(target, bm)
    modifiers.budget(
        target, modifiers.stack(target), source_size=mesh.work_size(bm), strict=True
    )


def rebind_vertices(bm: Any, previous: dict[Any, Any], state: Any) -> dict[Any, Any]:
    """Native subdivision may reallocate vertex custom data and invalidate wrappers."""
    mesh.refresh(bm)
    current = list(bm.verts)[: len(previous)]
    coordinates = list(previous.values())
    if [tuple(v.co) for v in current] != [tuple(co) for co in coordinates]:
        raise OperationError(
            "retopo_topology_invalid",
            "Construction moved or reordered original vertices",
        )
    rebound = dict(zip(previous, current, strict=True))
    state.elements["verts"] = {
        rebound[v]: value for v, value in state.elements["verts"].items()
    }
    state.history = [rebound.get(e, e) for e in state.history]
    return dict(zip(current, coordinates, strict=True))


def execute(arguments: RetopoEditArguments) -> RetopoEditResult:
    from . import retopo_finish as finish

    source, target = objects(arguments)
    rejected = blockers(source, target)
    if rejected:
        raise OperationError(
            "mesh_has_shape_keys"
            if "mesh_has_shape_keys" in rejected
            else "retopo_target_blocked",
            "Resolve target blockers before retopology edits",
            {"blockers": [code for code in rejected]},
        )
    original = target.data
    isolated = (
        sum(o.type == "MESH" and o.data == original for o in bpy.data.objects) > 1
    )
    finishing = isinstance(arguments, finish.FINISH_TYPES)
    topology = not isinstance(arguments, RetopoProjectArguments | RetopoSlideArguments)
    with (
        geometry.surface(source, target) as (reference, _),
        mesh.snapshot(target) as bm,
    ):
        from .mesh_models import IndexSelector, RegionSelector
        from .mesh_selectors import SelectionError, select

        updates = {}
        for field in type(arguments).model_fields:
            value = getattr(arguments, field)
            if isinstance(value, RegionSelector):
                try:
                    found = [e.index for e in select(bm, value, target)]
                except SelectionError as exc:
                    raise OperationError("invalid_arguments", str(exc)) from exc
                if not found or len(found) > 4096:
                    raise OperationError(
                        "invalid_arguments",
                        "Retopology frame region must select 1..4096 elements",
                    )
                updates[field] = IndexSelector(
                    domain=value.domain, mode="indices", indices=found
                )
        if updates:
            arguments = arguments.model_copy(update=updates)
        mirror_half(target, bm)
        before = geometry.quality(target, bm)
        if (
            before.non_manifold_edge_count
            or before.non_manifold_vertex_count
            or before.inconsistent_winding_edge_count
            or before.degenerate_face_count
        ):
            raise OperationError(
                "retopo_topology_invalid",
                "Repair invalid target topology before construction/projection",
            )
        correspondence_before = geometry.correspondence(
            reference, bm, geometry.matrix(target)
        )
        if finishing and (before.loose_edge_count or before.loose_vertex_count):
            raise OperationError(
                "retopo_topology_invalid",
                "Finishing requires surface geometry without loose elements",
            )
        previous_vertices = {v: v.co.copy() for v in bm.verts}
        previous_edges, previous_faces = set(bm.edges), set(bm.faces)
        selection_state = mesh._Selection(bm)
        candidate = None
        committed = False
        selected_count = 0
        frame = None
        finish_result = None
        moved_vertices = []
        projection_distances: list[float] = []
        try:
            growth = 0
            if isinstance(arguments, RetopoSeedArguments):
                growth = (arguments.u_segments + 1) * (arguments.v_segments + 1) * 12
            elif isinstance(arguments, RetopoExtrudeArguments):
                growth = MAX_BOUNDARY_EDGES * 12
            elif isinstance(arguments, RetopoBridgeArguments):
                growth = MAX_BOUNDARY_EDGES * arguments.segments * 12
            elif isinstance(arguments, RetopoInsertArguments):
                growth = 128 * 12 * len(arguments.factors or [arguments.factor])
            elif isinstance(arguments, RetopoSubdivideArguments):
                growth = 128 * (arguments.cuts + 1) ** 2 * 12
            elif isinstance(arguments, RetopoFillArguments):
                growth = 64 * 64 * 3
            if mesh.work_size(bm) + growth > MAX_TARGET_ELEMENTS:
                raise OperationError(
                    "retopo_geometry_limit",
                    "Construction estimate exceeds target geometry capacity",
                )
            if isinstance(arguments, finish.FINISH_TYPES):
                finish_result = finish.apply(bm, target, arguments)
                selected_count = finish_result.selected_count
                if finish_result.rebind_vertices:
                    previous_vertices = rebind_vertices(
                        bm, previous_vertices, selection_state
                    )
                moved_vertices = finish_result.vertices
                projection_distances = project(
                    reference, target, moved_vertices, arguments
                )
            elif isinstance(arguments, RetopoSeedArguments):
                _, projection_distances, frame = seed(reference, target, bm, arguments)
                moved_vertices = [v for v in bm.verts if v not in previous_vertices]
            elif isinstance(arguments, RetopoExtrudeArguments):
                edges = selection(bm, arguments.selector, MAX_BOUNDARY_EDGES)
                boundary(edges, closed=False)
                selected_count = len(edges)
                output = bmesh.ops.extrude_edge_only(
                    bm, edges=edges, use_normal_flip=False, use_select_history=False
                )
                moved_vertices = [
                    item
                    for item in output["geom"]
                    if isinstance(item, bmesh.types.BMVert)
                ]
                pivot = sum((v.co for v in moved_vertices), Vector()) / len(
                    moved_vertices
                )
                rotation = Euler(arguments.rotation, "XYZ").to_matrix()
                offset = Vector(arguments.offset)
                mirror_axes = [
                    list(mod.use_axis).index(True)
                    for mod in target.modifiers
                    if mod.type == "MIRROR"
                ]
                for vertex in moved_vertices:
                    original_point = vertex.co.copy()
                    relative = original_point - pivot
                    scaled = Vector(
                        [relative[i] * arguments.scale[i] for i in range(3)]
                    )
                    proposed = geometry.checked_point(
                        pivot + rotation @ scaled + offset
                    )
                    if any(
                        abs(original_point[axis]) <= 1e-6 and abs(proposed[axis]) > 1e-6
                        for axis in mirror_axes
                    ):
                        raise OperationError(
                            "retopo_boundary_invalid",
                            "Boundary shaping must preserve an existing Mirror seam",
                        )
                    vertex.co = proposed
                projection_distances = project(
                    reference, target, moved_vertices, arguments
                )
            elif isinstance(arguments, RetopoBridgeArguments):
                original_count = len(previous_vertices)
                selected_count = bridge(bm, arguments)
                previous_vertices = rebind_vertices(
                    bm, previous_vertices, selection_state
                )
                moved_vertices = list(bm.verts)[original_count:]
                projection_distances = project(
                    reference, target, moved_vertices, arguments
                )
            else:
                vertices = selection(bm, arguments.selector, MAX_SELECTED_VERTICES)
                selected_count = len(vertices)
                if isinstance(arguments, RetopoRelaxArguments):
                    if selected_count * arguments.iterations > MAX_RELAX_WORK:
                        raise OperationError(
                            "retopo_geometry_limit",
                            "Relaxation exceeds 100,000 vertex-iterations",
                        )
                    moved_vertices = [
                        v
                        for v in vertices
                        if not arguments.preserve_boundary or not v.is_boundary
                    ]
                    seams = [
                        (v, axis)
                        for mod in target.modifiers
                        if mod.type == "MIRROR"
                        for axis in [list(mod.use_axis).index(True)]
                        for v in moved_vertices
                        if abs(v.co[axis]) <= 1e-6
                    ]
                    for _ in range(arguments.iterations):
                        bmesh.ops.smooth_vert(
                            bm,
                            verts=moved_vertices,
                            factor=arguments.factor,
                            use_axis_x=True,
                            use_axis_y=True,
                            use_axis_z=True,
                        )
                        for vertex, axis in seams:
                            vertex.co[axis] = 0
                        projection_distances.extend(
                            project(reference, target, moved_vertices, arguments)
                        )
                else:
                    moved_vertices = vertices
                    projection_distances = project(
                        reference, target, vertices, arguments
                    )
            created_faces = [f for f in bm.faces if f not in previous_faces]
            affected = set(created_faces) | {
                f for v in moved_vertices for f in v.link_faces
            }
            if finish_result is not None:
                affected.update(f for f in finish_result.faces if f.is_valid)
                edge_signatures = [
                    frozenset(v.index for v in e.verts) for e in bm.edges
                ]
                if len(set(edge_signatures)) != len(edge_signatures):
                    raise OperationError(
                        "retopo_topology_invalid", "Finishing produced duplicate edges"
                    )
                if any(e.is_wire or e.calc_length() <= 1e-9 for e in bm.edges) or any(
                    not v.link_edges for v in bm.verts
                ):
                    raise OperationError(
                        "retopo_topology_invalid",
                        "Finishing produced loose or zero-length geometry",
                    )
                for mod in target.modifiers:
                    if mod.type == "MIRROR":
                        axis = list(mod.use_axis).index(True)
                        if any(
                            v.is_valid
                            and abs(co[axis]) <= 1e-6
                            and abs(v.co[axis]) > 1e-6
                            for v, co in previous_vertices.items()
                        ):
                            raise OperationError(
                                "retopo_flow_invalid",
                                "Finishing moved a Mirror seam vertex off its plane",
                            )
            validate(
                bm,
                reference,
                target,
                affected,
                created_faces,
                quad_only=not isinstance(
                    arguments, RetopoCollapseArguments | RetopoRotateArguments
                ),
                allow_straight=isinstance(arguments, RetopoRotateArguments),
            )
            if isinstance(arguments, RetopoFillArguments):
                with geometry.world_mesh(bm, geometry.matrix(target)) as world:
                    if any(
                        max(e.calc_length() for e in world.faces[f.index].edges) ** 2
                        / world.faces[f.index].calc_area()
                        > 10
                        for f in created_faces
                    ):
                        raise OperationError(
                            "retopo_topology_invalid",
                            "Grid fill produces extreme quad aspect",
                        )
            selection_state.restore(bm)
            candidate = original.copy()
            bm.to_mesh(candidate)
            candidate.update()
            if list(candidate.materials) != list(original.materials):
                raise RuntimeError("Target material slots changed during staging")
            if not topology and (
                [tuple(e.vertices) for e in candidate.edges]
                != [tuple(e.vertices) for e in original.edges]
                or [tuple(f.vertices) for f in candidate.polygons]
                != [tuple(f.vertices) for f in original.polygons]
                or len(candidate.vertices) != len(original.vertices)
            ):
                raise RuntimeError(
                    "A topology-preserving operation changed element ordering"
                )
            target_budget(candidate)
            after = geometry.quality(target, bm, candidate)
            after = after.model_copy(
                update={"mesh": after.mesh.model_copy(update={"mesh_users": 1})}
            )
            result = RetopoEditResult(
                source_object=source.name,
                target_object=target.name,
                topology_changed=topology,
                indices_invalidated=topology,
                mesh_isolated=isolated,
                selected_count=selected_count,
                moved_count=sum(
                    v.is_valid and (v.co - co).length > 1e-7
                    for v, co in previous_vertices.items()
                ),
                created=ElementCounts(
                    vertices=sum(v not in previous_vertices for v in bm.verts),
                    edges=sum(e not in previous_edges for e in bm.edges),
                    faces=len(created_faces),
                ),
                created_faces=geometry.bounded(sorted(f.index for f in created_faces)),
                projection=geometry.distances(projection_distances),
                before=before,
                after=after,
                correspondence_before=correspondence_before,
                correspondence_after=geometry.correspondence(
                    reference, bm, geometry.matrix(target)
                ),
                patch=frame,
                input_edges_before=geometry.bounded(finish_result.input_edges_before)
                if finish_result
                else geometry.bounded([]),
                removed=ElementCounts(
                    vertices=sum(not v.is_valid for v in previous_vertices),
                    edges=sum(not e.is_valid for e in previous_edges),
                    faces=sum(not f.is_valid for f in previous_faces),
                ),
                created_vertices=geometry.bounded(
                    [v.index for v in bm.verts if v not in previous_vertices]
                ),
                created_edges=geometry.bounded(
                    [e.index for e in bm.edges if e not in previous_edges]
                ),
                flow_edges=geometry.bounded(
                    sorted(e.index for e in finish_result.flow_edges)
                )
                if finish_result
                else geometry.bounded([]),
            )
            target.data = candidate
            bpy.context.view_layer.update()
            committed = True
            return result
        except OperationError:
            raise
        except Exception as exc:
            raise OperationError(
                "retopo_operation_failed",
                "Staged retopology failed; original target preserved",
            ) from exc
        finally:
            if not committed and candidate is not None:
                if target.data == candidate:
                    target.data = original
                if candidate.users == 0:
                    bpy.data.meshes.remove(candidate)
            if committed and original.users == 0:
                bpy.data.meshes.remove(original)
