"""Typed motion boundaries, mapping math and compact contract discovery."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.motion_math import expression, mapped
from tyvrana_blender.motion_models import (
    ActionEditArguments,
    ActionInspectArguments,
    CouplingConfigureArguments,
    LinearMapping,
    MotionSampleArguments,
    OutputClamp,
    RemapMapping,
    TimelineArguments,
)
from tyvrana_blender.operations import REGISTRY

SOURCE = dict(
    kind="transform", object_name="Rig", bone="A", property="rotation", axis="x"
)
TARGET = SOURCE | dict(bone="B")


@pytest.mark.parametrize(
    "scale,offset,x,expected",
    [(2.0, 0.5, 2.0, 4.5), (-0.5, 1.0, 2.0, 0.0), (0.0, 0.2, 100.0, 0.2)],
)
def test_linear_mapping_is_deterministic(
    scale: float, offset: float, x: float, expected: float
) -> None:
    value = LinearMapping(scale=scale, offset=offset)
    assert mapped(value, x) == (expected, False)
    assert "x*" in expression(value) and "__" not in expression(value)


@pytest.mark.parametrize(
    "x,expected,saturated",
    [
        (-1.0, 0.0, True),
        (0.0, 0.0, False),
        (0.5, 0.5, False),
        (1.0, 1.0, False),
        (2.0, 1.0, True),
    ],
)
def test_remap_clamp_and_inverse(x: float, expected: float, saturated: bool) -> None:
    value = RemapMapping(input_min=0, input_max=1, output_min=0, output_max=1)
    assert mapped(value, x) == (expected, saturated)
    inverse = value.model_copy(update=dict(output_min=1.0, output_max=0.0))
    assert mapped(inverse, x)[0] == 1 - expected


@pytest.mark.parametrize(
    "patch",
    [
        dict(expression='__import__("os")'),
        dict(source=SOURCE | dict(path="location[0]")),
        dict(source=SOURCE | dict(axis="w")),
        dict(target=TARGET | dict(kind="python")),
        dict(mapping=dict(kind="linear", scale=math.nan)),
        dict(
            mapping=dict(
                kind="remap", input_min=1, input_max=1, output_min=0, output_max=1
            )
        ),
    ],
)
def test_no_expression_path_or_invalid_mapping(patch: dict[str, Any]) -> None:
    spec = (
        dict(name="Follow", source=SOURCE, target=TARGET, mapping=dict(kind="linear"))
        | patch
    )
    with pytest.raises(ValidationError):
        CouplingConfigureArguments.model_validate(dict(couplings=[spec]))


def test_bounds_and_duplicate_targets() -> None:
    spec = dict(
        name="Follow", source=SOURCE, target=TARGET, mapping=dict(kind="linear")
    )
    with pytest.raises(ValidationError):
        CouplingConfigureArguments.model_validate(
            dict(couplings=[spec, spec | dict(name="Other")])
        )
    with pytest.raises(ValidationError):
        OutputClamp(minimum=1, maximum=0)
    with pytest.raises(ValidationError):
        ActionInspectArguments(name="Clip", limit=128, key_limit=512)


@pytest.mark.parametrize(
    "patch",
    [
        dict(range=dict(start=1, end=129)),
        dict(range=dict(start=5, end=1)),
        dict(frames=[1, 1]),
        dict(frames=[1], detail_frames=[2]),
        dict(frames=[1], bones=["A"]),
        dict(frames=[1], channels=[]),
        dict(frames=[1], thresholds=[dict(metric="a")]),
    ],
)
def test_frame_sampling_work_and_detail_bounds(patch: dict[str, Any]) -> None:
    args = dict(channels=[dict(name="A", channel=SOURCE)]) | patch
    with pytest.raises(ValidationError):
        MotionSampleArguments.model_validate(args)


def test_sampling_merges_uniform_and_explicit_frames() -> None:
    args = MotionSampleArguments.model_validate(
        dict(
            range=dict(start=1, end=10, step=4),
            frames=[10, 5],
            channels=[dict(name="A", channel=SOURCE)],
        )
    )
    assert args.samples() == [1, 5, 9, 10]


@pytest.mark.parametrize(
    "keys,extra",
    [
        ([dict(frame=1, value=0)] * 2, {}),
        ([dict(frame=1, value=0)], dict(remove_frames=[1])),
        ([dict(frame=1, value=0)], dict(remove_channel=True)),
        ([], {}),
        ([dict(frame=1, value=0, interpolation="SINE")], {}),
    ],
)
def test_action_key_edits_are_unambiguous(keys: Any, extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ActionEditArguments.model_validate(
            dict(name="Clip", channels=[dict(target=SOURCE, keys=keys) | extra])
        )


def test_timeline_patch_and_operation_contract_security() -> None:
    with pytest.raises(ValidationError):
        TimelineArguments()
    with pytest.raises(ValidationError):
        TimelineArguments(frame=None)
    assert TimelineArguments(frame=4).model_fields_set == {"frame"}
    names = [
        "timeline.inspect",
        "timeline.configure",
        "motion.set_properties",
        "coupling.configure",
        "coupling.inspect",
        "coupling.remove",
        "action.edit",
        "action.assign",
        "action.inspect",
        "action.remove",
        "motion.sample",
    ]
    for name in names:
        contract = REGISTRY["blender." + name].contract
        assert not contract.requires_interactive
        assert contract.execution == "synchronous"
        properties = contract.arguments_schema.get("properties", {})
        assert isinstance(properties, dict) and "expression" not in properties
    assert REGISTRY["blender.motion.sample"].contract.effect == "transient"
    assert (
        "simple expressions"
        in REGISTRY["blender.coupling.configure"].contract.description
    )
