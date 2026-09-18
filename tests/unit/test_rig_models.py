import copy
from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.operations import OPERATIONS
from tyvrana_blender.rig_models import (
    ArmatureBindArguments,
    ArmatureCreateArguments,
    ArmaturePoseArguments,
    DeformationInspectArguments,
    EnvelopeWeights,
    ExplicitWeights,
    PoseBone,
    RestBone,
)


def chain() -> dict[str, Any]:
    return {
        "name": "Rig",
        "bones": [
            {"name": "Root", "head": [0.0, 0.0, 0.0], "tail": [0.0, 1.0, 0.0]},
            {
                "name": "Tip",
                "head": [0.0, 1.0, 0.0],
                "tail": [0.0, 2.0, 0.0],
                "parent": "Root",
                "connected": True,
            },
        ],
    }


def test_declarative_hierarchy_accepts_forward_references() -> None:
    data = chain()
    data["bones"].reverse()
    result = ArmatureCreateArguments.model_validate(data)
    assert result.bones[0].parent == "Root"
    assert OPERATIONS == tuple(sorted(set(OPERATIONS))) and OPERATIONS == tuple(
        sorted(OPERATIONS)
    )


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "missing",
        "cycle",
        "self",
        "connected",
        "zero",
        "nan",
        "infinite",
        "huge",
        "long_name",
        "connected_root",
        "unknown",
    ],
)
def test_invalid_rest_hierarchy(kind: str) -> None:
    data = chain()
    if kind == "duplicate":
        data["bones"][1]["name"] = "Root"
    elif kind == "missing":
        data["bones"][1]["parent"] = "Absent"
    elif kind == "cycle":
        data["bones"][0]["parent"] = "Tip"
    elif kind == "self":
        data["bones"][0]["parent"] = "Root"
    elif kind == "connected":
        data["bones"][1]["head"][0] = 0.1
    elif kind == "zero":
        data["bones"][0]["tail"] = [0.0, 0.0, 0.0]
    elif kind in {"nan", "infinite", "huge"}:
        data["bones"][0]["head"][0] = {
            "nan": float("nan"),
            "infinite": float("inf"),
            "huge": 10001.0,
        }[kind]
    elif kind == "long_name":
        data["bones"][0]["name"] = "字" * 22
    elif kind == "connected_root":
        data["bones"][0]["connected"] = True
    else:
        data["bones"][0]["python"] = "unsupported"
    with pytest.raises(ValidationError):
        ArmatureCreateArguments.model_validate(data)


@pytest.mark.parametrize(
    "influences",
    [
        [],
        [{"bone": "A", "weight": -0.1}],
        [{"bone": "A", "weight": 0.8}],
        [{"bone": "A", "weight": 1.1}],
        [{"bone": "A", "weight": 0.5}, {"bone": "A", "weight": 0.5}],
        [{"bone": "A", "weight": float("nan")}],
    ],
)
def test_invalid_explicit_weights(influences: list[Any]) -> None:
    with pytest.raises(ValidationError):
        ExplicitWeights.model_validate(
            {
                "method": "explicit",
                "vertices": [{"vertex": 0, "influences": influences}],
            }
        )


def test_weight_batch_and_limits() -> None:
    row = {
        "vertex": 0,
        "influences": [{"bone": "A", "weight": 0.25}, {"bone": "B", "weight": 0.75}],
    }
    result = ArmatureBindArguments.model_validate(
        {
            "object_name": "Mesh",
            "armature_object": "Rig",
            "weights": {"method": "explicit", "vertices": [row]},
        }
    )
    assert isinstance(result.weights, ExplicitWeights)
    for rows in [[row, copy.deepcopy(row)], [{**row, "vertex": 100000}]]:
        with pytest.raises(ValidationError):
            ExplicitWeights.model_validate({"method": "explicit", "vertices": rows})
    with pytest.raises(ValidationError):
        EnvelopeWeights(bones=["A", "A"])
    with pytest.raises(ValidationError):
        EnvelopeWeights(bones=["A"], max_influences=5)


@pytest.mark.parametrize(
    "patch",
    [
        {"scale": [0.0, 1.0, 1.0]},
        {"scale": [-1.0, 1.0, 1.0]},
        {"rotation": [float("inf"), 0.0, 0.0]},
        {"location": [0.0, 0.0]},
        {"location": None},
    ],
)
def test_invalid_pose_channels(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PoseBone.model_validate({"name": "Bone", **patch})


def test_pose_reset_and_duplicate_validation() -> None:
    assert ArmaturePoseArguments(object_name="Rig", reset=True).reset
    with pytest.raises(ValidationError):
        ArmaturePoseArguments(object_name="Rig")
    with pytest.raises(ValidationError):
        ArmaturePoseArguments(
            object_name="Rig", bones=[PoseBone(name="A"), PoseBone(name="A")]
        )
    with pytest.raises(ValidationError):
        DeformationInspectArguments(armature_object="Rig", objects=["A", "A"])
    with pytest.raises(ValidationError):
        DeformationInspectArguments(
            armature_object="Rig", objects=["A"], sample_limit=17
        )


def test_rest_bone_rejects_negative_envelope() -> None:
    with pytest.raises(ValidationError):
        RestBone(
            name="A", head=[0.0, 0.0, 0.0], tail=[0.0, 1.0, 0.0], envelope_distance=-1.0
        )
