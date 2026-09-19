"""Admission, finite variation, sparse edits and bounded diagnostic contracts."""

import math
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from tyvrana_blender.assembly_models import (
    AssemblyConfigureArguments,
    AssemblyCreateArguments,
)
from tyvrana_blender.cleanup_models import CleanupArguments
from tyvrana_blender.loft_models import (
    LoftConfigureArguments,
    LoftFeature,
    LoftSectionEdit,
    LoftSpec,
)
from tyvrana_blender.models import RenderArguments
from tyvrana_blender.placement_models import PlacementArguments
from tyvrana_blender.reference_models import (
    LandmarkDeriveArguments,
    MeasurementArguments,
)

from ..assembly_fixtures import master_spec, varying_member


def test_sparse_master_limits_and_no_script_or_mesh_escape_hatch() -> None:
    spec = master_spec()
    value = AssemblyCreateArguments.model_validate(spec)
    assert sum(f.count for f in value.families) == 41
    assert len(value.templates) == 6
    assert len(value.model_dump_json()) < 15000
    for field in ("script", "vertices", "expression", "properties"):
        with pytest.raises(ValidationError):
            AssemblyCreateArguments.model_validate({**spec, field: []})
    oversized = deepcopy(spec)
    oversized["families"][3]["count"] = 64
    with pytest.raises(ValidationError, match="components"):
        AssemblyCreateArguments.model_validate(oversized)


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_template",
        "duplicate_family",
        "unknown_mirror",
        "exception_range",
        "exception_duplicate",
        "zero_scale",
        "missing_path",
    ],
)
def test_family_admission(fault: str) -> None:
    spec = master_spec()
    if fault == "unknown_template":
        spec["families"][0]["template"] = "missing"
    elif fault == "duplicate_family":
        spec["families"][1]["id"] = spec["families"][0]["id"]
    elif fault == "unknown_mirror":
        spec["families"][2]["mirror_of"] = "missing"
    elif fault == "exception_range":
        spec["families"][3]["overrides"][0]["index"] = 16
    elif fault == "exception_duplicate":
        spec["families"][3]["overrides"].append(spec["families"][3]["overrides"][0])
    elif fault == "zero_scale":
        spec["families"][3]["scale_start"] = [0, 1, 1]
    else:
        spec["families"][3]["path"] = [[0, 0, 0]]
    with pytest.raises(ValidationError):
        AssemblyCreateArguments.model_validate(spec)


def test_sparse_revision_and_named_sections() -> None:
    value = LoftSpec.model_validate(varying_member())
    assert value.vertex_count() == 1440
    assert len(value.features) == 3
    edit = LoftConfigureArguments.model_validate(
        {
            "components": [
                {
                    "name": value.name,
                    "expected_revision": 1,
                    "section_edits": [{"id": "middle", "center": [0.3, 1.4, 0.2]}],
                }
            ]
        }
    )
    assert edit.components[0].sections is None
    with pytest.raises(ValidationError):
        LoftConfigureArguments.model_validate({"components": [{"name": value.name}]})
    with pytest.raises(ValidationError):
        AssemblyConfigureArguments(name="assembly", expected_revision=1)
    with pytest.raises(ValidationError):
        AssemblyConfigureArguments.model_validate(
            {
                "name": "assembly",
                "expected_revision": 1,
                "members": [{"family": "rail", "index": 0}] * 2,
            }
        )


def test_mirror_counts_resolve_forward_chains_and_exceptions() -> None:
    spec = master_spec()
    spec["families"][2].pop("count")
    spec["families"][2]["overrides"] = [{"index": 1, "offset": [0, 0, 0.1]}]
    spec["families"].insert(0, {"id": "reflection", "mirror_of": "membersR"})
    value = AssemblyCreateArguments.model_validate(spec)
    assert value.families[0].count == 2
    assert AssemblyCreateArguments.model_validate_json(value.model_dump_json()) == value
    for fault, pattern in (
        ("count", "match source"),
        ("cycle", "cycle"),
        ("range", "index"),
    ):
        invalid = deepcopy(spec)
        if fault == "count":
            invalid["families"][0]["count"] = 3
        elif fault == "cycle":
            invalid["families"][3]["mirror_of"] = "reflection"
        else:
            invalid["families"][0]["overrides"] = [{"index": 2}]
        with pytest.raises(ValidationError, match=pattern):
            AssemblyCreateArguments.model_validate(invalid)


def test_family_revision_can_patch_only_progression() -> None:
    args = AssemblyConfigureArguments.model_validate(
        {
            "name": "Assembly",
            "expected_revision": 1,
            "families": [{"id": "series", "scale_end": [1.1, 1, 1]}],
        }
    )
    assert args.families[0].model_fields_set == {"id", "scale_end"}


def test_sparse_sections_roundtrip_with_null_defaults() -> None:
    edit = LoftSectionEdit(id="middle", radii=[1, 1, 1, 1])
    assert LoftSectionEdit.model_validate(edit.model_dump()) == edit
    with pytest.raises(ValidationError):
        LoftSectionEdit(id="middle", center=None)


def test_discovery_requires_union_tags_before_branch_defaults() -> None:
    from tyvrana_blender.operations import REGISTRY

    schema: Any = REGISTRY["blender.assembly.create"].contract.arguments_schema
    assert isinstance(schema, dict)
    definitions = schema["$defs"]
    surface = definitions["SurfaceSpec"]
    opening = definitions[
        surface["properties"]["openings"]["items"]["$ref"].split("/")[-1]
    ]
    assert opening["required"] == ["kind"]
    template = definitions[
        schema["properties"]["templates"]["items"]["$ref"].split("/")[-1]
    ]
    assert template["required"] == ["kind"]


def test_placement_and_geometry_point_contracts() -> None:
    point = {
        "kind": "geometry",
        "object": "part",
        "region": "socket",
        "position": [0.5, 0.5, 0.5],
        "offset": 0.01,
    }
    measurements = MeasurementArguments.model_validate(
        {
            "queries": [
                {
                    "kind": "distance",
                    "name": "fit",
                    "a": point,
                    "b": {"kind": "world", "point": [0, 0, 0]},
                }
            ]
        }
    )
    assert len(measurements.queries) == 1
    assert LandmarkDeriveArguments.model_validate(
        {
            "landmarks": [
                {"name": "interface", "source": {"kind": "geometry", "source": point}}
            ]
        }
    ).landmarks
    assert PlacementArguments(refresh=["part"]).refresh == ["part"]
    with pytest.raises(ValidationError):
        PlacementArguments(refresh=["part", "part"])
    with pytest.raises(ValidationError):
        MeasurementArguments.model_validate(
            {
                "queries": [
                    {
                        "kind": "distance",
                        "name": "fit",
                        "a": {**point, "project": False},
                        "b": point,
                    }
                ]
            }
        )


def test_cleanup_and_multiview_are_bounded_and_explicit() -> None:
    cleanup = CleanupArguments(names=["part"])
    assert cleanup.fill_holes_up_to == 0 and not cleanup.detach_construction
    with pytest.raises(ValidationError):
        CleanupArguments(names=["part"], fill_holes_up_to=1000)
    render = RenderArguments.model_validate({"inspection": {}})
    assert render.inspection and len(render.inspection.views) == 7
    changes: list[dict[str, Any]] = [
        {"frames": [1]},
        {"format": "exr", "bit_depth": 16},
        {"width": 2048},
        {"cycles": {}},
    ]
    for change in changes:
        with pytest.raises(ValidationError):
            RenderArguments.model_validate({"inspection": {}, **change})
    with pytest.raises(ValidationError):
        RenderArguments.model_validate(
            {
                "width": 1024,
                "height": 1024,
                "inspection": {},
                "budget": {"max_pixels": 4096},
            }
        )


def test_feature_angles_accept_float32_pi_roundtrip() -> None:
    for angle in (-math.pi, math.pi, 3.14159265):
        feature = LoftFeature(
            id="ridge",
            position=0.5,
            angle=angle,
            axial_width=0.3,
            angular_width=math.pi,
            height=0.1,
        )
        assert LoftFeature.model_validate_json(feature.model_dump_json()) == feature
    with pytest.raises(ValidationError):
        LoftFeature(
            id="ridge",
            position=0.5,
            angle=math.pi + 0.001,
            axial_width=0.3,
            angular_width=0.4,
            height=0.1,
        )
