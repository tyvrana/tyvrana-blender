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
    ("insert_loop", models.RetopoInsertArguments, {**BASE, "edge": EDGES}),
    (
        "slide",
        models.RetopoSlideArguments,
        {**BASE, "selector": EDGES, "toward_vertex": 1, "factor": 0.25},
    ),
    ("subdivide", models.RetopoSubdivideArguments, {**BASE, "selector": EDGES}),
    ("collapse", models.RetopoCollapseArguments, {**BASE, "selector": EDGES}),
    ("rotate_edge", models.RetopoRotateArguments, {**BASE, "edge": EDGES}),
    (
        "stitch",
        models.RetopoStitchArguments,
        {**BASE, "chain_a": EDGES, "chain_b": EDGES, "start_a": 0, "start_b": 1},
    ),
    (
        "fill_boundary",
        models.RetopoFillArguments,
        {**BASE, "boundary": EDGES, "corner_vertex": 0, "span": 2},
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
    assert len(OPERATIONS) == 82 and tuple(sorted(OPERATIONS)) == OPERATIONS
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


@pytest.mark.parametrize(
    "case,field,values",
    [
        (7, "factor", [0, 1, -0.1, 0.25, True, float("nan"), float("inf")]),
        (7, "from_vertex", [-1, True, 1.5, None]),
        (7, "edge", [VERTICES]),
        (8, "factor", [-0.1, 1, True, "0.5", float("nan")]),
        (8, "toward_vertex", [-1, True, 1.5]),
        (8, "selector", [{"mode": "all", "domain": "face"}]),
        (9, "cuts", [0, 5, True, 1.5, "2"]),
        (9, "selector", [VERTICES]),
        (10, "mode", ["first", "second", "dissolve", None]),
        (10, "allow_boundary", [0, 1, "true", None]),
        (11, "direction", ["CW", "left", True, None]),
        (11, "edge", [VERTICES]),
        (12, "max_weld_distance", [0, -1, 1.1, True, float("inf")]),
        (12, "start_a", [-1, True, 1.5]),
        (12, "start_b", [-1, True, 1.5]),
        (12, "chain_b", [VERTICES]),
        (13, "span", [0, 64, True, 1.5]),
        (13, "corner_vertex", [-1, True, 1.5]),
        (13, "mode", ["ngon", "auto", None]),
        (13, "boundary", [VERTICES]),
    ],
)
def test_finishing_contract_limits(case: int, field: str, values: list[Any]) -> None:
    _, model, args = CASES[case]
    for value in values:
        with pytest.raises(ValueError):
            model.model_validate({**args, field: value})


def test_insert_explicit_orientation_and_finishing_guidance() -> None:
    arguments = models.RetopoInsertArguments.model_validate(
        {**CASES[7][2], "factor": 0.25, "from_vertex": 0}
    )
    assert (
        models.RetopoInsertArguments.model_validate(
            arguments.model_dump(exclude_none=True)
        )
        == arguments
    )
    assert "toward_vertex" in models.GUIDANCE and "start_a/start_b" in models.GUIDANCE
    assert "changes valence without moving vertices" in models.GUIDANCE
