import json

import pytest
from pydantic import ValidationError
from tyvrana_protocol import JsonValue, OperationFailure, OperationSuccess

from tyvrana_blender.material_models import (
    PRINCIPLED_SOCKETS,
    MaterialAssignArguments,
    MaterialAssignment,
    MaterialAssignResult,
    MaterialConfigureArguments,
    MaterialCreateArguments,
    MaterialInspectResult,
    MaterialSummary,
    PrincipledSummary,
)
from tyvrana_blender.numeric import FLOAT32_MAX, binary32
from tyvrana_blender.operations import OPERATIONS, OperationError

from .test_operations import EMPTY_PAGE, Backend, call

SCALARS = set(PRINCIPLED_SOCKETS) - {
    "base_color",
    "emission_color",
    "subsurface_radius",
}


def test_empty_inspection_and_canonical_registration() -> None:
    assert MaterialInspectResult(materials=[], page=EMPTY_PAGE).model_dump() == {
        "materials": [],
        "page": EMPTY_PAGE.model_dump(),
    }
    assert tuple(sorted(OPERATIONS)) == OPERATIONS
    assert [op for op in OPERATIONS if op.startswith("blender.material.")] == [
        "blender.material.assign",
        "blender.material.assign_batch",
        "blender.material.author",
        "blender.material.configure_principled",
        "blender.material.copy",
        "blender.material.create_principled",
        "blender.material.inspect",
        "blender.material.remove",
    ]
    response = call(Backend(), "blender.material.inspect", {})
    assert isinstance(response, OperationSuccess) and response.result == {
        "materials": [],
        "page": EMPTY_PAGE.model_dump(),
    }


@pytest.mark.parametrize("field", sorted(SCALARS))
@pytest.mark.parametrize(
    "value",
    [None, True, "0.5", float("nan"), float("inf"), -float("inf"), 1e300, 1e-60],
)
def test_scalar_rejections(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        MaterialConfigureArguments.model_validate({"name": "Material", field: value})


@pytest.mark.parametrize("field", sorted(SCALARS))
def test_actual_dynamic_socket_hard_limits_not_slider_limits(field: str) -> None:
    for value in (-FLOAT32_MAX, -2, 0, 2, FLOAT32_MAX):
        args = MaterialCreateArguments.model_validate({field: value})
        assert getattr(args, field) == value
    assert getattr(
        MaterialCreateArguments.model_validate({field: 0.1}), field
    ) == binary32(0.1)
    assert getattr(MaterialCreateArguments.model_validate({field: 1e-40}), field) != 0


@pytest.mark.parametrize("field", ["base_color", "emission_color", "subsurface_radius"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        [1, 2],
        [1, 2, 3, 4],
        ["1", 0, 0],
        [True, 0, 0],
        [float("nan"), 0, 0],
        [float("inf"), 0, 0],
        [1e300, 0, 0],
        [1e-60, 0, 0],
    ],
)
def test_vector_rejections(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        MaterialCreateArguments.model_validate({field: value})


@pytest.mark.parametrize("field", ["base_color", "emission_color"])
def test_color_hard_limits(field: str) -> None:
    assert getattr(
        MaterialCreateArguments.model_validate({field: [4, FLOAT32_MAX, 0]}), field
    ) == [4, FLOAT32_MAX, 0]
    with pytest.raises(ValidationError):
        MaterialCreateArguments.model_validate({field: [-0.001, 0, 0]})


def test_radius_uses_signed_dynamic_vector_range() -> None:
    assert MaterialCreateArguments(
        subsurface_radius=[-FLOAT32_MAX, 101, FLOAT32_MAX]
    ).subsurface_radius == [-FLOAT32_MAX, 101, FLOAT32_MAX]


@pytest.mark.parametrize(
    "fields",
    [
        {"name": None},
        {"name": " "},
        {"name": ""},
        {"unknown": 1},
        {"type": "principled"},
        {"settings": {}},
        {"roughness": None},
        {"base_color": "#ffffff"},
    ],
)
def test_creation_shape(fields: dict[str, JsonValue]) -> None:
    response = call(Backend(), "blender.material.create_principled", fields)
    assert (
        isinstance(response, OperationFailure)
        and response.error.code == "invalid_arguments"
    )


def test_native_defaults_are_left_to_blender_and_patch_preserves_omissions() -> None:
    assert MaterialCreateArguments().model_dump(exclude_unset=True) == {}
    patch = MaterialConfigureArguments(name="Material", roughness=0.25)
    assert patch.model_dump(exclude_unset=True) == {
        "name": "Material",
        "roughness": 0.25,
    }
    with pytest.raises(ValidationError):
        MaterialConfigureArguments.model_validate({})


@pytest.mark.parametrize("surface", ["none", "custom", "principled"])
def test_summary_serialization(surface: str) -> None:
    fields: dict[str, JsonValue] = {field: 0.5 for field in SCALARS}
    fields.update(
        base_color=[1, 2, 3], emission_color=[0, 0, 0], subsurface_radius=[1, 0.2, 0.1]
    )
    principled = (
        PrincipledSummary.model_validate(fields) if surface == "principled" else None
    )
    summary = MaterialSummary.model_validate(
        {
            "name": "Material",
            "surface": surface,
            "principled": principled,
            "assignment_count": 2,
            "assignments_truncated": False,
            "assignments": [
                MaterialAssignment(object="Body", slot=0),
                MaterialAssignment(object="Eyes", slot=1),
            ],
        }
    )
    assert MaterialSummary.model_validate_json(summary.model_dump_json()) == summary
    assert json.loads(summary.model_dump_json())["assignments"] == [
        {"object": "Body", "slot": 0},
        {"object": "Eyes", "slot": 1},
    ]
    with pytest.raises(ValidationError):
        MaterialSummary.model_validate(
            {
                **summary.model_dump(),
                "surface": "principled" if principled is None else "custom",
            }
        )


@pytest.mark.parametrize("index,count", [(0, 0), (0, 1), (1, 1), (1, 3), (3, 3)])
def test_slot_replace_and_append(index: int, count: int) -> None:
    args = MaterialAssignArguments(
        object_name="Body", material_name="Material", slot_index=index
    )
    assert args.checked_slot(count) == index


def test_slot_default_and_gap() -> None:
    assert (
        MaterialAssignArguments(
            object_name="Body", material_name="Material"
        ).checked_slot(0)
        == 0
    )
    with pytest.raises(ValueError, match="gap"):
        MaterialAssignArguments(
            object_name="Body", material_name="Material", slot_index=2
        ).checked_slot(1)


@pytest.mark.parametrize("value", [-1, None, True, 0.0, "0"])
def test_slot_invalid_index(value: object) -> None:
    with pytest.raises(ValidationError):
        MaterialAssignArguments.model_validate(
            {"object_name": "Body", "material_name": "Material", "slot_index": value}
        )


def test_assignment_result_includes_empty_slots_in_order() -> None:
    result = MaterialAssignResult(
        object_name="Body",
        assigned_slot=2,
        material_name="Material",
        slots=[None, "Other", "Material"],
    )
    assert MaterialAssignResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        MaterialAssignResult.model_validate({**result.model_dump(), "assigned_slot": 0})


@pytest.mark.parametrize(
    "code",
    [
        "material_not_found",
        "unsupported_material_graph",
        "invalid_context",
        "invalid_arguments",
    ],
)
def test_material_errors_propagate(code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Backend()

    def fail(arguments: MaterialConfigureArguments) -> MaterialSummary:
        raise OperationError(
            code, "Material operation rejected", {"name": arguments.name}
        )

    monkeypatch.setattr(backend, "material_configure", fail)
    response = call(
        backend, "blender.material.configure_principled", {"name": "Material"}
    )
    assert isinstance(response, OperationFailure) and response.error.code == code
