"""Bounded evaluated surfaces and authored retopology quality in world space."""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

import bpy  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]
from mathutils.geometry import closest_point_on_tri  # type: ignore[import-not-found]

from . import mesh, modifiers
from .errors import OperationError
from .mesh_models import BoundedIndices
from .remesh import distribution
from .retopo_models import (
    EXTREME_ASPECT_RATIO,
    MAX_BOUNDARIES,
    MAX_BOUNDARY_EDGES,
    MAX_COORDINATE,
    MAX_POLES,
    BoundarySummary,
    Correspondence,
    DistanceSummary,
    EvaluatedSurfaceSummary,
    PoleSummary,
    RetopoQuality,
    ValenceSummary,
)

AREA_EPSILON = 1e-12
MAX_PLANE_PROJECTION_WORK = 2_000_000
PLANE_EPSILON = 1e-7


def checked_point(point: Any) -> Any:
    if any(not math.isfinite(v) or abs(v) > MAX_COORDINATE for v in point):
        raise OperationError(
            "retopo_geometry_limit",
            "Local/world coordinates must be finite and within 1,000,000",
        )
    return point


def matrix(obj: Any) -> Any:
    result = obj.matrix_world.copy()
    if any(not math.isfinite(v) for row in result for v in row):
        raise OperationError("invalid_context", "Object transform is not finite")
    try:
        inverse = result.inverted()
    except ValueError as exc:
        raise OperationError("invalid_context", "Object transform is singular") from exc
    if any(not math.isfinite(v) or abs(v) > 1e12 for row in inverse for v in row):
        raise OperationError(
            "invalid_context", "Object transform is numerically unsafe"
        )
    return result


def dependencies(obj: Any) -> list[Any]:
    """Read current-frame dependencies without imposing destructive animation guards."""
    result = [obj.parent] if obj.parent else []
    constraints = list(obj.constraints)
    if obj.type == "ARMATURE":
        from . import rig_constraints
        from .constraint_models import RigEndpoint

        refs = [
            RigEndpoint(object_name=obj.name, bone=p.name)
            for p in obj.pose.bones
            if p.constraints
        ]
        if refs:
            rig_constraints.graph_guard([], extra_refs=refs)
        constraints.extend(c for p in obj.pose.bones for c in p.constraints)
    for item in [*obj.modifiers, *constraints]:
        if item.type == "NODES":
            from . import curves, growth, instances

            if growth.KEY in obj:
                result.extend(growth.evaluation_dependencies(obj))
            elif curves.KEY in obj:
                result.extend(curves.evaluation_dependencies(obj))
            else:
                result.extend(instances.evaluation_dependencies(obj))
            continue
        for prop in item.bl_rna.properties:
            if prop.type != "POINTER":
                continue
            value = getattr(item, prop.identifier)
            if isinstance(value, bpy.types.Object):
                if value != obj:
                    result.append(value)
            elif isinstance(value, bpy.types.Collection):
                if len(value.all_objects) > 256:
                    raise OperationError(
                        "retopo_geometry_limit",
                        "Dependency collection exceeds 256 objects",
                    )
                result.extend(value.all_objects)
        if hasattr(item, "targets"):
            result.extend(t.target for t in item.targets if t.target)
    # Driver variables can add source dependencies outside modifier/constraint RNA.
    owners = [obj, getattr(obj, "data", None)]
    if obj.type == "MESH":
        owners.append(obj.data.shape_keys)
    for owner in owners:
        animation = getattr(owner, "animation_data", None)
        if animation is None:
            continue
        for driver in animation.drivers:
            for variable in driver.driver.variables:
                for target in variable.targets:
                    value = target.id
                    if isinstance(value, bpy.types.Object):
                        if value == obj:
                            from .couplings import internal_driver_dependency

                            if internal_driver_dependency(obj, driver):
                                continue
                        result.append(value)
                    elif isinstance(value, bpy.types.Mesh | bpy.types.Key):
                        for linked in bpy.data.objects:
                            if linked.type != "MESH" or not (
                                linked.data == value or linked.data.shape_keys == value
                            ):
                                continue
                            if linked == obj:
                                from .couplings import internal_driver_dependency

                                if internal_driver_dependency(obj, driver):
                                    continue
                            result.append(linked)
    return result


def graph(source: Any, target: Any | None = None) -> Any:
    if bpy.context.mode != "OBJECT" or bpy.app.is_job_running("RENDER"):
        raise OperationError(
            "invalid_context", "Retopology requires Object Mode outside rendering"
        )
    roots = [source, *([target] if target is not None else [])]
    for obj in roots:
        if obj.name not in bpy.context.view_layer.objects or obj.data.is_editmode:
            raise OperationError(
                "invalid_context",
                "Use current view-layer Mesh objects outside Edit Mode",
            )
    visited: set[int] = set()
    active: set[int] = set()
    source_dependencies: set[int] = set()

    def visit(obj: Any, *, from_source: bool = False) -> None:
        key = int(obj.as_pointer())
        if from_source:
            source_dependencies.add(key)
        if key in active:
            raise OperationError(
                "retopo_dependency_invalid", "Cyclic source/evaluation dependencies"
            )
        if key in visited:
            return
        if len(visited) + len(active) >= 256:
            raise OperationError(
                "retopo_geometry_limit", "Evaluation dependencies exceed 256 objects"
            )
        active.add(key)
        if obj.type == "MESH":
            from . import instances, surface_deform

            surface_deform.validate(obj)

            if instances.KEY in obj:
                # This owned graph emits bounded shared instances. Its carrier
                # and prototype stacks are visited and bounded below.
                instances.evaluation_dependencies(obj)
                mesh.check_budget(modifiers.data_size(obj.data))
            else:
                modifiers.budget(obj, modifiers.stack(obj), strict=True)
        for child in dependencies(obj):
            visit(child, from_source=from_source)
        active.remove(key)
        visited.add(key)

    visit(source, from_source=True)
    if target is not None and int(target.as_pointer()) in source_dependencies:
        raise OperationError(
            "retopo_dependency_invalid", "The source must not depend on the target"
        )
    # Bound the requested evaluated objects and their actual dependencies. Unrelated
    # visible objects are not diagnostic inputs; counting them defeats regional QA.
    for obj in roots:
        visit(obj)
    return bpy.context.evaluated_depsgraph_get()


@contextmanager
def evaluated_mesh(obj: Any, depsgraph: Any) -> Iterator[tuple[Any, Any]]:
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh.check_budget(modifiers.data_size(evaluated.data))
        data = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
        if data is None:
            raise OperationError(
                "retopo_source_invalid", "Evaluation did not produce Mesh data"
            )
        modifiers.check_geometry(data)
        yield data, matrix(evaluated)
    finally:
        evaluated.to_mesh_clear()


def surface_summary(obj: Any, data: Any, transform: Any) -> EvaluatedSurfaceSummary:
    points = [checked_point(transform @ checked_point(v.co)) for v in data.vertices]
    return EvaluatedSurfaceSummary(
        object_name=obj.name,
        vertex_count=len(data.vertices),
        edge_count=len(data.edges),
        face_count=len(data.polygons),
        triangle_count=len(data.loop_triangles),
        bounds_min_world=[min(v[i] for v in points) for i in range(3)]
        if points
        else None,
        bounds_max_world=[max(v[i] for v in points) for i in range(3)]
        if points
        else None,
    )


class Surface:
    def __init__(self, source: Any, depsgraph: Any) -> None:
        self.bvh: Any = None
        self.sections: dict[tuple[float, ...], list[tuple[list[Any], Any]]] = {}
        self.plane_work = 0
        with evaluated_mesh(source, depsgraph) as (data, transform):
            data.calc_loop_triangles()
            points = [
                checked_point(transform @ checked_point(v.co)) for v in data.vertices
            ]
            triangles = [tuple(t.vertices) for t in data.loop_triangles]
            if not triangles or any(
                (points[b] - points[a]).cross(points[c] - points[a]).length / 2
                <= AREA_EPSILON
                for a, b, c in triangles
            ):
                raise OperationError(
                    "retopo_source_invalid",
                    "Source must have a nondegenerate evaluated surface",
                )
            self.summary = surface_summary(source, data, transform)
            self.matrix = transform
            self.points = points
            self.triangles = triangles
            self.bvh = BVHTree.FromPolygons(points, triangles, all_triangles=True)

    def nearest(self, point: Any, maximum: float = 1e19) -> tuple[Any, Any, float]:
        checked_point(point)
        location, normal, _, distance = self.bvh.find_nearest(point, maximum)
        if location is None or normal is None or distance is None or normal.length == 0:
            raise OperationError(
                "retopo_projection_failed",
                "No source surface within the maximum projection distance",
            )
        return location, normal.normalized(), float(distance)

    def nearest_on_plane(
        self, point: Any, origin: Any, normal: Any, maximum: float
    ) -> tuple[Any, Any, float]:
        """Closest point on the actual source/plane intersection, not a clamped hit."""
        checked_point(point)
        key = tuple(origin) + tuple(normal)
        if key not in self.sections:
            sections = []
            for triangle in self.triangles:
                vertices = [self.points[i] for i in triangle]
                distances = [(v - origin).dot(normal) for v in vertices]
                face_normal = (
                    (vertices[1] - vertices[0])
                    .cross(vertices[2] - vertices[0])
                    .normalized()
                )
                if all(abs(d) <= PLANE_EPSILON for d in distances):
                    sections.append((vertices, face_normal))
                    continue
                points = []
                for i in range(3):
                    a, b = vertices[i], vertices[(i + 1) % 3]
                    da, db = distances[i], distances[(i + 1) % 3]
                    if abs(da) <= PLANE_EPSILON:
                        points.append(a - normal * da)
                    if da * db < 0:
                        points.append(a.lerp(b, da / (da - db)))
                if len(points) >= 2:
                    a, b = max(
                        ((a, b) for i, a in enumerate(points) for b in points[i + 1 :]),
                        key=lambda pair: (pair[1] - pair[0]).length_squared,
                    )
                    if (b - a).length_squared > PLANE_EPSILON**2:
                        sections.append(([a, b], face_normal))
            self.sections[key] = sections
        sections = self.sections[key]
        self.plane_work += len(sections)
        if self.plane_work > MAX_PLANE_PROJECTION_WORK:
            raise OperationError(
                "retopo_geometry_limit",
                "Mirror-plane projection exceeds its work limit",
            )
        best = None
        best_normal = None
        best_distance = maximum
        for vertices, face_normal in sections:
            if len(vertices) == 3:
                location = closest_point_on_tri(point, *vertices)
            else:
                a, b = vertices
                direction = b - a
                factor = max(
                    0.0, min(1.0, (point - a).dot(direction) / direction.length_squared)
                )
                location = a + direction * factor
            distance = float((location - point).length)
            if distance <= best_distance:
                best, best_normal, best_distance = location, face_normal, distance
        if best is None:
            raise OperationError(
                "retopo_projection_failed",
                "No source/Mirror-plane intersection within the projection distance",
            )
        return best, best_normal, best_distance


@contextmanager
def surface(source: Any, target: Any | None = None) -> Iterator[tuple[Surface, Any]]:
    depsgraph = graph(source, target)
    reference = Surface(source, depsgraph)
    try:
        yield reference, depsgraph
    finally:
        reference.bvh = None
        reference.points.clear()
        reference.triangles.clear()
        reference.sections.clear()


def distances(values: list[float]) -> DistanceSummary:
    n = len(values)
    return DistanceSummary(
        sample_count=n,
        mean_distance=math.fsum(values) / n if n else 0,
        rms_distance=math.sqrt(math.fsum(v * v for v in values) / n) if n else 0,
        max_distance=max(values, default=0),
        p95_distance=sorted(values)[max(0, math.ceil(0.95 * n) - 1)] if n else 0,
    )


def bounded(indices: list[int]) -> BoundedIndices:
    return BoundedIndices(
        indices=indices[:MAX_BOUNDARY_EDGES],
        total=len(indices),
        truncated=len(indices) > MAX_BOUNDARY_EDGES,
    )


def boundary_components(
    edges: list[Any],
) -> list[tuple[list[Any], list[Any], Literal["closed", "open", "branched"]]]:
    remaining = set(edges)
    result: list[
        tuple[list[Any], list[Any], Literal["closed", "open", "branched"]]
    ] = []
    for first in sorted(edges, key=lambda e: e.index):
        if first not in remaining:
            continue
        todo = [first]
        component = set()
        vertices = set()
        while todo:
            edge = todo.pop()
            if edge not in remaining:
                continue
            remaining.remove(edge)
            component.add(edge)
            vertices.update(edge.verts)
            todo.extend(e for v in edge.verts for e in v.link_edges if e in remaining)
        degree = {v: sum(e in component for e in v.link_edges) for v in vertices}
        ends = [v for v in vertices if degree[v] == 1]
        kind: Literal["closed", "open", "branched"] = (
            "closed"
            if all(n == 2 for n in degree.values())
            else "open"
            if len(ends) == 2 and all(n <= 2 for n in degree.values())
            else "branched"
        )
        ordered = []
        if kind != "branched":
            current = min(ends or list(vertices), key=lambda v: v.index)
            seen = set()
            while current not in seen:
                ordered.append(current)
                seen.add(current)
                choices = [
                    e.other_vert(current)
                    for e in current.link_edges
                    if e in component and e.other_vert(current) not in seen
                ]
                if not choices:
                    break
                current = min(choices, key=lambda v: v.index)
        else:
            ordered = sorted(vertices, key=lambda v: v.index)
        result.append((sorted(component, key=lambda e: e.index), ordered, kind))
    return result


@contextmanager
def world_mesh(bm: Any, transform: Any) -> Iterator[Any]:
    result = bm.copy()
    try:
        result.transform(transform)
        mesh.refresh(result)
        for vertex in result.verts:
            checked_point(vertex.co)
        yield result
    finally:
        result.free()


def correspondence(reference: Surface, bm: Any, transform: Any) -> Correspondence:
    with world_mesh(bm, transform) as world:
        vertex_distances = [reference.nearest(v.co)[2] for v in world.verts]
        face_distances = []
        normals = []
        for face in world.faces:
            _, normal, distance = reference.nearest(face.calc_center_median())
            face_distances.append(distance)
            if face.normal.length:
                normals.append(max(-1.0, min(1.0, float(face.normal.dot(normal)))))
        return Correspondence(
            vertices=distances(vertex_distances),
            face_centers=distances(face_distances),
            face_normal_dot=distribution(normals),
        )


def non_manifold_vertices(bm: Any) -> list[int]:
    """Find disconnected face fans and invalid boundary incidence in linear work."""
    invalid = []
    for vertex in bm.verts:
        if not vertex.link_faces:
            continue  # Loose geometry is reported separately.
        adjacent: dict[Any, set[Any]] = {face: set() for face in vertex.link_faces}
        bad_edge = False
        boundaries = 0
        for edge in vertex.link_edges:
            faces = list(edge.link_faces)
            boundaries += len(faces) == 1
            if len(faces) == 2:
                adjacent[faces[0]].add(faces[1])
                adjacent[faces[1]].add(faces[0])
            elif len(faces) != 1:
                bad_edge = True
        pending = [next(iter(adjacent))]
        reached = set(pending)
        while pending:
            for face in adjacent[pending.pop()]:
                if face not in reached:
                    reached.add(face)
                    pending.append(face)
        if bad_edge or boundaries not in {0, 2} or len(reached) != len(adjacent):
            invalid.append(vertex.index)
    return invalid


def quality(obj: Any, bm: Any, data: Any | None = None) -> RetopoQuality:
    transform = matrix(obj)
    components = boundary_components([e for e in bm.edges if e.is_boundary])
    boundaries = []
    for i, (edges, vertices, kind) in enumerate(components[:MAX_BOUNDARIES]):
        boundaries.append(
            BoundarySummary(
                loop_index=i,
                edge_count=len(edges),
                vertex_count=len(vertices),
                kind=kind,
                perimeter=math.fsum(
                    (transform @ e.verts[0].co - transform @ e.verts[1].co).length
                    for e in edges
                ),
                bounds_min=[min(v.co[j] for v in vertices) for j in range(3)],
                bounds_max=[max(v.co[j] for v in vertices) for j in range(3)],
                edge_indices=bounded([e.index for e in edges]),
                vertex_indices=bounded([v.index for v in vertices]),
            )
        )
    valences = [len(v.link_edges) for v in bm.verts]
    unusual = [v for v in bm.verts if len(v.link_edges) != (3 if v.is_boundary else 4)]
    # Report interior poles first; normal boundary corners are contextual entries.
    unusual.sort(key=lambda v: (v.is_boundary, v.index))
    poles = [
        PoleSummary(
            vertex_index=v.index,
            valence=len(v.link_edges),
            boundary=v.is_boundary,
            position=list(v.co),
            incident_face_count=len(v.link_faces),
        )
        for v in unusual[:MAX_POLES]
    ]
    with world_mesh(bm, transform) as world:
        areas = [float(f.calc_area()) for f in world.faces]
        aspects = [
            max(e.calc_length() for e in f.edges) ** 2 / f.calc_area()
            for f in world.faces
            if len(f.verts) == 4 and f.calc_area() > AREA_EPSILON
        ]
        return RetopoQuality(
            mesh=mesh.summary(obj, bm, data),
            quad_count=sum(len(f.verts) == 4 for f in bm.faces),
            triangle_count=sum(len(f.verts) == 3 for f in bm.faces),
            ngon_count=sum(len(f.verts) > 4 for f in bm.faces),
            boundary_edge_count=sum(e.is_boundary for e in bm.edges),
            boundary_loop_count=sum(c[2] == "closed" for c in components),
            boundary_chain_count=sum(c[2] == "open" for c in components),
            branched_boundary_count=sum(c[2] == "branched" for c in components),
            non_manifold_edge_count=sum(len(e.link_faces) > 2 for e in bm.edges),
            non_manifold_vertex_count=len(non_manifold_vertices(bm)),
            inconsistent_winding_edge_count=sum(
                e.is_manifold and not e.is_contiguous for e in bm.edges
            ),
            loose_vertex_count=sum(not v.link_edges for v in bm.verts),
            loose_edge_count=sum(e.is_wire for e in bm.edges),
            degenerate_face_count=sum(a <= AREA_EPSILON for a in areas),
            valence=ValenceSummary(
                **{f"valence_{i}": valences.count(i) for i in range(6)},
                valence_6_plus=sum(n >= 6 for n in valences),
                max_valence=max(valences, default=0),
            ),
            edge_length=distribution([float(e.calc_length()) for e in world.edges]),
            face_area=distribution(areas),
            quad_aspect_ratio=distribution(aspects),
            extreme_aspect_ratio_count=sum(a > EXTREME_ASPECT_RATIO for a in aspects),
            boundaries=boundaries,
            boundaries_truncated=len(components) > MAX_BOUNDARIES,
            poles=poles,
            poles_total=len(unusual),
            poles_truncated=len(unusual) > MAX_POLES,
        )
