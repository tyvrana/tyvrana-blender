"""Retopology dispatch, canonical names, strict inputs and bounded work settings."""

from typing import Any

import pytest
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender import retopo_models as models
from tyvrana_blender.operations import OPERATIONS, execute

from .test_operations import Backend

BASE = {"source_object": "Surface", "target_object": "Cage"}
VERTICES = {"mode": "all", "domain": "vertex"}
EDGES = {"mode": "boundary", "domain": "edge"}
CASES: list[tuple[str, Any, dict[str, Any]]] = [
    ("create_target", models.RetopoCreateArguments, {"source_object": "Surface"}),
    ("inspect", models.RetopoInspectArguments, BASE),
    (
        "seed_patch",
        models.RetopoSeedArguments,
        {
            **BASE,
            "center": [0, 0, 1],
            "tangent_direction": [1, 0, 0],
            "width": 0.5,
            "height": 0.5,
        },
    ),
    ("project", models.RetopoProjectArguments, {**BASE, "selector": VERTICES}),
    ("relax", models.RetopoRelaxArguments, {**BASE, "selector": VERTICES}),
    (
        "extrude_boundary",
        models.RetopoExtrudeArguments,
        {**BASE, "selector": EDGES, "offset": [1, 0, 0]},
    ),
    (
        "bridge_loops",
        models.RetopoBridgeArguments,
        {**BASE, "loop_a": EDGES, "loop_b": EDGES},
    ),
]


@pytest.mark.parametrize("name,model,arguments", CASES)
def test_canonical_dispatch(name: str, model: Any, arguments: dict[str, Any]) -> None:
    backend = Backend()
    response = execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="retopo",
            operation="blender.retopo." + name,
            arguments=arguments,
        ),
    )
    assert isinstance(response, OperationSuccess), response
    expected = (
        "retopo_create_target"
        if name == "create_target"
        else "retopo_inspect"
        if name == "inspect"
        else model.__name__
    )
    assert backend.calls[0] == expected
    assert len(OPERATIONS) == 73 and tuple(sorted(OPERATIONS)) == OPERATIONS
    if name == "inspect":
        assert "spatial QA" in str(response.result)
        assert "separate authored low-poly target" in str(response.result)


@pytest.mark.parametrize("name,model,arguments", CASES)
@pytest.mark.parametrize("value", [None, True, "", " ", "bad\x00name", "\ud800"])
def test_source_name_rejections(
    name: str, model: Any, arguments: dict[str, Any], value: Any
) -> None:
    with pytest.raises(ValueError):
        model.model_validate({**arguments, "source_object": value})


@pytest.mark.parametrize("name,model,arguments", CASES)
def test_unknown_null_and_same_target(
    name: str, model: Any, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        model.model_validate({**arguments, "unknown": 1})
    for field in arguments:
        with pytest.raises(ValueError):
            model.model_validate({**arguments, field: None})
    if name != "create_target":
        with pytest.raises(ValueError):
            model.model_validate({**arguments, "target_object": "Surface"})


@pytest.mark.parametrize(
    "field,values",
    [
        ("width", [0, -1, 0.00001, 1001, True, "1", None, float("nan"), float("inf")]),
        ("height", [0, 1001]),
        ("u_segments", [0, 17, True, 1.5, "1"]),
        ("v_segments", [0, 17]),
        ("center", [[0, 0], [0, 0, float("inf")], [True, 0, 0], ["0", 0, 0]]),
        ("tangent_direction", [[0, 0, 0], [0, 0, float("nan")]]),
        ("surface_offset", [-1.01, 1.01, True, "0", None]),
        ("max_projection_distance", [0, -1, 1001, True, "1", None, float("inf")]),
    ],
)
def test_seed_limits(field: str, values: list[Any]) -> None:
    for value in values:
        with pytest.raises(ValueError):
            models.RetopoSeedArguments.model_validate({**CASES[2][2], field: value})
    smallest = models.RetopoSeedArguments.model_validate(
        {**CASES[2][2], "width": 0.0001}
    )
    assert models.RetopoSeedArguments.model_validate(smallest.model_dump()) == smallest


@pytest.mark.parametrize(
    "model,arguments,field,values",
    [
        (models.RetopoProjectArguments, CASES[3][2], "selector", [EDGES]),
        (models.RetopoProjectArguments, CASES[3][2], "mode", ["directional", None]),
        (
            models.RetopoRelaxArguments,
            CASES[4][2],
            "iterations",
            [0, 51, True, 1.5, "5"],
        ),
        (models.RetopoRelaxArguments, CASES[4][2], "factor", [0, -1, 1.1, True, None]),
        (
            models.RetopoRelaxArguments,
            CASES[4][2],
            "preserve_boundary",
            [0, 1, "true", None],
        ),
        (models.RetopoExtrudeArguments, CASES[5][2], "selector", [VERTICES]),
        (
            models.RetopoExtrudeArguments,
            CASES[5][2],
            "offset",
            [[0, 0, 0], [1001, 0, 0]],
        ),
        (models.RetopoBridgeArguments, CASES[6][2], "loop_a", [VERTICES]),
        (models.RetopoBridgeArguments, CASES[6][2], "segments", [0, 17, True, "2"]),
        (models.RetopoBridgeArguments, CASES[6][2], "twist", [-128, 128, True, 1.5]),
    ],
)
def test_growth_projection_relaxation_limits(
    model: Any, arguments: dict[str, Any], field: str, values: list[Any]
) -> None:
    for value in values:
        with pytest.raises(ValueError):
            model.model_validate({**arguments, field: value})


def test_invalid_call_never_reaches_backend() -> None:
    backend = Backend()
    result = execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="bad",
            operation="blender.retopo.seed_patch",
            arguments={"source_object": "Surface", "target_object": "Surface"},
        ),
    )
    assert (
        isinstance(result, OperationFailure)
        and result.error.code == "invalid_arguments"
    )
    assert not backend.calls
