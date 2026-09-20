"""Exact separable Euclidean distances for bounded raster masks.

Lower-envelope distance transform; see Felzenszwalb and Huttenlocher (2012),
https://doi.org/10.4086/toc.2012.v008a019. Columns share vectorized envelope work.
"""

from typing import Any

import numpy as np  # type: ignore[import-not-found]


def distance_grid(boundary: Any, sx: float, sy: float) -> Any:
    """Distance to a nonempty boundary with positive, possibly unequal pixel sizes."""
    h, w = boundary.shape
    x = np.arange(w)[None, :]
    left = np.maximum.accumulate(np.where(boundary, x, -2 * w), axis=1)
    right = np.minimum.accumulate(np.where(boundary, x, 3 * w)[:, ::-1], axis=1)[
        :, ::-1
    ]
    horizontal = np.minimum(x - left, right - x).astype(np.float64) * sx
    cost = horizontal**2
    # Empty rows need a finite cost exceeding every possible in-image distance.
    cost[~boundary.any(axis=1)] = (w * sx) ** 2 + (h * sy) ** 2 + 1
    columns = np.arange(w)
    centers = np.zeros((h, w), dtype=np.int32)
    limits = np.full((h + 1, w), np.inf)
    limits[0] = -np.inf
    top = np.zeros(w, dtype=np.int32)
    scale = sy * sy
    # Build each column's lower envelope of squared-distance parabolas.
    for q in range(1, h):
        previous = centers[top, columns]
        crossing = (
            cost[q]
            - cost[previous, columns]
            + scale * (q * q - previous.astype(float) ** 2)
        ) / (2 * scale * (q - previous))
        remove = (crossing <= limits[top, columns]) & (top > 0)
        while remove.any():
            top[remove] -= 1
            previous = centers[top, columns]
            crossing = (
                cost[q]
                - cost[previous, columns]
                + scale * (q * q - previous.astype(float) ** 2)
            ) / (2 * scale * (q - previous))
            remove = (crossing <= limits[top, columns]) & (top > 0)
        top += 1
        centers[top, columns] = q
        limits[top, columns] = crossing
        limits[top + 1, columns] = np.inf
    top[:] = 0
    result = np.empty_like(cost)
    for q in range(h):
        advance = limits[top + 1, columns] < q
        while advance.any():
            top[advance] += 1
            advance = limits[top + 1, columns] < q
        nearest = centers[top, columns]
        result[q] = scale * (q - nearest) ** 2 + cost[nearest, columns]
    return np.sqrt(result)
