import pytest
from pydantic import ValidationError

from tyvrana_blender.models import (
    ConnectionConfig,
    CreateArguments,
    RenderArguments,
    TransformArguments,
)
from tyvrana_blender.operations import OPERATIONS, registration


@pytest.mark.parametrize(
    "primitive",
    ["cube", "plane", "uv_sphere", "ico_sphere", "cylinder", "cone", "torus"],
)
def test_primitive_defaults(primitive: str) -> None:
    args = CreateArguments.model_validate({"primitive": primitive})
    assert args.location == args.rotation == [0, 0, 0]
    assert args.scale == [1, 1, 1]


@pytest.mark.parametrize(
    "vector",
    [
        None,
        [],
        [1, 2],
        [1, 2, 3, 4],
        "1,2,3",
        [True, 1, 2],
        ["1", 2, 3],
        [float("nan"), 0, 0],
        [float("inf"), 0, 0],
        [float("-inf"), 0, 0],
    ],
)
@pytest.mark.parametrize("field", ["location", "rotation", "scale"])
def test_vectors_reject_malformed_values(field: str, vector: object) -> None:
    with pytest.raises(ValidationError):
        CreateArguments.model_validate({"primitive": "cube", field: vector})


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"primitive": "duck"},
        {"primitive": "cube", "extra": 1},
        {"primitive": "cube", "name": ""},
        {"primitive": 4},
    ],
)
def test_invalid_create_arguments(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CreateArguments.model_validate(data)


def test_partial_transform_and_explicit_null() -> None:
    args = TransformArguments(name="Cube", location=[1, 2, 3])
    assert args.rotation is None and args.scale is None
    with pytest.raises(ValidationError):
        TransformArguments.model_validate({"name": "Cube", "scale": None})


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.2", "example.com", "localhost", "::", "127.0.0.1/path"],
)
def test_configuration_rejects_non_numeric_loopback_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        ConnectionConfig(host=host)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_configuration_rejects_invalid_port(port: int) -> None:
    with pytest.raises(ValidationError):
        ConnectionConfig(port=port)


def test_configuration_defaults_and_ipv6() -> None:
    assert ConnectionConfig().uri == "ws://127.0.0.1:8765"
    assert ConnectionConfig(host="::1").uri == "ws://[::1]:8765"


def test_registration_is_canonical_and_optional_filepath_is_omitted() -> None:
    message = registration("process-id", "5.2.1 LTS", "")
    assert message.application == "blender"
    assert message.application_version == "5.2.1 LTS"
    assert message.operation_names == tuple(sorted(OPERATIONS))
    assert "project_path" not in message.model_dump()
    assert (
        registration("other", "5.2.1 LTS", "example.blend").project_path
        == "example.blend"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"width": 63},
        {"height": 1025},
        {"width": True},
        {"height": "512"},
        {"width": None},
        {"format": "jpeg"},
        {"path": "output.png"},
        {"cycles": None},
        {"cycles": {"device": "cuda"}},
        {"cycles": {"device": None}},
        {"cycles": {"samples": 0}},
        {"cycles": {"samples": 513}},
        {"cycles": {"samples": True}},
        {"cycles": {"samples": "16"}},
        {"cycles": {"denoise": 1}},
        {"cycles": {"script": "anything"}},
        {"wireframe": None},
        {"wireframe": {"objects": []}},
        {"wireframe": {"objects": ["Cage", "Cage"]}},
        {"wireframe": {"objects": ["Cage\u0000"]}},
        {"wireframe": {"objects": ["Cage"], "thickness": 0}},
        {"wireframe": {"objects": ["Cage"], "thickness": True}},
        {"wireframe": {"objects": ["Cage"], "surface_offset": float("nan")}},
        {"wireframe": {"objects": ["Cage"], "surface_offset": float("inf")}},
    ],
)
def test_invalid_render_arguments(arguments: object) -> None:
    from tyvrana_blender.models import RenderArguments

    with pytest.raises(ValueError):
        RenderArguments.model_validate(arguments)


def test_render_defaults_and_dimension_bounds() -> None:
    from tyvrana_blender.models import RenderArguments

    assert RenderArguments().model_dump() == {
        "width": 512,
        "height": 512,
        "format": "png",
        "cycles": None,
        "wireframe": None,
        "uv_checker": None,
        "surface": None,
        "show_result": False,
        "wait_seconds": 5,
        "output": None,
    }
    assert RenderArguments(width=64, height=1024).height == 1024
    assert RenderArguments.model_validate({"cycles": {}}).cycles is not None


@pytest.mark.parametrize("value", [None, 1, "true", []])
def test_render_display_requires_a_boolean(value: object) -> None:
    with pytest.raises(ValidationError):
        RenderArguments.model_validate({"show_result": value})
