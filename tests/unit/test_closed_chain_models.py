"""Admission and ownership-facing contract limits for closed couplings."""

from typing import Any

import pytest
from pydantic import ValidationError

from tests.closed_chain_fixture import definition
from tyvrana_blender.motion_models import (
    CouplingConfigureArguments,
    MechanismSolveArguments,
    MechanismSpec,
    MotionSampleArguments,
)
from tyvrana_blender.operations import REGISTRY


@pytest.mark.parametrize(
    "change",
    [
        dict(iterations=65),
        dict(tolerance=0),
        dict(maximum_condition=1),
        dict(closures=[]),
        dict(variables=[]),
    ],
)
def test_closed_problem_limits(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MechanismSpec.model_validate(definition() | change)


@pytest.mark.parametrize("property", ["scale", "python", "matrix"])
def test_no_scale_or_escape_channel(property: str) -> None:
    spec = definition()
    spec["variables"][1]["channel"]["property"] = property
    with pytest.raises(ValidationError):
        MechanismSpec.model_validate(spec)


def test_canonical_composition_and_compact_defaults() -> None:
    args = CouplingConfigureArguments.model_validate(dict(mechanisms=[definition()]))
    assert not args.couplings
    sample = MotionSampleArguments.model_validate(
        dict(mechanism="Linkage", range=dict(start=1, end=33))
    )
    assert len(sample.samples()) == 33 and not sample.detail_frames
    assert not MechanismSolveArguments(name="Linkage").apply
    assert REGISTRY["blender.coupling.solve"].contract.effect == "mutating"
    with pytest.raises(ValidationError):
        CouplingConfigureArguments(couplings=[])
    with pytest.raises(ValidationError):
        MotionSampleArguments(
            mechanism="Linkage", frames=[1], mechanism_max_evaluations=50001
        )


def test_variable_names_channels_and_ranges_unique() -> None:
    for key in ["name", "channel"]:
        spec = definition()
        spec["variables"][1][key] = spec["variables"][0][key]
        with pytest.raises(ValidationError):
            MechanismSpec.model_validate(spec)
    spec = definition()
    spec["variables"][1]["minimum"] = 4
    with pytest.raises(ValidationError):
        MechanismSpec.model_validate(spec)
