"""Compact native-space distortion and contact probes; no acceptance heuristic."""

import math
from collections import Counter
from typing import Any

from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from .deformation_models import (
    BoneDeformation,
    ContactProbe,
    ContactSummary,
    DeformationQA,
    EdgeDistortion,
    RatioDistribution,
)
from .operations import OperationError


def percentile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] * (1 - (position - lo)) + values[hi] * (position - lo)


def distribution(values: list[float]) -> RatioDistribution:
    values = sorted(values)
    return RatioDistribution(
        count=len(values),
        minimum=values[0] if values else None,
        maximum=values[-1] if values else None,
        **{
            name: percentile(values, f) if values else None
            for name, f in [("p05", 0.05), ("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
        },
    )


def volume(points: Any, triangles: Any) -> float | None:
    if not points or not triangles:
        return None
    counts: Counter[tuple[int, int]] = Counter()
    winding: Counter[tuple[int, int]] = Counter()
    for tri in triangles:
        for i, j in zip(tri, (*tri[1:], tri[0]), strict=True):
            edge = min(i, j), max(i, j)
            counts[edge] += 1
            winding[edge] += 1 if i < j else -1
    if any(count != 2 or winding[e] for e, count in counts.items()):
        return None
    origin = points[triangles[0][0]]
    result = (
        abs(
            sum(
                (points[i] - origin).dot((points[j] - origin).cross(points[k] - origin))
                for i, j, k in triangles
            )
        )
        / 6
    )
    return float(result)


def compare(rest: Any, posed: Any, limit: int) -> DeformationQA:
    a, edges, triangles, weights = rest
    b = posed[0]
    ratios = []
    for i, j in edges:
        length = (a[i] - a[j]).length
        if length > 1e-10:
            ratios.append((i, j, (b[i] - b[j]).length / length))
    areas = []
    for i, j, k in triangles:
        area = (a[j] - a[i]).cross(a[k] - a[i]).length
        if area > 1e-16:
            areas.append((i, j, k, (b[j] - b[i]).cross(b[k] - b[i]).length / area))
    angles = []
    for i, j, k, _ in areas:
        for origin, x, y in ((i, j, k), (j, k, i), (k, i, j)):
            ra, rb = a[x] - a[origin], a[y] - a[origin]
            pa, pb = b[x] - b[origin], b[y] - b[origin]
            if min(pa.length, pb.length) > 1e-10:
                angles.append(
                    abs(
                        math.atan2(pa.cross(pb).length, pa.dot(pb))
                        - math.atan2(ra.cross(rb).length, ra.dot(rb))
                    )
                )
    extremes = sorted(
        ratios, key=lambda e: (-abs(math.log(max(1e-12, e[2]))), e[0], e[1])
    )[:limit]
    samples = []
    for i, j, ratio in extremes:
        samples.append(
            EdgeDistortion(
                vertices=[i, j],
                rest_world=[list(a[i]), list(a[j])],
                posed_world=[list(b[i]), list(b[j])],
                rest_length=(a[i] - a[j]).length,
                posed_length=(b[i] - b[j]).length,
                ratio=ratio,
                endpoint_bones=[
                    max(weights[k], key=lambda n: (weights[k][n], n))
                    if weights and weights[k]
                    else None
                    for k in (i, j)
                ],
            )
        )
    regions = []
    for name in sorted({n for row in weights for n in row}):
        chosen = {i for i, row in enumerate(weights) if row.get(name, 0) >= 0.5}
        distances = sorted((b[i] - a[i]).length for i in chosen)
        regions.append(
            BoneDeformation(
                bone=name,
                vertex_count=len(chosen),
                displacement_max=max(distances, default=0),
                displacement_p95=percentile(distances, 0.95) if distances else 0,
                edge_ratios=distribution(
                    [r for i, j, r in ratios if i in chosen and j in chosen]
                ),
                triangle_area_ratios=distribution(
                    [
                        r
                        for i, j, k, r in areas
                        if i in chosen and j in chosen and k in chosen
                    ]
                ),
            )
        )
    vr, vp = volume(a, triangles), volume(b, triangles)
    return DeformationQA(
        triangle_angle_change_radians=distribution(angles),
        collapsed_triangle_count=sum(row[3] < 0.01 for row in areas),
        degenerate_rest_triangle_count=len(triangles) - len(areas),
        edge_ratios=distribution([r for _, _, r in ratios]),
        triangle_area_ratios=distribution([r for _, _, _, r in areas]),
        worst_edges=samples,
        bone_regions=regions,
        rest_volume=vr,
        posed_volume=vp,
        volume_ratio=vp / vr
        if vr is not None and vr > 1e-16 and vp is not None
        else None,
    )


def contact(
    probe: ContactProbe,
    obj: Any,
    rest: Any,
    posed: Any,
    target_rest: Any,
    target_pose: Any,
) -> ContactSummary:
    inverse = obj.matrix_world.inverted()
    chosen = []
    for i, point in enumerate(rest[0]):
        local = inverse @ point
        if all(
            a <= x <= b
            for a, x, b in zip(probe.rest_min, local, probe.rest_max, strict=True)
        ):
            chosen.append(i)
    if not chosen or len(chosen) > 100_000:
        raise OperationError(
            "rig_context_invalid",
            "Contact box must select 1..100000 evaluated rest vertices",
        )

    def distances(source: Any, target: Any) -> tuple[list[float], list[float]]:
        if not target[0] or not target[2]:
            raise OperationError(
                "rig_context_invalid", "Contact target has no evaluated surface"
            )
        tree = BVHTree.FromPolygons(target[0], target[2], all_triangles=True)
        lengths, signed = [], []
        for i in chosen:
            near, normal, _, distance = tree.find_nearest(source[0][i])
            if near is None or normal is None or distance is None:
                raise OperationError(
                    "rig_context_invalid",
                    "Contact target nearest-surface evaluation failed",
                )
            lengths.append(float(distance))
            signed.append((source[0][i] - near).dot(normal))
        return lengths, signed

    a, sa = distances(rest, target_rest)
    b, sb = distances(posed, target_pose)
    return ContactSummary(
        source_object=obj.name,
        target_object=probe.target_object,
        vertex_count=len(chosen),
        rest_distance=distribution(a),
        posed_distance=distribution(b),
        separation_increase_max=max(y - x for x, y in zip(a, b, strict=True)),
        rest_outside_max=max(0.0, max(sa)),
        posed_outside_max=max(0.0, max(sb)),
        rest_penetration_max=max(0.0, -min(sa)),
        posed_penetration_max=max(0.0, -min(sb)),
    )
