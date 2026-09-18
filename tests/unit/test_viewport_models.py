"""Strict checkpoint targets: no implicit window selection or code input."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.operations import REGISTRY
from tyvrana_blender.viewport_models import (
    ViewportCaptureArguments,
    ViewportConfigureArguments,
    ViewportFrameArguments,
    ViewportInspectArguments,
)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"viewport_id": "1:2", "object_names": []},
        {"viewport_id": "1:2", "object_names": ["a", "a"]},
        {"viewport_id": "1:2", "object_names": ["a"] * 65},
        {"viewport_id": "active", "object_names": ["a"]},
        {"viewport_id": "1:2", "object_names": ["a"], "python": "pass"},
    ],
)
def test_frame_rejects_ambiguous_targets(arguments: object) -> None:
    with pytest.raises(ValidationError):
        ViewportFrameArguments.model_validate(arguments)


def test_discovery_and_frame_effects_are_explicit() -> None:
    assert ViewportInspectArguments().viewport_id is None
    assert REGISTRY["blender.viewport.inspect"].contract.effect == "read_only"
    frame = REGISTRY["blender.viewport.frame"].contract
    assert frame.effect == "mutating" and frame.requires_interactive


@pytest.mark.parametrize(
    "orientation",
    [
        {"direction": [0, 0, 0]},
        {"direction": [0, 0, 1], "up": [0, 0, 1]},
        "camera",
        {"direction": [1, 0, 0], "script": "pass"},
    ],
)
def test_invalid_view_directions(orientation: object) -> None:
    with pytest.raises(ValidationError):
        ViewportConfigureArguments.model_validate(
            {"viewport_id": "1:2", "orientation": orientation}
        )


def test_capture_bounds_and_artifact_contract() -> None:
    with pytest.raises(ValidationError):
        ViewportCaptureArguments.model_validate(
            {"viewport_id": "1:2", "views": [{"orientation": "front"}] * 7}
        )
    contract = REGISTRY["blender.viewport.capture"].contract
    assert contract.output_artifacts == "required"
    assert contract.effect == "transient"
    assert "multiview" in contract.tags
