"""Generated orthographic blueprints of four cuboid mounting blocks."""

import itertools
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def create_fixture(directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    points = [
        [round(x + dx, 6), round(y + dy, 6), z]
        for x, y in itertools.product((-0.4, 0.4), repeat=2)
        for dx, dy, z in itertools.product((-0.15, 0.15), (-0.1, 0.1), (-0.2, 0.2))
    ]
    views = {"Front": (0, 2), "Side": (1, 2), "Top": (0, 1)}
    observations = []
    registrations = []
    for name, (u, v) in views.items():
        image = Image.new("RGB", (200, 200), "white")
        draw = ImageDraw.Draw(image)
        pixels = [[100 + p[u] * 100, 100 + p[v] * 100] for p in points]
        for i, (x, y) in enumerate(pixels):
            draw.ellipse((x - 2, 200 - y - 2, x + 2, 200 - y + 2), fill="black")
            observations.append(
                {
                    "id": f"{name}.{i:02}",
                    "reference": name,
                    "pixel": [x, y],
                    "label": f"Mounting block corner {i:02}",
                    "sigma_pixels": 0.25,
                }
            )
        for block in range(4):
            for a in range(8):
                for b in range(a):
                    if (a ^ b).bit_count() == 1:
                        p, q = pixels[block * 8 + a], pixels[block * 8 + b]
                        draw.line((p[0], 200 - p[1], q[0], 200 - q[1]), fill="gray")
        image.save(directory / f"{name}.png")
        registrations.append(
            {
                "reference": name,
                "projection": "orthographic",
                "calibration": {"a": [0, 0], "b": [200, 0], "distance": 2},
                "origin_pixel": [100, 100],
                "horizontal": "xyz"[u],
                "vertical": "xyz"[v],
            }
        )
    landmarks = [
        {
            "name": f"Corner.{i:02}",
            "source": {
                "kind": "observations",
                "observations": [f"{name}.{i:02}" for name in views],
            },
            "category": "mechanical",
        }
        for i in range(32)
    ]
    result = {
        "points": points,
        "observations": observations,
        "registrations": registrations,
        "landmarks": landmarks,
    }
    (directory / "fixture.json").write_text(json.dumps(result, indent=2))
    return result
