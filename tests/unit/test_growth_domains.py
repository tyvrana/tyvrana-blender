"""Bounded semantic field and root-row contracts."""

import pytest
from pydantic import ValidationError

from tyvrana_blender.growth_domain_models import GrowthField, GrowthRow
from tyvrana_blender.growth_models import GrowthCreateArguments, GrowthRegion


def test_ordered_region_is_exclusive_and_counts_mirrors() -> None:
    row = dict(name="Row", path=[[0.1, 0.1], [0.9, 0.1]], count=2048, mirror="v")
    with pytest.raises(ValidationError, match="guides=children=0"):
        GrowthRegion(name="Panel", family="Strip", rows=[row])
    region = dict(name="Panel", family="Strip", guides=0, rows=[row])
    spec = GrowthCreateArguments(
        name="Growth",
        surface="Surface",
        families=[dict(name="Strip")],
        regions=[region],
    )
    assert spec.regions[0].rows[0].count == 2048
    with pytest.raises(ValidationError, match="10000 guides"):
        GrowthCreateArguments(
            name="Growth",
            surface="Surface",
            families=[dict(name="Strip")],
            regions=[{**region, "name": str(i)} for i in range(3)],
        )
    with pytest.raises(ValidationError, match="families must be declared"):
        GrowthCreateArguments(
            name="Growth",
            surface="Surface",
            families=[dict(name="Strip")],
            regions=[{**region, "rows": [{**row, "family": "Missing"}]}],
        )


@pytest.mark.parametrize(
    "changes",
    [
        dict(count=None),
        dict(spacing=0.1),
        dict(count=None, spacing=0),
        dict(count=2049),
        dict(path=[[0, 0], [0, 0]]),
        dict(mirror="z"),
        dict(path=[[0, 0, 0], [1, 1, 1]]),
    ],
)
def test_invalid_rows(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GrowthRow.model_validate(dict(name="Row", path=[[0, 0], [1, 1]]) | changes)


def test_field_limits_and_singular_controls() -> None:
    control = dict(uv=[0.2, 0.3], direction=[1, 0])
    for controls in [
        [],
        [control] * 2,
        [dict(uv=[0, 0], direction=[0, 0])],
        [dict(uv=[i, 0], direction=[1, 0]) for i in range(65)],
    ]:
        with pytest.raises(ValidationError):
            GrowthField(controls=controls)
    assert GrowthField(controls=[control]).controls[0].length_scale == 1
