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
    return width, height


def rgb_pixels(data: bytes) -> bytes:
    """Decode the bounded 8-bit RGB/RGBA PNGs emitted by the render operation."""
    width, height = inspect_png(data)
    channels = 4 if data[25] == 6 else 3
    offset = 8
    compressed = bytearray()
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        if data[offset + 4 : offset + 8] == b"IDAT":
            compressed.extend(data[offset + 8 : offset + 8 + length])
        offset += length + 12
    raw = zlib.decompress(compressed)
    stride = width * channels
    previous = bytearray(stride)
    rgb = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        assert kind in (0, 1, 2, 3, 4)
        row = bytearray(raw[start + 1 : start + 1 + stride])
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            predictor = 0
            if kind == 1:
                predictor = left
            elif kind == 2:
                predictor = up
            elif kind == 3:
                predictor = (left + up) // 2
            elif kind == 4:
                p = left + up - corner
                distances = (abs(p - left), abs(p - up), abs(p - corner))
                predictor = (left, up, corner)[distances.index(min(distances))]
            row[x] = (row[x] + predictor) & 255
        for x in range(0, stride, channels):
            rgb.extend(row[x : x + 3])
        previous = row
    return bytes(rgb)


def assert_image_variation(data: bytes) -> None:
    """Require rich visible content for the contrasting render fixtures."""
    pixels = rgb_pixels(data)
    assert len(set(pixels)) > 32
    assert sum(value != 0 for value in pixels) > len(pixels) // 12


def red_bounds(data: bytes) -> tuple[int, int, int, int]:
    """Simple framing metric for the contrasting subject in render fixtures."""
    width, _ = inspect_png(data)
    rgb = rgb_pixels(data)
    points = [
        (i // 3 % width, i // 3 // width)
        for i in range(0, len(rgb), 3)
        if rgb[i] > 90 and rgb[i] > rgb[i + 1] * 1.5 and rgb[i] > rgb[i + 2] * 1.5
    ]
    assert points, "Rendered subject is not visible"
    return (
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    )


def channel_means(
    data: bytes, box: tuple[int, int, int, int]
) -> tuple[float, float, float]:
    """RGB averages in a fixed rectangular probe, with exclusive right/bottom."""
    width, height = inspect_png(data)
    left, top, right, bottom = box
    assert 0 <= left < right <= width and 0 <= top < bottom <= height
    rgb = rgb_pixels(data)
    count = (right - left) * (bottom - top)
    means = [
        sum(
            rgb[(y * width + x) * 3 + channel]
            for y in range(top, bottom)
            for x in range(left, right)
        )
        / count
        for channel in range(3)
    ]
    return means[0], means[1], means[2]


def mean_pixel_difference(first: bytes, second: bytes) -> float:
    assert inspect_png(first) == inspect_png(second)
    before, after = rgb_pixels(first), rgb_pixels(second)
    return sum(abs(a - b) for a, b in zip(before, after, strict=True)) / len(before)


def highlight_statistics(data: bytes) -> tuple[float, float, float]:
    """Median, upper percentile, and bright-area fraction inside the studio sphere."""
    width, height = inspect_png(data)
    assert width == height
    rgb = rgb_pixels(data)
    center = width / 2
    radius = width * 0.17
    values = sorted(
        sum(rgb[(y * width + x) * 3 : (y * width + x) * 3 + 3]) / 3
        for y in range(height)
        for x in range(width)
        if (x - center) ** 2 + (y - center) ** 2 < radius**2
    )
    return (
        values[len(values) // 2],
        values[int(len(values) * 0.999)],
        sum(value > 240 for value in values) / len(values),
    )


def texture_statistics(data: bytes) -> tuple[float, float, int]:
    """Interior luminance variance, adjacent RGB contrast, and quantized colors."""
    width, height = inspect_png(data)
    rgb = rgb_pixels(data)
    left, right = width // 4, width * 3 // 4
    top, bottom = height // 4, height * 3 // 4
    values = []
    colors = set()
    edges = []
    for y in range(top, bottom):
        for x in range(left, right):
            offset = (y * width + x) * 3
            pixel = rgb[offset : offset + 3]
            values.append(sum(pixel) / 3)
            colors.add(tuple(channel // 16 for channel in pixel))
            for neighbor in (offset + 3, offset + width * 3):
                edges.append(
                    sum(abs(pixel[c] - rgb[neighbor + c]) for c in range(3)) / 3
                )
    mean = sum(values) / len(values)
    return (
        sum((value - mean) ** 2 for value in values) / len(values),
        sum(edges) / len(edges),
        len(colors),
    )
