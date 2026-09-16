"""Contracts reject arbitrary shader programs and preserve patch omissions."""

import json

import pytest
from pydantic import ValidationError

from tyvrana_blender.material_author import NODES, recipe_graph, semantic_recipe
from tyvrana_blender.material_author_models import (
    Coordinates,
    GraphAuthorArguments,
    MaterialAuthorArguments,
    RampNode,
)
from tyvrana_blender.operations import REGISTRY


@pytest.mark.parametrize(
    "fields",
    [
        {"nodes": [{"id": "x", "type": "python", "code": "x"}]},
        {"nodes": [{"id": "x", "type": "noise", "properties": {"scale": 2}}]},
        {"nodes": [{"id": "x", "type": "noise", "scale": None}]},
        {"nodes": [{"id": "x", "type": "noise", "detail": 16}]},
        {"nodes": [{"id": "x", "type": "noise", "scale": float("nan")}]},
        {"nodes": [{"id": "x", "type": "noise"}] * 65},
        {"nodes": [{"id": "x", "type": "noise"}, {"id": "x", "type": "mapping"}]},
        {"mode": "replace"},
        {"remove_nodes": ["a"]},
        {
            "mode": "patch",
            "nodes": [{"id": "x", "type": "noise"}],
            "remove_nodes": ["x"],
        },
        {
            "links": [
                {
                    "source": {"node": "a", "socket": "arbitrary"},
                    "target": {"node": "b", "socket": "color"},
                }
            ]
        },
    ],
)
def test_graph_rejects_invalid_or_unbounded_programs(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GraphAuthorArguments.model_validate({"name": "Surface", **fields})


def test_patch_omissions_and_discriminated_schema() -> None:
    patch = GraphAuthorArguments.model_validate(
        {
            "name": "Surface",
            "mode": "patch",
            "nodes": [
                {"id": "p", "type": "principled", "parameters": {"roughness": 0.25}}
            ],
        }
    )
    assert patch.nodes[0].model_dump(exclude_unset=True) == {
        "id": "p",
        "type": "principled",
        "parameters": {"roughness": 0.25},
    }
    schema = GraphAuthorArguments.model_json_schema()
    choices = schema["$defs"]["NodeSpec"]["discriminator"]["mapping"]
    assert set(choices) == set(NODES)
    assert len(json.dumps(schema)) < 30000


@pytest.mark.parametrize(
    "fields",
    [
        {"textures": [{"channel": "roughness", "image": "Map"}] * 2},
        {
            "textures": [{"channel": "normal", "image": "Map"}],
            "remove_textures": ["normal"],
        },
        {"parameters": {"anisotropy": 2}},
        {"parameters": {"subsurface_anisotropy": -2}},
        {"parameters": {"thin_film_thickness": -1}},
        {"parameters": {"thin_wall": "true"}},
        {"coordinates": {"source": "uv", "object_name": "Cube"}},
        {"coordinates": {"source": "generated", "uv_map": "UVMap"}},
        {"tangent": {}, "remove_tangent": True},
    ],
)
def test_semantic_contract_rejections(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MaterialAuthorArguments.model_validate({"name": "Surface", **fields})


def test_ramp_order_and_bounds() -> None:
    with pytest.raises(ValidationError):
        RampNode.model_validate(
            {
                "id": "r",
                "type": "color_ramp",
                "stops": [
                    {"position": 0.5, "color": [0, 0, 0]},
                    {"position": 0.5, "color": [1, 1, 1]},
                ],
            }
        )
    assert Coordinates(source="generated").scale == [1, 1, 1]


def test_recipe_is_a_bounded_typed_graph_with_coherent_normals() -> None:
    request = MaterialAuthorArguments.model_validate(
        {
            "name": "Surface",
            "textures": [
                {"channel": channel, "image": "Map"}
                for channel in [
                    "base_color",
                    "roughness",
                    "normal",
                    "bump",
                    "displacement",
                ]
            ],
        }
    )
    graph = recipe_graph(request.name, semantic_recipe(request, None))
    assert len(graph.nodes) == 12
    assert len([n for n in graph.nodes if n.type == "mapping"]) == 1
    assert any(
        link.source.node == "normal" and link.target.node == "bump"
        for link in graph.links
    )
    assert any(
        link.source.node == "displacement" and link.target.node == "output"
        for link in graph.links
    )


def test_new_operations_advertise_typed_mutations() -> None:
    for name in [
        "material.author",
        "shader.author",
        "material.copy",
        "material.remove",
        "material.assign_batch",
    ]:
        definition = REGISTRY["blender." + name]
        assert definition.contract.effect == "mutating"
        assert definition.contract.arguments_schema["additionalProperties"] is False
