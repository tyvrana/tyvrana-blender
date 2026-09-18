"""Typed control boundaries and unsafe batch rejection."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.constraint_models import (
    ConstraintsConfigureArguments,
    ConstraintsRemoveArguments,
    PoseMatchArguments,
)
from tyvrana_blender.joint_models import IKJoint
from tyvrana_blender.rig_models import ArmatureRestArguments


def test_duplicates_and_untyped_properties_are_rejected() -> None:
    ref = dict(owner=dict(object_name="Rig", bone="A"), name="Solve")
    with pytest.raises(ValidationError):
        ConstraintsRemoveArguments.model_validate(dict(constraints=[ref, ref]))
    match = dict(source=dict(object_name="A"), target=dict(object_name="B"))
    with pytest.raises(ValidationError):
        PoseMatchArguments.model_validate(dict(matches=[match, match]))
    with pytest.raises(ValidationError):
        ConstraintsConfigureArguments.model_validate(
            dict(
                constraints=[
                    dict(
                        **ref,
                        settings=dict(
                            kind="ik",
                            target=dict(object_name="Goal"),
                            chain_length=17,
                            properties={"arbitrary": True},
                        ),
                    )
                ]
            )
        )


@pytest.mark.parametrize(
    "settings",
    [
        dict(x=dict(stiffness=1)),
        dict(stretch=-1),
        dict(z=dict(limits=dict(minimum=1, maximum=0))),
    ],
)
def test_ik_ranges(settings: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IKJoint.model_validate(settings)


def test_rest_preview_is_explicit_and_signature_bounded() -> None:
    data = dict(
        object_name="Rig", bones=[dict(name="A", head=[0, 0, 0], tail=[0, 1, 0])]
    )
    assert ArmatureRestArguments.model_validate(data).dependency_policy == "reject"
    with pytest.raises(ValidationError):
        ArmatureRestArguments.model_validate(dict(data, expected_rest_sha256="old"))
