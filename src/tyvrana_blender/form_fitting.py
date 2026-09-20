"""Bounded local surface reconstruction from registered contour observations."""

from collections.abc import Generator
from typing import Any

import numpy as np  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]
from mathutils.geometry import barycentric_transform  # type: ignore[import-not-found]
from mathutils.kdtree import KDTree  # type: ignore[import-not-found]

from .errors import OperationError
from .form_models import ReferenceSurfaceFit, SurfaceFitSummary
from .form_reference import compile_mask

POINT_LIMIT = 262144
WORK_LIMIT = 1048576
NEIGHBORS = 192
ADAPTIVE_NEIGHBORS = 512
CHUNK = 256


def coherent_displacements(
    original: Any, proposed: Any, edges: Any, supported: Any, radius: float
) -> Any:
    """Screened graph-Poisson fit of a displacement field, not mesh smoothing.

    Supported MLS observations supply the data term. Unsupported vertices have
    no observation to pin them: the same field extends harmonically through
    them. A tiny retention term makes wholly unsupported components well posed.
    The coherence length is one quarter of the MLS observation radius; inverse
    squared edge lengths make the regularization independent of tessellation.
    """
    count = len(original)
    a, b = edges.T
    squared = np.sum((original[a] - original[b]) ** 2, axis=1)
    if np.any(squared <= 0):
        raise OperationError("form_fit_invalid", "Surface fitting needs nonzero edges")
    valence = np.bincount(edges.reshape(-1), minlength=count)
    weights = (radius / 4) ** 2 / squared
    weights /= np.maximum((valence[a] + valence[b]) / 2, 1)
    degree = np.bincount(a, weights=weights, minlength=count)
    degree += np.bincount(b, weights=weights, minlength=count)
    fidelity = supported.astype(float) + 1e-6
    diagonal = fidelity + degree
    target = proposed - original

    def multiply(value: Any) -> Any:
        delta = weights[:, None] * (value[a] - value[b])
        return fidelity[:, None] * value + np.column_stack(
            [
                np.bincount(a, weights=delta[:, axis], minlength=count)
                - np.bincount(b, weights=delta[:, axis], minlength=count)
                for axis in range(3)
            ]
        )

    # Fixed bounded preconditioned conjugate-gradient solve. Each coordinate is
    # an independent right-hand side; no geometry-dependent iteration search.
    value = target.copy()
    residual = fidelity[:, None] * target - multiply(value)
    preconditioned = residual / diagonal[:, None]
    direction = preconditioned.copy()
    product = np.sum(residual * preconditioned, axis=0)
    initial = np.maximum(product, 1e-30)
    for _ in range(64):
        active = product > initial * 1e-12
        if not np.any(active):
            break
        applied = multiply(direction)
        denominator = np.sum(direction * applied, axis=0)
        step = np.where(active, product / np.maximum(denominator, 1e-30), 0)
        value += direction * step
        residual -= applied * step
        preconditioned = residual / diagonal[:, None]
        updated = np.sum(residual * preconditioned, axis=0)
        direction = preconditioned + direction * (updated / np.maximum(product, 1e-30))
        product = updated
    return value


def observations(
    specification: ReferenceSurfaceFit,
) -> Generator[None, None, tuple[Any, list[dict[str, Any]]]]:
    points, sources = [], []
    total = 0
    for spec in specification.masks:
        mask = compile_mask(spec, "plane")
        foreground = mask.foreground
        y, x = np.nonzero(foreground[:, 1:] != foreground[:, :-1])
        horizontal = np.column_stack([x + 0.5, y])
        y, x = np.nonzero(foreground[1:] != foreground[:-1])
        vertical = np.column_stack([x, y + 0.5])
        pixels = np.concatenate([horizontal, vertical])
        total += len(pixels)
        if total > POINT_LIMIT:
            raise OperationError(
                "form_fit_limit", "Surface fitting exceeds262144 contour observations"
            )
        points.append(
            mask.origin
            + pixels[:, 0, None] * mask.spacing[0] * mask.horizontal
            + pixels[:, 1, None] * mask.spacing[1] * mask.vertical
        )
        sources.append(mask.provenance)
        yield None
    if total < 16:
        raise OperationError(
            "form_fit_insufficient", "Surface fitting needs more contour observations"
        )
    return np.concatenate(points), sources


def fit_steps(
    mesh: Any, specification: ReferenceSurfaceFit
) -> Generator[None, None, tuple[SurfaceFitSummary, list[dict[str, Any]]]]:
    count = len(mesh.vertices)
    if count * specification.iterations > WORK_LIMIT:
        raise OperationError(
            "form_fit_limit",
            "Surface fitting exceeds1048576 vertex-iterations; reduce mesh "
            "density or fitting iterations",
        )
    cloud, sources = yield from observations(specification)
    mesh.calc_loop_triangles()
    mesh.update()
    triangles = list(mesh.loop_triangles)
    surface = BVHTree.FromPolygons(
        [v.co for v in mesh.vertices],
        [t.vertices[:] for t in triangles],
        all_triangles=True,
    )
    normals = np.empty_like(cloud)
    tree = KDTree(len(cloud))
    for first in range(0, len(cloud), CHUNK):
        for index in range(first, min(first + CHUNK, len(cloud))):
            point = Vector(cloud[index])
            nearest, _, triangle, _ = surface.find_nearest(point)
            if nearest is None:
                raise OperationError("form_fit_invalid", "Constructed surface is empty")
            vertices = [mesh.vertices[i] for i in triangles[triangle].vertices]
            normal = barycentric_transform(
                nearest, *(v.co for v in vertices), *(v.normal for v in vertices)
            )
            normals[index] = tuple(normal.normalized())
            tree.insert(point, index)
        yield None
    tree.balance()
    del surface, triangles
    original = np.empty(count * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", original)
    original = original.reshape((-1, 3))
    edges = np.empty((len(mesh.edges), 2), dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges.reshape(-1))
    radius = specification.radius
    limited = np.zeros(count, dtype=bool)
    unsupported = np.zeros(count, dtype=bool)
    neighbor_count = min(
        ADAPTIVE_NEIGHBORS if specification.adaptive_neighborhood else NEIGHBORS,
        len(cloud),
    )
    for _ in range(specification.iterations):
        current = np.empty_like(original)
        mesh.vertices.foreach_get("co", current.reshape(-1))
        vertex_normals = np.empty_like(current)
        mesh.vertices.foreach_get("normal", vertex_normals.reshape(-1))
        output = current.copy()
        for first in range(0, count, CHUNK):
            stop = min(first + CHUNK, count)
            positions, outward = current[first:stop], vertex_normals[first:stop]
            indices = np.array(
                [
                    [entry[1] for entry in tree.find_n(tuple(p), neighbor_count)]
                    for p in positions
                ],
                dtype=int,
            )
            offsets = cloud[indices] - positions[:, None, :]
            alignment = np.einsum("nki,ni->nk", normals[indices], outward)
            squared_distance = np.sum(offsets**2, axis=2)
            weights = np.exp(-squared_distance / radius**2)
            if specification.adaptive_neighborhood:
                support = weights * np.clip((alignment - 0.2) / 0.8, 0, 1) ** 2
                average = np.einsum("nki,nk->ni", normals[indices], support)
                average /= np.maximum(support.sum(axis=1)[:, None], 1e-12)
                variation = np.clip(1 - np.linalg.norm(average, axis=1), 0, 1)
                local_radius = radius * (0.4 + 0.6 * np.exp(-variation / 0.04))
                weights = np.exp(-squared_distance / local_radius[:, None] ** 2)
            weights *= np.clip((alignment - 0.5) / 0.5, 0, 1) ** 2
            weight_sum = weights.sum(axis=1)
            center = np.einsum("nki,nk->ni", offsets, weights)
            center /= np.maximum(weight_sum[:, None], 1e-12)
            centered = offsets - center[:, None, :]
            covariance = np.swapaxes(centered, 1, 2) @ (weights[..., None] * centered)
            eigenvalues, frame = np.linalg.eigh(covariance)
            normal = frame[:, :, 0]
            normal *= np.where(np.einsum("ni,ni->n", normal, outward) < 0, -1, 1)[
                :, None
            ]
            horizontal = frame[:, :, 2]
            vertical = np.cross(normal, horizontal)
            x = np.einsum("nki,ni->nk", offsets, horizontal) / radius
            y = np.einsum("nki,ni->nk", offsets, vertical) / radius
            z = np.einsum("nki,ni->nk", offsets, normal) / radius
            basis = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=2)
            transposed_basis = np.swapaxes(basis, 1, 2)
            gram = transposed_basis @ (weights[..., None] * basis)
            rhs = (transposed_basis @ (weights * z)[..., None])[..., 0]
            gram[:, np.arange(6), np.arange(6)] += 1e-5
            gram[:, np.arange(3, 6), np.arange(3, 6)] += 0.00002 * weight_sum[:, None]
            solution = np.linalg.solve(gram, rhs[..., None])[..., 0]
            valid = (np.sum(weights > 0.05, axis=1) >= 15) & (
                eigenvalues[:, 1] > eigenvalues[:, 2] * 0.025
            )
            shift = np.where(
                valid, np.clip(solution[:, 0] * radius, -radius, radius) * 0.7, 0
            )
            proposed = positions + normal * shift[:, None]
            output[first:stop] = proposed
            unsupported[first:stop] = ~valid
            yield None
        displacement = coherent_displacements(
            original, output, edges, ~unsupported, radius
        )
        distance = np.linalg.norm(displacement, axis=1)
        limited |= distance > specification.max_distance
        displacement *= np.minimum(
            1, specification.max_distance / np.maximum(distance, 1e-30)
        )[:, None]
        output = original + displacement
        if not np.isfinite(output).all():
            raise OperationError("form_fit_invalid", "Surface fitting was not finite")
        mesh.vertices.foreach_set("co", output.reshape(-1))
        mesh.update()
    if unsupported.all():
        raise OperationError(
            "form_fit_insufficient",
            "No surface vertices have a supported local fit; inspect observation "
            "coverage, neighborhood size and the initial surface",
        )
    displacement = np.linalg.norm(output - original, axis=1)
    missed = np.flatnonzero(unsupported)
    # Evenly spaced bounded samples expose uncovered areas without a vertex dump.
    samples = missed[np.linspace(0, len(missed) - 1, min(len(missed), 8), dtype=int)]
    return (
        SurfaceFitSummary(
            reference_points=len(cloud),
            iterations=specification.iterations,
            unsupported_vertices=int(unsupported.sum()),
            displacement_limited_vertices=int(limited.sum()),
            maximum_displacement=float(displacement.max()),
            unsupported_samples=output[samples].tolist(),
        ),
        sources,
    )
