"""Deterministic bounded sampling and local-frame sliding decomposition."""

import math
from collections.abc import Sequence


def sample_indices(chosen: Sequence[int], count: int) -> list[int]:
    """Even ranks in sorted indices, not uniform surface-area sampling."""
    if not chosen or count < 1:
        raise ValueError("Sampling requires a nonempty selection and positive count")
    if len(chosen) <= count:
        return list(chosen)
    if count == 1:
        return [chosen[len(chosen) // 2]]
    return [chosen[i * (len(chosen) - 1) // (count - 1)] for i in range(count)]


def sliding(
    current: Sequence[float], captured: Sequence[float]
) -> tuple[list[float], float, float]:
    delta = [a - b for a, b in zip(current, captured, strict=True)]
    if len(delta) != 3 or not all(math.isfinite(x) for x in delta):
        raise ValueError("Sliding offsets require three finite frame coordinates")
    return delta[:2], delta[2], math.hypot(delta[0], delta[1])
