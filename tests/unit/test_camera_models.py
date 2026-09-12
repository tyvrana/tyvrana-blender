import json

import pytest
from pydantic import ValidationError
from tyvrana_protocol import JsonValue, OperationFailure, OperationSuccess

from tyvrana_blender.camera_models import (
    CLIP_MIN,
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraProperties,
    CameraSetActiveArguments,
    CameraSummary,
    normalize_projection,
)
from tyvrana_blender.models import Model
from tyvrana_blender.numeric import FLOAT32_MAX
from tyvrana_blender.operations import OPERATIONS, OperationError, registration

from .test_operations import Backend, call, camera_summary


def test_default_perspective_and_orthographic_creation() -> None:
    default = CameraCreateArguments()
    assert default.projection == "perspective" and default.lens_mm == 50
    assert default.clip_start == 0.1 and default.clip_end == 1000
    assert default.location == default.rotation == [0, 0, 0]
    assert default.scale == [1, 1, 1] and not default.make_active
    ortho = CameraCreateArguments(projection="orthographic", ortho_scale=12)
    assert ortho.ortho_scale == 12


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("PERSP", "perspective"),
        ("ORTHO", "orthographic"),
        ("PANO", "panoramic"),
        ("CUSTOM", "custom"),
    ],
)
def test_projection_normalization(native: str, expected: str) -> None:
    assert normalize_projection(native) == expected


def test_summary_serialization_and_empty_inspection() -> None:
    empty = CameraInspectResult(active_camera=None, cameras=[])
    assert json.loads(empty.model_dump_json()) == {"active_camera": None, "cameras": []}
    summary = camera_summary("Camera")
    result = CameraInspectResult(active_camera=summary.name, cameras=[summary])
    assert CameraInspectResult.model_validate_json(result.model_dump_json()) == result
    assert result.model_dump(mode="json")["cameras"][0]["ortho_scale"] is None
    for mode in ("panoramic", "custom"):
        raw = summary.model_dump() | {"projection": mode, "lens_mm": None}
        assert CameraSummary.model_validate(raw).lens_mm is None
    with pytest.raises(ValidationError):
        CameraSummary.model_validate(summary.model_dump() | {"shift_x": float("inf")})


@pytest.mark.parametrize(
    "fields",
    [
        {"lens_mm": 0.99},
        {"ortho_scale": 0},
        {"ortho_scale": -1},
        {"clip_start": 0},
        {"clip_start": CLIP_MIN / 2},
        {"clip_end": 0},
        {"clip_start": 2, "clip_end": 1},
        {"clip_start": 2, "clip_end": 2},
        {"clip_start": 1, "clip_end": 1.000000001},
        {"shift_x": FLOAT32_MAX * 2},
        {"shift_y": -FLOAT32_MAX * 2},
        {"projection": "panoramic"},
        {"projection": "custom"},
        {"projection": "PERSP"},
        {"unknown": 3},
        {"location": [1, 2, 3]},
    ],
)
def test_invalid_configuration_arguments(fields: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        CameraConfigureArguments.model_validate({"name": "Camera", **fields})


@pytest.mark.parametrize(
    "field", ["lens_mm", "ortho_scale", "clip_start", "clip_end", "shift_x", "shift_y"]
)
@pytest.mark.parametrize(
    "value", [None, float("nan"), float("inf"), -float("inf"), "2", True, 1e300, 1e-60]
)
def test_invalid_numeric_inputs(field: str, value: object) -> None:
    cases: list[tuple[type[Model], dict[str, str]]] = [
        (CameraConfigureArguments, {"name": "Camera"}),
        (CameraCreateArguments, {}),
    ]
    for model, base in cases:
        with pytest.raises(ValidationError):
            model.model_validate({**base, field: value})


@pytest.mark.parametrize(
    "fields",
    [
        {"name": None},
        {"name": " "},
        {"make_active": None},
        {"make_active": "true"},
        {"location": [1, 2]},
        {"rotation": None},
        {"scale": [1, 2, 1e300]},
        {"ortho_scale": 6},
        {"projection": "orthographic", "lens_mm": 50},
        {"projection": "panoramic"},
        {"clip_start": 2000},
    ],
)
def test_invalid_creation(fields: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        CameraCreateArguments.model_validate(fields)


def test_partial_configuration_retains_omitted_fields() -> None:
    patch = CameraConfigureArguments(name="Camera", lens_mm=80)
    assert patch.model_dump(exclude_unset=True) == {"name": "Camera", "lens_mm": 80}
    assert CameraConfigureArguments(name="Camera").model_fields_set == {"name"}
    with pytest.raises(ValidationError):
        CameraProperties.model_validate({"clip_start": 2000})


def test_real_hard_limits_not_ui_soft_limits() -> None:
    properties = CameraProperties(
        lens_mm=8000, ortho_scale=20000, shift_x=8, shift_y=-8
    )
    assert properties.lens_mm == 8000 and properties.shift_x == 8
    assert CameraProperties(clip_start=CLIP_MIN, clip_end=1).clip_start == CLIP_MIN
    assert CameraProperties(lens_mm=FLOAT32_MAX).lens_mm == FLOAT32_MAX
    assert CameraProperties(ortho_scale=1e-40).ortho_scale > 0


@pytest.mark.parametrize(
    "fields", [{}, {"name": None}, {"name": " "}, {"name": "Camera", "active": True}]
)
def test_set_active_validation(fields: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        CameraSetActiveArguments.model_validate(fields)


def test_camera_registration_and_dispatch_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertised = registration("example", "5.2.1", "").operations
    assert advertised == tuple(sorted(OPERATIONS))
    assert len([name for name in advertised if name.startswith("blender.camera.")]) == 4
    backend = Backend()
    bad = call(backend, "blender.camera.configure", {"name": "Camera", "lens_mm": 0})
    assert isinstance(bad, OperationFailure) and bad.error.code == "invalid_arguments"
    assert backend.calls == []
    good = call(backend, "blender.camera.inspect", {})
    assert isinstance(good, OperationSuccess)
    assert good.result == {"active_camera": None, "cameras": []}

    def wrong_type(arguments: CameraSetActiveArguments) -> CameraSummary:
        raise OperationError(
            "object_not_camera", "Object is not a camera", {"name": arguments.name}
        )

    monkeypatch.setattr(backend, "camera_set_active", wrong_type)
    bad = call(backend, "blender.camera.set_active", {"name": "Cube"})
    assert isinstance(bad, OperationFailure) and bad.error.code == "object_not_camera"
    assert bad.error.details == {"name": "Cube"}
