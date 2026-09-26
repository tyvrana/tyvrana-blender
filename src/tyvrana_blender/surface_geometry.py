"""Sparse boundary interpolation and deterministic bounded surface assembly."""

import bisect
import math
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, cast

from tyvrana_protocol import JsonValue

from .errors import OperationError
from .surface_models import (
    EllipseOpening,
    SurfaceCurve,
    SurfaceFeature,
    SurfaceOpening,
    SurfacePatch,
    SurfaceSpec,
)

type Point = list[float]
type Face = list[int]
type Triangulator = Callable[
    [list[Point], list[tuple[int, int]]],
    tuple[list[Point], list[Face], list[list[int]]],
]


def edge_key(a: int, b: int) -> tuple[int, int]:
    return min(a, b), max(a, b)


def fail(code: str, message: str, details: JsonValue = None) -> Never:
    raise OperationError("surface_" + code, message, details)


def add(a: Point, b: Point) -> Point:
    return [x + y for x, y in zip(a, b, strict=True)]


def sub(a: Point, b: Point) -> Point:
    return [x - y for x, y in zip(a, b, strict=True)]


def mul(a: Point, s: float) -> Point:
    return [x * s for x in a]


def dot(a: Point, b: Point) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def cross(a: Point, b: Point) -> Point:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def unit(a: Point, *, details: dict[str, JsonValue] | None = None) -> Point:
    length = math.hypot(*a)
    if length < 1e-12:
        fail(
            "degenerate",
            "Surface tangent/normal collapsed; revise contours or features",
            {
                **(details or {}),
                "degeneracy": "collapsed_normal",
                "normal_length": length,
                "minimum_length": 1e-12,
            },
        )
    return mul(a, 1 / length)


def blend(a: Point, b: Point, t: float) -> Point:
    return add(mul(a, 1 - t), mul(b, t))


def cross2(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def segment_distance(p: Point, a: Point, b: Point) -> float:
    edge = sub(b, a)
    t = min(1.0, max(0.0, dot(sub(p, a), edge) / max(dot(edge, edge), 1e-30)))
    return math.dist(p, add(a, mul(edge, t)))


def loop_edges(points: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(points, points[1:] + points[:1], strict=True))


def area(points: list[Point]) -> float:
    return sum(cross2(a, b) for a, b in loop_edges(points)) / 2


def intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    ab, cd = sub(b, a), sub(d, c)
    q = cross2(ab, cd)
    if abs(q) < 1e-12:
        return (
            min(
                segment_distance(a, c, d),
                segment_distance(b, c, d),
                segment_distance(c, a, b),
                segment_distance(d, a, b),
            )
            < 1e-8
        )
    t, u = cross2(sub(c, a), cd) / q, cross2(sub(c, a), ab) / q
    return -1e-8 <= t <= 1 + 1e-8 and -1e-8 <= u <= 1 + 1e-8


def inside(point: Point, polygon: list[Point]) -> bool:
    result = False
    x, y = point
    for a, b in loop_edges(polygon):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (
            b[1] - a[1]
        ) + a[0]:
            result = not result
    return result


def validate_loop(points: list[Point]) -> list[Point]:
    if abs(area(points)) < 1e-8:
        fail("opening_invalid", "Opening contour has zero area")
    edges = loop_edges(points)
    for i, (a, b) in enumerate(edges):
        if math.dist(a, b) < 1e-6:
            fail("opening_invalid", "Opening contour contains collapsed edges")
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            if intersects(a, b, *edges[j]):
                fail("opening_invalid", "Opening contour intersects itself")
    return points if area(points) > 0 else list(reversed(points))


def opening_loops(opening: SurfaceOpening) -> tuple[list[Point], list[Point]]:
    if isinstance(opening, EllipseOpening):
        loops = []
        c, s = math.cos(opening.rotation), math.sin(opening.rotation)
        for extra in (0.0, opening.support_width):
            points = []
            for i in range(opening.samples):
                angle = 2 * math.pi * i / opening.samples
                x = (opening.radii[0] + extra) * math.cos(angle)
                y = (opening.radii[1] + extra) * math.sin(angle)
                points.append(
                    [
                        opening.center[0] + x * c - y * s,
                        opening.center[1] + x * s + y * c,
                    ]
                )
            loops.append(points)
    else:
        points = validate_loop([list(p) for p in opening.points])
        outer = []
        for i, p in enumerate(points):
            a, b = sub(p, points[i - 1]), sub(points[(i + 1) % len(points)], p)
            a, b = mul(a, 1 / math.hypot(*a)), mul(b, 1 / math.hypot(*b))
            normal = [a[1] + b[1], -a[0] - b[0]]
            denominator = dot(normal, [a[1], -a[0]])
            if denominator < 0.1:
                fail(
                    "opening_invalid",
                    "Opening corner is too sharp for its support ring",
                )
            outer.append(add(p, mul(normal, opening.support_width / denominator)))
        loops = [
            [
                blend(a, b, j / opening.subdivisions)
                for a, b in loop_edges(loop)
                for j in range(opening.subdivisions)
            ]
            for loop in (points, outer)
        ]
    for loop in loops:
        validate_loop(loop)
        if any(not 1e-5 < x < 1 - 1e-5 for p in loop for x in p):
            fail(
                "opening_invalid",
                "Opening and support ring must lie strictly inside their patch",
            )
    return loops[0], loops[1]


class Curve:
    def __init__(self, spec: SurfaceCurve, nodes: dict[str, Point]) -> None:
        self.spec = spec
        self.controls = [nodes[spec.start], *spec.through, nodes[spec.end]]
        self.count = 32 * (len(self.controls) - 1)
        self.points = [self.evaluate(i / self.count) for i in range(self.count + 1)]
        lengths = [0.0]
        for a, b in zip(self.points, self.points[1:], strict=False):
            lengths.append(lengths[-1] + math.dist(a, b))
        if lengths[-1] < 1e-6:
            fail("contour_invalid", "Contour length collapsed")
        self.lengths = [x / lengths[-1] for x in lengths]

    def evaluate(self, parameter: float) -> Point:
        controls = self.controls
        value = parameter * (len(controls) - 1)
        index = max(0, min(len(controls) - 2, int(value)))
        t = value - index
        p0, p1, p2, p3 = (
            controls[max(0, index - 1)],
            controls[index],
            controls[index + 1],
            controls[min(len(controls) - 1, index + 2)],
        )
        if self.spec.interpolation == "linear":
            return blend(p1, p2, t)
        return [
            0.5
            * (
                2 * b
                + (-a + c) * t
                + (2 * a - 5 * b + 4 * c - d) * t * t
                + (-a + 3 * b - 3 * c + d) * t * t * t
            )
            for a, b, c, d in zip(p0, p1, p2, p3, strict=True)
        ]

    def at(self, t: float) -> Point:
        i = max(0, min(self.count - 1, bisect.bisect_right(self.lengths, t) - 1))
        h = self.lengths[i + 1] - self.lengths[i]
        if h < 1e-12:
            fail("contour_invalid", "Contour arc-length sampling collapsed")
        u = min(1.0, max(0.0, (t - self.lengths[i]) / h))
        # Monotone C1 inverse arc-length map. Interpolating sampled chords would
        # introduce normal jumps that fold displaced/offset feature surfaces.
        previous = self.lengths[i] - self.lengths[i - 1] if i else h
        following = (
            self.lengths[i + 2] - self.lengths[i + 1] if i + 2 <= self.count else h
        )
        m0, m1 = 2 * h / (h + previous), 2 * h / (h + following)
        fraction = (
            (u * u * u - 2 * u * u + u) * m0
            + (-2 * u * u * u + 3 * u * u)
            + (u * u * u - u * u) * m1
        )
        return self.evaluate((i + fraction) / self.count)


def boundary_layout(
    spec: SurfaceSpec,
) -> tuple[dict[str, list[tuple[str, bool]]], dict[str, int], dict[str, list[str]]]:
    curves = {c.id: c for c in spec.curves}
    layouts: dict[str, list[tuple[str, bool]]] = {}
    uses: dict[str, list[str]] = defaultdict(list)
    for patch in spec.patches:
        ordered = [curves[x] for x in patch.boundaries]
        for first_reverse in (False, True):
            first = ordered[0].end if first_reverse else ordered[0].start
            current = ordered[0].start if first_reverse else ordered[0].end
            result = [(ordered[0].id, first_reverse)]
            for curve in ordered[1:]:
                if current == curve.start:
                    result.append((curve.id, False))
                    current = curve.end
                elif current == curve.end:
                    result.append((curve.id, True))
                    current = curve.start
                else:
                    break
            if len(result) == 4 and current == first:
                layouts[patch.id] = result
                break
        if patch.id not in layouts:
            fail(
                "junction_invalid",
                f"Patch {patch.id} boundaries do not form an ordered four-sided loop",
            )
        for curve_id in patch.boundaries:
            uses[curve_id].append(patch.id)
    if any(len(v) > 2 for v in uses.values()):
        fail(
            "junction_invalid",
            "At most two patches may share a boundary; use a manifold patch layout",
        )
    if set(uses) != set(curves):
        fail("contour_invalid", "Every declared curve must bound a patch")
    signs = {spec.patches[0].id: 1}
    queue = deque(signs)
    while queue:
        p = queue.popleft()
        for curve_id, reverse in layouts[p]:
            for neighbor in uses[curve_id]:
                if neighbor == p:
                    continue
                other_reverse = dict(layouts[neighbor])[curve_id]
                sign = signs[p] * (-1 if reverse == other_reverse else 1)
                if neighbor in signs and signs[neighbor] != sign:
                    fail("junction_invalid", "Patch network is not orientable")
                if neighbor not in signs:
                    signs[neighbor] = sign
                    queue.append(neighbor)
    if len(signs) != len(spec.patches):
        fail(
            "junction_invalid",
            "Each surface must be connected through shared boundaries",
        )
    return layouts, signs, dict(uses)


class Patch:
    def __init__(
        self,
        spec: SurfacePatch,
        layout: list[tuple[str, bool]],
        curves: dict[str, Curve],
        features: list[SurfaceFeature],
        sign: int,
    ) -> None:
        self.spec, self.layout, self.curves, self.features, self.sign = (
            spec,
            layout,
            curves,
            features,
            sign,
        )
        self.quad_dominant = False

    def boundary(self, i: int, t: float) -> Point:
        name, reverse = self.layout[i]
        return self.curves[name].at(1 - t if reverse else t)

    def base(self, u: float, v: float) -> Point:
        lower, upper = self.boundary(0, u), self.boundary(2, 1 - u)
        left, right = self.boundary(3, 1 - v), self.boundary(1, v)
        a, b, c, d = (
            self.boundary(0, 0),
            self.boundary(0, 1),
            self.boundary(2, 0),
            self.boundary(2, 1),
        )
        bilinear = blend(blend(a, b, u), blend(d, c, u), v)
        return sub(add(blend(lower, upper, v), blend(left, right, u)), bilinear)

    def at(self, uv: Point) -> Point:
        u, v = uv
        point = self.base(u, v)
        height = sum(feature_value(f, uv) * f.height for f in self.features)
        if height:
            epsilon = 1e-4
            du = sub(
                self.base(min(1, u + epsilon), v), self.base(max(0, u - epsilon), v)
            )
            dv = sub(
                self.base(u, min(1, v + epsilon)), self.base(u, max(0, v - epsilon))
            )
            normal = unit(
                cross(du, dv),
                details={
                    "patch_id": self.spec.id,
                    "curve_ids": [name for name, _ in self.layout],
                    "uv": list(uv),
                    "tangent_lengths": [math.hypot(*du), math.hypot(*dv)],
                },
            )
            # Fade displacements at shared boundaries to preserve exact stitches.
            fade = min(1.0, 10 * min(u, 1 - u, v, 1 - v))
            point = add(
                point, mul(normal, height * self.sign * fade * fade * (3 - 2 * fade))
            )
        return point


def feature_value(feature: SurfaceFeature, uv: Point) -> float:
    if feature.kind == "ridge":
        distance = (
            min(
                segment_distance(uv, a, b)
                for a, b in zip(feature.path, feature.path[1:], strict=False)
            )
            / feature.width
        )
    else:
        radius = math.hypot(
            *[
                (x - c) / r
                for x, c, r in zip(uv, feature.center, feature.radii, strict=True)
            ]
        )
        distance = (
            radius
            if feature.kind == "bulge"
            else abs(radius - 1) * min(feature.radii) / feature.width
        )
    return max(0.0, 1 - distance * distance) ** 3


def feature_samples(feature: SurfaceFeature) -> list[Point]:
    points = []
    if feature.kind == "ridge":
        for a, b in zip(feature.path, feature.path[1:], strict=False):
            length = math.dist(a, b)
            if length < 1e-6:
                fail("feature_invalid", "Ridge path has coincident controls")
            steps = min(
                32, max(2, math.ceil(length / feature.width) * feature.refinement)
            )
            for i in range(steps + 1):
                center = blend(a, b, i / steps)
                for j in range(-feature.refinement, feature.refinement + 1):
                    shift = feature.width * j / feature.refinement
                    points.append(
                        [
                            center[0] - (b[1] - a[1]) / length * shift,
                            center[1] + (b[0] - a[0]) / length * shift,
                        ]
                    )
    else:
        points.append(list(feature.center))
        for ring in range(
            0 if feature.kind == "ring" else 1, feature.refinement * 2 + 1
        ):
            radius = ring / (feature.refinement * 2)
            if feature.kind == "ring":
                radius = 1 + (radius * 2 - 1) * feature.width / min(feature.radii)
            samples = 16 * feature.refinement
            if feature.kind == "bulge":
                samples = max(8, round(samples * radius))
            for i in range(samples):
                theta = 2 * math.pi * i / samples
                points.append(
                    [
                        feature.center[0] + radius * feature.radii[0] * math.cos(theta),
                        feature.center[1] + radius * feature.radii[1] * math.sin(theta),
                    ]
                )
    return [p for p in points if all(1e-5 < v < 1 - 1e-5 for v in p)]


def pair_triangles(
    points: list[Point], faces: list[Face], features: list[SurfaceFeature]
) -> list[Face]:
    edges: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, f in enumerate(faces):
        for a, b in zip(f, f[1:] + f[:1], strict=True):
            edges[edge_key(a, b)].append(i)
    candidates = []
    for edge, incident in edges.items():
        if len(incident) != 2:
            continue
        a, b = incident
        ids = set(faces[a]) | set(faces[b])
        if len(ids) != 4:
            continue
        center = [sum(points[i][k] for i in ids) / 4 for k in range(2)]
        if any(feature_value(f, points[i]) > 0.001 for f in features for i in ids):
            continue
        quad = sorted(
            ids,
            key=lambda i: math.atan2(
                points[i][1] - center[1], points[i][0] - center[0]
            ),
        )
        angles = []
        for j, vertex in enumerate(quad):
            left, right = (
                sub(points[quad[j - 1]], points[vertex]),
                sub(points[quad[(j + 1) % 4]], points[vertex]),
            )
            lengths = math.hypot(*left) * math.hypot(*right)
            if cross2(right, left) <= 1e-12 or lengths < 1e-15:
                break
            angles.append(abs(dot(left, right) / lengths))
        if len(angles) == 4 and max(angles) < 0.8:
            candidates.append((max(angles), edge, a, b, quad))
    used: set[int] = set()
    result = []
    for _, _, a, b, quad in sorted(candidates):
        if a not in used and b not in used:
            used.update((a, b))
            result.append(quad)
    result.extend(f for i, f in enumerate(faces) if i not in used)
    return result


@dataclass
class Geometry:
    vertices: list[Point]
    faces: list[Face]
    patch_ids: list[str]
    region_ids: list[str]
    regions: dict[str, str]
    thickness_min: float
    thickness_max: float
    junction_count: int


def tessellate(
    patch: Patch, openings: list[SurfaceOpening], triangulate: Triangulator
) -> tuple[list[Point], list[Face], list[str], list[str]]:
    points: list[Point] = []
    keys: list[str] = []
    edges: list[tuple[int, int]] = []
    loops: list[tuple[SurfaceOpening, list[Point], list[Point], list[int]]] = []
    index: dict[tuple[float, float], int] = {}

    def insert(p: Point, key: str = "") -> int:
        rounded = (round(p[0], 10), round(p[1], 10))
        if rounded not in index:
            index[rounded] = len(points)
            points.append(p)
            keys.append(key)
        return index[rounded]

    # Stable keys identify shared native vertices independently of tessellator ordering.
    for side, (name, reverse) in enumerate(patch.layout):
        curve = patch.curves[name].spec
        segment = []
        for i in range(curve.samples + 1):
            t = i / curve.samples
            uv = ([t, 0.0], [1.0, t], [1 - t, 1.0], [0.0, 1 - t])[side]
            sample = curve.samples - i if reverse else i
            key = f"curve:{name}:{sample}"
            if sample in (0, curve.samples):
                key = "node:" + (curve.start if sample == 0 else curve.end)
            segment.append(insert(uv, key))
        edges.extend(zip(segment, segment[1:], strict=False))
    for opening in openings:
        inner, outer = opening_loops(opening)
        for _, _, other_outer, _ in loops:
            if (
                inside(outer[0], other_outer)
                or inside(other_outer[0], outer)
                or any(
                    intersects(a, b, c, d)
                    for a, b in loop_edges(outer)
                    for c, d in loop_edges(other_outer)
                )
            ):
                fail(
                    "opening_invalid",
                    "Opening support rings overlap; separate or resize openings",
                )
        ids = [
            insert(p, f"opening:{opening.id}:outer:{i}") for i, p in enumerate(outer)
        ]
        edges.extend(zip(ids, ids[1:] + ids[:1], strict=True))
        loops.append((opening, inner, outer, ids))
    constraints = [(points[a], points[b]) for a, b in edges]
    grid = [
        [i / patch.spec.resolution, j / patch.spec.resolution]
        for i in range(1, patch.spec.resolution)
        for j in range(1, patch.spec.resolution)
    ]
    candidates = [p for f in patch.features for p in feature_samples(f)] + grid
    spacing = min(
        [0.3 / patch.spec.resolution]
        + [
            (min(f.radii) if f.kind == "bulge" else f.width) / (2 * f.refinement)
            for f in patch.features
        ]
    )
    buckets: dict[tuple[int, int], list[Point]] = defaultdict(list)

    def bucket(p: Point) -> tuple[int, int]:
        return math.floor(p[0] / spacing), math.floor(p[1] / spacing)

    for p in points:
        buckets[bucket(p)].append(p)
    if len(points) + len(candidates) > 16384:
        fail(
            "limit_exceeded", "Patch exceeds 16384 candidate samples; reduce refinement"
        )
    for p in candidates:
        if any(inside(p, outer) for _, _, outer, _ in loops):
            continue
        if any(segment_distance(p, a, b) < 1e-5 for a, b in constraints):
            continue
        x, y = bucket(p)
        if any(
            math.dist(p, q) < spacing
            for a in range(x - 1, x + 2)
            for b in range(y - 1, y + 2)
            for q in buckets[(a, b)]
        ):
            continue
        insert(p)
        buckets[(x, y)].append(p)
    if len(points) > 8192:
        fail(
            "limit_exceeded",
            "Patch tessellation exceeds 8192 input samples; reduce refinement",
        )
    coords, triangles, originals = triangulate(points, edges)
    output_keys = []
    input_to_output = {}
    for i, original in enumerate(originals):
        if not original:
            fail(
                "tessellation_failed",
                "Tessellator inserted an unexpected constraint intersection",
            )
        for j in original:
            input_to_output[j] = i
        named = {keys[j] for j in original if keys[j]}
        if len(named) > 1:
            fail(
                "tessellation_failed",
                "Distinct boundaries collapsed at tessellation precision",
            )
        output_keys.append(next(iter(named)) if named else f"patch:{patch.spec.id}:{i}")
    kept = []
    for face in triangles:
        center = [sum(coords[i][k] for i in face) / len(face) for k in range(2)]
        if any(inside(center, outer) for _, _, outer, _ in loops):
            continue
        if area([coords[i] for i in face]) < 0:
            face = list(reversed(face))
        kept.append(face)
    if patch.spec.id and len(kept) == 0:
        fail("tessellation_failed", "Patch has no remaining surface")
    regions = [patch.spec.id] * len(kept)
    if patch.quad_dominant:
        kept = pair_triangles(coords, kept, patch.features)
        regions = [patch.spec.id] * len(kept)
    for i, face in enumerate(kept):
        center = [sum(coords[j][k] for j in face) / len(face) for k in range(2)]
        for feature in patch.features:
            if feature_value(feature, center) > 0.1:
                regions[i] = feature.id
    for opening, inner, _, ids in loops:
        outer_ids = [input_to_output[i] for i in ids]
        inner_ids = []
        for i, uv in enumerate(inner):
            inner_ids.append(len(coords))
            coords.append(uv)
            output_keys.append(f"opening:{opening.id}:inner:{i}")
        for i in range(len(inner)):
            j = (i + 1) % len(inner)
            kept.append([outer_ids[i], outer_ids[j], inner_ids[j], inner_ids[i]])
            regions.append(opening.id)
    return coords, kept, output_keys, regions


def topology(
    vertices: list[Point], faces: list[Face], *, closed: bool
) -> dict[str, int]:
    edges: dict[tuple[int, int], list[int]] = defaultdict(list)
    neighbors: dict[int, set[int]] = defaultdict(set)
    links: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for face in faces:
        if len(set(face)) != len(face):
            fail("degenerate", "Generated face repeats vertices")
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            edges[edge_key(a, b)].append(1 if a < b else -1)
            neighbors[a].add(b)
            neighbors[b].add(a)
            left, right = face[i - 1], b
            links[a][left].add(right)
            links[a][right].add(left)
    bad = sum(len(e) > 2 or len(e) == 2 and sum(e) != 0 for e in edges.values())
    boundary = sum(len(e) == 1 for e in edges.values())
    if bad or closed and boundary:
        fail(
            "junction_invalid",
            "Generated network has non-manifold or inconsistent boundary connectivity",
        )
    for graph in links.values():
        todo = [next(iter(graph))]
        visited = set()
        while todo:
            v = todo.pop()
            if v not in visited:
                visited.add(v)
                todo.extend(graph[v] - visited)
        if len(visited) != len(graph) or any(len(n) > 2 for n in graph.values()):
            fail(
                "junction_invalid",
                "Junction has a disconnected or branching vertex fan",
            )
        if closed and any(len(n) != 2 for n in graph.values()):
            fail("junction_invalid", "Closed shell has a non-manifold vertex")
    unseen = set(neighbors)
    components = 0
    while unseen:
        components += 1
        todo = [unseen.pop()]
        while todo:
            v = todo.pop()
            new = neighbors[v] & unseen
            unseen -= new
            todo.extend(new)
    if components != 1 or len(neighbors) != len(vertices):
        fail("junction_invalid", "Surface contains disconnected or unused geometry")
    return {
        "edge_count": len(edges),
        "boundary_edges": boundary,
        "non_manifold_edges": bad,
        "components": components,
        "max_vertex_valence": max(map(len, neighbors.values())),
    }


def generate(spec: SurfaceSpec, triangulate: Triangulator) -> Geometry:
    layouts, signs, uses = boundary_layout(spec)
    nodes = {n.id: n.point for n in spec.nodes}
    curves = {c.id: Curve(c, nodes) for c in spec.curves}
    vertices: list[Point] = []
    faces: list[Face] = []
    region_ids = []
    patch_ids = []
    keys: dict[str, int] = {}
    thicknesses: list[list[float]] = []
    vertex_normals: list[list[Point]] = []
    reverse_keys = []
    regions = {p.id: "patch" for p in spec.patches}
    regions.update({o.id: "opening" for o in spec.openings})
    regions.update({f.id: "feature" for f in spec.features})
    regions.update(
        {c: "junction" if len(p) == 2 else "boundary" for c, p in uses.items()}
    )
    for p in spec.patches:
        patch = Patch(
            p,
            layouts[p.id],
            curves,
            [f for f in spec.features if f.patch == p.id],
            signs[p.id],
        )
        patch.quad_dominant = spec.topology == "quad_dominant"
        coords, local_faces, local_keys, local_regions = tessellate(
            patch, [o for o in spec.openings if o.patch == p.id], triangulate
        )
        mapping = []
        for uv, key in zip(coords, local_keys, strict=True):
            point = patch.at(uv)
            if key not in keys:
                keys[key] = len(vertices)
                vertices.append(point)
                thicknesses.append([])
                vertex_normals.append([])
                reverse_keys.append(key)
            index = keys[key]
            if math.dist(vertices[index], point) > 1e-5 * max(1.0, math.hypot(*point)):
                fail("junction_invalid", "Shared boundary positions disagree")
            mapping.append(index)
            u, v = uv
            epsilon = 1e-4
            du = sub(
                patch.at([min(1, u + epsilon), v]), patch.at([max(0, u - epsilon), v])
            )
            dv = sub(
                patch.at([u, min(1, v + epsilon)]), patch.at([u, max(0, v - epsilon)])
            )
            vertex_normals[index].append(
                mul(
                    unit(
                        cross(du, dv),
                        details={
                            "patch_id": p.id,
                            "curve_ids": list(p.boundaries),
                            "uv": list(uv),
                            "tangent_lengths": [math.hypot(*du), math.hypot(*dv)],
                        },
                    ),
                    signs[p.id],
                )
            )
            t0, t1, t2, t3 = p.thickness or [spec.thickness] * 4
            thicknesses[index].append(
                (t0 * (1 - u) + t1 * u) * (1 - v) + (t3 * (1 - u) + t2 * u) * v
            )
        for face, region in zip(local_faces, local_regions, strict=True):
            mapped = [mapping[i] for i in face]
            if signs[p.id] < 0:
                mapped.reverse()
            faces.append(mapped)
            region_ids.append(region)
            patch_ids.append(p.id)
    if len(vertices) > 32768 or len(faces) > 65536:
        fail("limit_exceeded", "Midsurface exceeds 32768 vertices/65536 faces")
    junction_edges = {}
    for name, curve_users in uses.items():
        if len(curve_users) != 2:
            continue
        curve = curves[name].spec
        ordered = [
            keys["node:" + curve.start],
            *[keys[f"curve:{name}:{i}"] for i in range(1, curve.samples)],
            keys["node:" + curve.end],
        ]
        for a, b in zip(ordered, ordered[1:], strict=False):
            junction_edges[edge_key(a, b)] = name
    for i, face in enumerate(faces):
        for a, b in zip(face, face[1:] + face[:1], strict=True):
            if (edge := edge_key(a, b)) in junction_edges:
                region_ids[i] = junction_edges[edge]
                break
    topology(vertices, faces, closed=False)
    edges: dict[tuple[int, int], list[tuple[int, int, str]]] = defaultdict(list)
    for face, patch_id in zip(faces, patch_ids, strict=True):
        n = [0.0] * 3
        for i in range(1, len(face) - 1):
            triangle = cross(
                sub(vertices[face[i]], vertices[face[0]]),
                sub(vertices[face[i + 1]], vertices[face[0]]),
            )
            if math.hypot(*triangle) < 1e-12:
                fail("degenerate", "Contour or feature folds/collapses a face")
            n = add(n, triangle)
        for a, b in zip(face, face[1:] + face[:1], strict=True):
            edges[edge_key(a, b)].append((a, b, patch_id))
    thickness = [sum(t) / len(t) for t in thicknesses]
    unit_normals = [
        unit([sum(n[k] for n in incident) for k in range(3)])
        for incident in vertex_normals
    ]
    count = len(vertices)
    result_vertices = [
        add(p, mul(n, t / 2))
        for p, n, t in zip(vertices, unit_normals, thickness, strict=True)
    ] + [
        sub(p, mul(n, t / 2))
        for p, n, t in zip(vertices, unit_normals, thickness, strict=True)
    ]
    for face_index, face in enumerate(faces):
        for i in range(1, len(face) - 1):
            a, b, c = face[0], face[i], face[i + 1]
            normal = cross(sub(vertices[b], vertices[a]), sub(vertices[c], vertices[a]))
            for offset in (0, count):
                shifted = cross(
                    sub(result_vertices[b + offset], result_vertices[a + offset]),
                    sub(result_vertices[c + offset], result_vertices[a + offset]),
                )
                if dot(normal, shifted) <= 0:
                    fail(
                        "degenerate",
                        "Thickness inverts a face; reduce thickness or curvature",
                        {
                            "patch_id": patch_ids[face_index],
                            "degeneracy": "offset_orientation_inversion",
                            "orientation_dot": dot(normal, shifted),
                            "required_orientation_dot": "> 0",
                            "positions": [
                                cast(JsonValue, vertices[j]) for j in (a, b, c)
                            ],
                            "boundary_curves": [
                                c
                                for p in spec.patches
                                if p.id == patch_ids[face_index]
                                for c in p.boundaries
                            ],
                            "face_index": face_index,
                            "triangle_vertices": [a, b, c],
                            "shell_side": "positive" if offset == 0 else "negative",
                            "thicknesses": [thickness[j] for j in (a, b, c)],
                            "edge_lengths": [
                                math.dist(vertices[j], vertices[k])
                                for j, k in ((a, b), (b, c), (c, a))
                            ],
                            "length_units": "scene_units",
                        },
                    )
    result_faces = faces + [[i + count for i in reversed(f)] for f in faces]
    result_regions = region_ids * 2
    result_patches = patch_ids * 2
    for incident in edges.values():
        if len(incident) == 1:
            a, b, patch_id = incident[0]
            result_faces.append([b, a, a + count, b + count])
            key = (
                reverse_keys[a]
                if not reverse_keys[a].startswith("node:")
                else reverse_keys[b]
            )
            region = (
                key.split(":")[1]
                if key.startswith(("opening:", "curve:"))
                else patch_id
            )
            result_regions.append(region)
            result_patches.append(patch_id)
    topology(result_vertices, result_faces, closed=True)
    if len(result_faces) > 131072:
        fail("limit_exceeded", "Shell exceeds 131072 generated faces")
    return Geometry(
        result_vertices,
        result_faces,
        result_patches,
        result_regions,
        regions,
        min(thickness),
        max(thickness),
        sum(len(v) == 2 for v in uses.values()),
    )
