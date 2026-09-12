import hashlib
from collections.abc import Iterator

import pytest

from tyvrana_blender.artifacts import (
    MAX_RENDER_BYTES,
    MAX_SPOOLED_RENDERS,
    ArtifactSpool,
    ArtifactTooLarge,
    SpoolFull,
)


@pytest.fixture
def spool() -> Iterator[ArtifactSpool]:
    spool = ArtifactSpool()
    try:
        yield spool
    finally:
        spool.close()
        assert not spool.root.exists()


def test_prepared_file_descriptor_and_release(spool: ArtifactSpool) -> None:
    with spool.reserve() as (identifier, path):
        path.write_bytes(b"image bytes")
        descriptor = spool.describe(identifier)
    assert descriptor.sha256 == hashlib.sha256(b"image bytes").hexdigest()
    assert descriptor.byte_size == 11
    assert descriptor.media_type == "image/png"
    assert str(path) not in descriptor.model_dump_json()
    spool.release((descriptor,))
    assert not path.exists()


def test_failed_render_reservation_is_deleted(spool: ArtifactSpool) -> None:
    with pytest.raises(RuntimeError):
        with spool.reserve() as (_, path):
            path.write_bytes(b"partial")
            raise RuntimeError("render failed")
    assert list(spool.root.iterdir()) == []


def test_render_spool_capacity(spool: ArtifactSpool) -> None:
    for _ in range(MAX_SPOOLED_RENDERS):
        with spool.reserve() as (_, path):
            path.write_bytes(b"pending")
    with pytest.raises(SpoolFull):
        with spool.reserve():
            pytest.fail("Capacity exceeded")


@pytest.mark.parametrize("size", [0, MAX_RENDER_BYTES + 1])
def test_invalid_render_file_size_removed(spool: ArtifactSpool, size: int) -> None:
    with pytest.raises((ArtifactTooLarge, ValueError)):
        with spool.reserve() as (identifier, path):
            with path.open("wb") as stream:
                stream.truncate(size)
            spool.describe(identifier)
    assert list(spool.root.iterdir()) == []
