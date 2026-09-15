"""Safety boundaries for selected-to-active baking contracts."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.bake_models import (
    BakeImageArguments,
    BakeInspectArguments,
    BakeTarget,
)
from tyvrana_blender.models import RenderArguments


def target(**kwargs: object) -> dict[str, object]:
    return {"target": "Low", "sources": ["High"], "uv_map": "UV", **kwargs}


@pytest.mark.parametrize(
    "extra",
    [
        {"sources": ["Low"]},
        {"sources": ["High", "High"]},
        {"cage_extrusion": 0.1},
        {"max_ray_distance": 0.2},
        {"cage_extrusion": 0.1, "max_ray_distance": 0},
        {"cage_extrusion": float("nan"), "max_ray_distance": 1},
        {"cage_extrusion": -0.1, "max_ray_distance": 1},
    ],
)
def test_invalid_projection_is_rejected(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BakeTarget.model_validate(target(**extra))


def test_analysis_permits_omitted_rays_but_execution_does_not() -> None:
    BakeInspectArguments.model_validate({"targets": [target()]})
    with pytest.raises(ValidationError):
        BakeImageArguments.model_validate({"targets": [target()], "name": "Map"})


def test_distinct_target_and_source_roles() -> None:
    for targets in [
        [target(), target()],
        [target(), {"target": "High", "sources": ["Other"], "uv_map": "UV"}],
    ]:
        with pytest.raises(ValidationError):
            BakeInspectArguments.model_validate({"targets": targets})


@pytest.mark.parametrize(
    "extra",
    [
        {"resolution": 8192},
        {"margin": 64, "resolution": 64},
        {"type": "curvature"},
        {"normal_space": "world"},
    ],
)
def test_only_bounded_supported_bakes(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BakeImageArguments.model_validate(
            {
                "targets": [target(cage_extrusion=0.1, max_ray_distance=0.2)],
                "name": "Map",
                **extra,
            }
        )


def test_diagnostics_are_exclusive() -> None:
    with pytest.raises(ValidationError):
        RenderArguments.model_validate(
            {
                "surface": {"objects": ["Low"]},
                "uv_checker": {"objects": ["Low"], "uv_map": "UV"},
            }
        )
    with pytest.raises(ValidationError):
        RenderArguments.model_validate(
            {"surface": {"objects": ["Low"], "exclude_objects": ["Low"]}}
        )
