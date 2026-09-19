"""Rank, residual, uncertainty and finite construction contracts."""

import math

import pytest
from pydantic import ValidationError

from tyvrana_blender.construction_math import Equation, solve
from tyvrana_blender.reference_models import (
    LandmarkDeriveArguments,
    ObservationSetArguments,
    ReferenceRegistration,
)


def test_rank_never_fabricates_depth() -> None:
    assert solve([]).rank == 0
    one = solve([Equation((1, 0, 0), 2), Equation((0, 1, 0), 3)])
    assert one.rank == 2 and one.point is None and one.sigma is None
    parallel = solve([Equation((1, 0, 0), 2), Equation((1, 0, 0), 3)])
    assert parallel.rank == 1 and parallel.point is None


def test_multi_view_weighted_reconstruction_and_residual() -> None:
    equations = [
        Equation((1, 0, 0), 2, 1),
        Equation((0, 1, 0), 3, 1),
        Equation((0, 0, 1), 4, 1),
    ]
    exact = solve(equations * 2)
    assert exact.point == [2, 3, 4]
    assert exact.residuals == [0] * 6
    assert exact.sigma == pytest.approx([1 / math.sqrt(2)] * 3)
    conflict = solve(equations + [Equation((1, 0, 0), 4, 1)])
    assert conflict.point == [3, 3, 4]
    assert conflict.residuals == [1, 0, 0, -1]
    unknown = solve([Equation(e.normal, e.value) for e in equations])
    assert unknown.point == [2, 3, 4] and unknown.sigma is None


def test_rotated_frame_equations_and_near_parallel_rejection() -> None:
    s = 1 / math.sqrt(2)
    value = solve(
        [Equation((s, s, 0), 5 * s), Equation((-s, s, 0), s), Equation((0, 0, 1), 4)]
    )
    assert value.point == pytest.approx([2, 3, 4])
    singular = solve(
        [Equation((1, 0, 0), 1), Equation((0, 1, 0), 1), Equation((1, 0, 1e-8), 1)]
    )
    assert singular.point is None


@pytest.mark.parametrize("count", [0, 33])
def test_derive_batch_bound(count: int) -> None:
    with pytest.raises(ValidationError):
        LandmarkDeriveArguments.model_validate(
            {
                "landmarks": [
                    {"name": str(i), "source": {"observations": ["a"]}}
                    for i in range(count)
                ]
            }
        )


def test_source_and_registration_contracts_reject_escape_hatches() -> None:
    with pytest.raises(ValidationError):
        ObservationSetArguments.model_validate(
            {"observations": [{"id": "a", "reference": "R", "pixel": [1, 2, 3]}]}
        )
    with pytest.raises(ValidationError):
        ReferenceRegistration.model_validate(
            {
                "reference": "R",
                "projection": "orthographic",
                "calibration": {"a": [0, 0], "b": [1, 0], "distance": 1},
                "origin_pixel": [0, 0],
                "horizontal": "x",
                "vertical": "-x",
            }
        )
    with pytest.raises(ValidationError):
        LandmarkDeriveArguments.model_validate(
            {
                "landmarks": [
                    {
                        "name": "a",
                        "source": {"kind": "reflection", "landmark": "a", "axis": "x"},
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        LandmarkDeriveArguments.model_validate(
            {
                "landmarks": [
                    {
                        "name": "a",
                        "source": {
                            "kind": "observations",
                            "observations": ["p"],
                            "matrix": [[1, 0], [0, 1]],
                        },
                    }
                ]
            }
        )
