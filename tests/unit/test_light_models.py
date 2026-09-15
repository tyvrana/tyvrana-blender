import json

import pytest
from pydantic import ValidationError
from tyvrana_protocol import JsonValue, OperationFailure, OperationSuccess

from tyvrana_blender.light_models import (
    ANGLE_MAX,
    LIGHT_TYPES,
    SPOT_MIN,
    LightConfigureArguments,
    LightCreateArguments,
    LightInspectResult,
    LightSummary,
    LightType,
    light_state,
)
from tyvrana_blender.numeric import FLOAT32_MAX, binary32
from tyvrana_blender.operations import OPERATIONS, OperationError

from .test_operations import EMPTY_PAGE, Backend, call, light_summary


@pytest.mark.parametrize("kind", ["point", "sun", "spot", "area"])
def test_native_creation_defaults_and_serialized_summary(kind: LightType) -> None:
    arguments = LightCreateArguments(type=kind)
    state = arguments.settings_state()
    assert state.energy == 10 and state.color == [1, 1, 1]
    assert state.normalize and state.use_shadow and state.exposure == 0
    assert arguments.location == arguments.rotation == [0, 0, 0]
    assert arguments.scale == [1, 1, 1]
    summary = LightSummary.model_validate(
        {**light_summary("Light").model_dump(), **state.model_dump()}
    )
    assert LightSummary.model_validate_json(summary.model_dump_json()) == summary
    assert (
        json.loads(summary.model_dump_json())["settings"] == state.settings.model_dump()
    )
    assert (
        set(state.settings.model_dump())
        == {
            "point": {"radius"},
            "sun": {"angle"},
            "spot": {"radius", "spot_size", "spot_blend"},
            "area": {"shape", "size"},
        }[kind]
    )
    assert LIGHT_TYPES[kind.upper()] == kind


def test_empty_inspection_and_registration() -> None:
    assert LightInspectResult(lights=[], page=EMPTY_PAGE).model_dump() == {
        "lights": [],
        "page": EMPTY_PAGE.model_dump(),
    }
    assert [op for op in OPERATIONS if op.startswith("blender.light.")] == [
        "blender.light.configure",
        "blender.light.create",
        "blender.light.inspect",
    ]
    assert tuple(sorted(OPERATIONS)) == OPERATIONS
    result = call(Backend(), "blender.light.inspect", {})
    assert isinstance(result, OperationSuccess) and result.result == {
        "lights": [],
        "page": EMPTY_PAGE.model_dump(),
    }


@pytest.mark.parametrize(
    "field",
    [
        "energy",
        "exposure",
        "radius",
        "angle",
        "spot_size",
        "spot_blend",
        "size",
        "size_y",
    ],
)
@pytest.mark.parametrize(
    "value", [None, True, "2", float("nan"), float("inf"), -float("inf"), 1e300, 1e-60]
)
def test_invalid_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        LightConfigureArguments.model_validate({"name": "Light", field: value})


@pytest.mark.parametrize(
    "value",
    [
        None,
        [0, 0],
        [1, 2, 3, 4],
        [-0.01, 0, 0],
        [True, 0, 0],
        ["1", 0, 0],
        [float("nan"), 0, 0],
        [float("inf"), 0, 0],
        [1e300, 0, 0],
        [1e-60, 0, 0],
    ],
)
def test_invalid_color(value: object) -> None:
    with pytest.raises(ValidationError):
        LightCreateArguments.model_validate({"type": "point", "color": value})


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "POINT"},
        {"type": "laser"},
        {"type": None},
        {"type": "point", "name": None},
        {"type": "sun", "name": " "},
        {"type": "area", "shape": "triangle"},
        {"type": "point", "angle": 0},
        {"type": "sun", "radius": 0},
        {"type": "spot", "shape": "disk"},
        {"type": "area", "spot_size": 1},
        {"type": "area", "radius": 0},
        {"type": "area", "size_y": 2},
        {"type": "area", "shape": "disk", "size_y": 2},
        {"type": "point", "scale": [1, 2]},
        {"type": "point", "rotation": [0, 0, 1e300]},
        {"type": "point", "location": [True, 0, 0]},
        {"type": "point", "normalize": "true"},
        {"type": "sun", "use_shadow": 1},
    ],
)
def test_invalid_create_combinations(fields: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        LightCreateArguments.model_validate(fields)
    response = call(Backend(), "blender.light.create", fields)
    assert (
        isinstance(response, OperationFailure)
        and response.error.code == "invalid_arguments"
    )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"name": ""},
        {"name": "  "},
        {"name": None},
        {"name": "Light", "type": "point"},
        {"name": "Light", "shape": "DISK"},
        {"name": "Light", "location": [0, 0, 0]},
        {"name": "Light", "use_shadow": None},
        {"name": "Light", "normalize": False, "unknown": 1},
    ],
)
def test_invalid_configure_shape(fields: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        LightConfigureArguments.model_validate(fields)


@pytest.mark.parametrize(
    "field, minimum, maximum",
    [
        ("energy", -FLOAT32_MAX, FLOAT32_MAX),
        ("exposure", -32, 32),
        ("radius", 0, FLOAT32_MAX),
        ("angle", 0, ANGLE_MAX),
        ("spot_size", SPOT_MIN, ANGLE_MAX),
        ("spot_blend", 0, 1),
        ("size", 0, FLOAT32_MAX),
        ("size_y", 0, FLOAT32_MAX),
    ],
)
def test_exact_hard_limits(field: str, minimum: float, maximum: float) -> None:
    for value in (minimum, maximum):
        model = LightConfigureArguments.model_validate({"name": "Light", field: value})
        assert getattr(model, field) == value
    for value in (minimum - max(abs(minimum) * 0.01, 0.00001), maximum * 1.001):
        with pytest.raises(ValidationError):
            LightConfigureArguments.model_validate({"name": "Light", field: value})


def test_hdr_color_negative_power_and_precision() -> None:
    state = LightCreateArguments(
        type="point", color=[4, 2, 0.1], energy=-20, radius=150
    ).settings_state()
    assert state.color == [4, 2, binary32(0.1)] and state.energy == -20
    assert state.settings.model_dump()["radius"] == 150
    assert LightConfigureArguments(name="Light", energy=1e-40).energy != 0
    assert LightConfigureArguments(name="Light", color=[FLOAT32_MAX, 0, 0]).color == [
        FLOAT32_MAX,
        0,
        0,
    ]


@pytest.mark.parametrize("shape", ["square", "disk", "rectangle", "ellipse"])
def test_area_shapes_and_combined_partial_validation(shape: str) -> None:
    values: dict[str, JsonValue] = {"shape": shape, "size": 2}
    if shape in ("rectangle", "ellipse"):
        values["size_y"] = 3
    state = light_state("area", values, set(values))
    patch = LightConfigureArguments(name="Light", energy=40)
    assert patch.model_dump(exclude_unset=True) == {"name": "Light", "energy": 40}
    changed = light_state("area", {**state.writable_values(), "energy": 40}, {"energy"})
    assert changed.settings == state.settings and state.energy == 10
    with pytest.raises(ValueError):
        light_state(
            "area",
            {**state.writable_values(), "shape": "disk", "size_y": 3},
            {"shape", "size_y"},
        )
    assert state.settings.model_dump()["shape"] == shape


def test_mismatched_summary_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LightSummary.model_validate(
            {**light_summary("Light").model_dump(), "type": "sun"}
        )


def test_object_not_light_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def wrong_type(arguments: LightConfigureArguments) -> LightSummary:
        raise OperationError(
            "object_not_light", "Object is not a light", {"name": arguments.name}
        )

    backend = Backend()
    monkeypatch.setattr(backend, "light_configure", wrong_type)
    result = call(backend, "blender.light.configure", {"name": "Cube", "energy": 30})
    assert (
        isinstance(result, OperationFailure) and result.error.code == "object_not_light"
    )
    assert result.error.details == {"name": "Cube"}
