"""Deterministic normalized least-squares fits with explicit rank/quality checks."""

import time
from typing import Any, Literal

import numpy as np  # type: ignore[import-not-found]

from . import references
from .layer_geometry import SurfaceCache
from .mechanics_geometry import (
    bounded_indices,
    digest,
    evidence,
    selected,
    triangle_indices,
)
from .mechanics_models import (
    EvidenceDirection,
    FitArguments,
    FitFrame,
    FitQuery,
    FitResult,
    FitSummary,
)


class UncertainFit(Exception):
    pass


def unit(vector: Any) -> Any:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-10:
        raise UncertainFit("Coincident direction evidence")
    return vector / norm


def directed(vector: Any, hint: Any = None) -> Any:
    vector = unit(vector)
    if hint is not None:
        return vector if float(vector @ hint) >= 0 else -vector
    index = int(np.argmax(np.abs(vector)))
    return vector if vector[index] >= 0 else -vector


def direction(spec: EvidenceDirection | None, provenance: list[Any]) -> Any:
    if spec is None:
        return None
    points = [list(references.resolve(p)) for p in [spec.start, spec.end]]
    provenance.append([spec.model_dump(), points])
    return unit(np.asarray(points[1]) - np.asarray(points[0]))


def perpendicular(axis: Any, hint: Any = None) -> Any:
    ref = np.eye(3)[int(np.argmin(np.abs(axis)))] if hint is None else hint
    return unit(ref - axis * float(axis @ ref))


def least_squares(matrix: Any, target: Any, maximum: float) -> tuple[Any, float]:
    values, _, rank, singular = np.linalg.lstsq(matrix, target, rcond=1e-12)
    if rank < matrix.shape[1] or singular[-1] <= 0:
        raise UncertainFit("Rank-deficient fit evidence")
    condition = float(singular[0] / singular[-1])
    if condition > maximum:
        raise UncertainFit("Poorly conditioned fit evidence")
    return values, condition


def fit_one(query: FitQuery, cache: SurfaceCache) -> FitSummary:
    provenance: list[Any] = []
    samples: list[Any] = []
    normals: list[Any] = []
    support = 0
    sources = []
    source_evidence = []
    reasons: list[str] = []
    center = axis = radius = frame = rms = maximum_error = condition = None
    orientation: Literal["evidence", "conventional_roll", "unconstrained"] = (
        "unconstrained"
    )
    for region in query.regions:
        surface = cache.get(region.object_name)
        ids = selected(surface, region)
        support += len(ids)
        chosen = bounded_indices(ids, query.sample_limit)
        samples.extend(list(surface.points[i]) for i in chosen)
        face_ids = triangle_indices(surface, region, ids)
        for i in bounded_indices(face_ids, query.sample_limit):
            a, b, c = (surface.points[v] for v in surface.triangles[i])
            n = (b - a).cross(c - a)
            if n.length > 1e-12:
                normals.append(list(n.normalized()))
        source_evidence.append(evidence(surface, region))
        provenance.append([region.model_dump(), source_evidence[-1].geometry_sha256])
        sources.append(region.object_name)
    for point in query.points:
        value = list(references.resolve(point))
        samples.append(value)
        support += 1
        provenance.append([point.model_dump(), value])
        sources.append(point.model_dump_json())
    samples = [
        samples[i]
        for i in bounded_indices(list(range(len(samples))), query.sample_limit)
    ]
    # Fingerprints include complete source geometry, point identities and
    # orientation evidence.
    try:
        hint = direction(query.axis, provenance)
        secondary = direction(query.secondary, provenance)
        if len(samples) < (1 if query.method == "landmarks" else 4):
            raise UncertainFit("Insufficient distinct samples")
        points = np.asarray(samples, dtype=float)
        origin = points.mean(axis=0)
        scale = float(np.max(np.linalg.norm(points - origin, axis=1)))
        if scale < 1e-10 and query.method != "landmarks":
            raise UncertainFit("Coincident support")
        normalized = (points - origin) / max(scale, 1e-10)
        condition = 1.0
        if query.method == "landmarks":
            center, axis = origin, hint
            errors = np.zeros(len(points))
        elif query.method == "sphere":
            m = np.column_stack([2 * normalized, np.ones(len(points))])
            coeff, condition = least_squares(
                m, np.sum(normalized**2, axis=1), query.maximum_condition
            )
            r2 = float(coeff[3] + coeff[:3] @ coeff[:3])
            if r2 <= 0:
                raise UncertainFit("Nonpositive fitted radius")
            center, radius, axis = origin + scale * coeff[:3], scale * np.sqrt(r2), hint
            errors = np.abs(np.linalg.norm(points - center, axis=1) - radius)
        else:
            _, singular, vectors = np.linalg.svd(normalized, full_matrices=False)
            if singular[1] < 1e-8 * max(singular[0], 1e-10):
                raise UncertainFit("Collinear evidence cannot define this surface")
            if query.method == "cylinder":
                if hint is not None:
                    axis = hint
                elif len(normals) >= 4:
                    normal_array = np.asarray(normals)
                    values, vectors_n = np.linalg.eigh(normal_array.T @ normal_array)
                    if values[1] < 1e-8 or values[0] > 0.15 * values[1]:
                        raise UncertainFit(
                            "Surface normals do not identify a unique cylinder axis"
                        )
                    axis = directed(vectors_n[:, 0])
                    condition = float(values[-1] / values[1])
                else:
                    raise UncertainFit(
                        "Cylinder requires surface normals or an evidence axis"
                    )
            else:
                if singular[2] > 0.5 * singular[1]:
                    raise UncertainFit(
                        "Surface does not identify a unique plane normal"
                    )
                axis = directed(vectors[-1], hint)
                condition = float(singular[0] / singular[1])
            x = perpendicular(axis)
            z = np.cross(axis, x)
            if query.method == "plane":
                center = origin
                errors = np.abs((points - center) @ axis)
            else:
                xy = np.column_stack([normalized @ x, normalized @ z])
                coeff, circle_condition = least_squares(
                    np.column_stack([2 * xy, np.ones(len(points))]),
                    np.sum(xy**2, axis=1),
                    query.maximum_condition,
                )
                condition = max(condition, circle_condition)
                r2 = float(coeff[2] + coeff[:2] @ coeff[:2])
                if r2 <= 0:
                    raise UncertainFit("Nonpositive fitted radius")
                center = origin + scale * (coeff[0] * x + coeff[1] * z)
                radius = float(scale * np.sqrt(r2))
                delta = points - center
                radial = delta - (delta @ axis)[:, None] * axis
                errors = np.abs(np.linalg.norm(radial, axis=1) - radius)
                if query.method == "circle":
                    errors = np.hypot(errors, delta @ axis)
        rms = float(np.sqrt(np.mean(errors**2)))
        maximum_error = float(np.max(errors))
        if condition > query.maximum_condition or maximum_error > query.tolerance:
            raise UncertainFit("Fit exceeds residual or conditioning tolerance")
        if axis is not None:
            x = perpendicular(axis, secondary)
            frame = FitFrame(
                head=center.tolist(),
                tail=(center + axis * query.frame_length).tolist(),
                x_reference=x.tolist(),
            )
            orientation = "evidence" if secondary is not None else "conventional_roll"
        else:
            reasons.append(
                "Spherical evidence does not determine an axis; supply directed "
                "landmarks"
            )
    except (UncertainFit, np.linalg.LinAlgError) as exc:
        reasons.append(str(exc))
        center = axis = frame = radius = None
    fingerprint = digest(provenance)
    fresh = query.expected_sha256 is None or query.expected_sha256 == fingerprint
    status: Literal["FITTED", "UNCERTAIN", "STALE"] = (
        "FITTED" if center is not None else "UNCERTAIN"
    )
    if not fresh:
        status = "STALE"
        reasons.append(
            "Source evidence changed; refit and revalidate dependent mechanics"
        )
        frame = None
    return FitSummary(
        name=query.name,
        method=query.method,
        status=status,
        center=None if center is None else center.tolist(),
        axis=None if axis is None else axis.tolist(),
        radius=radius,
        frame=frame,
        orientation=orientation,
        rms_error=rms,
        maximum_error=maximum_error,
        condition=condition,
        support_count=support,
        sample_count=len(samples),
        source_sha256=fingerprint,
        sources=sources,
        provenance=source_evidence,
        fresh=fresh,
        reasons=reasons,
    )


def inspect(args: FitArguments) -> FitResult:
    start = time.perf_counter()
    with SurfaceCache() as cache:
        fits = [fit_one(query, cache) for query in args.fits]
        vertices = cache.vertices
    return FitResult(
        fits=fits,
        evaluated_vertices=vertices,
        processing_seconds=time.perf_counter() - start,
    )
