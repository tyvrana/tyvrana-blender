"""Joint bounds, frame construction and shared structural point contracts."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.joint_models import (
    JointConfigureArguments,
    JointLimits,
    StructureInspectArguments,
    orthonormal_axes,
)
from tyvrana_blender.operations import OPERATIONS, registration
from tyvrana_blender.reference_models import MeasurementArguments
from tyvrana_blender.rig_models import (
    ArmatureCreateArguments,
    ArmatureRestArguments,
    RestBone,
)


def test_frame_is_right_handed_and_normalized() -> None:
    x, y, z = orthonormal_axes([0, 3, 4], [2, 1, 0])
    for axis in (x, y, z):
        assert math.hypot(*axis) == pytest.approx(1)
    for a, b in [(x, y), (x, z), (y, z)]:
        assert sum(i * j for i, j in zip(a, b, strict=True)) == pytest.approx(
            0, abs=1e-14
        )
    cross = [
        x[1] * y[2] - x[2] * y[1],
        x[2] * y[0] - x[0] * y[2],
        x[0] * y[1] - x[1] * y[0],
    ]
    assert cross == pytest.approx(z)


@pytest.mark.parametrize(
    ("direction", "reference"),
    [
        ([0, 0, 0], [1, 0, 0]),
        ([0, 1, 0], [0, 0, 0]),
        ([0, 1, 0], [0, 2, 0]),
        ([0, 1, 0], [0, -2, 0]),
        ([0, float("nan"), 1], [1, 0, 0]),
    ],
)
def test_invalid_frames(direction: list[float], reference: list[float]) -> None:
    with pytest.raises(ValueError):
        orthonormal_axes(direction, reference)


@pytest.mark.parametrize(
    "limits",
    [
        {},
        {"x": {"minimum": 1, "maximum": 0}},
        {"y": {"minimum": -2, "maximum": 2}},
        {"x": {"minimum": -4, "maximum": 4}},
        {"x": {"minimum": 0, "maximum": float("nan")}},
        {"rotation_mode": "ZYX", "x": {"minimum": 0, "maximum": 1}},
        {"x": {"minimum": 0, "maximum": 1}, "properties": {"influence": 0.2}},
    ],
)
def test_invalid_limits(limits: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JointLimits.model_validate(limits)


def test_hinge_and_three_axis_limits() -> None:
    value = JointLimits.model_validate(
        {
            "x": {"minimum": -0.5, "maximum": 0.8},
            "y": {"minimum": 0, "maximum": 0},
            "z": {"minimum": 0, "maximum": 0},
        }
    )
    assert (
        value.rotation_mode == "XYZ"
        and value.y
        and value.y.minimum == value.y.maximum == 0
    )
    assert JointLimits.model_validate({"z": {"minimum": 0, "maximum": 1}}).x is None


def test_roll_and_frame_reference_are_exclusive() -> None:
    with pytest.raises(ValidationError):
        RestBone.model_validate(
            dict(
                name="Part",
                head=[0, 0, 0],
                tail=[0, 1, 0],
                roll=0,
                x_reference=[1, 0, 0],
            )
        )
    with pytest.raises(ValidationError):
        RestBone.model_validate(
            dict(name="Part", head=[0, 0, 0], tail=[0, 1, 0], x_reference=[0, 1, 0])
        )


def test_shared_point_sources_and_compact_summary() -> None:
    args = ArmatureCreateArguments.model_validate(
        dict(
            name="Structure",
            sample_limit=0,
            bones=[
                dict(
                    name="Part",
                    head={"kind": "landmark", "name": "Center"},
                    tail={"kind": "world", "point": [0, 1, 0]},
                    x_reference=[1, 0, 0],
                )
            ],
        )
    )
    assert args.sample_limit == 0 and not isinstance(args.bones[0].head, list)
    query = MeasurementArguments.model_validate(
        {
            "queries": [
                {
                    "kind": "distance",
                    "name": "reach",
                    "a": {
                        "kind": "bone",
                        "object": "Structure",
                        "bone": "A",
                        "state": "rest",
                    },
                    "b": {
                        "kind": "bone",
                        "object": "Structure",
                        "bone": "B",
                        "endpoint": "tail",
                    },
                }
            ]
        }
    )
    assert query.queries[0].kind == "distance"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"object_name": "Rig"},
        {
            "object_name": "Rig",
            "renames": [{"name": "A", "rename": "B"}, {"name": "A", "rename": "C"}],
        },
    ],
)
def test_empty_or_duplicate_rest_updates(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ArmatureRestArguments.model_validate(values)


def test_joint_batch_and_inspection_bounds() -> None:
    with pytest.raises(ValidationError):
        JointConfigureArguments.model_validate(
            {"object_name": "Rig", "joints": [{"name": "A", "limits": None}] * 2}
        )
    for change in ({"fields": ["frame", "frame"]}, {"limit": 129}, {"space": "CUSTOM"}):
        with pytest.raises(ValidationError):
            StructureInspectArguments.model_validate({"object_name": "Rig", **change})
    args = JointConfigureArguments.model_validate(
        {"object_name": "Rig", "joints": [{"name": "A", "limits": None}]}
    )
    assert args.joints[0].limits is None


def test_registry_remains_bounded() -> None:
    assert len(OPERATIONS) == 128
    assert (
        len(registration("fixture", "5.2.1", "").model_dump_json().encode()) < 1048576
    )
