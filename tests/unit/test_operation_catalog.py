"""Canonical registry, partial-update semantics and bounded repair feedback."""

import json

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationFailure, encode_message

from tyvrana_blender.inspection import page
from tyvrana_blender.models import InspectArguments
from tyvrana_blender.operations import OPERATIONS, REGISTRY, registration
from tyvrana_blender.transport import MAX_FRAME

from .test_operations import Backend, call


def test_complete_catalog_fits_transport_and_preserves_patch_omissions() -> None:
    message = registration("catalog", "5.2.1", "")
    assert message.operation_names == OPERATIONS
    assert len(encode_message(message)) < MAX_FRAME
    assert all(spec.contract.description for spec in REGISTRY.values())
    assert all(
        spec.contract.arguments_schema and spec.contract.result_schema
        for spec in REGISTRY.values()
    )
    camera = REGISTRY["blender.camera.configure"].parse(
        {"name": "Camera", "lens_mm": 75.0}
    )
    assert camera.model_fields_set == {"name", "lens_mm"}
    orthographic = REGISTRY["blender.camera.create"].parse(
        {"projection": "orthographic", "ortho_scale": 3.0}
    )
    assert "lens_mm" not in orthographic.model_fields_set
    assert REGISTRY["blender.sculpt.stroke"].contract.requires_interactive
    assert REGISTRY["blender.bake.image"].contract.execution == "job_start"
    assert REGISTRY["blender.bake.image"].contract.requires_interactive
    assert (
        REGISTRY["blender.image.create_from_artifact"].contract.input_artifacts
        == "required"
    )
    assert REGISTRY["blender.render.image"].contract.output_artifacts == "optional"


def test_pages_filter_before_summarizing_and_keep_counts() -> None:
    items = [f"Part {i:03}" for i in reversed(range(70))]
    selected, info = page(items, InspectArguments(), lambda value: value)
    assert selected == sorted(items)[:32] and info.next_offset == 32
    selected, info = page(
        items,
        InspectArguments(prefix="Part 06", limit=3, offset=3),
        lambda value: value,
    )
    assert selected == ["Part 063", "Part 064", "Part 065"]
    assert info.total_count == 70 and info.matched_count == 10 and info.next_offset == 6
    selected, info = page(
        items, InspectArguments(names=["Part 032", "Absent"]), lambda value: value
    )
    assert selected == ["Part 032"] and info.next_offset is None
    selected, info = page(items, InspectArguments(offset=100), lambda value: value)
    assert selected == [] and info.matched_count == 70 and info.next_offset is None
    with pytest.raises(ValidationError):
        InspectArguments(names=["A", "A"])


def test_argument_feedback_is_bounded_and_identifies_repair() -> None:
    backend = Backend()
    result = call(
        backend, "blender.scene.inspect", {f"extra{i}": [0] * 100 for i in range(20)}
    )
    assert isinstance(result, OperationFailure)
    assert (
        "blender.scene.inspect" in result.error.message
        and "20 errors" in result.error.message
    )
    assert isinstance(result.error.details, list) and len(result.error.details) == 8
    assert len(json.dumps(result.error.details)) < 3000
    assert backend.calls == []


def test_structural_authoring_contracts_distinguish_geometry_from_controls() -> None:
    descriptions = {
        name: spec.contract.description.lower() for name, spec in REGISTRY.items()
    }
    create = descriptions["blender.armature.create"]
    assert "articulation/deformation" in create
    assert "not anatomical bone or physical component geometry" in create
    inspect = descriptions["blender.armature.inspect_structure"]
    assert "not anatomical/physical geometry acceptance" in inspect
    for name in (
        "blender.object_set.create",
        "blender.mesh.create",
        "blender.curve.create",
    ):
        assert "structural" in descriptions[name]
    assert "role/tags" in descriptions["blender.object_set.create"]
    for description in descriptions.values():
        assert "mallard" not in description and "duck" not in description
