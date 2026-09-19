"""Bounded encoded-image admission; pixel decoding belongs to Blender."""

import os
import struct
import zlib
from pathlib import Path
from typing import BinaryIO

from .errors import OperationError

MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16384
MAX_IMAGE_PIXELS = 32 * 1024 * 1024


def image_dimensions_supported(width: int, height: int) -> bool:
    """Shared allocation bounds for imported images and their reference users."""
    return (
        0 < width <= MAX_IMAGE_DIMENSION
        and 0 < height <= MAX_IMAGE_DIMENSION
        and width * height <= MAX_IMAGE_PIXELS
    )


class RasterError(OperationError):
    """Path-free raster admission failure using the canonical operation error."""


def _read(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise RasterError(
            "artifact_truncated",
            "Incomplete image header or segment",
            {"stage": "image_header"},
        )
    return data


def _jpeg_size(stream: BinaryIO, byte_size: int) -> tuple[int, int]:
    stream.seek(2)
    # Skip length-delimited metadata without copying it. The encoded-byte limit
    # bounds this walk; large EXIF/ICC/APP metadata is not a pixel allocation.
    while stream.tell() < byte_size:
        if _read(stream, 1) != b"\xff":
            break
        marker = _read(stream, 1)
        while marker == b"\xff":
            marker = _read(stream, 1)
        if marker in {b"\x00", b"\xd8", b"\xd9", b"\xda"}:
            break
        if marker == b"\x01":
            continue
        length = int.from_bytes(_read(stream, 2), "big")
        if length < 2:
            break
        end = stream.tell() + length - 2
        if end > byte_size:
            raise RasterError(
                "artifact_truncated",
                "JPEG segment exceeds available bytes",
                {"stage": "image_header"},
            )
        if marker in {b"\xc0", b"\xc1", b"\xc2"}:
            frame = _read(stream, 6)
            if frame[0] != 8 or frame[5] not in {1, 3, 4}:
                raise RasterError(
                    "unsupported_image_encoding",
                    "JPEG requires 8-bit DCT samples",
                    {
                        "stage": "image_header",
                        "precision": frame[0],
                        "components": frame[5],
                    },
                )
            if length != 8 + 3 * frame[5]:
                break
            height, width = struct.unpack(">HH", frame[1:5])
            return width, height
        if 0xC0 <= marker[0] <= 0xCF and marker not in {b"\xc4", b"\xc8", b"\xcc"}:
            raise RasterError(
                "unsupported_image_encoding",
                "Unsupported JPEG frame encoding",
                {"stage": "image_header", "marker": marker[0]},
            )
        stream.seek(end)
    raise RasterError(
        "image_decode_failed",
        "JPEG dimensions are unavailable or malformed",
        {"stage": "image_header"},
    )


def raster_size(
    path: Path, media_type: str, *, expected_bytes: int | None = None
) -> tuple[int, int]:
    """Admit complete PNG/8-bit DCT JPEG before native pixel allocation.

    Transport already verifies descriptor SHA256. Check materialized length again,
    then encoded size, signatures/header and dimensions without copying the file.
    Blender remains the single pixel decoder; this is not an entropy validator.
    """
    if media_type not in {"image/png", "image/jpeg"}:
        raise RasterError(
            "unsupported_artifact_media_type",
            "Only PNG and JPEG are supported",
            {"stage": "image_format", "media_type": media_type},
        )
    try:
        with path.open("rb") as stream:
            byte_size = os.fstat(stream.fileno()).st_size
            if expected_bytes is not None and byte_size != expected_bytes:
                raise RasterError(
                    "artifact_truncated"
                    if byte_size < expected_bytes
                    else "artifact_integrity_failed",
                    "Materialized image size does not match its descriptor",
                    {
                        "stage": "artifact_integrity",
                        "expected_bytes": expected_bytes,
                        "actual_bytes": byte_size,
                    },
                )
            if byte_size > MAX_IMAGE_BYTES:
                raise RasterError(
                    "artifact_too_large",
                    "Encoded image exceeds the import byte limit",
                    {
                        "stage": "image_limits",
                        "byte_size": byte_size,
                        "max_bytes": MAX_IMAGE_BYTES,
                    },
                )
            signature = stream.read(12)
            detected = (
                "image/png"
                if signature.startswith(b"\x89PNG\r\n\x1a\n")
                else "image/jpeg"
                if signature.startswith(b"\xff\xd8")
                else "image/webp"
                if signature[:4] == b"RIFF" and signature[8:12] == b"WEBP"
                else None
            )
            if detected is not None and detected != media_type:
                raise RasterError(
                    "artifact_media_type_mismatch",
                    "Image signature differs from MIME type",
                    {
                        "stage": "image_format",
                        "declared": media_type,
                        "detected": detected,
                    },
                )
            if detected is None:
                raise RasterError(
                    "image_decode_failed",
                    "Invalid image signature",
                    {"stage": "image_header"},
                )
            stream.seek(0)
            if media_type == "image/png":
                header = _read(stream, 33)
                if header[8:16] != b"\x00\x00\x00\rIHDR" or zlib.crc32(
                    header[12:29]
                ) != int.from_bytes(header[29:33], "big"):
                    raise RasterError(
                        "image_decode_failed",
                        "Invalid PNG header or checksum",
                        {"stage": "image_header"},
                    )
                width, height = struct.unpack(">II", header[16:24])
                stream.seek(-12, 2)
                complete = stream.read(12) == b"\x00\x00\x00\x00IEND\xaeB`\x82"
            else:
                width, height = _jpeg_size(stream, byte_size)
                stream.seek(-2, 2)
                complete = stream.read(2) == b"\xff\xd9"
            if not complete:
                raise RasterError(
                    "artifact_truncated",
                    "Image end marker is missing",
                    {"stage": "image_header"},
                )
    except OSError as exc:
        raise RasterError(
            "artifact_read_failed",
            "Cannot read materialized input image",
            {"stage": "artifact_read"},
        ) from exc
    if not width or not height:
        raise RasterError(
            "image_decode_failed",
            "Image dimensions must be positive",
            {"stage": "image_header", "width": width, "height": height},
        )
    if not image_dimensions_supported(width, height):
        raise RasterError(
            "image_dimensions_exceeded",
            "Image exceeds dimension or decoded-pixel limits",
            {
                "stage": "image_limits",
                "width": width,
                "height": height,
                "pixels": width * height,
                "max_dimension": MAX_IMAGE_DIMENSION,
                "max_pixels": MAX_IMAGE_PIXELS,
            },
        )
    return width, height
