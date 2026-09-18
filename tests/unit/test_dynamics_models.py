"""Secondary-motion bounds reject unbounded solver and cache work."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.dynamics_models import DynamicsBakeArguments, DynamicsSettings


@pytest.mark.parametrize(
    "patch",
    [
        dict(frame_end=1),
        dict(frame_end=130),
        dict(max_cached_points=4000001),
        dict(max_seconds=601),
        dict(max_solver_work=100000001),
    ],
)
def test_bake_bounds(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DynamicsBakeArguments.model_validate(
            dict(object_name="Field", frame_start=1, frame_end=20) | patch
        )


@pytest.mark.parametrize(
    "data",
    [
        dict(strategy="python"),
        dict(colliders=["A", "A"]),
        dict(substeps=65),
        dict(gravity=[float("nan"), 0, 0]),
        dict(bendiness=1.01),
        dict(properties={}),
    ],
)
def test_typed_settings(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DynamicsSettings.model_validate(data)
