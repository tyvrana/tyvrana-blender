"""Deterministic mapping generation; no user expression is parsed or executed."""

from .motion_models import LinearMapping, Mapping


def coefficients(mapping: Mapping) -> tuple[float, float, tuple[float, float] | None]:
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


def mapped(mapping: Mapping, source: float) -> tuple[float, bool]:
    scale, offset, clamp = coefficients(mapping)
    raw = scale * source + offset
    value = min(clamp[1], max(clamp[0], raw)) if clamp else raw
    return value, value != raw


def expression(mapping: Mapping) -> str:
    scale, offset, clamp = coefficients(mapping)
    text = f"(x*({scale!r})+({offset!r}))"
    if clamp:
        text = f"min({clamp[1]!r},max({clamp[0]!r},{text}))"
    return text
