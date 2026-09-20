"""Public sculpt contracts reject ambiguous, unbounded and coercible input."""

from typing import Any

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender.operations import OPERATIONS, execute
from tyvrana_blender.sculpt_models import (
    RAYCAST,
    CameraRayArguments,
    SculptStrokeArguments,
    WorldRayArguments,
)

from .test_operations import Backend


def image_path() -> dict[str, Any]:
    identity = [[float(i == j) for j in range(4)] for i in range(4)]
    return {
        "view": {
            "width": 640,
            "height": 480,
            "camera_world": identity,
            "projection_matrix": identity,
        },
        "samples": [{"u": 0.5, "v": 0.5}],
    }


@pytest.mark.parametrize("bad", [-0.1, 1.1, True, "0.5", float("nan")])
def test_image_stroke_rejects_invalid_image_coordinates(bad: Any) -> None:
    path = image_path()
    path["samples"][0]["u"] = bad
    with pytest.raises(ValidationError):
        SculptStrokeArguments.model_validate(
            {
                "object_name": "Surface",
                "brush": "draw",
                "radius": 0.1,
                "strength": 0.5,
                "image_path": path,
            }
        )


def test_image_stroke_exclusive_bounded_target_and_motion() -> None:
    data: dict[str, Any] = {
        "object_name": "Surface",
        "brush": "draw",
        "radius": 0.1,
        "strength": 0.5,
        "image_path": image_path(),
    }
    assert not SculptStrokeArguments.model_validate(data).samples
    with pytest.raises(ValidationError, match="exclusively"):
        SculptStrokeArguments.model_validate(
            {**data, "samples": [{"location": [0, 0, 0]}]}
        )
    with pytest.raises(ValidationError, match="distinct"):
        SculptStrokeArguments.model_validate({**data, "brush": "flatten"})
    data["image_path"]["samples"] *= 257
    with pytest.raises(ValidationError):
        SculptStrokeArguments.model_validate(data)


VALID: dict[str, dict[str, Any]] = {
    "sculpt.mask.inspect": {"object_name": "Surface"},
    "sculpt.mask.clear": {"object_name": "Surface"},
    "sculpt.mask.invert": {"object_name": "Surface"},
    "sculpt.mask.stroke": {
        "object_name": "Surface",
        "mode": "add",
        "samples": [{"location": [0, 0, 1]}],
        "radius": 0.3,
        "strength": 0.5,
    },
    "sculpt.face_sets.inspect": {"object_name": "Surface"},
    "sculpt.face_sets.assign": {
        "object_name": "Surface",
        "selector": {"mode": "all", "domain": "face"},
    },
    "sculpt.face_sets.initialize": {"object_name": "Surface", "mode": "loose_parts"},
    "sculpt.filter": {"object_name": "Surface", "type": "smooth", "strength": 0.5},
    "scene.raycast": {"mode": "camera", "u": 0.5, "v": 0.5},
    "multires.inspect": {"object_name": "Surface"},
    "multires.create": {"object_name": "Surface"},
    "multires.subdivide": {"object_name": "Surface", "levels": 2},
    "multires.configure": {"object_name": "Surface", "sculpt_level": 1},
    "sculpt.inspect": {"object_name": "Surface"},
    "sculpt.stroke": {
        "object_name": "Surface",
        "brush": "draw",
        "samples": [{"location": [0, 0, 1]}],
        "radius": 0.3,
        "strength": 0.5,
    },
}


def response(operation: str, arguments: dict[str, Any], backend: Backend) -> Any:
    return execute(
        backend,
        OperationRequest.model_construct(
            type="operation.request",
            request_id="sculpt",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )


@pytest.mark.parametrize("operation", VALID)
def test_dispatch_reaches_exact_typed_backend(operation: str) -> None:
    backend = Backend()
    result = response(operation, VALID[operation], backend)
    assert isinstance(result, OperationSuccess), result
    assert backend.calls[0] == operation.replace(".", "_")
    assert "blender." + operation in OPERATIONS
    assert tuple(sorted(OPERATIONS)) == OPERATIONS


@pytest.mark.parametrize("operation", VALID)
@pytest.mark.parametrize(
    "patch",
    [
        {"unexpected": 1},
        {"object_name": None},
        {"object_name": "bad\x00name"},
        {"object_name": "\ud800"},
        {"object_name": " "},
    ],
)
def test_invalid_fields_never_reach_backend(
    operation: str, patch: dict[str, Any]
) -> None:
    backend = Backend()
    result = response(operation, {**VALID[operation], **patch}, backend)
    assert isinstance(result, OperationFailure)
    assert result.error.code == "invalid_arguments"
    assert not backend.calls


@pytest.mark.parametrize("field", ["u", "v"])
@pytest.mark.parametrize(
    "value", [-0.1, 1.1, True, "0.5", None, float("nan"), float("inf")]
)
def test_camera_coordinate_validation(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        RAYCAST.validate_python({"mode": "camera", "u": 0.5, "v": 0.5, field: value})


@pytest.mark.parametrize(
    "patch",
    [
        {"direction": [0, 0, 0]},
        {"direction": [True, 0, 1]},
        {"origin": [0, 0]},
        {"max_distance": 0},
        {"max_distance": float("inf")},
        {"camera_name": "Camera"},
    ],
)
def test_world_ray_invalid_values(patch: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RAYCAST.validate_python(
            {"mode": "world", "origin": [0, 0, 0], "direction": [0, 0, -1], **patch}
        )


def test_dimensions_pair_and_unscaled_direction() -> None:
    with pytest.raises(ValueError):
        CameraRayArguments(mode="camera", u=0.5, v=0.5, width=512)
    assert WorldRayArguments(
        mode="world", origin=[0, 0, 0], direction=[0, 0, -20]
    ).direction == [0, 0, -20]


@pytest.mark.parametrize(
    "operation,field",
    [
        ("multires.subdivide", "levels"),
        ("multires.configure", "viewport_level"),
        ("multires.configure", "sculpt_level"),
        ("multires.configure", "render_level"),
    ],
)
@pytest.mark.parametrize("value", [-1, 7, 1000000, 1.2, "2", True, None])
def test_level_bounds_before_host_access(
    operation: str, field: str, value: Any
) -> None:
    backend = Backend()
    result = response(operation, {**VALID[operation], field: value}, backend)
    assert isinstance(result, OperationFailure)
    assert not backend.calls


@pytest.mark.parametrize(
    "patch",
    [
        {"brush": "flatten"},
        {"brush": "grab"},
        {"brush": "snake_hook"},
        {"brush": "pose"},
        {"brush": "paint"},
        {"radius": 0},
        {"radius": 0.0001},
        {"radius": 1e20},
        {"radius": True},
        {"radius": "1"},
        {"strength": -1},
        {"strength": 1.01},
        {"strength": "0.5"},
        {"strength": True},
        {"invert": 1},
        {"symmetry": {"x": 1}},
        {"samples": []},
        {"samples": [{"location": [0, 0, 0]}] * 257},
        *[
            {"samples": [{"location": [0, 0, 0], "pressure": v}]}
            for v in [-0.1, 1.1, True, "1", None, float("nan")]
        ],
        {"samples": [{"location": [float("inf"), 0, 0]}]},
        {"samples": [{"location": [0, 0, 0], "mouse": [0, 0]}]},
    ],
)
def test_stroke_validation(patch: dict[str, Any]) -> None:
    backend = Backend()
    result = response("sculpt.stroke", {**VALID["sculpt.stroke"], **patch}, backend)
    assert isinstance(result, OperationFailure)
    assert result.error.code == "invalid_arguments"
    assert not backend.calls


@pytest.mark.parametrize(
    "brush", ["draw", "smooth", "inflate", "clay", "crease", "flatten"]
)
def test_six_brushes_deterministic_defaults(brush: str) -> None:
    args = SculptStrokeArguments.model_validate(
        {
            **VALID["sculpt.stroke"],
            "brush": brush,
            "samples": [{"location": [0, 0, 1]}, {"location": [0.1, 0, 1]}],
        }
    )
    assert args.symmetry.model_dump() == {"x": False, "y": False, "z": False}
    assert args.samples[0].pressure == 1
    assert not args.invert


@pytest.mark.parametrize(
    "operation,patch",
    [
        *[
            ("sculpt.mask.stroke", {"mode": v})
            for v in ["draw", "invert", "MASK", None, True]
        ],
        *[
            ("sculpt.mask.stroke", {"samples": v})
            for v in [
                [],
                [{"location": [0, 0, 1]}] * 257,
                [{"location": [0, 0, 1], "pressure": 1.1}],
                [{"location": [0, 0, float("nan")]}],
            ]
        ],
        *[
            ("sculpt.mask.stroke", {field: v})
            for field in ["radius", "strength"]
            for v in [True, "0.2", None, -0.1, float("nan"), float("inf")]
        ],
        *[
            ("sculpt.face_sets.assign", {"face_set_id": v})
            for v in [0, -1, 2**31, True, "2", 1.5, None]
        ],
        ("sculpt.face_sets.assign", {"selector": {"mode": "all", "domain": "vertex"}}),
        *[
            ("sculpt.face_sets.initialize", {"mode": v})
            for v in ["normals", "masked", "random", None, True]
        ],
        *[
            ("sculpt.filter", {"type": v})
            for v in ["random", "sharpen", "sphere", "erase_displacement", "view", None]
        ],
        *[
            ("sculpt.filter", {"strength": v})
            for v in [-0.1, 1.1, True, "0.5", None, float("nan"), float("inf")]
        ],
        *[
            ("sculpt.filter", {"iterations": v})
            for v in [0, -1, 101, 1.5, True, "3", None]
        ],
        ("sculpt.filter", {"axes": {"x": False, "y": False, "z": False}}),
        ("sculpt.filter", {"axes": {"x": 1}}),
        ("sculpt.filter", {"orientation": "view"}),
        ("sculpt.filter", {"type": "scale", "strength": -1}),
        ("sculpt.filter", {"type": "scale", "iterations": 2}),
        ("sculpt.filter", {"type": "inflate", "iterations": 2}),
    ],
)
def test_regional_invalid_arguments_never_reach_host(
    operation: str, patch: dict[str, Any]
) -> None:
    backend = Backend()
    result = response(operation, {**VALID[operation], **patch}, backend)
    assert isinstance(result, OperationFailure), result
    assert result.error.code == "invalid_arguments"
    assert not backend.calls


@pytest.mark.parametrize(
    "kind", ["smooth", "surface_smooth", "relax", "inflate", "scale"]
)
def test_native_filter_contracts(kind: str) -> None:
    backend = Backend()
    strength = -0.2 if kind in {"inflate", "scale"} else 0.5
    result = response(
        "sculpt.filter",
        {**VALID["sculpt.filter"], "type": kind, "strength": strength},
        backend,
    )
    assert isinstance(result, OperationSuccess)
    assert backend.calls[0] == "sculpt_filter"
