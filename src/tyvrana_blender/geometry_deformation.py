"""Rotation-invariant local affine volume ratios with explicit rank coverage."""

from typing import Any

import numpy as np  # type: ignore[import-not-found]


def jacobians(
    surface: Any, reference: Any, collapse: float, limit: int
) -> dict[str, Any]:
    current_matrix = surface.source.obj.matrix_world.inverted()
    rest_matrix = reference.source.obj.matrix_world.inverted()
    current = np.array([list(current_matrix @ p) for p in surface.points], dtype=float)
    rest = np.array([list(rest_matrix @ p) for p in reference.points], dtype=float)
    neighbors: list[set[int]] = [set() for _ in rest]
    for triangle in reference.indices:
        for i in triangle:
            neighbors[i].update(j for j in triangle if j != i)
    values = []
    unavailable = 0
    for i, adjacent in enumerate(neighbors):
        if len(adjacent) < 3:
            unavailable += 1
            continue
        indices = sorted(adjacent)
        x = rest[indices] - rest[i]
        y = current[indices] - current[i]
        gram = x.T @ x
        eigen = np.linalg.eigvalsh(gram)
        if eigen[-1] <= 1e-30 or eigen[0] <= eigen[-1] * 1e-6:
            unavailable += 1
            continue
        gradient = np.linalg.solve(gram, x.T @ y).T
        values.append((float(np.linalg.det(gradient)), i))
    values.sort()
    return dict(
        jacobian_samples=len(values),
        jacobian_unavailable=unavailable,
        negative_jacobians=sum(v < 0 for v, _ in values),
        collapsed_jacobians=sum(abs(v) < collapse for v, _ in values),
        minimum_jacobian=values[0][0] if values else None,
        worst_jacobian_vertices=[i for _, i in values[:limit]],
    )
