"""Selection, refinement and pose-set contracts reject ambiguous or unbounded work."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from tyvrana_blender.deformation_sweep_models import DeformationSweepArguments
from tyvrana_blender.mesh_models import MeshElementSelector
from tyvrana_blender.operations import REGISTRY
from tyvrana_blender.retopo_models import RetopoInsertArguments
from tyvrana_blender.topology_models import MeshInsertLoopsArguments


@pytest.mark.parametrize(
    "factors",
    [[], [0], [1], [0.5, 0.5], [0.7, 0.2], [0.5] * 17, [float("nan")], [0.5, 0.500001]],
)
def test_refinement_requires_ordered_distinct_bounded_fractions(
    factors: list[float],
) -> None:
    with pytest.raises(ValidationError):
        MeshInsertLoopsArguments.model_validate(
            dict(
                object_name="Mesh", cuts=[dict(edge=0, from_vertex=0, factors=factors)]
            )
        )


def test_multiring_limit_and_empty_intent() -> None:
    for cuts in [[], [dict(edge=0, from_vertex=0, factors=[0.5])] * 17]:
        with pytest.raises(ValidationError):
            MeshInsertLoopsArguments.model_validate(dict(object_name="Mesh", cuts=cuts))


@pytest.mark.parametrize("patch", [dict(factors=[0.2, 0.8]), dict(factor=0.3)])
def test_source_insertion_requires_orientation(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RetopoInsertArguments.model_validate(
            dict(
                source_object="Source",
                target_object="Target",
                edge=dict(domain="edge", mode="indices", indices=[0]),
                **patch,
            )
        )


def test_insertion_factor_modes_are_unambiguous() -> None:
    base = dict(
        source_object="Source",
        target_object="Target",
        edge=dict(domain="edge", mode="indices", indices=[0]),
        from_vertex=0,
    )
    with pytest.raises(ValidationError):
        RetopoInsertArguments.model_validate(dict(base, factor=0.5, factors=[0.5]))
    assert RetopoInsertArguments.model_validate(
        dict(base, factors=[0.2, 0.8])
    ).factors == pytest.approx([0.2, 0.8])


@pytest.mark.parametrize(
    "selector",
    [
        dict(mode="topology", domain="vertex", path="loop", seed=0),
        dict(mode="topology", domain="edge", path="automatic", seed=0),
        dict(mode="neighborhood", domain="vertex", vertex=0, steps=33),
        dict(mode="connected", domain="face", seed=-1),
        dict(mode="valence", domain="vertex", minimum=6, maximum=3),
        dict(mode="region", domain="vertex", region=dict(min=[1, 0, 0], max=[0, 1, 1])),
        dict(
            mode="region",
            domain="edge",
            region=dict(
                frame=dict(kind="bone", object="Rig"), min=[0, 0, 0], max=[1, 1, 1]
            ),
        ),
    ],
)
def test_semantic_selectors_reject_ambiguous_regions(
    selector: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(MeshElementSelector).validate_python(selector)


def test_sweep_limits_total_work_and_duplicate_names() -> None:
    base = dict(armature_object="Rig", objects=["Mesh"])
    patches: list[dict[str, Any]] = [
        dict(poses=[]),
        dict(poses=[dict(name="A")] * 2),
        dict(poses=[dict(name=str(i)) for i in range(17)]),
        dict(poses=[dict(name="A", bones=[dict(name="B"), dict(name="B")])]),
    ]
    for patch in patches:
        with pytest.raises(ValidationError):
            DeformationSweepArguments.model_validate(dict(base, **patch))
    with pytest.raises(ValidationError):
        DeformationSweepArguments.model_validate(
            dict(
                armature_object="Rig",
                objects=[str(i) for i in range(8)],
                poses=[dict(name=str(i)) for i in range(16)],
                regions=[dict(name="R", min=[0, 0, 0], max=[1, 1, 1])],
            )
        )


def test_registry_exposes_limits_and_restoring_semantics() -> None:
    for name in ["mesh.inspect_topology", "mesh.insert_loops", "deformation.sweep"]:
        spec = REGISTRY["blender." + name]
        assert spec.contract.arguments_schema and spec.contract.result_schema
    assert "restores" in REGISTRY["blender.deformation.sweep"].contract.description
    assert (
        "surface attachments"
        in REGISTRY["blender.mesh.insert_loops"].contract.description
    )


def test_sweep_bounds_serialized_detail_work() -> None:
    with pytest.raises(ValidationError, match="detail budget"):
        DeformationSweepArguments.model_validate(
            dict(
                armature_object="Rig",
                objects=["Mesh"],
                sample_limit=16,
                poses=[dict(name=str(i)) for i in range(16)],
            )
        )
