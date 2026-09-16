"""Machine contracts bound work and keep coordinate meanings explicit."""

from typing import Any, cast

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationFailure, OperationRequest

from tyvrana_blender.operations import OPERATIONS, REGISTRY, execute
from tyvrana_blender.reference_models import (
    LandmarkSetArguments,
    MeasurementArguments,
    ReferenceConfigureArguments,
    ReferenceCreateArguments,
)

from .test_operations import Backend

WORLD = {"kind": "world", "point": [0.0, 0.0, 0.0]}
DISTANCE = {"kind": "distance", "name": "Length", "a": WORLD, "b": WORLD}


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("reference.create", {"references": []}),
        ("reference.create", {"references": [{"name": "A", "image": "I", "size": 0}]}),
        (
            "reference.create",
            {"references": [{"name": "A", "image": "I", "opacity": 1.1}]},
        ),
        (
            "reference.create",
            {"references": [{"name": "A", "image": "I", "rotation": [0, 0]}]},
        ),
        (
            "reference.create",
            {"references": [{"name": "A", "image": "I", "depth": "invalid"}]},
        ),
        (
            "reference.create",
            {"references": [{"name": "A", "image": "I", "source_label": "x" * 513}]},
        ),
        ("reference.create", {"references": [{"name": "A", "image": "I"}] * 2}),
        (
            "reference.create",
            {"references": [{"name": str(i), "image": "I"} for i in range(17)]},
        ),
        ("reference.configure", {"references": [{"name": "A"}]}),
        ("reference.configure", {"references": [{"name": "A", "opacity": None}]}),
        ("reference.inspect", {"limit": 129}),
        ("reference.inspect", {"limit": 0}),
        ("reference.remove", {"names": ["A", "A"]}),
        ("reference.remove", {"names": []}),
        ("landmark.set", {"landmarks": [{"name": "A", "point": [True, 0, 0]}]}),
        ("landmark.set", {"landmarks": [{"name": "A", "point": [1e13, 0, 0]}]}),
        ("landmark.set", {"landmarks": [{"name": "A", "point": ["1", 0, 0]}]}),
        (
            "landmark.set",
            {"landmarks": [{"name": str(i), "point": [0, 0, 0]} for i in range(33)]},
        ),
        ("measurement.inspect", {"queries": []}),
        ("measurement.inspect", {"queries": [DISTANCE] * 65}),
        ("measurement.inspect", {"queries": [DISTANCE, DISTANCE]}),
        (
            "measurement.inspect",
            {"queries": [DISTANCE], "frame": "Part", "unit": "meters"},
        ),
        (
            "measurement.inspect",
            {"queries": [DISTANCE | {"comparison": {"target": -1, "tolerance": 0}}]},
        ),
        (
            "measurement.inspect",
            {"queries": [DISTANCE | {"comparison": {"target": 1, "tolerance": -1}}]},
        ),
        ("measurement.inspect", {"queries": [DISTANCE | {"kind": "unknown"}]}),
        (
            "reference.calibrate",
            {"name": "A", "a": [0, 0], "b": [0, 0], "target_distance": 1},
        ),
        (
            "reference.calibrate",
            {"name": "A", "a": [0, 0], "b": [1, 0], "target_distance": 0},
        ),
        ("scene.configure_units", {"system": "metric", "meters_per_unit": 0}),
        ("scene.configure_units", {"system": "unknown", "meters_per_unit": 1}),
    ],
)
def test_invalid_contracts_are_bounded_errors(operation: str, arguments: Any) -> None:
    result = execute(
        Backend(),
        OperationRequest(
            type="operation.request",
            request_id="invalid",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(result, OperationFailure)
    assert result.error.code == "invalid_arguments"
    assert len(result.model_dump_json()) < 5000


def test_explicit_patch_fields_and_full_landmark_redefinition() -> None:
    patch = ReferenceConfigureArguments.model_validate(
        {"references": [{"name": "A", "opacity": 0.2}]}
    )
    assert patch.references[0].model_fields_set == {"name", "opacity"}
    spec = LandmarkSetArguments.model_validate(
        {"landmarks": [{"name": "Point", "point": [1, 2, 3]}]}
    ).landmarks[0]
    assert spec.object is None and spec.label == spec.category == ""
    with pytest.raises(ValidationError):
        ReferenceCreateArguments.model_validate(
            {"references": [{"name": "A", "image": "I", "pixels": [1, 2]}]}
        )


def test_all_point_kinds_and_angle_schema_are_discoverable() -> None:
    op = REGISTRY["blender.measurement.inspect"].contract
    assert op.effect == "read_only" and op.input_artifacts == "none"
    schema = cast(dict[str, Any], op.arguments_schema)
    assert schema["properties"]["queries"]["maxItems"] == 64
    query = MeasurementArguments.model_validate(
        {
            "queries": [
                {
                    "name": "Angle",
                    "kind": "angle",
                    "a": WORLD,
                    "vertex": {"kind": "object", "object": "Part", "point": [0, 0, 0]},
                    "b": {"kind": "landmark", "name": "Anchor"},
                }
            ]
        }
    )
    assert query.frame is None and query.unit == "blender"
    assert len(OPERATIONS) == 170


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_points_fail_before_protocol_transport(value: float) -> None:
    with pytest.raises(ValidationError):
        LandmarkSetArguments.model_validate(
            {"landmarks": [{"name": "A", "point": [value, 0, 0]}]}
        )
