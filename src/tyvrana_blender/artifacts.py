"""Private extension spool, shared only with its owned networking subprocess."""

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tyvrana_protocol import MAX_ARTIFACT_CHUNK_SIZE, ArtifactDescriptor

MAX_RENDER_BYTES = 16 * 1024 * 1024
MAX_DATA_IMAGE_BYTES = 128 * 1024 * 1024
MAX_SPOOLED_RENDERS = 4


class SpoolFull(Exception):
    pass


class ArtifactTooLarge(Exception):
    pass


class ArtifactSpool:
    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(
            prefix="tyvrana-blender-artifacts-"
        )
        self.root = Path(self._directory.name)

    @contextmanager
    def reserve(self) -> Iterator[tuple[str, Path]]:
        if len(list(self.root.glob("*.png"))) >= MAX_SPOOLED_RENDERS:
            raise SpoolFull("Too many renders await transfer")
        artifact_id = uuid4().hex
        path = self.root / (artifact_id + ".png")
        path.touch(exist_ok=False)
        try:
            yield artifact_id, path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def describe(self, artifact_id: str) -> ArtifactDescriptor:
        path = self.root / (artifact_id + ".png")
        size = path.stat().st_size
        if size > MAX_RENDER_BYTES:
            raise ArtifactTooLarge(f"PNG is {size} bytes; limit is {MAX_RENDER_BYTES}")
        digest = hashlib.sha256()
        received = 0
        with path.open("rb") as stream:
            while chunk := stream.read(MAX_ARTIFACT_CHUNK_SIZE):
                received += len(chunk)
                if received > MAX_RENDER_BYTES:
                    raise ArtifactTooLarge("PNG grew beyond the byte limit")
                digest.update(chunk)
        if received != size or size == 0:
            raise ValueError("Render file is empty or changed during hashing")
        return ArtifactDescriptor(
            artifact_id=artifact_id,
            name="render.png",
            media_type="image/png",
            byte_size=size,
            sha256=digest.hexdigest(),
        )

    def release(self, descriptors: tuple[ArtifactDescriptor, ...]) -> None:
        for descriptor in descriptors:
            (self.root / (descriptor.artifact_id + ".png")).unlink(missing_ok=True)

    def close(self) -> None:
        self._directory.cleanup()
