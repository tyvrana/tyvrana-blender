"""Layer correction admits bounded semantic settings and reports conflicts."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.growth_layers_models import LayerCorrectArguments
from tyvrana_blender.operations import REGISTRY


@pytest.mark.parametrize(
    "change",
    [
        dict(frame_end=1),
        dict(samples=65),
        dict(direction=[0, 0, 0]),
        dict(full_lift=0.01),
        dict(colliders=["A", "A"]),
        dict(max_seconds=31),
        dict(max_cached_vertices=1000001),
        dict(clearance=0),
        dict(maximum_lift=float("nan")),
    ],
)
def test_reject_unbounded_or_invalid_correction(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LayerCorrectArguments.model_validate(
            dict(object_name="Field", frame_start=1, frame_end=3) | change
        )


def test_contracts_explain_sampled_correction_and_complements() -> None:
    correct = REGISTRY["blender.growth.layers.correct"].contract
    assert correct.effect == "mutating"
    assert "No physics or continuous collision guarantee" in correct.description
    assert REGISTRY["blender.growth.layers.inspect"].contract.effect == "read_only"
    assert REGISTRY["blender.growth.layers.clear"].contract.effect == "mutating"
    args = LayerCorrectArguments(object_name="Field", frame_start=1.25, frame_end=2.5)
    assert args.samples == 9 and not args.replace
