"""The local fairing extension is explicit, exclusive and finitely bounded."""

from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.sculpt_models import SculptFilterArguments


def request(**settings: Any) -> dict[str, Any]:
    return {
        "object_name": "Socket",
        "type": "fair",
        "strength": 1.0,
        "iterations": 30,
        "fairing": {"vertex_group": "Rim", "max_distance": 0.01, **settings},
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_distance", 0),
        ("max_distance", float("nan")),
        ("max_points", 262145),
        ("max_points", True),
        ("max_work", 64000001),
        ("max_triangle_tests", 2000001),
        ("max_thinning", 0.11),
        ("max_thinning", -0.1),
        ("boundary_rings", 0),
        ("boundary_rings", 9),
        ("vertex_group", ""),
        ("expression", "x + 1"),
        ("vertices", [[0, 0, 0]]),
    ],
)
def test_fairing_rejects_invalid_or_unbounded_settings(key: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        SculptFilterArguments.model_validate(request(**{key: value}))


@pytest.mark.parametrize(
    "patch",
    [
        {"type": "smooth"},
        {"iterations": 41},
        {"strength": 0},
        {"strength": -0.5},
        {"orientation": "world"},
        {"axes": {"x": False}},
    ],
)
def test_fairing_mode_rejects_ambiguous_controls(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SculptFilterArguments.model_validate({**request(), **patch})


def test_fairing_requires_its_settings_and_supports_existing_mask_scope() -> None:
    data = request()
    del data["fairing"]
    with pytest.raises(ValidationError):
        SculptFilterArguments.model_validate(data)
    args = SculptFilterArguments.model_validate(request())
    assert args.fairing is not None and args.fairing.vertex_group == "Rim"
    data["fairing"] = {"max_distance": 0.01}
    assert SculptFilterArguments.model_validate(data).fairing is not None


def test_region_and_named_group_are_exclusive() -> None:
    with pytest.raises(ValidationError, match="not both"):
        SculptFilterArguments.model_validate(
            request(regions=[{"min": [-1, -1, -1], "max": [1, 1, 1]}])
        )


@pytest.mark.parametrize("value", [0.1, 0.05])
def test_thinning_limits_accept_exact_documented_ratios(value: float) -> None:
    args = SculptFilterArguments.model_validate(request(max_thinning=value))
    assert args.fairing is not None
    assert args.fairing.max_thinning == value
