"""Admission bounds and canonical placement seating discovery."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.placement_models import PlacementArguments


def request() -> dict[str, Any]:
    return {
        "seating": {
            "members": ["Assembly"],
            "interfaces": [
                {
                    "kind": "frame",
                    "name": f"mate{i}",
                    "source_object": f"Source{i}",
                    "target_object": f"Target{i}",
                }
                for i in range(2)
            ],
        }
    }


def test_seating_is_one_canonical_exclusive_mode() -> None:
    q = request()
    assert PlacementArguments.model_validate(q).seating is not None
    with pytest.raises(ValidationError):
        PlacementArguments.model_validate({**q, "refresh": ["Assembly"]})
    schema = PlacementArguments.model_json_schema()
    assert "seating" in schema["properties"]
    for definition in schema["$defs"].values():
        assert (
            not {"script", "vertices", "triangles"}
            & definition.get("properties", {}).keys()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("members", []),
        ("members", ["A", "A"]),
        ("interfaces", []),
        ("iterations", 81),
        ("max_evaluations", 1601),
        ("sample_limit", 257),
        ("translation_limit", [-1, 1, 1]),
        ("rotation_limit", [4, 1, 1]),
        ("convergence_tolerance", 0),
        ("max_tests", 2000001),
        ("worst_limit", 9),
    ],
)
def test_bounds(field: str, value: Any) -> None:
    q = deepcopy(request())
    q["seating"][field] = value
    with pytest.raises(ValidationError):
        PlacementArguments.model_validate(q)


def test_clearance_and_penetration_are_exclusive() -> None:
    q = request()
    q["seating"]["guards"] = [
        dict(
            name="guard",
            source=dict(object_name="A"),
            target=dict(object_name="B"),
            minimum_clearance=0.1,
            maximum_penetration=0.2,
        )
    ]
    with pytest.raises(ValidationError):
        PlacementArguments.model_validate(q)
