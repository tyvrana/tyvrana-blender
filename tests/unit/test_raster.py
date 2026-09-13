import struct
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from tyvrana_protocol import OperationRequest, OperationSuccess

from tyvrana_blender.image_models import ImageFromArtifactArguments
from tyvrana_blender.operations import OPERATIONS, execute
from tyvrana_blender.raster import RasterError, raster_size

from ..png import checker_png
from .test_operations import Backend


@pytest.mark.parametrize("size", [(1, 1), (128, 64), (512, 256)])
def test_png_header_dimensions(tmp_path: Path, size: tuple[int, int]) -> None:
    path = tmp_path / "opaque.complete"
    path.write_bytes(checker_png(*size))
    assert raster_size(path, "image/png") == size


@pytest.mark.parametrize("marker", [b"\xc0", b"\xc1", b"\xc2"])
def test_supported_jpeg_headers(tmp_path: Path, marker: bytes) -> None:
    # Header admission only; full decoding is covered in native Blender tests.
    path = tmp_path / "opaque.complete"
    path.write_bytes(
        b"\xff\xd8\xff"
        + marker
        + struct.pack(">HBHHB", 17, 8, 32, 64, 3)
        + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xd9"
    )
    assert raster_size(path, "image/jpeg") == (64, 32)


@pytest.mark.parametrize(
    "failure",
    [
        "magic",
        "crc",
        "truncated",
        "missing",
        "mismatch",
        "oversize",
        "zero",
        "unsupported",
    ],
)
def test_raster_rejections_are_path_free(tmp_path: Path, failure: str) -> None:
    path = tmp_path / "opaque.complete"
    data = checker_png(2, 2)
    if failure == "magic":
        data = b"x" + data[1:]
    elif failure == "crc":
        data = data[:32] + bytes([data[32] ^ 1]) + data[33:]
    elif failure == "truncated":
        data = data[:-1]
    elif failure in {"oversize", "zero"}:
        data = (
            data[:16]
            + struct.pack(">I", 4097 if failure == "oversize" else 0)
            + data[20:]
        )
        data = data[:29] + struct.pack(">I", zlib.crc32(data[12:29])) + data[33:]
    if failure != "missing":
        path.write_bytes(data)
    with pytest.raises(RasterError) as error:
        raster_size(
            path,
            "image/jpeg"
            if failure == "mismatch"
            else "image/tiff"
            if failure == "unsupported"
            else "image/png",
        )
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xd8\xff\xd9",
        b"\xff\xd8garbage",
        b"\xff\xd8\xff\xe0\x00\x00",
        b"\xff\xd8\xff\xc0\x00\x11\x10\x00\x01\x00\x01\x03\xff\xd9",
    ],
)
def test_malformed_jpeg_headers(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "input"
    path.write_bytes(data)
    with pytest.raises(RasterError):
        raster_size(path, "image/jpeg")


@pytest.mark.parametrize(
    "arguments",
    [
        {"artifact_id": "bad"},
        {"artifact_id": "a" * 32, "name": None},
        {"artifact_id": "a" * 32, "name": " "},
        {"artifact_id": "a" * 32, "color_space": None},
        {"artifact_id": "a" * 32, "alpha_mode": "bad"},
        {"artifact_id": "a" * 32, "path": "texture.png"},
        {"artifact_id": 1},
        {"artifact_id": "a" * 32, "name": 123},
    ],
)
def test_image_import_arguments_reject_invalid_values(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ImageFromArtifactArguments.model_validate(arguments)


def test_image_import_dispatch_and_schema() -> None:
    backend = Backend()
    result = execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="test",
            operation="blender.image.create_from_artifact",
            arguments={
                "artifact_id": "a" * 32,
                "name": "Surface",
                "color_space": "sRGB",
                "alpha_mode": "straight",
            },
        ),
    )
    assert isinstance(result, OperationSuccess) and backend.calls == [
        "image_from_artifact"
    ]
    assert "blender.image.create_from_artifact" in OPERATIONS
    schema = ImageFromArtifactArguments.model_json_schema()
    assert (
        "path" not in schema["properties"] and schema["additionalProperties"] is False
    )
