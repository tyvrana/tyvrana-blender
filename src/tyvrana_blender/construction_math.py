"""Bounded three-dimensional weighted linear reconstruction; no image inference."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Equation:
    normal: tuple[float, float, float]
    value: float
    sigma: float | None = None


@dataclass(frozen=True)
class Solution:
    rank: int
    point: list[float] | None
    residuals: list[float]
    sigma: list[float] | None


def solve(equations: list[Equation]) -> Solution:
    """Rank-revealing elimination of a 3x3 normal system in float64.

    Uncertainty is conditional on independent supplied observation errors and
    exact registration. Unknown errors never become invented confidence values.
    """
    if not equations:
        return Solution(0, None, [], None)
    if len(equations) > 32:
        raise ValueError("At most 32 scalar constraints")
    weights = [1 / e.sigma**2 if e.sigma is not None else 1.0 for e in equations]
    maximum = max(weights)
    weights = [w / maximum for w in weights]
    normal = [
        [
            sum(
                w * e.normal[i] * e.normal[j]
                for e, w in zip(equations, weights, strict=True)
            )
            for j in range(3)
        ]
        for i in range(3)
    ]
    rhs = [
        sum(w * e.normal[i] * e.value for e, w in zip(equations, weights, strict=True))
        for i in range(3)
    ]
    augmented = [
        normal[i] + [rhs[i]] + [float(i == j) for j in range(3)] for i in range(3)
    ]
    scale = max(abs(v) for row in normal for v in row)
    rank = 0
    for column in range(3):
        pivot = max(range(rank, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= max(scale * 1e-10, 1e-15):
            continue
        augmented[rank], augmented[pivot] = augmented[pivot], augmented[rank]
        divisor = augmented[rank][column]
        augmented[rank] = [v / divisor for v in augmented[rank]]
        for row in range(3):
            if row != rank:
                factor = augmented[row][column]
                augmented[row] = [
                    v - factor * p
                    for v, p in zip(augmented[row], augmented[rank], strict=True)
                ]
        rank += 1
    if rank != 3:
        return Solution(rank, None, [], None)
    point = [row[3] for row in augmented]
    residuals = [
        sum(a * b for a, b in zip(e.normal, point, strict=True)) - e.value
        for e in equations
    ]
    sigma = (
        [math.sqrt(max(0, augmented[i][4 + i] / maximum)) for i in range(3)]
        if all(e.sigma is not None for e in equations)
        else None
    )
    if not all(math.isfinite(v) for v in point + residuals + (sigma or [])):
        raise ValueError("Nonfinite reconstruction")
    return Solution(rank, point, residuals, sigma)
