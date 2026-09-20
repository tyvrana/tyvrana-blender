"""Bounded, host-independent UV island and triangulated distortion analysis."""

import colorsys
import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from .raster import png_rgb
from .uv_models import UVLayoutResult, UVMetricSummary

type Point = tuple[float, float]
type Point3 = tuple[float, float, float]
MAX_FACES = 128000
MAX_TRIANGLES = 128000
MAX_PAIR_CHECKS = 2_000_000
EPS = 1e-12
UV_CONTINUITY_TOLERANCE = 1e-7


@dataclass
class Face:
    index: int
    vertices: list[int]
    uv: list[Point]
    points: list[Point3]
    triangles: list[tuple[int, int, int]]


@dataclass
class Surface:
    name: str
    faces: list[Face]


@dataclass
class Triangle:
    object_name: str
    face_index: int
    island: int
    uv: list[Point]
    area: float


def cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def area(points: list[Point]) -> float:
    return (
        sum(
            cross(points[0], points[i], points[i + 1])
            for i in range(1, len(points) - 1)
        )
        / 2
        if len(points) > 2
        else 0.0
    )


def stats(values: list[float]) -> UVMetricSummary:
    ordered = sorted(values)
    if not ordered:
        return UVMetricSummary(
            minimum=None, p05=None, median=None, p95=None, maximum=None
        )

    def percentile(q: float) -> float:
        at = (len(ordered) - 1) * q
        low = int(at)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (at - low)

    return UVMetricSummary(
        minimum=ordered[0],
        p05=percentile(0.05),
        median=percentile(0.5),
        p95=percentile(0.95),
        maximum=ordered[-1],
    )


def islands(surface: Surface) -> list[list[Face]]:
    """Connect faces only across the same mesh edge with continuous endpoint UVs."""
    links: dict[tuple[int, int], list[tuple[int, Point, Point]]] = defaultdict(list)
    adjacent: dict[int, set[int]] = defaultdict(set)
    by_index = {face.index: face for face in surface.faces}
    for face in surface.faces:
        for i, v in enumerate(face.vertices):
            j = (i + 1) % len(face.vertices)
            w = face.vertices[j]
            a, b = (face.uv[i], face.uv[j]) if v < w else (face.uv[j], face.uv[i])
            key = (min(v, w), max(v, w))
            for other, c, d in links[key]:
                if (
                    math.dist(a, c) <= UV_CONTINUITY_TOLERANCE
                    and math.dist(b, d) <= UV_CONTINUITY_TOLERANCE
                ):
                    adjacent[face.index].add(other)
                    adjacent[other].add(face.index)
            links[key].append((face.index, a, b))
    remaining = set(by_index)
    result = []
    while remaining:
        start = min(remaining)
        pending = [start]
        group = []
        remaining.remove(start)
        while pending:
            index = pending.pop()
            group.append(by_index[index])
            for other in adjacent[index] & remaining:
                remaining.remove(other)
                pending.append(other)
        result.append(sorted(group, key=lambda f: f.index))
    return result


def triangle_metric(
    points: list[Point3], uv: list[Point]
) -> tuple[float, float, float | None, float]:
    """Return world area, signed UV area, Jacobian singular-value ratio, angle error."""
    a, b, c = points
    ab = math.dist(a, b)
    ac = math.dist(a, c)
    bc = math.dist(b, c)
    signed = area(uv)
    if ab <= 1e-20:
        return 0, signed, None, 180
    x = (ab * ab + ac * ac - bc * bc) / (2 * ab)
    y = math.sqrt(max(0, ac * ac - x * x))
    world = ab * y / 2
    if world <= 1e-20 or abs(signed) <= EPS:
        return world, signed, None, 180
    du = (uv[1][0] - uv[0][0], uv[1][1] - uv[0][1])
    dv = (uv[2][0] - uv[0][0], uv[2][1] - uv[0][1])
    j00, j10 = du[0] / ab, du[1] / ab
    j01, j11 = (dv[0] - j00 * x) / y, (dv[1] - j10 * x) / y
    aa = j00 * j00 + j10 * j10
    bb = j01 * j01 + j11 * j11
    off = j00 * j01 + j10 * j11
    maximum = (aa + bb + math.hypot(aa - bb, 2 * off)) / 2
    determinant = (j00 * j11 - j01 * j10) ** 2
    minimum = determinant / maximum if maximum else 0
    anisotropy = math.sqrt(maximum / minimum) if minimum > 0 else None

    def angles(lengths: list[float]) -> list[float]:
        out = []
        for i, opposite in enumerate(lengths):
            q, r = lengths[(i + 1) % 3], lengths[(i + 2) % 3]
            out.append(
                math.degrees(
                    math.acos(
                        max(
                            -1,
                            min(1, (q * q + r * r - opposite * opposite) / (2 * q * r)),
                        )
                    )
                )
                if q * r > 1e-30
                else 180
            )
        return out

    error = max(
        abs(p - q)
        for p, q in zip(
            angles([ab, ac, bc]),
            angles(
                [
                    math.dist(uv[0], uv[1]),
                    math.dist(uv[0], uv[2]),
                    math.dist(uv[1], uv[2]),
                ]
            ),
            strict=True,
        )
    )
    return world, signed, anisotropy, error


def intersection_area(first: list[Point], second: list[Point]) -> float:
    polygon = first[:] if area(first) >= 0 else list(reversed(first))
    clip = second if area(second) >= 0 else list(reversed(second))
    for i, a in enumerate(clip):
        b = clip[(i + 1) % 3]
        output = []
        for j, p in enumerate(polygon):
            q = polygon[(j + 1) % len(polygon)]
            dp, dq = cross(a, b, p), cross(a, b, q)
            if dp >= 0:
                output.append(p)
            if (dp >= 0) != (dq >= 0):
                t = dp / (dp - dq)
                output.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
        polygon = output
        if not polygon:
            return 0.0
    return abs(area(polygon))


def shared_edge_roundoff(a: Triangle, b: Triangle, overlap: float) -> bool:
    """Ignore only a sub-precision strip along an already continuous UV edge."""
    if a.object_name != b.object_name or a.island != b.island:
        return False
    for i in range(3):
        p, q = a.uv[i], a.uv[(i + 1) % 3]
        for j in range(3):
            r, s = b.uv[j], b.uv[(j + 1) % 3]
            if (
                math.dist(p, s) <= UV_CONTINUITY_TOLERANCE
                and math.dist(q, r) <= UV_CONTINUITY_TOLERANCE
                and overlap <= math.dist(p, q) * UV_CONTINUITY_TOLERANCE
            ):
                return True
    return False


def overlaps(triangles: list[Triangle]) -> dict[tuple[str, int, str, int], float]:
    bounds = [
        (
            min(p[0] for p in t.uv),
            max(p[0] for p in t.uv),
            min(p[1] for p in t.uv),
            max(p[1] for p in t.uv),
        )
        for t in triangles
    ]
    active: list[int] = []
    found: dict[tuple[str, int, str, int], float] = defaultdict(float)
    checks = 0
    for i in sorted(range(len(triangles)), key=lambda k: bounds[k][0]):
        a = triangles[i]
        left, right, bottom, top = bounds[i]
        active = [j for j in active if bounds[j][1] > left + 1e-12]
        for j in active:
            b = triangles[j]
            if (a.object_name, a.face_index) == (b.object_name, b.face_index):
                continue
            if bounds[j][3] <= bottom + 1e-12 or bounds[j][2] >= top - 1e-12:
                continue
            checks += 1
            if checks > MAX_PAIR_CHECKS:
                raise ValueError("UV overlap analysis exceeds bounded pair capacity")
            overlap = intersection_area(a.uv, b.uv)
            if overlap > EPS and not shared_edge_roundoff(a, b, overlap):
                first, second = sorted(
                    [(a.object_name, a.face_index), (b.object_name, b.face_index)]
                )
                found[(*first, *second)] += overlap
        active.append(i)
    return dict(found)


def point_segment(p: Point, a: Point, b: Point) -> float:
    d = (b[0] - a[0], b[1] - a[1])
    den = d[0] * d[0] + d[1] * d[1]
    t = (
        max(0, min(1, ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / den))
        if den
        else 0
    )
    return math.hypot(p[0] - a[0] - t * d[0], p[1] - a[1] - t * d[1])


def boundary(group: list[Face]) -> list[tuple[Point, Point]]:
    edges: dict[tuple[Point, Point], list[tuple[Point, Point]]] = defaultdict(list)
    for face in group:
        for i, a in enumerate(face.uv):
            b = face.uv[(i + 1) % len(face.uv)]
            key = tuple(
                sorted(
                    [(round(a[0], 7), round(a[1], 7)), (round(b[0], 7), round(b[1], 7))]
                )
            )
            edges[(key[0], key[1])].append((a, b))
    return [items[0] for items in edges.values() if len(items) == 1]


def island_gap(groups: list[list[Face]]) -> float | None:
    if len(groups) < 2:
        return None
    outlines = [boundary(group) for group in groups]
    best = float("inf")
    checks = 0
    for first, second in combinations(outlines, 2):
        for a, b in first:
            for c, d in second:
                dx = max(
                    0,
                    min(a[0], b[0]) - max(c[0], d[0]),
                    min(c[0], d[0]) - max(a[0], b[0]),
                )
                dy = max(
                    0,
                    min(a[1], b[1]) - max(c[1], d[1]),
                    min(c[1], d[1]) - max(a[1], b[1]),
                )
                if math.hypot(dx, dy) >= best:
                    continue
                checks += 1
                if checks > MAX_PAIR_CHECKS:
                    raise ValueError("UV margin analysis exceeds bounded pair capacity")
                if (
                    cross(a, b, c) * cross(a, b, d) < 0
                    and cross(c, d, a) * cross(c, d, b) < 0
                ):
                    return 0.0
                best = min(
                    best,
                    point_segment(a, c, d),
                    point_segment(b, c, d),
                    point_segment(c, a, b),
                    point_segment(d, a, b),
                )
    return best if math.isfinite(best) else None


def analyze(
    surfaces: list[Surface],
    resolution: int,
    evaluated: bool,
    *,
    island_offset: int = 0,
    island_limit: int = 16,
) -> tuple[UVLayoutResult, list[Triangle]]:
    if sum(len(s.faces) for s in surfaces) > MAX_FACES:
        raise ValueError(f"UV inspection exceeds {MAX_FACES} faces")
    reports: list[dict[str, Any]] = []
    triangles = []
    all_groups = []
    issues: list[dict[str, Any]] = []
    all_density = []
    all_anisotropy = []
    all_angles = []
    total_area = total_world = 0.0
    flipped = degenerate = outside = 0
    all_points = []
    for surface in surfaces:
        for group in islands(surface):
            all_groups.append(group)
            points = [uv for face in group for uv in face.uv]
            if any(not math.isfinite(c) or abs(c) > 100 for p in points for c in p):
                raise ValueError(
                    "UV coordinates must be finite and within bounded inspection range"
                )
            all_points.extend(points)
            group_density = []
            group_anisotropy = []
            group_angles = []
            group_area = group_world = 0.0
            group_flipped = group_degenerate = 0
            tiles = set()
            for face in group:
                face_area = face_world = 0.0
                face_ratios = []
                face_angles = []
                face_flip = face_bad = False
                outside += any(not -1e-6 <= c <= 1 + 1e-6 for p in face.uv for c in p)
                center = (
                    sum(p[0] for p in face.uv) / len(face.uv),
                    sum(p[1] for p in face.uv) / len(face.uv),
                )
                tiles.add(1001 + math.floor(center[0]) + 10 * math.floor(center[1]))
                for indices in face.triangles:
                    uv = [face.uv[i] for i in indices]
                    world, signed, ratio, angle = triangle_metric(
                        [face.points[i] for i in indices], uv
                    )
                    face_area += abs(signed)
                    face_world += world
                    face_flip |= signed < -EPS
                    face_bad |= abs(signed) <= EPS or world <= 1e-20
                    if ratio is not None:
                        face_ratios.append(ratio)
                    face_angles.append(angle)
                    triangles.append(
                        Triangle(
                            surface.name,
                            face.index,
                            len(all_groups) - 1,
                            uv,
                            abs(signed),
                        )
                    )
                density = (
                    resolution * math.sqrt(face_area / face_world) if face_world else 0
                )
                group_density.append(density)
                group_anisotropy.extend(face_ratios)
                group_angles.extend(face_angles)
                group_area += face_area
                group_world += face_world
                group_flipped += face_flip
                group_degenerate += face_bad
                issues.append(
                    dict(
                        object_name=surface.name,
                        face_index=face.index,
                        anisotropy=max(face_ratios) if face_ratios else None,
                        angle_error_degrees=max(face_angles, default=180),
                        texel_density=density,
                    )
                )
            reports.append(
                dict(
                    object_name=surface.name,
                    island_id=group[0].index,
                    face_count=len(group),
                    face_indices=[f.index for f in group[:256]],
                    face_indices_truncated=len(group) > 256,
                    bounds_min=[min(p[i] for p in points) for i in range(2)],
                    bounds_max=[max(p[i] for p in points) for i in range(2)],
                    uv_area=group_area,
                    world_area=group_world,
                    texel_density=resolution * math.sqrt(group_area / group_world)
                    if group_world
                    else 0,
                    face_density=stats(group_density),
                    anisotropy=stats(group_anisotropy),
                    angle_error_degrees=stats(group_angles),
                    flipped_faces=group_flipped,
                    degenerate_faces=group_degenerate,
                    tiles=sorted(tiles),
                )
            )
            all_density.extend(group_density)
            all_anisotropy.extend(group_anisotropy)
            all_angles.extend(group_angles)
            total_area += group_area
            total_world += group_world
            flipped += group_flipped
            degenerate += group_degenerate
    if not all_points:
        raise ValueError("UV inspection requires mapped faces")
    if len(triangles) > MAX_TRIANGLES:
        raise ValueError(f"UV inspection exceeds {MAX_TRIANGLES} triangles")
    if len(reports) > 512:
        raise ValueError("UV inspection exceeds 512 islands")
    intersections = overlaps(triangles)
    gap = 0.0 if intersections else island_gap(all_groups)
    minimum = [min(p[i] for p in all_points) for i in range(2)]
    maximum = [max(p[i] for p in all_points) for i in range(2)]
    return UVLayoutResult.model_validate(
        dict(
            evaluated=evaluated,
            resolution=resolution,
            island_count=len(reports),
            islands=reports[island_offset : island_offset + island_limit],
            next_island_offset=island_offset + island_limit
            if island_offset + island_limit < len(reports)
            else None,
            islands_truncated=island_offset > 0 or island_limit < len(reports),
            face_count=len(issues),
            triangle_count=len(triangles),
            flipped_face_count=flipped,
            degenerate_face_count=degenerate,
            overlap_pair_count=len(intersections),
            overlaps=[
                dict(
                    first_object=k[0],
                    first_face=k[1],
                    second_object=k[2],
                    second_face=k[3],
                    uv_area=value,
                )
                for k, value in list(sorted(intersections.items()))[:64]
            ],
            overlaps_truncated=len(intersections) > 64,
            worst_faces=sorted(
                issues, key=lambda f: f["anisotropy"] or float("inf"), reverse=True
            )[:32],
            bounds_min=minimum,
            bounds_max=maximum,
            tiles=sorted({tile for report in reports for tile in report["tiles"]}),
            out_of_unit_face_count=outside,
            uv_area=total_area,
            world_area=total_world,
            minimum_island_gap_pixels=gap * resolution if gap is not None else None,
            minimum_tile_border_pixels=min(*minimum, 1 - maximum[0], 1 - maximum[1])
            * resolution,
            density=stats(all_density),
            anisotropy=stats(all_anisotropy),
            angle_error_degrees=stats(all_angles),
            limitations=[
                "World area uses object transforms and native polygon triangulation. "
                "Density is pixels per world unit; metric percentiles are unweighted "
                "face/triangle samples.",
                "Overlap means positive triangle intersection area above 1e-12 UV "
                "units squared, excluding 1e-7-coordinate rounding strips on "
                "continuous shared edges within one object's UV island. Touching "
                "edges do not overlap. Intentional stacking "
                "must be classified by the client.",
                "Island identifiers and face references belong to this mesh snapshot. "
                "Evaluated references cannot select authored faces.",
                "Tile identifiers describe face centroids; bounds and out-of-unit "
                "counts also expose tile crossings. Border measurement is relative to "
                "tile 1001.",
                "Checks describe the selected authored mesh or current viewport "
                "evaluation, not future subdivision, deformation, bake rays or export "
                "tangent conventions.",
            ],
        )
    ), triangles


def png_layout(
    triangles: list[Triangle],
    size: int,
    bounds_min: list[float],
    bounds_max: list[float],
) -> bytes:
    """Rasterize diagnostic UVs without host image/editor state or dependencies."""
    low = [min(0, math.floor(v)) for v in bounds_min]
    high = [max(1, math.ceil(v)) for v in bounds_max]
    span = max(high[i] - low[i] for i in range(2))
    pixels = bytearray([25, 28, 34]) * (size * size)

    def point(uv: Point) -> Point:
        return (
            8 + (uv[0] - low[0]) / span * (size - 16),
            size - 8 - (uv[1] - low[1]) / span * (size - 16),
        )

    def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < size and 0 <= y < size:
            off = (y * size + x) * 3
            pixels[off : off + 3] = bytes(color)

    def line(a: Point, b: Point, color: tuple[int, int, int]) -> None:
        count = max(1, int(max(abs(a[0] - b[0]), abs(a[1] - b[1]))) + 1)
        for step in range(count + 1):
            t = step / count
            set_pixel(
                round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), color
            )

    for i in range(int(span) * 10 + 1):
        v = i / 10
        line(
            point((low[0] + v, low[1])),
            point((low[0] + v, low[1] + span)),
            (60, 64, 72),
        )
        line(
            point((low[0], low[1] + v)),
            point((low[0] + span, low[1] + v)),
            (60, 64, 72),
        )
    for tri in triangles:
        pts = [point(p) for p in tri.uv]
        rgb = colorsys.hsv_to_rgb((tri.island * 0.61803398875) % 1, 0.45, 0.72)
        color = tuple(int(c * 255) for c in rgb)
        for y in range(
            max(0, math.floor(min(p[1] for p in pts))),
            min(size, math.ceil(max(p[1] for p in pts)) + 1),
        ):
            cuts = []
            for i, a in enumerate(pts):
                b = pts[(i + 1) % 3]
                if min(a[1], b[1]) <= y + 0.5 < max(a[1], b[1]):
                    t = (y + 0.5 - a[1]) / (b[1] - a[1])
                    cuts.append(a[0] + t * (b[0] - a[0]))
            if len(cuts) == 2:
                for x in range(
                    max(0, math.ceil(min(cuts))), min(size, math.ceil(max(cuts)))
                ):
                    set_pixel(x, y, (color[0], color[1], color[2]))
        for i, a in enumerate(pts):
            line(a, pts[(i + 1) % 3], (205, 216, 224))

    return png_rgb(size, size, pixels)
