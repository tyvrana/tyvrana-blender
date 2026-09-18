"""Remesh dispatch, strict inputs, and allocation guards without Blender work."""

from typing import Any

import pytest
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender.operations import OPERATIONS, execute
from tyvrana_blender.remesh_models import (
    MAX_GRID_CELLS,
    VoxelRemeshArguments,
    VoxelRemeshInspectArguments,
    estimate_grid,
)

from .test_operations import Backend


@pytest.mark.parametrize("suffix", ["", ".inspect"])
def test_dispatch_and_description(suffix: str) -> None:
    backend = Backend()
    result = execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="remesh",
            operation="blender.sculpt.voxel_remesh" + suffix,
            arguments={"object_name": "Surface", "voxel_size": 0.1},
        ),
    )
    assert isinstance(result, OperationSuccess), result
    assert backend.calls[0] == "sculpt_voxel_remesh" + suffix.replace(".", "_")
    assert (
        OPERATIONS == tuple(sorted(set(OPERATIONS)))
        and tuple(sorted(OPERATIONS)) == OPERATIONS
    )
    if suffix:
        assert "not production retopology" in str(result.result)
        assert "indices become invalid" in str(result.result)


@pytest.mark.parametrize(
    "field,values",
    [
        (
            "voxel_size",
            [0, -1, 0.00001, 1001, True, "0.1", None, float("nan"), float("inf")],
        ),
        ("adaptivity", [-0.01, 1.01, True, "0", None, float("nan")]),
        ("preserve_volume", [1, "true", None]),
        ("preserve_attributes", [0, "false", None]),
        ("discard_uv_maps", [0, "true", None]),
        ("fix_poles", [1, "true", None]),
        ("object_name", ["", " ", "bad\x00name", "\ud800", None]),
        ("unexpected", [True]),
    ],
)
@pytest.mark.parametrize("model", [VoxelRemeshArguments, VoxelRemeshInspectArguments])
def test_invalid_inputs_rejected(model: Any, field: str, values: list[Any]) -> None:
    for value in values:
        with pytest.raises(ValueError):
            model.model_validate(
                {"object_name": "Surface", "voxel_size": 0.1, field: value}
            )


def test_required_size_and_default_inspection() -> None:
    with pytest.raises(ValueError):
        VoxelRemeshArguments(object_name="Surface")  # type: ignore[call-arg]
    args = VoxelRemeshInspectArguments(object_name="Surface")
    assert args.voxel_size == 0.1 and args.adaptivity == 0
    assert args.preserve_volume and args.preserve_attributes and args.fix_poles
    assert not args.discard_uv_maps
    smallest = VoxelRemeshArguments(object_name="Surface", voxel_size=0.0001)
    assert VoxelRemeshArguments.model_validate(smallest.model_dump()) == smallest


def test_invalid_request_never_reaches_backend() -> None:
    backend = Backend()
    result = execute(
        backend,
        OperationRequest.model_construct(
            type="operation.request",
            request_id="invalid",
            operation="blender.sculpt.voxel_remesh",
            arguments={"object_name": "Surface", "voxel_size": True},
        ),
    )
    assert isinstance(result, OperationFailure)
    assert result.error.code == "invalid_arguments" and backend.calls == []


def test_grid_exact_boundary_and_overflow_safe_rejection() -> None:
    # Padded dimensions 100 x 100 x 200, using exactly representable unit cells.
    exact = estimate_grid([0, 0, 0], [92, 92, 192], 1)
    assert exact.cells == MAX_GRID_CELLS and not exact.exceeds_limit
    assert estimate_grid([0, 0, 0], [92, 92, 193], 1).exceeds_limit
    oversized: list[tuple[list[float], float]] = [
        ([2, 2, 2], 0.0001),
        ([1e308] * 3, 0.0001),
        ([1e6] * 3, 0.1),
    ]
    for bounds, size in oversized:
        huge = estimate_grid([0, 0, 0], bounds, size)
        assert huge.exceeds_limit and huge.cells is None
    far = estimate_grid([1e6, 1e6, 1e6], [1e6 + 1, 1e6 + 1, 1e6 + 1], 0.1)
    assert not far.exceeds_limit and far.coordinate_limit_exceeded
