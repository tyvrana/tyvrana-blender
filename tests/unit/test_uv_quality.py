import math
import struct
import zlib
from typing import Any

import pytest

from tyvrana_blender import uv_quality as quality
from tyvrana_blender.uv_models import UVLayoutArguments, UVPackArguments


def face(index: int = 0, x: float = 0, width: float = 1) -> quality.Face:
    return quality.Face(
        index,
        [index * 4 + i for i in range(4)],
        [(x, 0), (x + width, 0), (x + width, 1), (x, 1)],
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
    )


def test_isometric_density_area_and_invariance() -> None:
    report, _ = quality.analyze([quality.Surface("Panel", [face()])], 4096, False)
    assert report.island_count == 1 and report.overlap_pair_count == 0
    assert report.uv_area == pytest.approx(1) and report.world_area == pytest.approx(1)
    assert report.density.median == pytest.approx(4096)
    assert report.anisotropy.maximum == pytest.approx(1)
    assert report.angle_error_degrees.maximum == pytest.approx(0, abs=1e-6)
    other = face()
    other.uv = [(-2 * y + 5, 2 * x + 8) for x, y in other.uv]
    second, _ = quality.analyze([quality.Surface("Panel", [other])], 4096, False)
    assert second.anisotropy.maximum == pytest.approx(1)
    assert second.density.median == pytest.approx(8192)


def test_stretch_flip_and_degeneracy_are_distinct() -> None:
    stretched = face(width=4)
    report, _ = quality.analyze([quality.Surface("Panel", [stretched])], 1024, False)
    assert report.anisotropy.maximum == pytest.approx(4)
    assert report.angle_error_degrees.maximum is not None
    assert report.angle_error_degrees.maximum > 20
    flipped = face()
    flipped.uv = [(-x, y) for x, y in flipped.uv]
    report, _ = quality.analyze([quality.Surface("Panel", [flipped])], 1024, False)
    assert report.flipped_face_count == 1 and report.degenerate_face_count == 0
    collapsed = face()
    collapsed.uv = [(0, 0)] * 4
    report, _ = quality.analyze([quality.Surface("Panel", [collapsed])], 1024, False)
    assert report.degenerate_face_count == 1 and report.flipped_face_count == 0


def test_overlap_across_objects_and_touching_boundaries() -> None:
    for offset, expected in [(0, 1), (0.5, 1), (1, 0), (2, 0)]:
        report, _ = quality.analyze(
            [quality.Surface("A", [face()]), quality.Surface("B", [face(x=offset)])],
            1000,
            True,
        )
        assert report.overlap_pair_count == expected
        if expected:
            assert report.overlaps[0].uv_area == pytest.approx(1 - offset)
        else:
            assert report.minimum_island_gap_pixels == pytest.approx(
                (offset - 1) * 1000
            )


def test_connectivity_requires_both_shared_edge_and_shared_uvs() -> None:
    a = face()
    b = face(1, x=1)
    b.vertices = [1, 4, 5, 2]
    surface = quality.Surface("Panel", [a, b])
    assert len(quality.islands(surface)) == 1
    b.uv = [(x + 0.001, y) for x, y in b.uv]
    assert len(quality.islands(surface)) == 2
    assert quality.island_gap(quality.islands(surface)) == pytest.approx(0.001)


def test_native_triangulation_diagonal_does_not_report_self_overlap() -> None:
    report, _ = quality.analyze([quality.Surface("A", [face()])], 1000, False)
    assert report.triangle_count == 2 and report.overlap_pair_count == 0


def test_png_signature_dimensions_crc_and_decompression() -> None:
    report, triangles = quality.analyze([quality.Surface("A", [face()])], 1024, False)
    png = quality.png_layout(triangles, 128, report.bounds_min, report.bounds_max)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    compressed = b""
    while pos < len(png):
        size = struct.unpack("!I", png[pos : pos + 4])[0]
        kind = png[pos + 4 : pos + 8]
        data = png[pos + 8 : pos + 8 + size]
        assert (
            zlib.crc32(kind + data)
            == struct.unpack("!I", png[pos + 8 + size : pos + 12 + size])[0]
        )
        if kind == b"IHDR":
            assert struct.unpack("!2I", data[:8]) == (128, 128)
        if kind == b"IDAT":
            compressed += data
        pos += size + 12
    raw = zlib.decompress(compressed)
    assert len(raw) == 128 * (128 * 3 + 1) and len(set(raw)) > 6


@pytest.mark.parametrize("value", [math.nan, math.inf, 101])
def test_nonfinite_and_unbounded_coordinates_rejected(value: float) -> None:
    invalid = face()
    invalid.uv[0] = (value, 0)
    with pytest.raises(ValueError, match="finite"):
        quality.analyze([quality.Surface("A", [invalid])], 1024, False)


def test_joint_pack_and_inspection_contracts() -> None:
    model = UVPackArguments.model_validate(
        dict(
            objects=[dict(object_name="A", density_weight=1.5)],
            bounds_max=[0.5, 1],
            padding_pixels=16,
        )
    )
    assert model.bounds_max == [0.5, 1]
    patches: list[dict[str, Any]] = [
        dict(objects=[dict(object_name="A")] * 2),
        dict(bounds_max=[0, 1]),
        dict(bounds_min=[-0.1, 0]),
        dict(resolution=64, padding_pixels=256),
    ]
    for patch in patches:
        with pytest.raises(ValueError):
            UVPackArguments.model_validate(
                dict(objects=[dict(object_name="A")]) | patch
            )
    with pytest.raises(ValueError):
        UVLayoutArguments(objects=["A", "A"])
