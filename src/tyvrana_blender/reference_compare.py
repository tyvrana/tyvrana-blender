"""Calibrated silhouette/section disagreement against evaluated native geometry."""

import math
import time
from collections import Counter
from typing import Any

import numpy as np  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.kdtree import KDTree  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from . import construction, modifiers, references, retopo_geometry
from .artifacts import ArtifactSpool
from .errors import OperationError
from .form_models import (
    ReferenceBoundaryError,
    ReferenceCompareArguments,
    ReferenceCompareResult,
    ReferenceShapeResult,
)
from .form_reference import Mask, compile_mask
from .raster import png_rgb


def boundary(mask: Any, spacing: tuple[float, float]) -> tuple[Any, Any]:
    edge = np.zeros(mask.shape, dtype=bool)
    edge[1:, :] |= mask[1:, :] != mask[:-1, :]
    edge[:-1, :] |= mask[1:, :] != mask[:-1, :]
    edge[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    edge[:, :-1] |= mask[:, 1:] != mask[:, :-1]
    y, x = np.nonzero(edge)
    pixels = np.column_stack((x, y))
    return pixels, pixels * np.array(spacing)


def errors(
    reference: Mask, actual: Any, limit: int
) -> tuple[dict[str, float | None], list[ReferenceBoundaryError]]:
    a, points_a = boundary(reference.foreground, reference.spacing)
    b, points_b = boundary(actual, reference.spacing)
    if not len(a) or not len(b):
        return dict(boundary_mean=None, boundary_p95=None, boundary_max=None), []
    values = []
    for side, pixels, points, target in (
        ("reference", a, points_a, points_b),
        ("authored", b, points_b, points_a),
    ):
        tree = KDTree(len(target))
        for index, p in enumerate(target):
            tree.insert((float(p[0]), float(p[1]), 0), index)
        tree.balance()
        for pixel, point in zip(pixels, points, strict=True):
            distance = float(tree.find((float(point[0]), float(point[1]), 0))[2])
            values.append((distance, side, pixel, point))
    distances = np.array([v[0] for v in values])
    worst = []
    for distance, side, pixel, point in sorted(
        values, key=lambda v: v[0], reverse=True
    )[:limit]:
        world = (
            reference.origin
            + reference.horizontal * point[0]
            + reference.vertical * point[1]
        )
        worst.append(
            ReferenceBoundaryError(
                pixel=[
                    float(v * reference.provenance["sample_stride"] + 0.5)
                    for v in pixel
                ],
                world=world.tolist(),
                deviation=distance,
                side="reference" if side == "reference" else "authored",
            )
        )
    return dict(
        boundary_mean=float(distances.mean()),
        boundary_p95=float(np.percentile(distances, 95)),
        boundary_max=float(distances.max()),
    ), worst


def compare(
    arguments: ReferenceCompareArguments, spool: ArtifactSpool | None
) -> tuple[ReferenceCompareResult, ArtifactDescriptor | None]:
    started = time.perf_counter()
    references.idle()
    surfaces: dict[str, Any] = {}
    total_triangles = 0
    section_valid = set()
    results, overlays = [], []
    casts = 0
    for query in arguments.comparisons:
        obj = references.owned(query.mask.reference, references.REFERENCE)
        registration, _ = construction.current_registration(obj)
        projection = registration["specification"]["projection"]
        mask = compile_mask(query.mask, projection, arguments.resolution)
        if projection not in {"plane", "orthographic"}:
            raise OperationError(
                "reference_projection_invalid",
                (
                    "Perspective evidence is qualitative; metric comparison requires "
                    "calibrated planes/orthographic sources"
                ),
            )
        selected = []
        for name in query.objects:
            if name not in surfaces:
                target = modifiers.object_mesh(name)
                surfaces[name] = retopo_geometry.Surface(
                    target, retopo_geometry.graph(target)
                )
                total_triangles += len(surfaces[name].triangles)
                if total_triangles > 524288:
                    raise OperationError(
                        "reference_limit",
                        "Reference comparison exceeds524288 evaluated triangles",
                    )
            surface = surfaces[name]
            if projection == "plane" and name not in section_valid:
                edges = Counter(
                    tuple(sorted((t[i], t[(i + 1) % 3])))
                    for t in surface.triangles
                    for i in range(3)
                )
                if any(count != 2 for count in edges.values()):
                    raise OperationError(
                        "reference_section_invalid",
                        "Section comparison requires closed manifold surfaces",
                    )
                section_valid.add(name)
            selected.append(surface)
        normal = Vector(mask.normal)
        bounds = [float(p.dot(normal)) for surface in selected for p in surface.points]
        minimum, maximum = min(bounds), max(bounds)
        span = max(maximum - minimum, max(mask.spacing))
        epsilon = max(span * 1e-6, min(mask.spacing) * 1e-4, 1e-8)
        h, w = mask.foreground.shape
        actual = np.zeros((h, w), dtype=bool)
        for y in range(h):
            for x in range(w):
                point = Vector(
                    mask.origin
                    + x * mask.spacing[0] * mask.horizontal
                    + y * mask.spacing[1] * mask.vertical
                )
                if projection == "orthographic":
                    point += normal * (minimum - span * 0.01 - point.dot(normal))
                distance = max(0, maximum - point.dot(normal)) + span * 0.02
                for surface in selected:
                    origin, remaining, hits = point.copy(), distance, 0
                    while remaining > epsilon:
                        casts += 1
                        if casts > 8388608:
                            raise OperationError(
                                "reference_limit",
                                (
                                    "Reference ray budget exceeded; "
                                    "reduce "
                                    "selections/resolution"
                                ),
                            )
                        location, _, _, hit_distance = surface.bvh.ray_cast(
                            origin, normal, remaining
                        )
                        if location is None:
                            break
                        hits += 1
                        if projection == "orthographic":
                            break
                        if hits > 128:
                            raise OperationError(
                                "reference_limit",
                                "Section ray crosses more than128 layers",
                            )
                        origin = location + normal * epsilon
                        remaining -= float(hit_distance) + epsilon
                    if hits % 2 == 1:
                        actual[y, x] = True
                        break
        union = int(np.count_nonzero(actual | mask.foreground))
        intersection = int(np.count_nonzero(actual & mask.foreground))
        boundary_errors, worst = errors(mask, actual, arguments.worst_limit)
        issues = (
            []
            if actual.any()
            else ["No authored material intersects this comparison view"]
        )
        if (
            actual[0].any()
            or actual[-1].any()
            or actual[:, 0].any()
            or actual[:, -1].any()
        ):
            issues.append(
                "Authored silhouette reaches image bounds; out-of-frame extent is "
                "not measured"
            )
        results.append(
            ReferenceShapeResult(
                id=query.id,
                reference=query.mask.reference,
                projection=projection,
                objects=query.objects,
                overlap_iou=intersection / union if union else 1,
                disagreement_fraction=(union - intersection) / union if union else 0,
                sampling_uncertainty=mask.provenance["sampling_uncertainty"],
                worst=worst,
                issues=issues,
                source_sha256=mask.provenance["source_sha256"],
                **boundary_errors,
            )
        )
        if arguments.overlay:
            rgb = np.full((h, w, 3), 25, dtype=np.uint8)
            rgb[actual & mask.foreground] = (180, 180, 180)
            rgb[mask.foreground & ~actual] = (45, 225, 110)
            rgb[actual & ~mask.foreground] = (230, 65, 200)
            overlays.append(rgb[::-1])
    descriptor = None
    if overlays:
        if spool is None:
            raise OperationError(
                "invalid_context", "Reference overlay artifact storage is unavailable"
            )
        columns = min(4, len(overlays))
        rows = math.ceil(len(overlays) / columns)
        tile = arguments.resolution
        canvas = np.full((rows * tile, columns * tile, 3), 25, dtype=np.uint8)
        for i, rgb in enumerate(overlays):
            h, w = rgb.shape[:2]
            x, y = i % columns * tile, i // columns * tile
            canvas[y : y + h, x : x + w] = rgb
        with spool.reserve() as (artifact_id, path):
            path.write_bytes(
                png_rgb(canvas.shape[1], canvas.shape[0], canvas.tobytes())
            )
            descriptor = spool.describe(artifact_id, name="reference-comparison.png")
    return ReferenceCompareResult(
        comparisons=results, processing_seconds=time.perf_counter() - started
    ), descriptor
