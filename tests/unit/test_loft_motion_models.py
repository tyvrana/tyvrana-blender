"""Structural admission bounds and deterministic typed nonlinear mappings."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.loft_models import LoftCreateArguments
from tyvrana_blender.motion_math import mapped, output_bounds
from tyvrana_blender.motion_models import PiecewiseMapping


def test_section_batch_bounds_and_no_expression() -> None:
    spec = dict(
        name="Fixture",
        sections=[
            dict(center=[0, 0, 0], radii=[1, 2, 3, 4]),
            dict(center=[0, 1, 0], radii=[2, 2, 1, 1]),
        ],
    )
    assert (
        LoftCreateArguments.model_validate(dict(components=[spec]))
        .components[0]
        .vertex_count()
        == 36
    )
    with pytest.raises(ValidationError):
        LoftCreateArguments.model_validate(dict(components=[spec, spec]))
    with pytest.raises(ValidationError):
        LoftCreateArguments.model_validate(
            dict(components=[spec | dict(expression="x")])
        )
    with pytest.raises(ValidationError):
        LoftCreateArguments.model_validate(
            dict(components=[spec | dict(sections=[spec["sections"][0]] * 2)])
        )


@pytest.mark.parametrize("mode", ["constant", "linear"])
def test_piecewise_interpolation_and_extrapolation(mode: str) -> None:
    value = PiecewiseMapping.model_validate(
        dict(
            kind="piecewise",
            knots=[
                dict(input=0, output=0),
                dict(input=1, output=2),
                dict(input=2, output=1),
            ],
            extrapolation=mode,
        )
    )
    assert mapped(value, 0.5) == (1, False)
    assert mapped(value, 1.5) == (1.5, False)
    assert mapped(value, -1) == ((0, True) if mode == "constant" else (-2, False))
    assert mapped(value, 3) == ((1, True) if mode == "constant" else (0, False))
    assert output_bounds(value) == ((0, 2) if mode == "constant" else None)
    with pytest.raises(ValidationError):
        PiecewiseMapping.model_validate(
            dict(kind="piecewise", knots=[dict(input=1, output=0)] * 2)
        )
