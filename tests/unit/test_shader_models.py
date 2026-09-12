import math
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tyvrana_blender.compatibility import require_blender
from tyvrana_blender.image_models import ImageConfigureArguments, ImageCreateArguments
from tyvrana_blender.shader_models import (
    ConnectArguments,
    NodeConfigureArguments,
    NodeCreateArguments,
    ShaderGraphSummary,
    compatible_sockets,
    safe_socket_default,
)


@pytest.mark.parametrize("version", [(5, 2, 1), (5, 2, 2), (5, 3, 0), (6, 0, 0)])
def test_minimum_and_newer_host_guard(version: tuple[int, int, int]) -> None:
    require_blender(version)


@pytest.mark.parametrize("version", [(5, 2, 0), (5, 1, 99), (4, 9, 99)])
def test_older_host_rejected(version: tuple[int, int, int]) -> None:
    with pytest.raises(RuntimeError, match="5.2.1"):
        require_blender(version)


def test_manifest_minimum_matches_runtime() -> None:
    manifest = Path(__file__).resolve().parents[2] / "blender_manifest.toml"
    assert tomllib.loads(manifest.read_text())["blender_version_min"] == "5.2.1"


def test_entrypoint_rejects_old_host_before_importing_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tyvrana_blender

    monkeypatch.setitem(
        sys.modules, "bpy", SimpleNamespace(app=SimpleNamespace(version=(5, 2, 0)))
    )
    with pytest.raises(RuntimeError, match="5.2.1"):
        tyvrana_blender.register()
    assert "tyvrana_blender.blender" not in sys.modules


@pytest.mark.parametrize("dimension", [1, 4, 4096])
@pytest.mark.parametrize("floating", [False, True])
@pytest.mark.parametrize("kind", ["blank", "uv_grid", "color_grid"])
def test_generated_images(dimension: int, floating: bool, kind: str) -> None:
    value = ImageCreateArguments.model_validate(
        {
            "width": dimension,
            "height": dimension,
            "float_buffer": floating,
            "generated_type": kind,
        }
    )
    assert value.width == dimension and value.height == dimension
    assert value.float_buffer == floating


@pytest.mark.parametrize("field", ["width", "height"])
@pytest.mark.parametrize("value", [0, -1, 4097, True, "4", 4.0, None])
def test_bad_dimensions(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ImageCreateArguments.model_validate({"width": 4, "height": 4, field: value})


@pytest.mark.parametrize(
    "patch",
    [
        {"name": " "},
        {"name": None},
        {"name": False},
        {"path": "image.png"},
        {"color": [0, 0, 0]},
        {"color": [0, 0, 0, 1, 1]},
        {"color": [-1, 0, 0, 1]},
        {"color": [2, 0, 0, 1]},
        {"color": [True, 0, 0, 1]},
        {"color": ["0", 0, 0, 1]},
        {"color": [math.nan, 0, 0, 1]},
        {"color": [math.inf, 0, 0, 1]},
        {"color": [1e-50, 0, 0, 1]},
        {"color": [0, 0, 0, 0.5], "alpha": False},
        {"color": [0, 0, 0, 1], "generated_type": "uv_grid"},
        {"generated_type": "noise"},
        {"float_buffer": 1},
        {"alpha": 1},
        {"color_space": None},
        {"color_space": ""},
    ],
)
def test_bad_image_creation(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ImageCreateArguments.model_validate({"width": 4, "height": 4, **patch})


@pytest.mark.parametrize("space", ["sRGB", "Non-Color", "ACEScg", "Custom OCIO Name"])
def test_color_space_names_are_resolved_by_host(space: str) -> None:
    assert ImageConfigureArguments(name="Image", color_space=space).color_space == space


@pytest.mark.parametrize(
    "mode", ["straight", "premultiplied", "channel_packed", "none"]
)
def test_alpha_modes(mode: str) -> None:
    assert (
        ImageConfigureArguments.model_validate(
            {"name": "Image", "alpha_mode": mode}
        ).alpha_mode
        == mode
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"alpha_mode": "invalid"},
        {"alpha_mode": None},
        {"width": 2},
        {"color_space": True},
    ],
)
def test_bad_image_configuration(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ImageConfigureArguments.model_validate({"name": "Image", **patch})


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        (
            "image_texture",
            {
                "image_name": "Image",
                "interpolation": "closest",
                "projection": "box",
                "extension": "mirror",
            },
        ),
        ("texture_coordinate", {"from_instancer": True}),
        (
            "mapping",
            {
                "vector_type": "texture",
                "location": [1, 2, 3],
                "rotation": [0, 0, 1],
                "scale": [2, 3, 4],
            },
        ),
        ("normal_map", {"strength": 0.5, "space": "tangent", "uv_map": "UVMap"}),
        ("bump", {"strength": 0.5, "distance": 0.01, "invert": True}),
    ],
)
def test_node_settings_by_type(kind: str, fields: dict[str, object]) -> None:
    created = NodeCreateArguments.model_validate(
        {"material_name": "Material", "node_type": kind, **fields}
    )
    assert set(created.settings_for(kind)) == set(fields)
    changed = NodeConfigureArguments.model_validate(
        {"material_name": "Material", "node_name": "Node", **fields}
    )
    assert changed.settings_for(kind) == created.settings_for(kind)


@pytest.mark.parametrize(
    "kind", ["image_texture", "texture_coordinate", "mapping", "normal_map", "bump"]
)
@pytest.mark.parametrize("value", [True, "1", math.nan, math.inf, 1e40, 1e-50, None])
def test_numeric_node_fields_strict(kind: str, value: object) -> None:
    field = "scale" if kind == "mapping" else "strength"
    with pytest.raises(ValidationError):
        NodeCreateArguments.model_validate(
            {
                "material_name": "Material",
                "node_type": kind,
                field: [value] * 3 if field == "scale" else value,
            }
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"node_type": "ShaderNodeTexImage"},
        {"node_type": "noise"},
        {"interpolation": "nearest"},
        {"projection": "plane"},
        {"extension": "wrap"},
        {"color_space": "sRGB"},
        {"strength": 1},
        {"image_name": None},
        {"name": None},
        {"image_name": " "},
        {"interpolation": None},
    ],
)
def test_invalid_image_node_settings(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NodeCreateArguments.model_validate(
            {"material_name": "Material", "node_type": "image_texture", **patch}
        )


@pytest.mark.parametrize(
    "patch", [{"space": "camera"}, {"space": "blender_world"}, {"invert": True}]
)
def test_normal_map_invalid_fields(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NodeCreateArguments.model_validate(
            {"material_name": "Material", "node_type": "normal_map", **patch}
        )


def test_configure_preserves_omitted_fields_and_rejects_wrong_type() -> None:
    value = NodeConfigureArguments(
        material_name="Material", node_name="Node", scale=[2, 2, 2]
    )
    assert value.settings_for("mapping") == {"scale": [2, 2, 2]}
    with pytest.raises(ValueError, match="scale"):
        value.settings_for("bump")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "private/path",
        object(),
        {"huge": [1]},
        list(range(5)),
        [math.inf],
        [True],
        ["1"],
        math.nan,
    ],
)
def test_unsafe_socket_defaults_omitted(value: object) -> None:
    assert safe_socket_default(value) is None


@pytest.mark.parametrize("value", [True, 4, 1.5, [1, 2, 3], [1.0, 2.0, 3.0, 4.0]])
def test_safe_socket_defaults(value: object) -> None:
    assert safe_socket_default(value) == value


@pytest.mark.parametrize("source", ["VALUE", "INT", "BOOLEAN", "RGBA", "VECTOR"])
@pytest.mark.parametrize(
    "target", ["VALUE", "INT", "BOOLEAN", "RGBA", "VECTOR", "SHADER"]
)
def test_native_shader_numeric_conversions(source: str, target: str) -> None:
    assert compatible_sockets(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("SHADER", "RGBA"),
        ("STRING", "VECTOR"),
        ("BUNDLE", "BUNDLE"),
        ("VECTOR", "GEOMETRY"),
    ],
)
def test_unsupported_socket_pairs(source: str, target: str) -> None:
    assert not compatible_sockets(source, target)


def test_connect_requires_explicit_replacement() -> None:
    value = ConnectArguments(
        material_name="M",
        from_node="A",
        from_socket="Color",
        to_node="B",
        to_socket="Base Color",
    )
    assert value.replace_existing is False
    for bad in (None, 1, "true"):
        with pytest.raises(ValidationError):
            ConnectArguments.model_validate(
                {**value.model_dump(), "replace_existing": bad}
            )


def test_empty_graph_serialization() -> None:
    summary = ShaderGraphSummary(
        material_name="M", node_tree_present=False, nodes=[], links=[]
    )
    assert ShaderGraphSummary.model_validate_json(summary.model_dump_json()) == summary
