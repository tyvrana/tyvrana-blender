"""Bounded header admission before Blender decodes supported input rasters."""

import struct
import zlib
from pathlib import Path


class RasterError(Exception):
    pass


def raster_size(path: Path, media_type: str) -> tuple[int, int]:
    """Check format, dimensions and terminal markers without decoding pixels."""
    try:
        with path.open("rb") as stream:
            if media_type == "image/png":
                header = stream.read(33)
                if (
                    len(header) != 33
                    or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                    or zlib.crc32(header[12:29]) != int.from_bytes(header[29:33], "big")
                ):
                    raise RasterError("Invalid PNG header")
                width, height = struct.unpack(">II", header[16:24])
                stream.seek(-12, 2)
                if stream.read() != b"\x00\x00\x00\x00IEND\xaeB`\x82":
                    raise RasterError("Incomplete PNG")
            elif media_type == "image/jpeg":
                if stream.read(2) != b"\xff\xd8":
                    raise RasterError("Invalid JPEG signature")
                width = height = 0
                # Baseline/extended sequential and progressive DCT JPEG. Headers
                # are bounded independently of the encoded-artifact size limit.
                while stream.tell() < 1024 * 1024:
                    if stream.read(1) != b"\xff":
                        raise RasterError("Invalid JPEG marker")
                    marker = stream.read(1)
                    while marker == b"\xff" and stream.tell() < 1024 * 1024:
                        marker = stream.read(1)
                    if not marker or marker in {b"\x00", b"\xda", b"\xd9"}:
                        break
                    length_bytes = stream.read(2)
                    if len(length_bytes) != 2:
                        break
                    length = int.from_bytes(length_bytes, "big")
                    if length < 2:
                        break
                    if marker in {b"\xc0", b"\xc1", b"\xc2"}:
                        frame = stream.read(6)
                        if (
                            len(frame) != 6
                            or frame[0] != 8
                            or length != 8 + 3 * frame[5]
                        ):
                            raise RasterError("Unsupported JPEG frame")
                        height, width = struct.unpack(">HH", frame[1:5])
                        break
                    stream.seek(length - 2, 1)
                if not width or not height:
                    raise RasterError("JPEG dimensions are unavailable")
                stream.seek(-2, 2)
                if stream.read() != b"\xff\xd9":
                    raise RasterError("Incomplete JPEG")
            else:
                raise RasterError("Unsupported raster media type")
    except (OSError, ValueError, struct.error) as exc:
        raise RasterError("Cannot inspect input raster") from exc
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise RasterError("Input raster dimensions must be between 1 and 4096")
    return width, height
