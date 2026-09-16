"""Bounds and coordinate definitions for compact volume/layer inspection."""

import math

import pytest
from pydantic import ValidationError

from tyvrana_blender.deformation_sweep_models import DeformationSweepArguments
from tyvrana_blender.layer_math import sample_indices, sliding
from tyvrana_blender.layer_models import (
    LayerCaptureArguments,
    LayerInspectArguments,
    LayerRemoveArguments,
)
from tyvrana_blender.operations import REGISTRY
from tyvrana_blender.volume_models import (
    VolumeInspectArguments,
    VolumeSnapshotArguments,
)


def test_rank_sampling_is_bounded_deterministic_and_includes_ends() -> None:
    assert sample_indices([1, 7, 11, 90, 100], 3) == [1, 11, 100]
    assert sample_indices([1, 7, 11, 90, 100], 1) == [11]
    assert sample_indices([1, 7], 5) == [1, 7]
    assert len(set(sample_indices(list(range(10000)), 8192))) == 8192


def test_frame_delta_has_signed_components_and_tangent_magnitude() -> None:
    tangent, normal, length = sliding([2, -3, 0.4], [0.5, -1, 0.6])
    assert tangent == [1.5, -2]
    assert normal == pytest.approx(-0.2)
    assert length == 2.5
    with pytest.raises(ValueError):
        sliding([math.nan, 0, 0], [0, 0, 0])


@pytest.mark.parametrize(
    "args",
    [
        dict(objects=[]),
        dict(objects=[dict(object_name="A")], section_samples=1),
        dict(objects=[dict(object_name=str(i)) for i in range(17)]),
        dict(objects=[dict(object_name=str(i)) for i in range(16)], section_samples=32),
    ],
)
def test_volume_bounds(args: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VolumeInspectArguments.model_validate(args)


@pytest.mark.parametrize(
    "updates", [dict(sample_count=0), dict(sample_count=8193), dict(target="A")]
)
def test_layer_sampling_and_distinct_objects(updates: dict[str, object]) -> None:
    query = dict(mode="current", source="A", target="B") | updates
    with pytest.raises(ValidationError):
        LayerInspectArguments.model_validate(dict(queries=[query]))


def test_reference_mutation_names_and_metadata_contracts() -> None:
    pair = dict(name="QA", source="A", target="B")
    with pytest.raises(ValidationError):
        LayerCaptureArguments.model_validate(dict(references=[pair, pair]))
    with pytest.raises(ValidationError):
        LayerRemoveArguments(names=["QA", "QA"])
    with pytest.raises(ValidationError):
        VolumeSnapshotArguments.model_validate(
            dict(objects=[dict(source="A", name="New"), dict(source="B", name="New")])
        )
    for suffix in [
        "volume.inspect",
        "volume.snapshot",
        "layer.inspect",
        "layer.capture_reference",
        "layer.remove_reference",
    ]:
        contract = REGISTRY["blender." + suffix].contract
        assert contract.effect == (
            "read_only" if suffix.endswith("inspect") else "mutating"
        )
        assert contract.execution == "synchronous"
        assert not contract.requires_interactive
    description = REGISTRY["blender.layer.inspect"].contract.description
    assert "not universal thickness" in description and "barycentric" in description


def test_sweep_volume_only_and_detail_budgets() -> None:
    values = dict(armature_object="Rig", poses=[dict(name="rest")])
    with pytest.raises(ValidationError):
        DeformationSweepArguments.model_validate(values)
    result = DeformationSweepArguments.model_validate(
        values | dict(volumes=[dict(object_name="Curve")])
    )
    assert result.objects == []
    with pytest.raises(ValidationError):
        DeformationSweepArguments.model_validate(
            values
            | dict(
                poses=[dict(name=str(i)) for i in range(16)],
                volumes=[dict(object_name=str(i)) for i in range(16)],
            )
        )
    with pytest.raises(ValidationError):
        DeformationSweepArguments.model_validate(
            values
            | dict(
                poses=[dict(name=str(i)) for i in range(16)],
                layers=dict(
                    queries=[dict(mode="current", source="A", target="B")],
                    worst_limit=32,
                ),
            )
        )
