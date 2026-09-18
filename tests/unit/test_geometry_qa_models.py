"""Surface QA admission prevents unbounded output or ambiguous comparisons."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.geometry_qa_models import GeometryInspectArguments
from tyvrana_blender.operations import REGISTRY


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"pairs": [{"left": "A", "right": "A"}]},
        {"pairs": [{"left": "A", "right": "B"}, {"left": "B", "right": "A"}]},
        {"objects": [{"object_name": "A"}] * 2},
        {"objects": [{"object_name": "A"}], "frames": [1, 1]},
        {
            "objects": [{"object_name": str(i)} for i in range(16)],
            "frames": list(range(9)),
        },
        {
            "objects": [{"object_name": "A"}],
            "frames": list(range(64)),
            "worst_limit": 8,
        },
        {"objects": [{"object_name": "A"}], "max_triangle_tests": 2000001},
        {"objects": [{"object_name": "A"}], "tolerance": float("nan")},
        {
            "pairs": [
                {
                    "left": "A",
                    "right": "B",
                    "exemptions": [{"left_faces": [], "right_faces": [0]}],
                }
            ]
        },
    ],
)
def test_qa_bounds(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GeometryInspectArguments.model_validate(values)


def test_qa_contract_explains_sampling_and_inversion() -> None:
    contract = REGISTRY["blender.geometry.inspect"].contract
    assert contract.effect == "transient"
    assert "never continuous" in contract.description
    assert "not inversion proof" in contract.description
