"""Deterministic mapping generation; no user expression is parsed or executed."""

from .motion_models import LinearMapping, Mapping, PiecewiseMapping


def coefficients(mapping: Mapping) -> tuple[float, float, tuple[float, float] | None]:
    if isinstance(mapping, PiecewiseMapping):
        raise ValueError("Piecewise mappings have segment-specific coefficients")
    if isinstance(mapping, LinearMapping):
        return (
            mapping.scale,
            mapping.offset,
            (mapping.clamp.minimum, mapping.clamp.maximum) if mapping.clamp else None,
        )
    scale = (mapping.output_max - mapping.output_min) / (
        mapping.input_max - mapping.input_min
    )
    return (
        scale,
        mapping.output_min - mapping.input_min * scale,
        (
            min(mapping.output_min, mapping.output_max),
            max(mapping.output_min, mapping.output_max),
        )
        if mapping.clamp
        else None,
    )


def output_bounds(mapping: Mapping) -> tuple[float, float] | None:
    if isinstance(mapping, PiecewiseMapping):
        values = [k.output for k in mapping.knots]
        return (
            (min(values), max(values)) if mapping.extrapolation == "constant" else None
        )
    return coefficients(mapping)[2]


def mapped(mapping: Mapping, source: float) -> tuple[float, bool]:
    if isinstance(mapping, PiecewiseMapping):
        knots = mapping.knots
        if mapping.extrapolation == "constant":
            if source < knots[0].input:
                return knots[0].output, True
            if source > knots[-1].input:
                return knots[-1].output, True
        i = next(
            (i for i in range(len(knots) - 1) if source <= knots[i + 1].input),
            len(knots) - 2,
        )
        a, b = knots[i], knots[i + 1]
        return a.output + (source - a.input) * (b.output - a.output) / (
            b.input - a.input
        ), False
    scale, offset, clamp = coefficients(mapping)
    raw = scale * source + offset
    value = min(clamp[1], max(clamp[0], raw)) if clamp else raw
    return value, value != raw


def expression(mapping: Mapping, source: str = "x") -> str:
    if isinstance(mapping, PiecewiseMapping):
        knots = mapping.knots
        slopes = [
            (b.output - a.output) / (b.input - a.input)
            for a, b in zip(knots, knots[1:], strict=False)
        ]
        # Hinge representation is compact, continuous and a native simple expression.
        if mapping.extrapolation == "constant":
            text = (
                f"({knots[0].output!r}+{slopes[0]!r}*"
                f"max(0,{source}-({knots[0].input!r})))"
            )
        else:
            text = (
                f"({knots[0].output!r}+{slopes[0]!r}*({source}-({knots[0].input!r})))"
            )
        for i in range(1, len(slopes)):
            text += (
                f"+({slopes[i] - slopes[i - 1]!r})*max(0,{source}-({knots[i].input!r}))"
            )
        if mapping.extrapolation == "constant":
            text += f"-({slopes[-1]!r})*max(0,{source}-({knots[-1].input!r}))"
        return text
    scale, offset, clamp = coefficients(mapping)
    text = f"({source}*({scale!r})+({offset!r}))"
    if clamp:
        text = f"min({clamp[1]!r},max({clamp[0]!r},{text}))"
    return text
