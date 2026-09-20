from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.assembly_models import AssemblySpec
from tyvrana_blender.form_models import (
    FormCreateArguments,
    FormSpec,
    ReferenceCompareArguments,
    ReferenceSurfaceFit,
)
from tyvrana_blender.operations import REGISTRY


def shape() -> dict[str, Any]:
    return {
        "name": "Form",
        "voxel_size": 0.02,
        "parts": [
            {
                "id": "body",
                "kind": "ellipsoid",
                "center": [0, 0, 0],
                "radii": [1, 0.5, 0.7],
            }
        ],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"voxel_size": 0},
        {"max_voxels": 8388609},
        {"parts": [{"id": "code", "kind": "python", "source": "pass"}]},
    ],
)
def test_rejects_invalid_or_unrestricted_form_intent(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FormSpec.model_validate({**shape(), **change})


def test_form_batch_and_discovery_contracts() -> None:
    with pytest.raises(ValidationError, match="unique"):
        FormCreateArguments.model_validate({"forms": [shape(), shape()]})
    assert REGISTRY["blender.form.create"].contract.effect == "mutating"
    assert REGISTRY["blender.form.inspect"].contract.effect == "read_only"
    assert REGISTRY["blender.reference.compare"].contract.output_artifacts == "optional"
    with pytest.raises(ValidationError, match="ray samples"):
        ReferenceCompareArguments.model_validate(
            {
                "resolution": 512,
                "comparisons": [
                    {
                        "id": "a",
                        "objects": [str(i) for i in range(32)],
                        "mask": {"reference": "ref"},
                    },
                    {"id": "b", "objects": ["a"], "mask": {"reference": "ref"}},
                ],
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"radius": 0},
        {"radius": float("nan")},
        {"max_distance": float("inf")},
        {"iterations": 7},
        {"masks": [{"reference": "one"}] * 2},
        {"masks": [{"reference": str(i)} for i in range(97)]},
        {"points": [[0, 0, 0]]},
    ],
)
def test_surface_fit_rejects_unbounded_or_ambiguous_intent(
    change: dict[str, Any],
) -> None:
    data = {
        "masks": [{"reference": "first"}, {"reference": "second"}],
        "radius": 0.1,
        "max_distance": 0.2,
    }
    with pytest.raises(ValidationError):
        ReferenceSurfaceFit.model_validate({**data, **change})


def test_surface_fit_neighborhood_must_resolve_construction_grid() -> None:
    data = shape()
    data["surface_fit"] = {
        "masks": [{"reference": "first"}, {"reference": "second"}],
        "radius": 0.01,
        "max_distance": 0.2,
    }
    with pytest.raises(ValidationError, match="one voxel"):
        FormSpec.model_validate(data)


def test_family_morph_validates_template_references() -> None:
    data = {
        "name": "Family",
        "templates": [{"id": "base", "kind": "form", "spec": shape()}],
        "families": [
            {
                "id": "set",
                "template": "base",
                "count": 15,
                "path": [[0, 0, 0], [0, 30, 0]],
                "morphs": [{"position": 1, "template": "missing"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="unknown template"):
        AssemblySpec.model_validate(data)
