"""Deterministic synthetic image fixtures; no binary assets or runtime decoder."""

import hashlib
import json
import random
import struct
import zlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms


def create_rasters(directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    def save(
        name: str,
        size: tuple[int, int],
        *,
        mode: str = "RGB",
        noise: bool = False,
        fmt: str = "JPEG",
        **options: Any,
    ) -> bytes:
        path = directory / name
        if noise:
            channels = len(mode) if mode != "L" else 1
            image = Image.frombytes(
                mode,
                size,
                random.Random(20260919).randbytes(size[0] * size[1] * channels),
            )
        else:
            image = Image.new(mode, size)
        with image:
            image.save(path, format=fmt, **options)
        data = path.read_bytes()
        rows.append(
            dict(
                name=name,
                media_type=Image.MIME[fmt],
                width=size[0],
                height=size[1],
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                error=None,
            )
        )
        return data

    def bad(name: str, data: bytes, media_type: str, code: str) -> None:
        (directory / name).write_bytes(data)
        rows.append(
            dict(
                name=name,
                media_type=media_type,
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                error=code,
            )
        )

    base = save("baseline.jpg", (128, 64), noise=True, quality=90)
    for width in [4095, 4096, 4097, 16384]:
        save(f"width-{width}.jpg", (width, 8))
    hard = save("large-rgb.jpg", (4672, 3314), noise=True, quality=85)
    assert len(hard) > 5_027_127
    save("large-solid.jpg", (6000, 4000), quality=85)
    large_bytes = save(
        "large-bytes.jpg", (2048, 1536), noise=True, quality=100, subsampling=0
    )
    assert len(large_bytes) > 5_027_127
    save("progressive.jpg", (768, 512), noise=True, progressive=True, quality=85)
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "Synthetic orientation fixture"
    save("exif.jpg", (321, 123), exif=exif)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    save("icc.jpg", (128, 96), icc_profile=icc)
    save("gray.jpg", (128, 96), mode="L", noise=True)
    save("cmyk.jpg", (128, 96), mode="CMYK")
    png = save("rgba.png", (128, 96), mode="RGBA", fmt="PNG")
    save("gray16.png", (128, 96), mode="I;16", fmt="PNG")
    save("pixel-bound.png", (8192, 4096), fmt="PNG")
    # Six MiB of legal ignored APP metadata before SOF; small decoded allocation.
    metadata = (b"\xff\xef\xff\xff" + b"m" * 65533) * 96
    data = base[:2] + metadata + base[2:]
    (directory / "metadata.jpg").write_bytes(data)
    rows.append(
        dict(
            name="metadata.jpg",
            media_type="image/jpeg",
            width=128,
            height=64,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            error=None,
        )
    )
    save("unsupported.webp", (128, 64), fmt="WEBP")
    rows[-1]["error"] = "unsupported_artifact_media_type"
    bad("truncated.jpg", base[:-2], "image/jpeg", "artifact_truncated")
    bad("truncated.png", png[:-12], "image/png", "artifact_truncated")
    bad(
        "bad-header.png",
        png[:32] + bytes([png[32] ^ 1]) + png[33:],
        "image/png",
        "image_decode_failed",
    )
    for name, size in [
        ("over-dimension.png", (16385, 1)),
        ("over-pixels.png", (8192, 4097)),
    ]:
        header = png[:16] + struct.pack(">II", *size) + png[24:29]
        data = header + struct.pack(">I", zlib.crc32(header[12:29])) + png[33:]
        bad(name, data, "image/png", "image_dimensions_exceeded")
    (directory / "manifest.json").write_text(json.dumps(rows, indent=2) + "\n")
    return rows
