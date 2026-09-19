"""Adaptive diagnostics reveal midpoint events and report incomplete coverage."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.geometry_qa_models import (
    AdaptiveGeometryRange,
    ClearanceSummary,
    GeometryInspectArguments,
    GeometrySample,
)
from tyvrana_blender.geometry_temporal import sample


def observation(t: float) -> GeometrySample:
    return GeometrySample(
        frame=t,
        objects=[],
        pairs=[
            ClearanceSummary(
                left="A",
                right="B",
                minimum_distance=abs(t - 1.25),
                closest=None,
                contact_triangle_pairs=int(t == 1.25),
                exempt_triangle_pairs=0,
                left_representatives_inside_right=None,
                right_representatives_inside_left=None,
                contacts=[],
            )
        ],
    )


def test_midpoint_event_refines_without_unbounded_sampling() -> None:
    options = AdaptiveGeometryRange(start=1, end=2, max_samples=13, minimum_step=0.001)
    rows, coverage = sample(options, observation)
    assert any(row.frame == 1.25 and row.pairs[0].minimum_distance == 0 for row in rows)
    assert len(rows) <= 13 and len({row.frame for row in rows}) == len(rows)
    assert coverage.risky_intervals_remaining and coverage.stopped_by_budget
    assert sample(options, observation) == (rows, coverage)


def test_minimum_step_is_not_a_continuous_clearance_claim() -> None:
    rows, coverage = sample(
        AdaptiveGeometryRange(
            start=1,
            end=2,
            initial_samples=2,
            max_samples=64,
            minimum_step=0.125,
            near_clearance=10,
        ),
        observation,
    )
    assert coverage.risky_intervals_remaining > 0
    assert not coverage.stopped_by_budget
    assert coverage.maximum_gap <= 0.125
    assert len(rows) < 64


@pytest.mark.parametrize(
    "patch",
    [
        dict(adaptive=dict(start=1, end=1)),
        dict(adaptive=dict(start=1, end=2, initial_samples=4, max_samples=5)),
        dict(adaptive=dict(start=1, end=2), frames=[1]),
        dict(frames=[float("nan")]),
        dict(max_instance_vertices=2000001),
        dict(instances=[dict(object_name="A", obstacles=["A"])]),
    ],
)
def test_temporal_instance_bounds(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GeometryInspectArguments.model_validate(
            dict(objects=[dict(object_name="A")]) | patch
        )


def test_fractional_times_are_canonical() -> None:
    args = GeometryInspectArguments(
        objects=[dict(object_name="A")], frames=[1, 1.25, 1.5]
    )
    assert args.frames == [1, 1.25, 1.5]
