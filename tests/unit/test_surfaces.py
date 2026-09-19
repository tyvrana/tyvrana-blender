"""Contract limits and geometric rejection before native allocation."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from tests.surface_fixtures import fixtures
from tyvrana_blender.errors import OperationError
from tyvrana_blender.surface_geometry import (
    Curve,
    boundary_layout,
    opening_loops,
    topology,
)
from tyvrana_blender.surface_models import (
    SurfaceCreateArguments,
    SurfaceRevision,
    SurfaceSpec,
)


@pytest.mark.parametrize("index", range(4))
def test_sparse_contract_and_connected_layout(index: int) -> None:
    spec = SurfaceSpec.model_validate(fixtures()[index])
    layouts, signs, uses = boundary_layout(spec)
    assert len(layouts) == len(spec.patches) == len(signs)
    assert sum(len(v) == 2 for v in uses.values()) == len(spec.patches) - 1
    for opening in spec.openings:
        inner, outer = opening_loops(opening)
        assert len(inner) == len(outer)
    assert len(spec.model_dump_json()) < 10000


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_node",
        "duplicate_id",
        "zero_curve",
        "excess_nodes",
        "excess_controls",
        "excess_resolution",
        "nan",
    ],
)
def test_invalid_sparse_contract(fault: str) -> None:
    spec = fixtures()[0]
    if fault == "unknown_node":
        spec["curves"][0]["start"] = "missing"
    if fault == "duplicate_id":
        spec["curves"][0]["id"] = "a"
    if fault == "zero_curve":
        spec["nodes"][1]["point"] = spec["nodes"][0]["point"]
        spec["curves"][0]["through"] = []
    if fault == "excess_nodes":
        spec["nodes"] *= 33
    if fault == "excess_controls":
        spec["curves"][0]["through"] = [[1, 2, 3]] * 9
    if fault == "excess_resolution":
        spec["patches"][0]["resolution"] = 49
    if fault == "nan":
        spec["thickness"] = float("nan")
    with pytest.raises(ValidationError):
        SurfaceSpec.model_validate(spec)


def test_invalid_junction_and_curve_order() -> None:
    spec = fixtures()[1]
    spec["patches"].append({**deepcopy(spec["patches"][0]), "id": "extra"})
    with pytest.raises(OperationError, match="At most two"):
        boundary_layout(SurfaceSpec.model_validate(spec))
    spec = fixtures()[0]
    spec["patches"][0]["boundaries"][1:3] = reversed(
        spec["patches"][0]["boundaries"][1:3]
    )
    with pytest.raises(OperationError, match="ordered four-sided"):
        boundary_layout(SurfaceSpec.model_validate(spec))


def test_invalid_opening_and_nonmanifold_fan() -> None:
    spec = fixtures()[0]
    spec["openings"][0]["center"] = [0, 0]
    with pytest.raises(OperationError, match="strictly inside"):
        opening_loops(SurfaceSpec.model_validate(spec).openings[0])
    with pytest.raises(OperationError):
        topology([[0, 0, 0]] * 5, [[0, 1, 2], [0, 3, 4]], closed=False)


def test_curve_resampling_honors_endpoints_and_guides() -> None:
    spec = SurfaceSpec.model_validate(fixtures()[0])
    curve = Curve(spec.curves[0], {n.id: n.point for n in spec.nodes})
    assert curve.at(0) == pytest.approx(spec.nodes[0].point)
    assert curve.at(1) == pytest.approx(spec.nodes[1].point)
    assert curve.at(0.5)[1] < -1.5


def test_revision_batch_limits_and_required_intent() -> None:
    with pytest.raises(ValidationError):
        SurfaceRevision(name="x", expected_revision=1)
    with pytest.raises(ValidationError):
        SurfaceCreateArguments.model_validate({"surfaces": fixtures() + fixtures()})


def test_saved_constraints_revalidate_without_precision_changes() -> None:
    for data in fixtures():
        first = SurfaceSpec.model_validate(data)
        restored = SurfaceSpec.model_validate_json(first.model_dump_json())
        assert first == restored


def test_curve_tangents_remain_continuous_across_arc_samples() -> None:
    from tyvrana_blender.surface_geometry import sub, unit

    spec = SurfaceSpec.model_validate(fixtures()[0])
    curve = Curve(spec.curves[0], {n.id: n.point for n in spec.nodes})
    for t in curve.lengths[1:-1]:
        left = unit(sub(curve.at(t), curve.at(t - 1e-6)))
        right = unit(sub(curve.at(t + 1e-6), curve.at(t)))
        assert left == pytest.approx(right, abs=0.001)
