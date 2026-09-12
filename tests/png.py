"""Small PNG test inspection using only the standard library."""

import struct
import zlib


def inspect_png(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    compressed = bytearray()
    width = height = color = 0
    ended = False
    while offset < len(data):
        (length,) = struct.unpack("!I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack("!I", data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(kind + payload) == crc
        if kind == b"IHDR":
            width, height, depth, color, compression, filters, interlace = (
                struct.unpack("!2I5B", payload)
            )
            assert depth == 8 and color in (2, 6)
            assert (compression, filters, interlace) == (0, 0, 0)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            ended = True
        offset += length + 12
    assert ended and offset == len(data)
    raw = zlib.decompress(compressed)
    channels = 4 if color == 6 else 3
    assert len(raw) == height * (1 + width * channels)
    # Filtered scanlines of the contrasting, lit scene must contain real variation.
    assert len(set(raw)) > 32
    assert sum(value != 0 for value in raw) > width * height // 4
    return width, height
