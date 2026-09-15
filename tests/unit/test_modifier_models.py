import math
from typing import Any

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender.modifier_models import (
    CONFIGURE,
    CREATE,
    EvaluatedMeshArguments,
    MirrorSettings,
    ModifierApplyArguments,
    ModifierInspectArguments,
    ModifierInspectResult,
    ModifierMoveArguments,
    ModifierRemoveArguments,
    ModifierSummary,
    SubdivisionSettings,
)
from tyvrana_blender.operations import OPERATIONS, OperationError, execute

from .test_operations import Backend

KINDS = [
    "mirror",
    "subdivision_surface",
    "shrinkwrap",
    "boolean",
    "solidify",
    "triangulate",
]


def create(kind: str, **settings: Any) -> Any:
    return CREATE.validate_python(
        {"object_name": "Surface", "type": kind, "settings": settings}
    )


@pytest.mark.parametrize("kind", KINDS)
def test_create_and_partial_configure_preserve_omission(kind: str) -> None:
    settings = (
        {"target": "Target"}
        if kind == "shrinkwrap"
        else {"operand_object": "Operand"}
        if kind == "boolean"
        else {}
    )
    args = create(kind, **settings)
    assert args.type == kind
    assert args.settings.model_dump(exclude_unset=True) == settings
    patch = CONFIGURE.validate_python(
        {
            "object_name": "Surface",
            "modifier_name": "Shape",
            "type": kind,
            "enabled_render": False,
        }
    )
    assert patch.settings.model_dump(exclude_unset=True) == {}
    assert patch.model_fields_set == {
        "object_name",
        "modifier_name",
        "type",
        "enabled_render",
    }


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "field,value",
    [
        ("object_name", ""),
        ("object_name", " \t"),
        ("object_name", None),
        ("object_name", "Surface\x00Other"),
        ("name", "Shape\x00Other"),
        ("name", None),
        ("name", ""),
        ("name", 42),
        ("settings", None),
        ("enabled_render", 1),
        ("enabled_viewport", "true"),
        ("show_in_editmode", None),
        ("show_on_cage", True),
        ("unknown", True),
    ],
)
def test_common_creation_is_strict(kind: str, field: str, value: Any) -> None:
    settings = (
        {"target": "Target"}
        if kind == "shrinkwrap"
        else {"operand_object": "Operand"}
        if kind == "boolean"
        else {}
    )
    with pytest.raises(ValidationError):
        CREATE.validate_python(
            {"object_name": "Surface", "type": kind, "settings": settings, field: value}
        )


@pytest.mark.parametrize(
    "kind,settings",
    [
        ("triangulate", {"quad_method": "guess"}),
        ("triangulate", {"ngon_method": "fixed"}),
        ("triangulate", {"min_vertices": 3}),
        ("triangulate", {"min_vertices": 129}),
        ("triangulate", {"keep_custom_normals": 1}),
        ("mirror", {"axes": []}),
        ("mirror", {"axes": ["x", "x"]}),
        ("mirror", {"axes": ["X"]}),
        ("mirror", {"axes": {"x": True}}),
        ("mirror", {"bisect_axes": ["z", "z"]}),
        ("mirror", {"bisect_flip_axes": ["y", "y"]}),
        ("mirror", {"mirror_object": ""}),
        ("mirror", {"mirror_object": "Target\x00Other"}),
        ("mirror", {"mirror_object": "Pivot", "clear_mirror_object": True}),
        ("mirror", {"clear_mirror_object": False}),
        ("mirror", {"clear_mirror_object": 1}),
        ("mirror", {"merge_threshold": -0.1}),
        ("subdivision_surface", {"levels": -1}),
        ("subdivision_surface", {"levels": 7}),
        ("subdivision_surface", {"render_levels": 7}),
        ("subdivision_surface", {"levels": True}),
        ("subdivision_surface", {"levels": 2.0}),
        ("subdivision_surface", {"levels": "2"}),
        ("subdivision_surface", {"mode": "smooth"}),
        ("subdivision_surface", {"quality": 99}),
        ("subdivision_surface", {"uv_smooth": "invalid"}),
        ("shrinkwrap", {}),
        ("shrinkwrap", {"target": ""}),
        ("shrinkwrap", {"target": "Target\x00Other"}),
        ("shrinkwrap", {"target": "Target", "method": "nearest_vertex"}),
        ("shrinkwrap", {"target": "Target", "mode": "invalid"}),
        ("shrinkwrap", {"target": "Target", "projection": {"axes": ["x", "x"]}}),
        ("shrinkwrap", {"target": "Target", "projection": {"positive": 0}}),
        ("shrinkwrap", {"target": "Target", "projection": {"limit": -1}}),
        ("boolean", {}),
        ("boolean", {"operand_object": ""}),
        ("boolean", {"operand_object": "Target\x00Other"}),
        ("boolean", {"operand_object": "Operand", "operation": "subtract"}),
        ("boolean", {"operand_object": "Operand", "solver": "fast"}),
        ("boolean", {"operand_object": "Operand", "collection": "Collection"}),
        ("solidify", {"even_thickness": 1}),
        ("solidify", {"rim": "false"}),
        ("solidify", {"mode": "complex"}),
    ],
)
def test_invalid_type_settings(kind: str, settings: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        create(kind, **settings)


@pytest.mark.parametrize(
    "value", [None, math.nan, math.inf, -math.inf, True, False, "0.1", 3.5e38]
)
@pytest.mark.parametrize(
    "kind,field",
    [
        ("mirror", "merge_threshold"),
        ("solidify", "thickness"),
        ("solidify", "offset"),
        ("shrinkwrap", "offset"),
    ],
)
def test_numeric_settings_reject_nonfinite_coercion_and_float32_overflow(
    kind: str, field: str, value: Any
) -> None:
    supplied = {"target": "Target"} if kind == "shrinkwrap" else {}
    with pytest.raises(ValidationError):
        create(kind, **supplied, **{field: value})


@pytest.mark.parametrize("mode", ["catmull_clark", "simple"])
@pytest.mark.parametrize("level", [0, 1, 6])
def test_subdivision_native_modes_and_safety_boundary(mode: str, level: int) -> None:
    args = create("subdivision_surface", mode=mode, levels=level, render_levels=level)
    assert args.settings.levels == level


@pytest.mark.parametrize(
    "method", ["nearest_surface", "project", "target_normal_project"]
)
@pytest.mark.parametrize(
    "mode", ["on_surface", "inside", "outside", "outside_surface", "above_surface"]
)
def test_shrinkwrap_typed_methods_and_modes(method: str, mode: str) -> None:
    args = create("shrinkwrap", target="Target", method=method, mode=mode, offset=-0.1)
    assert args.settings.offset == pytest.approx(-0.1)


@pytest.mark.parametrize("operation", ["union", "intersect", "difference"])
@pytest.mark.parametrize("solver", ["float", "exact", "manifold"])
def test_current_boolean_solver_contract(operation: str, solver: str) -> None:
    assert (
        create(
            "boolean", operand_object="Operand", operation=operation, solver=solver
        ).settings.solver
        == solver
    )


def test_mirror_axes_bisect_clipping_and_explicit_reference_clear() -> None:
    args = create(
        "mirror",
        axes=["z", "x"],
        bisect_axes=["x"],
        bisect_flip_axes=["x"],
        clipping=True,
        merge=False,
        merge_threshold=0,
    )
    assert args.settings.axes == ["z", "x"]
    patch = CONFIGURE.validate_python(
        {
            "object_name": "Surface",
            "modifier_name": "Shape",
            "type": "mirror",
            "settings": {"clear_mirror_object": True},
        }
    )
    assert patch.type == "mirror"
    assert patch.settings.clear_mirror_object is True


@pytest.mark.parametrize("thickness,offset", [(-0.5, -2), (0, 0), (0.5, 2)])
def test_solidify_signed_native_ranges(thickness: float, offset: float) -> None:
    args = create(
        "solidify",
        thickness=thickness,
        offset=offset,
        rim=True,
        rim_only=True,
        even_thickness=True,
        quality_normals=True,
    )
    assert args.settings.thickness == thickness


@pytest.mark.parametrize(
    "model",
    [
        ModifierInspectArguments,
        EvaluatedMeshArguments,
        ModifierMoveArguments,
        ModifierRemoveArguments,
        ModifierApplyArguments,
    ],
)
@pytest.mark.parametrize("bad", [None, "", " \n", 1, "Surface\x00Other"])
def test_named_operations_reject_invalid_object_names(model: Any, bad: Any) -> None:
    fields = {"object_name": bad}
    if "modifier_name" in model.model_fields:
        fields["modifier_name"] = "Shape"
    if "index" in model.model_fields:
        fields["index"] = 0
    with pytest.raises(ValidationError):
        model.model_validate(fields)


@pytest.mark.parametrize("index", [-1, True, 1.5, "1", None])
def test_move_index_is_a_strict_nonnegative_integer(index: Any) -> None:
    with pytest.raises(ValidationError):
        ModifierMoveArguments.model_validate(
            {"object_name": "Surface", "modifier_name": "Shape", "index": index}
        )


def test_inspection_preserves_unsupported_and_out_of_policy_native_state() -> None:
    unsupported = ModifierSummary(
        name="Other",
        index=0,
        type="nodes",
        supported=False,
        enabled_viewport=False,
        enabled_render=True,
        show_in_editmode=False,
        show_on_cage=None,
        settings=None,
    )
    subdiv = unsupported.model_copy(
        update={
            "name": "Fine",
            "index": 1,
            "type": "subdivision_surface",
            "supported": True,
            "settings": SubdivisionSettings(
                mode="simple",
                levels=11,
                render_levels=11,
                uv_smooth="none",
                boundary_smooth="all",
                use_creases=True,
                show_only_control_edges=True,
            ),
        }
    )
    mirror = MirrorSettings(
        uv_flip_u=False,
        uv_flip_v=False,
        uv_flip_per_tile=False,
        uv_flip_offset_u=0,
        uv_flip_offset_v=0,
        uv_offset_u=0,
        uv_offset_v=0,
        axes=[],
        bisect_axes=[],
        bisect_flip_axes=[],
        clipping=False,
        merge=True,
        merge_threshold=0,
        mirror_object=None,
    )
    assert mirror.axes == []
    result = ModifierInspectResult(
        object_name="Surface", modifiers=[unsupported, subdiv]
    )
    data = result.model_dump(mode="json")
    assert [m["name"] for m in data["modifiers"]] == ["Other", "Fine"]
    assert data["modifiers"][0]["settings"] is None
    assert data["modifiers"][1]["settings"]["levels"] == 11


@pytest.mark.parametrize(
    "operation,fields,method",
    [
        ("modifier.inspect", {}, "modifier_inspect"),
        ("modifier.create", {"type": "mirror"}, "modifier_create"),
        (
            "modifier.configure",
            {"type": "solidify", "modifier_name": "Shape"},
            "modifier_configure",
        ),
        ("modifier.move", {"modifier_name": "Shape", "index": 0}, "modifier_move"),
        ("modifier.remove", {"modifier_name": "Shape"}, "modifier_remove"),
        ("modifier.apply", {"modifier_name": "Shape"}, "modifier_apply"),
        ("mesh.inspect_evaluated", {}, "mesh_inspect_evaluated"),
    ],
)
def test_registered_typed_dispatch_and_evaluated_summary(
    operation: str, fields: dict[str, Any], method: str
) -> None:
    name = "blender." + operation
    assert name in OPERATIONS
    backend = Backend()
    response = execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="modifiers",
            operation=name,
            arguments={"object_name": "Surface", **fields},
        ),
    )
    assert isinstance(response, OperationSuccess)
    assert backend.calls[0] == method
    if operation == "mesh.inspect_evaluated":
        assert isinstance(response.result, dict)
        assert response.result["evaluation"] == "viewport"
        assert response.result["modifier_stack"] == []
        assert "vertices" not in response.result
        assert "mesh_name" not in response.result


@pytest.mark.parametrize(
    "code",
    [
        "modifier_not_found",
        "modifier_type_unsupported",
        "modifier_dependency_invalid",
        "modifier_apply_failed",
        "mesh_has_shape_keys",
        "invalid_context",
        "object_not_found",
        "object_not_mesh",
    ],
)
def test_modifier_errors_remain_structured(
    code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Backend()

    def fail(arguments: ModifierApplyArguments) -> Any:
        raise OperationError(code, "Failure without internal state")

    monkeypatch.setattr(backend, "modifier_apply", fail)
    result = execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="modifiers",
            operation="blender.modifier.apply",
            arguments={"object_name": "Surface", "modifier_name": "Shape"},
        ),
    )
    assert isinstance(result, OperationFailure)
    assert result.error.code == code
    assert backend.calls == []


@pytest.mark.parametrize(
    "model", [ModifierMoveArguments, ModifierRemoveArguments, ModifierApplyArguments]
)
def test_modifier_lookup_names_cannot_truncate_at_nul(model: Any) -> None:
    fields: dict[str, Any] = {
        "object_name": "Surface",
        "modifier_name": "Shape\x00Other",
    }
    if "index" in model.model_fields:
        fields["index"] = 0
    with pytest.raises(ValidationError):
        model.model_validate(fields)
