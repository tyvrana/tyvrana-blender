"""Bounded regional editing and contact/region QA request contracts."""

from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.deformation_models import ContactProbe
from tyvrana_blender.rig_models import (
    ArmatureBindArguments,
    DeformationInspectArguments,
)
from tyvrana_blender.weight_models import (
    WeightsAssignArguments,
    WeightsInspectArguments,
)


def invalid_patches(*items: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return items


def layer(**patch: object) -> dict[str, object]:
    return {
        "selector": {"mode": "all", "domain": "vertex"},
        "weights": {"mode": "constant", "influences": [{"bone": "A", "weight": 1.0}]},
        **patch,
    }


def test_regions_accept_unnormalized_competing_profiles() -> None:
    a = WeightsAssignArguments.model_validate(
        {
            "object_name": "Mesh",
            "layers": [
                layer(
                    weights={
                        "mode": "gradient",
                        "start": [0.0, 0.0, 0.0],
                        "end": [0.0, 1.0, 0.0],
                        "start_influences": [
                            {"bone": "A", "weight": 1.0},
                            {"bone": "B", "weight": 1.0},
                        ],
                        "end_influences": [{"bone": "B", "weight": 1.0}],
                    }
                )
            ],
        }
    )
    assert a.layers[0].weights.mode == "gradient"


@pytest.mark.parametrize(
    "change",
    [
        {"layers": []},
        {"layers": [layer()] * 33},
        {"max_influences": 0},
        {"max_influences": 5},
        {"smooth_iterations": 21},
        {"smooth_factor": 0},
        {"fixed_selector": {"mode": "all", "domain": "vertex"}},
        {"layers": [layer(selector={"mode": "all", "domain": "face"})]},
        {
            "layers": [
                layer(
                    selector={"mode": "indices", "domain": "vertex", "indices": [1, 1]}
                )
            ]
        },
        {
            "layers": [
                layer(
                    weights={
                        "mode": "constant",
                        "influences": [{"bone": "A", "weight": 0.0}],
                    }
                )
            ]
        },
        {
            "layers": [
                layer(
                    weights={
                        "mode": "constant",
                        "influences": [{"bone": "A", "weight": 1.0}] * 2,
                    }
                )
            ]
        },
        {
            "layers": [
                layer(
                    weights={
                        "mode": "constant",
                        "influences": [{"bone": "A", "weight": float("nan")}],
                    }
                )
            ]
        },
        {
            "layers": [
                layer(
                    weights={
                        "mode": "gradient",
                        "start": [0.0, 0.0, 0.0],
                        "end": [0.0, 0.0, 0.0],
                        "start_influences": [{"bone": "A", "weight": 1.0}],
                        "end_influences": [{"bone": "B", "weight": 1.0}],
                    }
                )
            ]
        },
        {"smooth_iterations": 1, "fixed_selector": {"mode": "all", "domain": "edge"}},
        {"object_name": None},
        {"python": "pass"},
    ],
)
def test_invalid_edits(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WeightsAssignArguments.model_validate(
            {"object_name": "Mesh", "layers": [layer()], **change}
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"sample_limit": 33},
        {"bone_names": ["A", "A"]},
        {"selector": {"mode": "all", "domain": "face"}},
        {"filter": "arbitrary"},
    ],
)
def test_inspection_limits(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WeightsInspectArguments.model_validate({"object_name": "Mesh", **patch})


def test_contact_regions_and_retargeting_are_explicit() -> None:
    probe: dict[str, Any] = {
        "source_object": "Mesh",
        "target_object": "Body",
        "rest_min": [-1.0, -1.0, -1.0],
        "rest_max": [1.0, 1.0, 1.0],
    }
    a = DeformationInspectArguments.model_validate(
        {
            "armature_object": "Rig",
            "objects": ["Mesh"],
            "contacts": [probe],
            "bone_names": ["A"],
        }
    )
    assert len(a.contacts) == 1
    for patch in invalid_patches(
        {"source_object": "Body"}, {"rest_min": [2.0, 2.0, 2.0]}
    ):
        with pytest.raises(ValidationError):
            ContactProbe.model_validate({**probe, **patch})
    for patch in invalid_patches(
        {"objects": ["Other"]},
        {"bone_names": ["A"] * 2},
        {"contacts": [probe] * 9},
    ):
        with pytest.raises(ValidationError):
            DeformationInspectArguments.model_validate(
                {
                    "armature_object": "Rig",
                    "objects": ["Mesh"],
                    "contacts": [probe],
                    **patch,
                }
            )
    b = ArmatureBindArguments.model_validate(
        {
            "object_name": "Mesh",
            "armature_object": "Rig",
            "weights": {"method": "envelopes", "bones": ["A"]},
        }
    )
    assert not b.replace_binding_target
