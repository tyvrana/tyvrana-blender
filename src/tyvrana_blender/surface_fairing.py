"""Atomic, bounded local fairing of an authored surface, without native sculpt UI."""

import heapq
from itertools import count
from typing import Any, Never

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]
from mathutils.geometry import closest_point_on_tri  # type: ignore[import-not-found]

from . import geometry_qa, mesh, multires, sculpt_regions
from .errors import OperationError
from .geometry_qa_models import GeometryInspectArguments
from .layer_geometry import Surface as SurfaceData
from .sculpt_models import (
    SculptFilterArguments,
    SculptFilterResult,
    SurfaceFairingSummary,
)
from .topology_selection import contains, region_matrix


def fail(message: str) -> Never:
    raise OperationError("surface_fairing_rejected", message)


def group_weights(obj: Any, name: str) -> Any:
    group = obj.vertex_groups.get(name)
    if group is None:
        fail(f"Vertex group does not exist: {name}")
    values = np.zeros(len(obj.data.vertices))
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            if assignment.group == group.index:
                values[vertex.index] = assignment.weight
    return values


def fair_points(
    original: Any, edges: Any, weights: Any, arguments: SculptFilterArguments
) -> Any:
    """Fixed two-pass Taubin filter; topology, support and coefficients stay fixed."""
    settings = arguments.fairing
    assert settings is not None
    active_edges = edges[np.any(weights[edges] > 0, axis=1)]
    if len(active_edges) * arguments.iterations * 4 > settings.max_work:
        fail("Edge/pass work exceeds max_work; reduce region or iterations")
    a, b = active_edges.T
    lengths = np.linalg.norm(original[a] - original[b], axis=1)
    if np.any(lengths <= 0):
        fail("Degenerate source edges require repair before fairing")
    inverse = 1 / lengths
    degree = np.bincount(a, weights=inverse, minlength=len(original))
    degree += np.bincount(b, weights=inverse, minlength=len(original))
    degree = np.maximum(degree, 1e-30)
    current = original.copy()
    for _ in range(arguments.iterations):
        for coefficient in (0.5, -0.53):
            delta = current[b] - current[a]
            average = np.column_stack(
                [
                    (
                        np.bincount(
                            a, weights=inverse * delta[:, axis], minlength=len(original)
                        )
                        - np.bincount(
                            b, weights=inverse * delta[:, axis], minlength=len(original)
                        )
                    )
                    / degree
                    for axis in range(3)
                ]
            )
            proposed = (
                current
                + average * (weights * coefficient * arguments.strength)[:, None]
            )
            displacement = proposed - original
            magnitude = np.linalg.norm(displacement, axis=1)
            displacement *= np.minimum(
                1, settings.max_distance / np.maximum(magnitude, 1e-30)
            )[:, None]
            current = original + displacement
    # Validate the actual float32 coordinates that Blender will store.
    return current.astype(np.float32).astype(np.float64)


class ThicknessCorrespondence:
    """Nearest opposing sheet distances with immutable source-side membership.

    An anchor is a source vertex and its candidate counterpart. The opposite
    side is classified exclusively from source face orientation and halfspace;
    candidate normals never select a different side. Both distances are exact
    minima over that same face set, allowing the foot point to slide naturally.
    """

    def __init__(
        self, before: Any, after: Any, triangles: Any, surface: Any, cross: Any
    ) -> None:
        self.before, self.after, self.indices = before, after, triangles
        self.surface = surface
        self.old_triangles = [tuple(Vector(before[i]) for i in t) for t in triangles]
        self.normals = cross / np.linalg.norm(cross, axis=1)[:, None]
        self.centers = before[triangles].mean(axis=1)
        self.motion = float(np.linalg.norm(after - before, axis=1).max())
        self.displacements = after - before
        self.normal_bounds: dict[int, tuple[Any, Any]] = {}
        self.certificates: dict[int, tuple[Any, Any, Any, Any]] = {}

        def bounds(node: Any) -> tuple[Any, Any]:
            if node.children:
                left, right = (bounds(child) for child in node.children)
                lo, hi = np.minimum(left[0], right[0]), np.maximum(left[1], right[1])
                a, b = (self.certificates[id(child)] for child in node.children)
                self.certificates[id(node)] = (
                    np.minimum(a[0], b[0]),
                    np.maximum(a[1], b[1]),
                    np.minimum(a[2], b[2]),
                    np.maximum(a[3], b[3]),
                )
            else:
                values = self.normals[node.indices]
                lo, hi = values.min(axis=0), values.max(axis=0)
                ids = triangles[node.indices].reshape(-1)
                coordinates, moves = before[ids], self.displacements[ids]
                self.certificates[id(node)] = (
                    coordinates.min(axis=0),
                    coordinates.max(axis=0),
                    moves.min(axis=0),
                    moves.max(axis=0),
                )
            self.normal_bounds[id(node)] = (lo, hi)
            return lo, hi

        bounds(surface.root)

    def certify(
        self,
        index: int,
        normal: Any,
        budget: geometry_qa.Budget,
        thinning: float,
        minimum_distance: float,
    ) -> float | None:
        """Prove a ratio lower bound over the SAME opposing face set.

        For a triangle at source distance d and relative vertex motion bounded
        by m, its mapped candidate distance is at least d-m (Hausdorff bound).
        If m <= thinning*d for every opposing triangle, taking minima preserves
        the requested shell ratio. Spatial lower bounds certify entire branches.
        Uncertain branches use exact pairs; an uncertain anchor falls back to
        the original nearest-side comparison, never a weaker acceptance rule.
        """
        p, move = self.before[index], self.displacements[index]
        ratio = 1.0
        pending = [self.surface.root]
        while pending:
            node = pending.pop()
            budget.nodes += 1
            if budget.nodes > 8 * budget.maximum + 1024:
                fail("Thickness traversal exceeds max_triangle_tests work budget")
            lo, hi = self.normal_bounds[id(node)]
            if float(np.where(normal >= 0, lo, hi) @ normal) >= 0:
                continue
            low, high, least, most = self.certificates[id(node)]
            # The source inward halfspace and orientation remain immutable.
            if float((np.where(normal >= 0, low, high) - p) @ normal) >= 0:
                continue
            distance = float(
                np.linalg.norm(np.maximum(0, np.maximum(low - p, p - high)))
            )
            motion = float(
                np.linalg.norm(np.maximum(abs(least - move), abs(most - move)))
            )
            if distance > minimum_distance and motion <= thinning * distance:
                ratio = min(ratio, 1 - motion / distance)
                continue
            if node.children:
                pending.extend(node.children)
                continue
            for face in node.indices:
                if (
                    index in self.indices[face]
                    or self.normals[face] @ normal >= 0
                    or (self.centers[face] - p) @ normal >= 0
                ):
                    continue
                budget.test()
                old_distance = (
                    closest_point_on_tri(Vector(p), *self.old_triangles[face])
                    - Vector(p)
                ).length
                budget.test()
                q = Vector(self.after[index])
                new_distance = (
                    closest_point_on_tri(q, *self.surface.triangles[face]) - q
                ).length
                if (
                    old_distance <= minimum_distance
                    or new_distance < (1 - thinning) * old_distance
                ):
                    return None
                ratio = min(ratio, new_distance / old_distance)
        return ratio

    def measure(
        self, index: int, normal: Any, seed: int | None, budget: geometry_qa.Budget
    ) -> tuple[float, float]:
        p, q = Vector(self.before[index]), Vector(self.after[index])
        old_min = new_min = float("inf")
        if seed is not None:
            old_min = (closest_point_on_tri(p, *self.old_triangles[seed]) - p).length
            new_min = (
                closest_point_on_tri(q, *self.surface.triangles[seed]) - q
            ).length
        serial = count()
        queue: list[Any] = [(0, next(serial), self.surface.root)]
        while queue:
            _, _, node = heapq.heappop(queue)
            budget.nodes += 1
            if budget.nodes > 8 * budget.maximum + 1024:
                fail("Thickness traversal exceeds max_triangle_tests work budget")
            lo, hi = self.normal_bounds[id(node)]
            if (
                sum((lo[k] if normal[k] >= 0 else hi[k]) * normal[k] for k in range(3))
                >= 0
            ):
                continue
            old_bound = sum(
                max(0, node.lo[k] - self.motion - p[k], p[k] - node.hi[k] - self.motion)
                ** 2
                for k in range(3)
            )
            new_bound = sum(
                max(0, node.lo[k] - q[k], q[k] - node.hi[k]) ** 2 for k in range(3)
            )
            if old_bound > old_min**2 and new_bound > new_min**2:
                continue
            if node.children:
                for child in node.children:
                    priority = sum(
                        max(0, child.lo[k] - q[k], q[k] - child.hi[k]) ** 2
                        for k in range(3)
                    )
                    heapq.heappush(queue, (priority, next(serial), child))
                continue
            for face in node.indices:
                if (
                    index in self.indices[face]
                    or self.normals[face] @ normal >= 0
                    or (self.centers[face] - self.before[index]) @ normal >= 0
                ):
                    continue
                budget.test()
                old_min = min(
                    old_min,
                    (closest_point_on_tri(p, *self.old_triangles[face]) - p).length,
                )
                budget.test()
                new_min = min(
                    new_min,
                    (closest_point_on_tri(q, *self.surface.triangles[face]) - q).length,
                )
        return float(old_min), float(new_min)

    def independent(self, index: int, normal: Any) -> tuple[float, float]:
        """Cross-check the worst anchor using native BVHs, not our traversal."""
        selected = (self.normals @ normal < 0) & (
            (self.centers - self.before[index]) @ normal < 0
        )
        selected &= ~np.any(self.indices == index, axis=1)
        faces = self.indices[selected].tolist()
        if not faces:
            fail("Opposing thickness side disappeared")
        values = []
        for points in (self.before, self.after):
            tree = BVHTree.FromPolygons(points.tolist(), faces, all_triangles=True)
            nearest = tree.find_nearest(Vector(points[index]))
            if nearest[0] is None:
                fail("Opposing thickness anchor cannot be reprojected")
            values.append(float(nearest[3]))
        return values[0], values[1]


def validate_geometry(
    before: Any,
    after: Any,
    triangles: Any,
    changed: Any,
    arguments: SculptFilterArguments,
) -> tuple[int, float | None, int]:
    settings = arguments.fairing
    assert settings is not None
    scale = float(np.linalg.norm(np.ptp(before, axis=0)))
    epsilon = max(scale * 1e-7, 1e-9)
    old_tri, new_tri = before[triangles], after[triangles]
    old_cross = np.cross(old_tri[:, 1] - old_tri[:, 0], old_tri[:, 2] - old_tri[:, 0])
    new_cross = np.cross(new_tri[:, 1] - new_tri[:, 0], new_tri[:, 2] - new_tri[:, 0])
    old_area = np.einsum("ij,ij->i", old_cross, old_cross)
    if np.any(old_area <= epsilon**4):
        fail("Degenerate source triangles require repair before fairing")
    if not np.isfinite(after).all() or np.any(
        np.einsum("ij,ij->i", old_cross, new_cross) < 0.2 * old_area
    ):
        fail("Fairing would invert or collapse a triangle")
    # Topology is unchanged. Exact bounded nonincident triangle distances reject
    # contact at the proposed final surface, including disconnected sheets.
    points = [Vector(p) for p in after]
    indices = [(int(t[0]), int(t[1]), int(t[2])) for t in triangles]
    surface = geometry_qa.Surface(
        SurfaceData(
            obj=None,
            topology="",
            authored="",
            curve=None,
            points=points,
            triangles=indices,
            triangle_faces=list(range(len(indices))),
            bm=None,
        )
    )
    budget = geometry_qa.Budget(settings.max_triangle_tests)
    query = GeometryInspectArguments.model_validate(
        {
            "objects": [{"object_name": "fairing"}],
            "tolerance": epsilon,
            "max_triangle_tests": settings.max_triangle_tests,
            "worst_limit": 0,
        }
    )
    _, contacts, _, _ = geometry_qa.proximity(surface, surface, query, budget)
    if contacts:
        fail("Proposed surface has nonincident triangle contact; prior mesh retained")
    old_points = [Vector(p) for p in before]
    old_tree = BVHTree.FromPolygons(old_points, indices, all_triangles=True)
    normal = np.zeros_like(before)
    for corner in range(3):
        np.add.at(normal, triangles[:, corner], old_cross)
    normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-30)[:, None]
    correspondence = ThicknessCorrespondence(
        before, after, triangles, surface, old_cross
    )
    ratios = []
    worst: tuple[int, float] | None = None
    for index in np.flatnonzero(changed):
        old_n = Vector(normal[index])
        # Source-only rays accelerate the nearest query. A miss never suppresses
        # an anchor: sharp corners can have ambiguous inward ray intersections.
        old_hit = old_tree.ray_cast(
            old_points[index] - old_n * epsilon, -old_n, scale * 2
        )
        seed = None
        if old_hit[0] is not None and old_hit[3] > epsilon * 4:
            face = old_hit[2]
            if (
                index not in triangles[face]
                and correspondence.normals[face] @ normal[index] < 0
                and (correspondence.centers[face] - before[index]) @ normal[index] < 0
            ):
                seed = face
        ratio = (
            correspondence.certify(
                int(index), normal[index], budget, settings.max_thinning, epsilon * 4
            )
            if seed is not None
            else None
        )
        if ratio is None:
            old_distance, new_distance = correspondence.measure(
                int(index), normal[index], seed, budget
            )
            if not np.isfinite(old_distance):
                # An open single sheet may have no opposing source surface.
                continue
            if old_distance <= epsilon * 4:
                fail("Source opposing thickness is below measurement resolution")
            ratio = new_distance / old_distance
        ratios.append(ratio)
        if worst is None or ratio < worst[1]:
            worst = (int(index), ratio)
        if ratio < 1 - settings.max_thinning:
            independent = correspondence.independent(int(index), normal[index])
            if independent[1] / independent[0] < 1 - settings.max_thinning:
                fail(
                    "Corresponding local wall exceeds max_thinning; prior mesh retained"
                )
            fail("Independent thickness measurements disagree; prior mesh retained")
    if worst is not None:
        index, lower_bound = worst
        independent = correspondence.independent(index, normal[index])
        if independent[1] / independent[0] < lower_bound - 1e-5:
            fail("Independent thickness measurements disagree; prior mesh retained")
        if independent[1] / independent[0] < 1 - settings.max_thinning:
            fail("Corresponding local wall exceeds max_thinning; prior mesh retained")
    return len(ratios), min(ratios) if ratios else None, budget.tests


def execute(obj: Any, arguments: SculptFilterArguments) -> SculptFilterResult:
    settings = arguments.fairing
    assert settings is not None
    data = obj.data
    if (
        bpy.context.mode != "OBJECT"
        or obj.mode != "OBJECT"
        or obj.modifiers
        or data.shape_keys
        or obj.animation_data
        or data.animation_data
        or obj.constraints
        or obj.library
        or obj.override_library
        or data.library
        or data.override_library
        or not obj.is_editable
        or not data.is_editable
        or data.has_custom_normals
        or obj.use_dynamic_topology_sculpting
        or not multires.unit_scale(obj)
        or bpy.app.is_job_running("RENDER")
    ):
        fail(
            "Use an editable unanimated base mesh in Object Mode with unit scale; "
            "apply modifiers explicitly first"
        )
    count = len(data.vertices)
    if count < 3 or count > settings.max_points:
        fail("Surface exceeds max_points or is empty")
    mesh.check_budget(
        len(data.vertices) + len(data.edges) + len(data.loops) + len(data.polygons)
    )
    mask_summary = sculpt_regions.mask_inspect(obj)
    mask_attribute = sculpt_regions.attribute(data, ".sculpt_mask", "POINT", "FLOAT")
    masks = np.zeros(count)
    if mask_attribute:
        mask_attribute.data.foreach_get("value", masks)
    weights = 1 - masks
    if settings.regions:
        matrices = [
            region_matrix(region) @ obj.matrix_world for region in settings.regions
        ]
        inside = [
            any(
                contains(matrix @ vertex.co, region)
                for matrix, region in zip(matrices, settings.regions, strict=True)
            )
            for vertex in data.vertices
        ]
        weights *= np.array(inside)
    elif settings.vertex_group:
        weights *= group_weights(obj, settings.vertex_group)
    elif mask_attribute is None or not np.any(masks >= 0.999):
        fail(
            "Choose local regions, a vertex group, or an existing mask "
            "with protected points"
        )
    if settings.protect_vertex_group:
        weights[group_weights(obj, settings.protect_vertex_group) > 0] = 0
    if not np.isfinite(weights).all() or np.any((weights < 0) | (weights > 1)):
        fail("Region and mask weights must be finite in [0, 1]")
    original = np.empty((count, 3))
    data.vertices.foreach_get("co", original.reshape(-1))
    edges = np.empty((len(data.edges), 2), dtype=np.int32)
    data.edges.foreach_get("vertices", edges.reshape(-1))
    with mesh.snapshot(obj) as bm:
        if any(len(e.link_faces) not in (1, 2) for e in bm.edges):
            fail("Non-manifold source edges require repair before fairing")
        for vertex in bm.verts:
            if vertex.hide or not vertex.link_faces:
                weights[vertex.index] = 0
        for face in bm.faces:
            if face.hide:
                for vertex in face.verts:
                    weights[vertex.index] = 0
        for edge in bm.edges:
            if edge.is_boundary or not edge.smooth:
                for vertex in edge.verts:
                    weights[vertex.index] = 0
    crease = sculpt_regions.attribute(data, "crease_edge", "EDGE", "FLOAT")
    if crease:
        values = np.empty(len(edges))
        crease.data.foreach_get("value", values)
        weights[edges[values > 0].reshape(-1)] = 0
    # Pin the selection's edge and feather a fixed number of adjacency rings.
    selected = weights > 0
    boundary = np.any(selected[edges], axis=1) & ~np.all(selected[edges], axis=1)
    distance = np.full(count, settings.boundary_rings + 1)
    distance[~selected] = 0
    distance[edges[boundary].reshape(-1)] = 0
    for ring in range(1, settings.boundary_rings + 1):
        previous = distance.copy()
        nearby = np.any(previous[edges] == ring - 1, axis=1)
        vertices = edges[nearby].reshape(-1)
        distance[vertices] = np.minimum(distance[vertices], ring)
    t = np.minimum(1, distance / (settings.boundary_rings + 1))
    weights *= t * t * (3 - 2 * t)
    if not np.any(weights > 0):
        fail("Local region contains no movable interior points")
    result = fair_points(original, edges, weights, arguments)
    moved = np.linalg.norm(result - original, axis=1)
    changed = moved > 0
    if np.any(moved > settings.max_distance * 1.0001 + 1e-7):
        fail("Stored coordinates exceed max_distance")
    staged = data.copy()
    isolated = data.users > 1
    try:
        staged.vertices.foreach_set("co", result.reshape(-1))
        staged.update()
        staged.calc_loop_triangles()
        triangles = np.array(
            [tuple(t.vertices) for t in staged.loop_triangles], dtype=np.int32
        )
        samples, ratio, tests = validate_geometry(
            original, result, triangles, changed, arguments
        )
        world_before = np.array([tuple(obj.matrix_world @ Vector(p)) for p in original])
        world_after = np.array([tuple(obj.matrix_world @ Vector(p)) for p in result])
        response = SculptFilterResult(
            object_name=obj.name,
            type="fair",
            strength=arguments.strength,
            iterations=arguments.iterations,
            axes=arguments.axes,
            orientation="local",
            multires_level=0,
            bounds_before_min=world_before.min(axis=0).tolist(),
            bounds_before_max=world_before.max(axis=0).tolist(),
            bounds_after_min=world_after.min(axis=0).tolist(),
            bounds_after_max=world_after.max(axis=0).tolist(),
            changed=bool(changed.any()),
            mask=mask_summary,
            fairing=SurfaceFairingSummary(
                affected_points=int(changed.sum()),
                pinned_points=int((weights == 0).sum()),
                maximum_displacement=float(moved.max()),
                thickness_samples=samples,
                minimum_thickness_ratio=ratio,
                triangle_tests=tests,
                mesh_isolated=isolated,
            ),
        )
        publish(obj, staged)
    except BaseException:
        obj.data = data
        bpy.data.meshes.remove(staged)
        raise
    if not data.users:
        name = data.name
        bpy.data.meshes.remove(data)
        staged.name = name
    return response


def publish(obj: Any, staged: Any) -> None:
    obj.data = staged
    obj.update_tag()
    bpy.context.view_layer.update()
