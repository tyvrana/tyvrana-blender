"""Strict project-save contracts and dispatch."""

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationRequest, OperationSuccess

from tyvrana_blender.file_models import (
    FileInspectArguments,
    FileNewArguments,
    FileOpenArguments,
    FileSaveArguments,
)
from tyvrana_blender.operations import execute

from .test_operations import Backend


@pytest.mark.parametrize(
    "arguments",
    [
        {"filepath": None},
        {"filepath": ""},
        {"filepath": "relative.blend"},
        {"filepath": "//relative.blend"},
        {"filepath": "~/project.blend"},
        {"filepath": "/project/file.png"},
        {"filepath": "/project/\0.blend"},
        {"filepath": True},
        {"filepath": 3},
        {"filepath": "/" + "a" * 4096 + ".blend"},
        {"overwrite": None},
        {"overwrite": 1},
        {"overwrite": "true"},
        {"python": "anything"},
        {"copy": True},
    ],
)
def test_save_rejects_ambiguous_or_untyped_arguments(arguments: object) -> None:
    with pytest.raises(ValidationError):
        FileSaveArguments.model_validate(arguments)


def test_file_inspection_has_no_arguments() -> None:
    with pytest.raises(ValidationError):
        FileInspectArguments.model_validate({"filepath": "/project/file.blend"})


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("inspect", {}),
        ("new", {"discard_current": True}),
        ("open", {"filepath": "/project/model.blend", "discard_current": True}),
        ("save", {}),
        ("save", {"overwrite": True}),
        ("save", {"filepath": "/project/model.blend"}),
    ],
)
def test_file_dispatch(name: str, arguments: dict[str, str | bool]) -> None:
    response = execute(
        Backend(),
        OperationRequest.model_validate(
            {
                "type": "operation.request",
                "request_id": "file",
                "operation": "blender.file." + name,
                "arguments": arguments,
            }
        ),
    )
    assert isinstance(response, OperationSuccess)
    assert isinstance(response.result, dict)
    assert response.result["is_saved"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"filepath": "/project/file.blend"},
        {"filepath": "relative.blend", "discard_current": True},
        {"filepath": "/project/file.png", "discard_current": True},
        {"filepath": "/project/file.blend", "discard_current": False},
        {"filepath": "/project/file.blend", "discard_current": 1},
        {"filepath": "/project/file.blend", "discard_current": "true"},
        {"filepath": "/project/file.blend", "discard_current": True, "load_ui": None},
        {
            "filepath": "/project/file.blend",
            "discard_current": True,
            "use_scripts": True,
        },
    ],
)
def test_open_requires_explicit_typed_project_replacement(arguments: object) -> None:
    with pytest.raises(ValidationError):
        FileOpenArguments.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"discard_current": False},
        {"discard_current": 1},
        {"discard_current": "true"},
        {"discard_current": True, "filepath": "/tmp/model.blend"},
    ],
)
def test_new_requires_explicit_discard(arguments: object) -> None:
    with pytest.raises(ValidationError):
        FileNewArguments.model_validate(arguments)
