import math
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender.mesh_models import (
    AllSelector,
    BoundarySelector,
    BoxSelector,
    EdgeDetail,
    EdgeQueryResult,
    FaceDetail,
    FaceQueryResult,
    IndexSelector,
    MeshBevelArguments,
    MeshDeleteArguments,
    MeshEditResult,
    MeshElementSelector,
    MeshExtrudeArguments,
    MeshInsetArguments,
    MeshInspectArguments,
    MeshMergeArguments,
    MeshNormalsArguments,
    MeshQueryArguments,
    MeshQueryResult,
    MeshSeamArguments,
    MeshShadingArguments,
    MeshSubdivideArguments,
    MeshSummary,
    MeshTransformArguments,
    NormalSelector,
    SeamSelector,
    VertexDetail,
    VertexQueryResult,
)
from tyvrana_blender.mesh_selectors import SelectionError, select
from tyvrana_blender.models import Model
from tyvrana_blender.operations import OPERATIONS, OperationError, execute

from .test_operations import Backend

SELECTOR: TypeAdapter[MeshElementSelector] = TypeAdapter(MeshElementSelector)
FACE = {"domain": "face", "mode": "all"}
EDGE = {"domain": "edge", "mode": "all"}
VERTEX = {"domain": "vertex", "mode": "all"}

VALID_SELECTORS = [
    *({"domain": domain, "mode": "all"} for domain in ["vertex", "edge", "face"]),
    *(
        {"domain": domain, "mode": "indices", "indices": [4, 0, 2]}
        for domain in ["vertex", "edge", "face"]
    ),
    *(
        {"domain": domain, "mode": "box", "min": [-1, 0, 0], "max": [1, 2, 3]}
        for domain in ["vertex", "face"]
    ),
    {"domain": "face", "mode": "normal", "direction": [0, 0, 2], "min_dot": 1},
    {"domain": "edge", "mode": "boundary"},
    *({"domain": "edge", "mode": "seam", "value": flag} for flag in [False, True]),
]

CASES = [
    (MeshInspectArguments, "inspect", {}, "mesh_inspect"),
    (MeshQueryArguments, "query", {"selector": VERTEX}, "mesh_query"),
    (MeshQueryArguments, "query", {"selector": EDGE, "limit": 256}, "mesh_query"),
    (MeshQueryArguments, "query", {"selector": FACE, "limit": 1}, "mesh_query"),
    (
        MeshTransformArguments,
        "transform",
        {"selector": VERTEX, "translation": [1, 2, 3]},
        "mesh_edit",
    ),
    (
        MeshTransformArguments,
        "transform",
        {
            "selector": VERTEX,
            "translation": [0, 0, 0.2],
            "falloff": {"center": [0, 0, 0], "radii": [1, 2, 3]},
        },
        "mesh_edit",
    ),
    (
        MeshTransformArguments,
        "transform",
        {"selector": EDGE, "rotation": [0, 0, math.pi], "pivot": "origin"},
        "mesh_edit",
    ),
    (
        MeshTransformArguments,
        "transform",
        {"selector": FACE, "scale": [2, 1, 1], "pivot": [0, 0, 1]},
        "mesh_edit",
    ),
    (
        MeshExtrudeArguments,
        "extrude_faces",
        {"selector": FACE, "offset": [0, 0, 1], "scale": [0.5, 0.5, 1]},
        "mesh_edit",
    ),
    (
        MeshInsetArguments,
        "inset_faces",
        {"selector": FACE, "thickness": 0.1, "depth": -0.2, "even_offset": False},
        "mesh_edit",
    ),
    (
        MeshBevelArguments,
        "bevel_edges",
        {"selector": EDGE, "width": 0.1, "segments": 16, "profile": 0.5},
        "mesh_edit",
    ),
    (
        MeshSubdivideArguments,
        "subdivide_edges",
        {"selector": EDGE, "cuts": 32, "smooth": 1},
        "mesh_edit",
    ),
    (
        MeshDeleteArguments,
        "delete_elements",
        {"selector": FACE, "face_mode": "faces_and_unused"},
        "mesh_edit",
    ),
    (MeshDeleteArguments, "delete_elements", {"selector": VERTEX}, "mesh_edit"),
    (
        MeshMergeArguments,
        "merge_vertices",
        {"selector": VERTEX, "mode": "center"},
        "mesh_edit",
    ),
    (MeshSeamArguments, "mark_seam", {"selector": EDGE, "seam": True}, "mesh_edit"),
    (MeshNormalsArguments, "recalculate_normals", {"inside": True}, "mesh_edit"),
    (
        MeshShadingArguments,
        "set_shading",
        {"selector": FACE, "smooth": True},
        "mesh_edit",
    ),
    (
        MeshShadingArguments,
        "set_shading",
        {"selector": FACE, "smooth": False},
        "mesh_edit",
    ),
]


@pytest.mark.parametrize("data", VALID_SELECTORS)
def test_selector_union_roundtrip_strict_properties(data: dict[str, Any]) -> None:
    value = SELECTOR.validate_python(data)
    assert SELECTOR.validate_json(value.model_dump_json()) == value
    assert value.model_json_schema()["additionalProperties"] is False
    if isinstance(value, IndexSelector):
        assert value.indices == [0, 2, 4]
    for key in data:
        with pytest.raises(ValidationError):
            SELECTOR.validate_python({**data, key: None})
    with pytest.raises(ValidationError):
        SELECTOR.validate_python({**data, "selected": True})


@pytest.mark.parametrize(
    "data",
    [
        {"domain": "vertex", "mode": "boundary"},
        {"domain": "face", "mode": "seam", "value": True},
        {"domain": "edge", "mode": "normal", "direction": [0, 0, 1], "min_dot": 0},
        {"domain": "edge", "mode": "box", "min": [0, 0, 0], "max": [1, 1, 1]},
        {"domain": "face", "mode": "normal", "direction": [0, 0, 0], "min_dot": 0},
        {"domain": "face", "mode": "normal", "direction": [0, 0, 1], "min_dot": 1.01},
        {"domain": "face", "mode": "normal", "direction": [0, 0, 1], "min_dot": -1.01},
        {"domain": "vertex", "mode": "indices", "indices": [0, 0]},
        {"domain": "vertex", "mode": "indices", "indices": [-1]},
        {"domain": "vertex", "mode": "indices", "indices": []},
        {"domain": "vertex", "mode": "indices", "indices": list(range(4097))},
        {"domain": "vertex", "mode": "indices", "indices": [True]},
        {"domain": "vertex", "mode": "indices", "indices": [1.0]},
        {"domain": "vertex", "mode": "indices", "indices": ["1"]},
        {"domain": "vertex", "mode": "box", "min": [1, 0, 0], "max": [0, 1, 1]},
        {"domain": "vertex", "mode": "all", "indices": [0]},
        {"domain": "face", "mode": "expression", "expression": "all"},
        {"domain": "edge", "mode": "seam", "value": 1},
    ],
)
def test_invalid_selector_shapes(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SELECTOR.validate_python(data)


@pytest.mark.parametrize(
    "bad", [True, "1", float("nan"), float("inf"), -float("inf"), 1e40, 1e-50]
)
@pytest.mark.parametrize("kind", ["box", "normal"])
def test_selector_numeric_strictness(bad: Any, kind: str) -> None:
    data = (
        {"domain": "face", "mode": "normal", "direction": [bad, 0, 1], "min_dot": 0}
        if kind == "normal"
        else {"domain": "vertex", "mode": "box", "min": [bad, 0, 0], "max": [1, 1, 1]}
    )
    with pytest.raises(ValidationError):
        SELECTOR.validate_python(data)


@pytest.mark.parametrize("model,operation,data,called", CASES)
def test_argument_contracts_and_registered_dispatch(
    model: type[Model], operation: str, data: dict[str, Any], called: str
) -> None:
    args = {"object_name": "Mesh", **data}
    value = model.model_validate(args)
    assert model.model_validate_json(value.model_dump_json(exclude_none=True)) == value
    assert model.model_json_schema()["additionalProperties"] is False
    for key in model.model_fields:
        with pytest.raises(ValidationError):
            model.model_validate({**args, key: None})
    with pytest.raises(ValidationError):
        model.model_validate({**args, "unexpected": 0})
    backend = Backend()
    result = execute(
        backend,
        OperationRequest.model_validate(
            {
                "type": "operation.request",
                "request_id": "test",
                "operation": "blender.mesh." + operation,
                "arguments": args,
            }
        ),
    )
    assert isinstance(result, OperationSuccess), result
    assert backend.calls == [called]
    assert "blender.mesh." + operation in OPERATIONS


@pytest.mark.parametrize(
    "model,data",
    [
        (MeshTransformArguments, {"selector": VERTEX}),
        (MeshShadingArguments, {"selector": EDGE, "smooth": True}),
        (MeshShadingArguments, {"selector": FACE}),
        *[
            (MeshShadingArguments, {"selector": FACE, "smooth": value})
            for value in [0, 1, "true"]
        ],
        (
            MeshTransformArguments,
            {"selector": VERTEX, "translation": [0, 0, 1], "pivot": "cursor"},
        ),
        (MeshExtrudeArguments, {"selector": EDGE, "offset": [0, 0, 1]}),
        (MeshExtrudeArguments, {"selector": FACE, "offset": [0, 0, 0]}),
        (
            MeshExtrudeArguments,
            {"selector": FACE, "offset": [0, 0, 1], "scale": [0, 1, 1]},
        ),
        (MeshInsetArguments, {"selector": VERTEX, "thickness": 1}),
        (MeshInsetArguments, {"selector": FACE, "thickness": 0}),
        (MeshBevelArguments, {"selector": FACE, "width": 1}),
        (MeshBevelArguments, {"selector": EDGE, "width": -1}),
        (MeshBevelArguments, {"selector": EDGE, "width": 1, "segments": 17}),
        (MeshBevelArguments, {"selector": EDGE, "width": 1, "segments": 0}),
        (MeshBevelArguments, {"selector": EDGE, "width": 1, "profile": 1.01}),
        (MeshSubdivideArguments, {"selector": VERTEX}),
        (MeshSubdivideArguments, {"selector": EDGE, "cuts": 33}),
        (MeshSubdivideArguments, {"selector": EDGE, "cuts": 0}),
        (MeshSubdivideArguments, {"selector": EDGE, "smooth": -1}),
        (MeshDeleteArguments, {"selector": EDGE, "face_mode": "faces_only"}),
        (MeshDeleteArguments, {"selector": FACE, "face_mode": "EDGES"}),
        (MeshMergeArguments, {"selector": FACE}),
        (MeshMergeArguments, {"selector": VERTEX, "mode": "first"}),
        (MeshSeamArguments, {"selector": FACE, "seam": True}),
        (MeshQueryArguments, {"selector": FACE, "limit": 257}),
        (MeshQueryArguments, {"selector": FACE, "limit": 0}),
    ],
)
def test_invalid_operation_settings(model: type[Model], data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"object_name": "Mesh", **data})


@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf"), 1e40, 1e-50])
@pytest.mark.parametrize("field", ["width", "profile"])
def test_mesh_scalar_numeric_strictness(bad: Any, field: str) -> None:
    with pytest.raises(ValidationError):
        MeshBevelArguments.model_validate(
            {"object_name": "Mesh", "selector": EDGE, "width": 1, field: bad}
        )


def fake_topology() -> Any:
    vertices = [
        SimpleNamespace(index=i, co=(0, 0, i - 1), select=i % 2 == 0) for i in range(3)
    ]
    faces = [
        SimpleNamespace(
            index=i, normal=normal, calc_center_median=lambda i=i: (0, 0, i)
        )
        for i, normal in enumerate([(0, 0, 1), (0, 0, -1), (0, 0, 0)])
    ]
    edges = [
        SimpleNamespace(index=i, is_boundary=i == 0, seam=i == 1) for i in range(3)
    ]
    return SimpleNamespace(verts=vertices, edges=edges, faces=faces)


def test_predicates_boundaries_zero_normals_and_order_ignore_ui() -> None:
    bm = fake_topology()
    assert [v.index for v in select(bm, AllSelector(domain="vertex", mode="all"))] == [
        0,
        1,
        2,
    ]
    assert [
        v.index
        for v in select(
            bm, IndexSelector(domain="vertex", mode="indices", indices=[2, 0])
        )
    ] == [0, 2]
    assert [
        v.index
        for v in select(
            bm, BoxSelector(domain="vertex", mode="box", min=[0, 0, 0], max=[0, 0, 1])
        )
    ] == [1, 2]
    assert [
        v.index
        for v in select(
            bm, BoxSelector(domain="face", mode="box", min=[0, 0, 0], max=[0, 0, 1])
        )
    ] == [0, 1]
    assert [
        f.index
        for f in select(
            bm,
            NormalSelector(
                domain="face", mode="normal", direction=[0, 0, 2], min_dot=1
            ),
        )
    ] == [0]
    assert [
        f.index
        for f in select(
            bm,
            NormalSelector(
                domain="face", mode="normal", direction=[0, 0, 2], min_dot=-1
            ),
        )
    ] == [0, 1]
    assert [
        e.index for e in select(bm, BoundarySelector(domain="edge", mode="boundary"))
    ] == [0]
    assert [
        e.index
        for e in select(bm, SeamSelector(domain="edge", mode="seam", value=True))
    ] == [1]
    assert [
        e.index
        for e in select(bm, SeamSelector(domain="edge", mode="seam", value=False))
    ] == [0, 2]
    assert (
        list(
            select(
                bm,
                BoxSelector(domain="vertex", mode="box", min=[4, 4, 4], max=[5, 5, 5]),
            )
        )
        == []
    )
    with pytest.raises(SelectionError):
        list(select(bm, IndexSelector(domain="vertex", mode="indices", indices=[0, 3])))


def test_summary_and_query_serialization() -> None:
    state = Backend().mesh_inspect(MeshInspectArguments(object_name="Mesh"))
    assert state.bounds_min is state.bounds_max is None
    assert MeshSummary.model_validate_json(state.model_dump_json()) == state
    results = [
        VertexQueryResult(
            object_name="Mesh",
            matched_count=1000,
            truncated=True,
            elements=[VertexDetail(index=0, co=[1, 2, 3])],
        ),
        EdgeQueryResult(
            object_name="Mesh",
            matched_count=1,
            truncated=False,
            elements=[
                EdgeDetail(
                    index=0,
                    vertices=[0, 1],
                    seam=True,
                    sharp=False,
                    boundary=True,
                    manifold=False,
                )
            ],
        ),
        FaceQueryResult(
            object_name="Mesh",
            matched_count=1,
            truncated=False,
            elements=[
                FaceDetail(
                    index=0,
                    vertices=[0, 1, 2],
                    vertex_count=3,
                    vertices_truncated=False,
                    center=[0, 0, 0],
                    normal=[0, 0, 1],
                    area=0.5,
                    material_index=0,
                    smooth=False,
                )
            ],
        ),
    ]
    adapter: TypeAdapter[MeshQueryResult] = TypeAdapter(MeshQueryResult)
    for result in results:
        assert adapter.validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "code",
    [
        "object_not_mesh",
        "mesh_selection_empty",
        "mesh_has_shape_keys",
        "invalid_context",
        "invalid_arguments",
        "operation_failed",
    ],
)
def test_mesh_errors_remain_structured(code: str) -> None:
    class Failing(Backend):
        def mesh_edit(self, arguments: Any) -> MeshEditResult:
            raise OperationError(code, "Cannot edit mesh")

    result = execute(
        Failing(),
        OperationRequest(
            type="operation.request",
            request_id="test",
            operation="blender.mesh.recalculate_normals",
            arguments={"object_name": "Mesh"},
        ),
    )
    assert isinstance(result, OperationFailure) and result.error.code == code


@pytest.mark.parametrize("field", ["translation", "rotation", "scale", "pivot"])
@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf"), 1e40, 1e-50])
def test_transform_vectors_are_strict(field: str, bad: Any) -> None:
    with pytest.raises(ValidationError):
        MeshTransformArguments.model_validate(
            {
                "object_name": "Mesh",
                "selector": VERTEX,
                "translation": [0, 0, 1],
                field: [bad, 1, 1],
            }
        )


@pytest.mark.parametrize(
    "falloff",
    [
        {"center": [0, 0, 0], "radii": [0, 1, 1]},
        {"center": [0, 0, 0], "radii": [-1, 1, 1]},
        {"center": [0, 0, 0], "radii": [1, 1]},
        {"center": [0, 0, 0], "radii": None},
        {"center": None, "radii": [1, 1, 1]},
        {"center": [0, 0, 0], "radii": [1, 1, 1], "expression": "distance"},
        *[
            {"center": [0, 0, 0], "radii": [bad, 1, 1]}
            for bad in [True, "1", float("nan"), float("inf"), 1e40, 1e-50]
        ],
        *[
            {"center": [bad, 0, 0], "radii": [1, 1, 1]}
            for bad in [True, "1", float("nan"), float("inf"), 1e40, 1e-50]
        ],
    ],
)
def test_transform_falloff_rejects_invalid_regions(falloff: Any) -> None:
    with pytest.raises(ValidationError):
        MeshTransformArguments.model_validate(
            {
                "object_name": "Mesh",
                "selector": VERTEX,
                "translation": [0, 0, 1],
                "falloff": falloff,
            }
        )


@pytest.mark.parametrize("field", ["rotation", "scale"])
@pytest.mark.parametrize("translate", [False, True])
def test_falloff_requires_translation_only(field: str, translate: bool) -> None:
    with pytest.raises(ValidationError):
        MeshTransformArguments.model_validate(
            {
                "object_name": "Mesh",
                "selector": VERTEX,
                field: [1, 1, 1],
                "falloff": {"center": [0, 0, 0], "radii": [1, 1, 1]},
                **({"translation": [0, 0, 1]} if translate else {}),
            }
        )
