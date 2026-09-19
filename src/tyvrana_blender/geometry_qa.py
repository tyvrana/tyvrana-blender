"""Triangle-distance branch and bound; explicit finite-work QA, never CCD."""

import heapq
import math
import time
from dataclasses import dataclass
from itertools import count
from typing import Any, Never

import bpy  # type: ignore[import-not-found]
from mathutils.geometry import closest_point_on_tri  # type: ignore[import-not-found]

from . import layer_geometry as geometry
from .errors import OperationError
from .geometry_qa_models import (
    ClearancePair,
    ClearanceSummary,
    GeometryCoverage,
    GeometryInspectArguments,
    GeometryInspectResult,
    GeometryQuery,
    GeometrySample,
    GeometrySummary,
    SurfaceContact,
)


def fail(message: str) -> Never:
    raise OperationError("geometry_qa_invalid", message)


@dataclass
class Budget:
    maximum: int
    tests: int = 0
    nodes: int = 0

    def test(self) -> None:
        self.tests += 1
        if self.tests > self.maximum:
            fail(
                "Triangle test budget exceeded; isolate regions, reduce "
                "samples, or raise max_triangle_tests (up to 2000000). No "
                "clearance verdict produced"
            )


@dataclass
class Node:
    lo: tuple[float, ...]
    hi: tuple[float, ...]
    indices: list[int]
    children: tuple["Node", "Node"] | None = None


class Surface:
    def __init__(self, source: geometry.Surface) -> None:
        self.source = source
        self.points = source.points
        self.indices = source.triangles
        self.triangles = [tuple(self.points[i] for i in t) for t in self.indices]
        self.faces = source.triangle_faces
        self.lo = [
            tuple(min(p[k] for p in t) for k in range(3)) for t in self.triangles
        ]
        self.hi = [
            tuple(max(p[k] for p in t) for k in range(3)) for t in self.triangles
        ]
        self.root = self.node(list(range(len(self.triangles))))
        self.normals = [(b - a).cross(c - a) for a, b, c in self.triangles]
        edges: dict[tuple[int, int], list[int]] = {}
        for triangle in self.indices:
            for a, b in zip(triangle, (*triangle[1:], triangle[0]), strict=True):
                edges.setdefault((min(a, b), max(a, b)), []).append(1 if a < b else -1)
        self.closed = all(len(e) == 2 and sum(e) == 0 for e in edges.values())
        origin = self.points[0]
        self.volume = (
            sum(
                (a - origin).dot((b - origin).cross(c - origin))
                for a, b, c in self.triangles
            )
            / 6
            if self.closed
            else None
        )

    def node(self, indices: list[int]) -> Node:
        lo = tuple(min(self.lo[i][k] for i in indices) for k in range(3))
        hi = tuple(max(self.hi[i][k] for i in indices) for k in range(3))
        if len(indices) <= 8:
            return Node(lo, hi, indices)
        axis = max(range(3), key=lambda k: hi[k] - lo[k])
        indices.sort(key=lambda i: self.lo[i][axis] + self.hi[i][axis])
        middle = len(indices) // 2
        return Node(
            lo, hi, [], (self.node(indices[:middle]), self.node(indices[middle:]))
        )


def bounds_distance(a: Node, b: Node) -> float:
    return sum(max(0.0, a.lo[k] - b.hi[k], b.lo[k] - a.hi[k]) ** 2 for k in range(3))


def point_segment(p: Any, a: Any, b: Any) -> Any:
    edge = b - a
    t = (
        min(1.0, max(0.0, (p - a).dot(edge) / edge.length_squared))
        if edge.length_squared > 1e-30
        else 0.0
    )
    return a + edge * t


def segment_pair(a: Any, b: Any, c: Any, d: Any) -> list[tuple[Any, Any]]:
    candidates = [
        (a, point_segment(a, c, d)),
        (b, point_segment(b, c, d)),
        (point_segment(c, a, b), c),
        (point_segment(d, a, b), d),
    ]
    u, v, w = b - a, d - c, a - c
    aa, bb, cc = u.dot(u), u.dot(v), v.dot(v)
    dd, ee = u.dot(w), v.dot(w)
    denominator = aa * cc - bb * bb
    if denominator > 1e-14 * aa * cc:
        s, t = (bb * ee - cc * dd) / denominator, (aa * ee - bb * dd) / denominator
        if 0 <= s <= 1 and 0 <= t <= 1:
            candidates.append((a + s * u, c + t * v))
    return candidates


def triangle_distance(a: Any, b: Any) -> tuple[float, Any, Any]:
    """All vertex/face, edge/edge and edge/face candidates, including coplanar."""
    candidates = []
    for left, right, reverse in [(a, b, False), (b, a, True)]:
        normal = (right[1] - right[0]).cross(right[2] - right[0])
        if normal.length_squared > 1e-30:
            for p in left:
                q = closest_point_on_tri(p, *right)
                candidates.append((q, p) if reverse else (p, q))
            for i in range(3):
                p, end = left[i], left[(i + 1) % 3]
                direction = end - p
                if direction.length_squared <= 1e-30:
                    continue
                denominator = normal.dot(direction)
                # Native intersect_ray_tri uses an absolute determinant epsilon,
                # which misses crossings on small geometry. Scale the plane test.
                if abs(denominator) > 1e-12 * normal.length * direction.length:
                    t = normal.dot(right[0] - p) / denominator
                    if 0 <= t <= 1:
                        q = p + t * direction
                        projected = closest_point_on_tri(q, *right)
                        scale = max((right[k] - right[0]).length for k in (1, 2))
                        if (projected - q).length <= scale * 1e-7:
                            return 0.0, q, q
    for i in range(3):
        for j in range(3):
            candidates.extend(segment_pair(a[i], a[(i + 1) % 3], b[j], b[(j + 1) % 3]))
    p, q = min(candidates, key=lambda pair: (pair[0] - pair[1]).length_squared)
    return float((p - q).length), p, q


def contact(
    a: Surface, b: Surface, i: int, j: int, distance: float, p: Any, q: Any
) -> SurfaceContact:
    return SurfaceContact(
        left_face=a.faces[i],
        right_face=b.faces[j],
        distance=distance,
        left_point=list(p),
        right_point=list(q),
    )


def proximity(
    a: Surface,
    b: Surface,
    args: GeometryInspectArguments,
    budget: Budget,
    pair: ClearancePair | None = None,
) -> tuple[SurfaceContact | None, int, int, list[SurfaceContact]]:
    same = a is b
    exemptions = (
        [(set(e.left_faces), set(e.right_faces)) for e in pair.exemptions]
        if pair
        else []
    )
    if any(
        max(left) >= len(a.source.bm.faces) or max(right) >= len(b.source.bm.faces)
        for left, right in exemptions
    ):
        fail(
            "Exempt face index is outside evaluated topology; inspect "
            "current mesh and retry"
        )
    closest = None
    minimum = math.inf
    found = 0
    exempt = 0
    details: list[SurfaceContact] = []
    serial = count()
    queue: list[tuple[float, int, Node, Node]] = [(0, next(serial), a.root, b.root)]
    while queue:
        lower, _, left, right = heapq.heappop(queue)
        budget.nodes += 1
        if budget.nodes > 8 * budget.maximum + 1024 or len(queue) > 250000:
            fail(
                "Geometry traversal budget exceeded; reduce surface "
                "complexity or sample count"
            )
        threshold = args.tolerance**2 if same else max(minimum**2, args.tolerance**2)
        if lower > threshold:
            continue
        if left.children or right.children:
            if same and left is right:
                assert left.children is not None
                x, y = left.children
                children = [(x, x), (x, y), (y, y)]
            elif left.children and (
                not right.children
                or sum((x - y) ** 2 for x, y in zip(left.lo, left.hi, strict=True))
                >= sum((x - y) ** 2 for x, y in zip(right.lo, right.hi, strict=True))
            ):
                children = [(n, right) for n in left.children]
            else:
                assert right.children is not None
                children = [(left, n) for n in right.children]
            for x, y in children:
                distance = bounds_distance(x, y)
                if distance <= threshold:
                    heapq.heappush(queue, (distance, next(serial), x, y))
            continue
        for i in left.indices:
            for j in right.indices:
                if same and (
                    left is right and i >= j or set(a.indices[i]) & set(b.indices[j])
                ):
                    continue
                budget.test()
                if any(a.faces[i] in x and b.faces[j] in y for x, y in exemptions):
                    exempt += 1
                    continue
                distance, p, q = triangle_distance(a.triangles[i], b.triangles[j])
                row = None
                if distance < minimum:
                    minimum = distance
                    closest = row = contact(a, b, i, j, distance, p, q)
                if distance <= args.tolerance:
                    found += 1
                    if args.worst_limit:
                        details.append(row or contact(a, b, i, j, distance, p, q))
                        details.sort(
                            key=lambda r: (r.distance, r.left_face, r.right_face)
                        )
                        del details[args.worst_limit :]
    return closest, found, exempt, details


def representatives(surface: Surface) -> list[Any]:
    parent = list(range(len(surface.points)))
    used: set[int] = set()

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for tri in surface.indices:
        used.update(tri)
        a = root(tri[0])
        for index in tri[1:]:
            parent[root(index)] = a
    return [surface.points[i] for i in sorted({root(i) for i in used})]


def inside_count(a: Surface, b: Surface, budget: Budget) -> int | None:
    if not b.closed:
        return None
    result = 0
    for point in representatives(a):
        total = 0.0
        for triangle in b.triangles:
            budget.test()
            x, y, z = (p - point for p in triangle)
            numerator = x.dot(y.cross(z))
            denominator = (
                x.length * y.length * z.length
                + x.dot(y) * z.length
                + y.dot(z) * x.length
                + z.dot(x) * y.length
            )
            total += 2 * math.atan2(numerator, denominator)
        if abs(total) > 2 * math.pi:
            result += 1
    return result


def inspect_pair(
    pair: ClearancePair,
    surfaces: dict[str, Surface],
    args: GeometryInspectArguments,
    budget: Budget,
) -> ClearanceSummary:
    a, b = surfaces[pair.left], surfaces[pair.right]
    nearest, found, exempt, details = proximity(a, b, args, budget, pair)
    inside = args.containment and not pair.exemptions and found == 0
    return ClearanceSummary(
        left=pair.left,
        right=pair.right,
        minimum_distance=nearest.distance if nearest else None,
        closest=nearest,
        contact_triangle_pairs=found,
        exempt_triangle_pairs=exempt,
        left_representatives_inside_right=inside_count(a, b, budget)
        if inside
        else None,
        right_representatives_inside_left=inside_count(b, a, budget)
        if inside
        else None,
        contacts=details,
    )


def inspect_object(
    query: GeometryQuery,
    surfaces: dict[str, Surface],
    args: GeometryInspectArguments,
    budget: Budget,
) -> GeometrySummary:
    surface = surfaces[query.object_name]
    degenerate = [
        i
        for i, normal in enumerate(surface.normals)
        if normal.length <= args.tolerance**2
    ]
    reverse = collapsed = volume_reverse = None
    local: dict[str, Any] = {}
    if query.reference:
        reference = surfaces[query.reference]
        if surface.source.topology != reference.source.topology:
            fail(
                "Reference ordered topology differs; capture a matching "
                "reference with volume.snapshot and retry"
            )
        reverse, collapsed = 0, 0
        ma = surface.source.obj.matrix_world.inverted()
        mb = reference.source.obj.matrix_world.inverted()
        for current, rest in zip(surface.triangles, reference.triangles, strict=True):
            a, b, c = (ma @ p for p in current)
            x, y, z = (mb @ p for p in rest)
            normal, baseline = (b - a).cross(c - a), (y - x).cross(z - x)
            if baseline.length > args.tolerance**2:
                collapsed += normal.length / baseline.length < args.collapsed_area_ratio
                reverse += normal.dot(baseline) < 0
        if surface.volume is not None and reference.volume is not None:
            volume_reverse = surface.volume * reference.volume < 0
        from .geometry_deformation import jacobians

        local = jacobians(
            surface, reference, args.collapsed_volume_ratio, args.worst_limit
        )
    self_count = None
    contacts: list[SurfaceContact] = []
    if query.self_intersection:
        _, self_count, _, contacts = proximity(surface, surface, args, budget)
    return GeometrySummary(
        object_name=query.object_name,
        triangle_count=len(surface.triangles),
        topology_sha256=surface.source.topology,
        degenerate_triangles=len(degenerate),
        degenerate_faces=sorted({surface.faces[i] for i in degenerate})[
            : args.worst_limit
        ],
        closed_consistent=surface.closed,
        signed_volume=surface.volume,
        self_contact_triangle_pairs=self_count,
        self_contacts=contacts,
        normal_reversed_triangles=reverse,
        collapsed_triangles=collapsed,
        signed_volume_reversed=volume_reverse,
        **local,
    )


def inspect(args: GeometryInspectArguments) -> GeometryInspectResult:
    start = time.perf_counter()
    scene = bpy.context.scene
    frame, subframe = scene.frame_current, scene.frame_subframe
    budget = Budget(args.max_triangle_tests)
    names = {n for pair in args.pairs for n in (pair.left, pair.right)}
    names.update(query.object_name for query in args.objects)
    names.update(query.reference for query in args.objects if query.reference)
    from . import geometry_instances, geometry_temporal

    names.update(name for query in args.instances for name in query.obstacles)
    instance_budget = geometry_instances.InstanceBudget(args.max_instance_vertices)
    vertices = 0
    path_points = 0

    def evaluate(value: float | None) -> GeometrySample:
        nonlocal vertices, path_points
        if value is not None:
            whole = math.floor(value)
            scene.frame_set(whole, subframe=value - whole)
        with geometry.SurfaceCache(max_work=1000000 - vertices) as cache:
            sources = {name: cache.get(name) for name in sorted(names)}
            if sum(len(s.triangles) for s in sources.values()) > 250000:
                fail("At most250000 evaluated triangles per sample; isolate objects")
            surfaces = {name: Surface(source) for name, source in sources.items()}
            vertices += cache.vertices
            instances = [
                geometry_instances.inspect(q, surfaces, args, budget, instance_budget)
                for q in args.instances
            ]
            path_points += sum(row.path_points for row in instances)
            if path_points > 2000000:
                fail(
                    "Evaluated instance path sweep exceeds2000000 points; "
                    "reduce samples/density"
                )
            return GeometrySample(
                frame=scene.frame_current + scene.frame_subframe,
                pairs=[inspect_pair(p, surfaces, args, budget) for p in args.pairs],
                objects=[
                    inspect_object(q, surfaces, args, budget) for q in args.objects
                ],
                instances=instances,
            )

    try:
        if args.adaptive:
            samples, coverage = geometry_temporal.sample(args.adaptive, evaluate)
        else:
            times_to_sample: list[float | None] = (
                list(args.frames) if args.frames else [None]
            )
            samples = [evaluate(value) for value in times_to_sample]
            times = sorted(row.frame for row in samples)
            coverage = GeometryCoverage(
                mode="explicit" if args.frames else "current",
                sample_count=len(samples),
                maximum_gap=max(
                    (b - a for a, b in zip(times[:-1], times[1:], strict=True)),
                    default=0,
                ),
            )
    finally:
        if args.frames or args.adaptive:
            scene.frame_set(frame, subframe=subframe)
    extrema = [
        (pair.minimum_distance, sample.frame)
        for sample in samples
        for pair in sample.pairs
        if pair.minimum_distance is not None
    ]
    extrema.extend(
        (row.minimum_distance, sample.frame)
        for sample in samples
        for row in sample.instances
        if row.minimum_distance is not None
    )
    minimum, worst_frame = min(extrema) if extrema else (None, None)
    if worst_frame is None:
        local = [
            (obj.minimum_jacobian, sample.frame)
            for sample in samples
            for obj in sample.objects
            if obj.minimum_jacobian is not None
        ]
        if local:
            worst_frame = min(local)[1]
    return GeometryInspectResult(
        samples=samples,
        worst_frame=worst_frame,
        minimum_distance=minimum,
        triangle_tests=budget.tests,
        evaluated_vertex_samples=vertices + instance_budget.vertices,
        coverage=coverage,
        processing_seconds=time.perf_counter() - start,
        restored=True,
        limitations=[
            "Viewport surfaces and lazy shared mesh instances; owned growth "
            "uses native evaluated paths. Distances are world units within "
            "floating-point tolerance; no full template realization for QA.",
            "Instance QA tests candidate elements against each other/obstacles; "
            "degeneration counts cover tested elements only. Native persistent "
            "IDs remain stable only while the generating topology is unchanged.",
            "Contact counts are triangle pairs within tolerance, not "
            "intersection volume or penetration depth. Exemptions omit both"
            " specified face sets; exempt count covers visited candidates "
            "only.",
            "Self contacts exclude triangles sharing any vertex; folds "
            "between adjacent faces require additional deformation "
            "diagnostics.",
            "Containment uses component representative winding against "
            "closed consistently oriented surfaces without "
            "contact/exemptions; assumes no self intersection, not a "
            "solid-union proof.",
            "Local normal reversal is not proof of inversion; legitimate "
            "bending may reverse normals. Signed volume reversal is only a "
            "global closed-surface orientation diagnostic.",
            "Local Jacobians are least-squares affine one-ring volume ratios; "
            "rank-deficient planar neighborhoods are unavailable. Negative values "
            "are local orientation proxies, not a volumetric-element/FEM proof.",
            "Explicit/adaptive samples are not continuous collision or swept-volume "
            "certification. Unsampled narrow events can be missed. Coverage reports "
            "gaps and unresolved risky intervals; native evaluation memory precedes "
            "geometry budget checks.",
        ],
    )
