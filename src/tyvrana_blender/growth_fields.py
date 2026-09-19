"""Bounded rest-surface field interpolation and deterministic ordered roots."""

import bisect
import json
import math
from typing import Any

from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from .errors import OperationError
from .growth_domain_models import GrowthField, GrowthRow
from .growth_models import GrowthRegion


def fail(message: str) -> Any:
    raise OperationError("growth_domain_invalid", message)


def slot_key(region: str, row: str, mirrored: bool) -> str:
    return json.dumps([region, row, mirrored], separators=(",", ":"))


def region_key(region: str) -> str:
    return json.dumps([region], separators=(",", ":"))


def location(sf: Any, uv: Any, faces: set[int]) -> tuple[Any, Any, int, Any, Any]:
    point, normal = sf.sample(uv)
    found = sf.uv_bvh.find_nearest_range(uv, 1e-6)
    indices = [i for _, _, i, _ in found if sf.triangles[i].polygon_index in faces]
    if not indices:
        fail("Root/field coordinate lies outside its declared face domain")
    index = min(indices)
    tri = sf.triangles[index]
    a, b, c = [sf.uvs[i] for i in tri.loops]
    p, q, r = [sf.positions[i] for i in tri.vertices]
    ab, ac = b - a, c - a
    determinant = ab.x * ac.y - ab.y * ac.x
    if abs(determinant) < 1e-12:
        fail("Field/root domain contains a degenerate UV triangle")
    du = ((q - p) * ac.y - (r - p) * ab.y) / determinant
    dv = ((r - p) * ab.x - (q - p) * ac.x) / determinant
    return point, normal, index, du, dv


def interpolate(field: GrowthField, uv: Any) -> tuple[Any, float]:
    weighted = []
    for control in field.controls:
        distance = sum((a - b) ** 2 for a, b in zip(control.uv, uv[:2], strict=True))
        vector = Vector(control.direction).normalized()
        if distance < 1e-14:
            return vector, control.length_scale
        weighted.append((1 / distance, vector, control.length_scale))
    total = sum(w for w, _, _ in weighted)
    direction = sum((v * (w / total) for w, v, _ in weighted), Vector((0.0, 0.0)))
    if direction.length < 1e-6:
        fail("Interpolated field directions cancel; add a consistent control")
    return direction.normalized(), sum(w * scale / total for w, _, scale in weighted)


def frame(
    sf: Any,
    uv: Any,
    faces: set[int],
    region: GrowthRegion,
    row: GrowthRow | None = None,
    mirrored: bool = False,
) -> tuple[Any, Any, int, Any, float]:
    point, normal, index, du, dv = location(sf, uv, faces)
    scale = 1.0
    mirror_axis = (0 if row.mirror == "u" else 1) if row and mirrored else None
    if region.field:
        source = uv.copy()
        if mirror_axis is not None:
            assert row is not None
            source[mirror_axis] = 2 * row.mirror_center - source[mirror_axis]
        direction, scale = interpolate(region.field, source)
        if mirror_axis is not None:
            direction[mirror_axis] *= -1
        flow = du * direction.x + dv * direction.y
    else:
        flow = Vector(region.flow)
        if mirror_axis is not None:
            basis = Matrix((du, dv, normal)).transposed()
            if abs(basis.determinant()) < 1e-12:
                fail("Surface UV frame is singular")
            local = basis.inverted() @ flow
            local[mirror_axis] *= -1
            flow = basis @ local
    flow -= normal * flow.dot(normal)
    if flow.length < 1e-7:
        fail("Field flow is singular on this surface; revise its direction")
    return point, normal, index, flow.normalized(), scale


def row_uvs(sf: Any, faces: set[int], row: GrowthRow, mirrored: bool) -> list[Any]:
    # Fixed bounded quadrature gives repeatable surface-local arc spacing. Roots
    # themselves are independently checked against the exact face selection.
    path = [Vector((*uv, 0)) for uv in row.path]
    if mirrored:
        axis = 0 if row.mirror == "u" else 1
        for uv in path:
            uv[axis] = 2 * row.mirror_center - uv[axis]
    samples = [
        a.lerp(b, i / 16)
        for a, b in zip(path[:-1], path[1:], strict=True)
        for i in range(16)
    ] + [path[-1]]
    positions = [location(sf, uv, faces)[0] for uv in samples]
    lengths = [0.0]
    for a, b in zip(positions[:-1], positions[1:], strict=True):
        lengths.append(lengths[-1] + (b - a).length)
    total = lengths[-1]
    if total < 1e-7:
        fail("Ordered row has no surface length")
    spacing = row.spacing or 1.0
    count = row.count or (math.floor(total / spacing + 1e-5) + 1)
    if not 2 <= count <= 2048:
        fail("Row spacing must produce2..2048 roots per side")
    distances = (
        [total * i / (count - 1) for i in range(count)]
        if row.count
        else [min(total, i * spacing) for i in range(count)]
    )
    result = []
    for distance in distances:
        i = min(len(samples) - 2, max(0, bisect.bisect_right(lengths, distance) - 1))
        span = lengths[i + 1] - lengths[i]
        if span < 1e-12:
            fail("Ordered row contains a collapsed surface segment")
        result.append(samples[i].lerp(samples[i + 1], (distance - lengths[i]) / span))
    return result


def keys(regions: list[GrowthRegion]) -> list[str]:
    return [
        slot_key(region.name, row.name, mirror)
        for region in regions
        for row in region.rows
        for mirror in ([False, True] if row.mirror else [False])
    ]
