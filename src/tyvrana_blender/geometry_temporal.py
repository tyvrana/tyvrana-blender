"""Deterministic midpoint refinement; sampled coverage, never continuous proof."""

from collections.abc import Callable

from .geometry_qa_models import AdaptiveGeometryRange, GeometryCoverage, GeometrySample


def metrics(sample: GeometrySample) -> tuple[list[float], list[float]]:
    distances = [
        p.minimum_distance for p in sample.pairs if p.minimum_distance is not None
    ]
    distances.extend(
        p.minimum_distance for p in sample.instances if p.minimum_distance is not None
    )
    deformation: list[float] = []
    for instances in sample.instances:
        deformation.extend(
            [
                float(instances.contained_instances),
                float(instances.contact_element_pairs),
                float(instances.degenerate_tested_triangles),
            ]
        )
    for obj in sample.objects:
        deformation.extend(
            float(x)
            for x in [
                obj.degenerate_triangles,
                obj.collapsed_triangles,
                obj.negative_jacobians,
                obj.collapsed_jacobians,
                obj.minimum_jacobian,
            ]
            if x is not None
        )
    return distances, deformation


def risk(
    a: GeometrySample,
    m: GeometrySample,
    b: GeometrySample,
    options: AdaptiveGeometryRange,
) -> bool:
    triples = [metrics(s) for s in [a, m, b]]
    distances = [v for pair in triples for v in pair[0]]
    if distances and min(distances) <= options.near_clearance:
        return True
    for group in (0, 1):
        rows = [r[group] for r in triples]
        if len({len(r) for r in rows}) != 1:
            return True
        for left, middle, right in zip(*rows, strict=True):
            scale = max(abs(left), abs(middle), abs(right), options.near_clearance)
            if (
                min(left, middle, right) < 0 < max(left, middle, right)
                or max(left, middle, right) - min(left, middle, right)
                > options.change_ratio * scale
                or abs(middle - (left + right) / 2) > options.change_ratio * scale
            ):
                return True
    return False


def sample(
    options: AdaptiveGeometryRange, evaluate: Callable[[float], GeometrySample]
) -> tuple[list[GeometrySample], GeometryCoverage]:
    values: dict[float, GeometrySample] = {}

    def get(t: float) -> GeometrySample:
        if t not in values:
            values[t] = evaluate(t)
        return values[t]

    initial = [
        options.start
        + (options.end - options.start) * i / (options.initial_samples - 1)
        for i in range(options.initial_samples)
    ]
    for t in initial:
        get(t)
    pending = [(a, b) for a, b in zip(initial[:-1], initial[1:], strict=True)]
    remaining = 0
    exhausted = False
    while pending:
        # Widest intervals first, then time; deterministic and avoids one hotspot
        # consuming the whole budget before the other initial intervals are visited.
        pending.sort(key=lambda interval: (-(interval[1] - interval[0]), interval[0]))
        left, right = pending.pop(0)
        middle = (left + right) / 2
        if middle not in values and len(values) >= options.max_samples:
            remaining += len(pending) + 1
            exhausted = True
            break
        mid = get(middle)
        if risk(get(left), mid, get(right), options):
            if (right - left) / 2 > options.minimum_step:
                pending.extend([(left, middle), (middle, right)])
            else:
                remaining += 1
    ordered = sorted(values)
    return [values[t] for t in ordered], GeometryCoverage(
        mode="adaptive_midpoints",
        sample_count=len(values),
        maximum_gap=max(
            (b - a for a, b in zip(ordered[:-1], ordered[1:], strict=True)), default=0
        ),
        risky_intervals_remaining=remaining,
        stopped_by_budget=exhausted,
        criteria=[
            "Every initial interval midpoint",
            "Near clearance",
            "Metric changes",
            "Sign changes and midpoint extrema",
            "Bounded sample count/minimum step",
        ],
    )
