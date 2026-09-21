"""Admission limits and composable mechanics contracts."""

from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.mechanics_models import ContactArguments, FitArguments
from tyvrana_blender.motion_models import MotionSampleArguments
from tyvrana_blender.operations import REGISTRY


@pytest.mark.parametrize(
    "patch",
    [
        dict(sample_limit=8193),
        dict(tolerance=0),
        dict(method="anatomy"),
        dict(expected_sha256="bad"),
        dict(regions=[]),
        dict(
            axis=dict(
                start=dict(kind="world", point=[0, 0, 0]),
                end=dict(kind="python", code="pass"),
            )
        ),
    ],
)
def test_fit_bounded_evidence(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FitArguments.model_validate(
            dict(
                fits=[
                    dict(
                        name="Fit", method="sphere", regions=[dict(object_name="Ball")]
                    )
                    | patch
                ]
            )
        )


@pytest.mark.parametrize(
    "patch",
    [
        dict(target=dict(object_name="A")),
        dict(minimum_gap=0.1),
        dict(maximum_gap=-1),
        dict(sample_limit=2049),
        dict(mode="exempt"),
        dict(allowed_side="auto"),
    ],
)
def test_envelope_rejects_unsafe_or_ambiguous_contract(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ContactArguments.model_validate(
            dict(
                envelopes=[
                    dict(
                        name="Contact",
                        source=dict(object_name="A"),
                        target=dict(object_name="B"),
                    )
                    | patch
                ]
            )
        )


def test_contacts_compose_with_motion_and_remain_bounded() -> None:
    query = dict(
        name="Seat", source=dict(object_name="A"), target=dict(object_name="B")
    )
    args = MotionSampleArguments.model_validate(
        dict(range=dict(start=1, end=17), contacts=[query], scope=["Rig"])
    )
    assert len(args.samples()) == 17
    with pytest.raises(ValidationError):
        MotionSampleArguments.model_validate(dict(frames=[1], contacts=[query, query]))
    with pytest.raises(ValidationError):
        MotionSampleArguments.model_validate(
            dict(frames=[1], contacts=[query], scope=["Rig"] * 65)
        )


def test_mechanics_discovery_remains_read_only_and_compact_by_default() -> None:
    for name in ["blender.geometry.fit", "blender.contact.inspect"]:
        assert REGISTRY[name].contract.effect == "read_only"
    assert REGISTRY["blender.motion.sample"].contract.effect == "transient"
