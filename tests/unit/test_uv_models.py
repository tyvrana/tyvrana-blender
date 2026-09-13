import math

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender.models import Model
from tyvrana_blender.operations import OPERATIONS, OperationError, execute
from tyvrana_blender.uv_models import (
    UVCreateArguments,
    UVInspectArguments,
    UVInspectResult,
    UVPackArguments,
    UVSetActiveArguments,
    UVUnwrapArguments,
    map_summary,
)

from .test_operations import Backend


def test_bounded_summaries_empty_collapsed_and_outside() -> None:
    empty = map_summary("Map", True, True, [], 0)
    assert empty.loop_count == 0 and empty.uv_min is None and empty.uv_max is None
    collapsed = map_summary("Map", True, False, [(0.5, 0.5)] * 1000, 12)
    assert collapsed.loop_count == 1000 and collapsed.uv_min == collapsed.uv_max == [
        0.5,
        0.5,
    ]
    assert collapsed.pinned_count == 12 and collapsed.out_of_unit_square_count == 0
    outside = map_summary(
        "Map", False, True, [(-2, 4), (0, 0), (1, 1), (-1e-8, 1 + 1e-8)], 0
    )
    assert outside.uv_min == [-2, 0] and outside.uv_max == [1, 4]
    assert outside.out_of_unit_square_count == 1
    result = UVInspectResult(
        object_name="Mesh",
        active_map="Map",
        active_render_map="Map",
        mesh_users=2,
        maps=[collapsed],
    )
    assert UVInspectResult.model_validate_json(result.model_dump_json()) == result
    assert len(result.model_dump_json()) < 400


@pytest.mark.parametrize(
    "method",
    [
        "angle_based",
        "conformal",
        "smart_project",
        "cube_project",
        "cylinder_project",
        "sphere_project",
    ],
)
def test_canonical_unwrap_methods(method: str) -> None:
    model = UVUnwrapArguments.model_validate({"object_name": "Mesh", "method": method})
    assert model.method == method


@pytest.mark.parametrize(
    "model,arguments",
    [
        (UVInspectArguments, {"object_name": "Mesh"}),
        (
            UVCreateArguments,
            {
                "object_name": "Mesh",
                "name": "Detail",
                "set_active": False,
                "set_render": True,
            },
        ),
        (
            UVSetActiveArguments,
            {"object_name": "Mesh", "name": "Detail", "set_active": False},
        ),
        (
            UVUnwrapArguments,
            {
                "object_name": "Mesh",
                "method": "angle_based",
                "margin": 1,
                "fill_holes": True,
            },
        ),
        (
            UVUnwrapArguments,
            {"object_name": "Mesh", "method": "conformal", "margin": 0},
        ),
        (
            UVUnwrapArguments,
            {
                "object_name": "Mesh",
                "method": "smart_project",
                "angle_limit": math.pi / 2,
                "island_margin": 1,
                "area_weight": 1,
            },
        ),
        (
            UVUnwrapArguments,
            {
                "object_name": "Mesh",
                "method": "cube_project",
                "cube_size": 2,
                "scale_to_bounds": True,
            },
        ),
        (
            UVPackArguments,
            {"object_name": "Mesh", "margin": 0, "rotate": False, "scale": False},
        ),
    ],
)
def test_argument_shapes_and_schemas(
    model: type[Model], arguments: dict[str, object]
) -> None:
    value = model.model_validate(arguments)
    assert model.model_validate_json(value.model_dump_json(exclude_none=True)) == value
    assert model.model_json_schema()["additionalProperties"] is False
    for key in arguments:
        with pytest.raises(ValidationError):
            model.model_validate({**arguments, key: None})
    with pytest.raises(ValidationError):
        model.model_validate({**arguments, "unexpected": True})


@pytest.mark.parametrize(
    "field,method",
    [
        ("margin", "angle_based"),
        ("angle_limit", "smart_project"),
        ("area_weight", "smart_project"),
        ("island_margin", "smart_project"),
        ("cube_size", "cube_project"),
    ],
)
@pytest.mark.parametrize("value", ["0.2", True, -0.1, math.nan, math.inf, -math.inf])
def test_numeric_coercion_and_nonfinite_rejected(
    field: str, method: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        UVUnwrapArguments.model_validate(
            {"object_name": "Mesh", "method": method, field: value}
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"object_name": " "},
        {"object_name": "Mesh", "method": "view_project"},
        {"object_name": "Mesh", "method": "smart_project", "angle_limit": 1.58},
        {"object_name": "Mesh", "method": "smart_project", "area_weight": 1.01},
        {"object_name": "Mesh", "method": "angle_based", "island_margin": 0.1},
        {"object_name": "Mesh", "method": "cube_project", "fill_holes": True},
        {"object_name": "Mesh", "method": "sphere_project", "cube_size": 1},
        {"object_name": "Mesh", "method": "conformal", "margin": 1.01},
        {"object_name": "Mesh", "method": "smart_project", "correct_aspect": 1},
        {"object_name": "Mesh", "method": "smart_project", "uv_map": None},
    ],
)
def test_invalid_methods_settings_and_names(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UVUnwrapArguments.model_validate(arguments)


@pytest.mark.parametrize("value", [None, "0.1", True, -0.1, 1.1, math.nan, math.inf])
def test_pack_margin_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        UVPackArguments.model_validate({"object_name": "Mesh", "margin": value})


def test_active_flags_must_select_at_least_one_role() -> None:
    with pytest.raises(ValidationError):
        UVSetActiveArguments(
            object_name="Mesh", name="Map", set_active=False, set_render=False
        )
    assert UVSetActiveArguments(object_name="Mesh", name="Map").set_render
    assert UVCreateArguments(object_name="Mesh").set_render is None


@pytest.mark.parametrize(
    "operation,arguments,call",
    [
        ("blender.uv.inspect", {"object_name": "Mesh"}, "uv_inspect"),
        ("blender.uv.create_map", {"object_name": "Mesh"}, "uv_create"),
        (
            "blender.uv.set_active",
            {"object_name": "Mesh", "name": "Map"},
            "uv_set_active",
        ),
        (
            "blender.uv.unwrap",
            {"object_name": "Mesh", "method": "smart_project"},
            "uv_unwrap",
        ),
        ("blender.uv.pack_islands", {"object_name": "Mesh"}, "uv_pack"),
    ],
)
def test_registered_dispatch(
    operation: str, arguments: dict[str, object], call: str
) -> None:
    backend = Backend()
    message = OperationRequest.model_validate(
        {
            "type": "operation.request",
            "request_id": "test",
            "operation": operation,
            "arguments": arguments,
        }
    )
    result = execute(backend, message)
    assert operation in OPERATIONS and isinstance(result, OperationSuccess)
    assert backend.calls == [call]


@pytest.mark.parametrize(
    "code",
    ["object_not_mesh", "uv_map_not_found", "invalid_context", "uv_unwrap_failed"],
)
def test_uv_errors_remain_typed(code: str) -> None:
    class FailingBackend(Backend):
        def uv_inspect(self, arguments: UVInspectArguments) -> UVInspectResult:
            raise OperationError(code, "Cannot inspect UV map")

    result = execute(
        FailingBackend(),
        OperationRequest(
            type="operation.request",
            request_id="test",
            operation="blender.uv.inspect",
            arguments={"object_name": "Mesh"},
        ),
    )
    assert isinstance(result, OperationFailure) and result.error.code == code
