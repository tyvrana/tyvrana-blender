"""Correction/transfer contract bounds and ambiguous-state rejection."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.corrective_models import (
    DeformationCompareArguments,
    ShapeKeysEditArguments,
    ShapeKeysInspectArguments,
)
from tyvrana_blender.deformation_sweep_models import DeformationSweepArguments
from tyvrana_blender.operations import OPERATIONS, REGISTRY
from tyvrana_blender.surface_deform_models import SurfaceBindArguments
from tyvrana_blender.weight_transfer_models import (
    GroupsConfigureArguments,
    WeightsTransferArguments,
)


@pytest.mark.parametrize(
    "patch",
    [
        dict(keys=[]),
        dict(keys=[dict(name="A")] * 2),
        dict(keys=[dict(name="A", value=float("nan"))]),
        dict(keys=[dict(name="A", value=11)]),
        dict(
            keys=[
                dict(
                    name="A",
                    correction=dict(
                        mode="sparse", deltas=[dict(vertex=1, delta=[0, 0, 1])] * 2
                    ),
                )
            ]
        ),
        dict(
            keys=[
                dict(
                    name="A",
                    correction=dict(
                        mode="sparse", deltas=[dict(vertex=-1, delta=[0, 0, 1])]
                    ),
                )
            ]
        ),
        dict(
            keys=[
                dict(
                    name="A",
                    correction=dict(mode="captured_target", target="T", tolerance=0),
                )
            ]
        ),
        dict(
            keys=[
                dict(name="A", correction=dict(mode="captured_target", target="T")),
                dict(name="B"),
            ]
        ),
        dict(keys=[dict(name="A")], expected_topology_sha256="broken"),
        dict(
            keys=[
                dict(
                    name="A",
                    correction=dict(
                        mode="region",
                        delta=[0, 0, 1],
                        falloff=dict(center=[0, 0, 0], radii=[1, 0, 1]),
                    ),
                )
            ]
        ),
    ],
)
def test_reject_ambiguous_or_unbounded_keys(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ShapeKeysEditArguments.model_validate(dict(object_name="Mesh", **patch))


@pytest.mark.parametrize(
    "kwargs", [dict(limit=33), dict(detail_limit=257), dict(detail_offset=-1)]
)
def test_inspection_bounds(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        ShapeKeysInspectArguments.model_validate(dict(object_name="Mesh", **kwargs))


@pytest.mark.parametrize(
    "patch",
    [
        dict(driven=["D"]),
        dict(driven=["T", "T"]),
        dict(driven=["T"], falloff=17),
        dict(driven=["T"], strength=-1),
    ],
)
def test_surface_bind_bounds(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SurfaceBindArguments.model_validate(
            dict(driver="D", bind_state="current", **patch)
        )


@pytest.mark.parametrize(
    "patch",
    [
        dict(groups=[dict(source="A", target="B"), dict(source="A", target="C")]),
        dict(groups=[dict(source="A", target="B"), dict(source="C", target="B")]),
        dict(groups=[dict(source="A", target="B")], max_distance=0),
        dict(groups=[dict(source="A", target="B")], max_influences=5),
    ],
)
def test_transfer_bounds(patch: dict[str, object]) -> None:
    values = dict(source="S", target="T", max_distance=0.1)
    values.update(patch)
    with pytest.raises(ValidationError):
        WeightsTransferArguments.model_validate(values)


def test_native_group_identity_modes_are_explicit() -> None:
    with pytest.raises(ValidationError):
        GroupsConfigureArguments.model_validate(
            dict(object_name="M", groups=[dict(name="A", remove=True, create=True)])
        )


def test_sweep_key_channels_are_unique_and_explicit() -> None:
    with pytest.raises(ValidationError):
        DeformationSweepArguments.model_validate(
            dict(
                armature_object="Rig",
                objects=["M"],
                poses=[
                    dict(
                        name="pose",
                        shape_values=[dict(object_name="M", key="K", value=1)] * 2,
                    )
                ],
            )
        )


def test_new_contracts_are_current_and_self_contained() -> None:
    assert len(OPERATIONS) == 157
    names = [
        "shape_keys.edit",
        "shape_keys.inspect",
        "shape_keys.remove",
        "deformation.capture_target",
        "deformation.compare",
        "surface_deform.bind",
        "surface_deform.inspect",
        "surface_deform.unbind",
        "vertex_groups.configure",
        "weights.transfer",
    ]
    for name in names:
        contract = REGISTRY["blender." + name].contract
        assert contract.execution == "synchronous" and not contract.requires_interactive
        assert contract.arguments_schema and contract.result_schema
        assert len(contract.description) > 100


def test_defaults_do_not_request_coordinate_dump() -> None:
    result = ShapeKeysInspectArguments(object_name="M")
    assert result.detail_limit == 0 and result.limit == 16
    pair = DeformationCompareArguments.model_validate(
        dict(pairs=[dict(object_name="M", target="T")])
    )
    assert pair.sample_limit == 4
