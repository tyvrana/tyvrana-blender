import pytest
from pydantic import ValidationError

from tyvrana_blender.instance_models import MeshCreateArguments, SurfaceDistribution


def distribution(**changes: object) -> SurfaceDistribution:
    return SurfaceDistribution.model_validate(
        {
            "surface_object": "Surface",
            "prototype_object": "Asset",
            "uv_map": "UVMap",
            "guides": [[[0, 0, 0], [0, 1, 0]], [[1, 0, 0], [1, 1, 0]]],
            "rows": 4,
            "columns": 8,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"rows": 128, "columns": 128},
        {"scale": [0, 1, 1]},
        {"scale_end": [1, -1, 1]},
        {"scale_variation": [0, 0.6, 0]},
        {"surface_object": "Asset"},
        {"seed": True},
        {"guides": [[[0, 0, 0], [0, 0, 0]], [[1, 0, 0], [1, 1, 0]]]},
        {"direction_variation": float("nan")},
        {"rows": "4"},
        {"script": "print('not a supported contract')"},
    ],
)
def test_invalid_distribution(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        distribution(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"faces": [[0, 1, 3]]},
        {"faces": [[0, 1, 1]]},
        {"faces": [[0, 1, 2], [2, 1, 0]]},
        {"corner_uvs": [[0, 0]]},
        {"material_indices": [0]},
        {"vertices": [[0, 0, 0], [1, 0, 0], [float("inf"), 1, 0]]},
    ],
)
def test_invalid_mesh(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MeshCreateArguments.model_validate(
            {
                "name": "Asset",
                "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "faces": [[0, 1, 2]],
                **changes,
            }
        )


def test_distribution_roundtrip() -> None:
    spec = distribution()
    assert SurfaceDistribution.model_validate_json(spec.model_dump_json()) == spec
