from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.deformation_sweep_models import EvaluationPose
from tyvrana_blender.growth_models import (
    GrowthConfigureArguments,
    GrowthCreateArguments,
    GrowthFamily,
    GrowthGuideEdit,
    GrowthInspectArguments,
    GrowthRegion,
    GrowthSampleArguments,
)
from tyvrana_blender.operations import OPERATIONS, REGISTRY


def test_compact_defaults_and_registry() -> None:
    spec = GrowthCreateArguments(
        name="Field",
        surface="Carrier",
        families=[GrowthFamily(name="Fiber")],
        regions=[GrowthRegion(name="Patch", family="Fiber")],
    )
    assert spec.regions[0].children == 0
    assert GrowthInspectArguments(object_name="Field").guide_limit == 0
    names = {name for name in OPERATIONS if name.startswith("blender.growth.")}
    assert names == {
        "blender.growth.create",
        "blender.growth.configure",
        "blender.growth.inspect",
        "blender.growth.remove",
        "blender.growth.sample",
        "blender.growth.dynamics.bake",
        "blender.growth.dynamics.status",
        "blender.growth.dynamics.cancel",
        "blender.growth.dynamics.inspect",
        "blender.growth.dynamics.clear",
    }
    contracts = {name: spec.contract for name, spec in REGISTRY.items()}
    assert contracts["blender.growth.inspect"].effect == "read_only"
    assert contracts["blender.growth.sample"].effect == "transient"
    assert "normal curve.*" in contracts["blender.growth.create"].description


@pytest.mark.parametrize(
    "change",
    [
        {"families": [{"name": "Fiber"}, {"name": "Fiber"}]},
        {"regions": [{"name": "A", "family": "Absent"}]},
        {"regions": [{"name": "A", "family": "Fiber", "guides": 10001}]},
        {"regions": [{"name": "A", "family": "Fiber", "children": 50001}]},
        {"regions": [{"name": "A", "family": "Fiber", "flow": [0, 0, 0]}]},
        {
            "regions": [
                {
                    "name": "A",
                    "family": "Fiber",
                    "selector": {"domain": "edge", "mode": "all"},
                }
            ]
        },
        {"families": [{"name": "Fiber", "shape": [[1, 0, 0], [1, 0, 1]]}]},
        {"families": [{"name": "Fiber", "shape": [[0, 0, 0], [0, 0, 0]]}]},
        {"families": [{"name": "Fiber", "radius": float("nan")}]},
    ],
)
def test_invalid_creation_rejected(change: dict[str, object]) -> None:
    data: dict[str, Any] = {
        "name": "Field",
        "surface": "Carrier",
        "families": [{"name": "Fiber"}],
        "regions": [{"name": "A", "family": "Fiber"}],
    }
    data.update(change)
    with pytest.raises(ValidationError):
        GrowthCreateArguments.model_validate(data)


def test_combined_scale_and_edit_bounds() -> None:
    with pytest.raises(ValidationError):
        GrowthCreateArguments(
            name="Field",
            surface="Carrier",
            families=[GrowthFamily(name="Fiber")],
            regions=[
                GrowthRegion(name="A", family="Fiber", guides=6000),
                GrowthRegion(name="B", family="Fiber", guides=6000),
            ],
        )
    with pytest.raises(ValidationError):
        GrowthConfigureArguments(object_name="Field")
    with pytest.raises(ValidationError):
        GrowthConfigureArguments(
            object_name="Field",
            guides=[
                GrowthGuideEdit(root_id=1, length_scale=2),
                GrowthGuideEdit(root_id=1, length_scale=3),
            ],
        )
    with pytest.raises(ValidationError):
        GrowthSampleArguments(object_name="Field")
    with pytest.raises(ValidationError):
        GrowthSampleArguments(
            object_name="Field", frames=[1], poses=[EvaluationPose(name="Rest")]
        )
