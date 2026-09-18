"""Private extension spool, shared only with its owned networking subprocess."""

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tyvrana_protocol import MAX_ARTIFACT_CHUNK_SIZE, ArtifactDescriptor

MAX_RENDER_BYTES = 128 * 1024 * 1024
MAX_DATA_IMAGE_BYTES = 128 * 1024 * 1024
MAX_SPOOLED_RENDERS = 4
MEDIA_SUFFIXES = {"image/png": ".png", "image/x-exr": ".exr", "application/zip": ".zip"}


def artifact_path(root: Path, descriptor: ArtifactDescriptor) -> Path:
    return root / (descriptor.artifact_id + MEDIA_SUFFIXES[descriptor.media_type])


class SpoolFull(Exception):
    pass


class ArtifactTooLarge(Exception):
    pass


class ArtifactSpool:
    def __init__(self, root: Path | None = None) -> None:
        self._directory = (
            tempfile.TemporaryDirectory(prefix="tyvrana-blender-artifacts-")
            if root is None
            else None
        )
        if self._directory is not None:
            self.root: Path = Path(self._directory.name)
        else:
            assert root is not None
            self.root = root

    @contextmanager
    def reserve(self, media_type: str = "image/png") -> Iterator[tuple[str, Path]]:
        if (
            sum(
                len(list(self.root.glob("*" + suffix)))
                for suffix in MEDIA_SUFFIXES.values()
            )
            >= MAX_SPOOLED_RENDERS
        ):
            raise SpoolFull("Too many renders await transfer")
        artifact_id = uuid4().hex
        path = self.root / (artifact_id + MEDIA_SUFFIXES[media_type])
        path.touch(exist_ok=False)
        try:
            yield artifact_id, path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def describe(
        self,
        artifact_id: str,
        media_type: str = "image/png",
        *,
        maximum: int = MAX_RENDER_BYTES,
        name: str | None = None,
    ) -> ArtifactDescriptor:
        path = self.root / (artifact_id + MEDIA_SUFFIXES[media_type])
        size = path.stat().st_size
        maximum = min(maximum, MAX_RENDER_BYTES)
        if size > maximum:
            raise ArtifactTooLarge(f"Artifact is {size} bytes; limit is {maximum}")
        digest = hashlib.sha256()
        received = 0
        with path.open("rb") as stream:
            while chunk := stream.read(MAX_ARTIFACT_CHUNK_SIZE):
                received += len(chunk)
                if received > maximum:
                    raise ArtifactTooLarge("Artifact grew beyond the byte limit")
                digest.update(chunk)
        if received != size or size == 0:
            raise ValueError("Render file is empty or changed during hashing")
        return ArtifactDescriptor(
            artifact_id=artifact_id,
            name=name or "render" + MEDIA_SUFFIXES[media_type],
            media_type=media_type,
            byte_size=size,
            sha256=digest.hexdigest(),
        )

    def release(self, descriptors: tuple[ArtifactDescriptor, ...]) -> None:
        for descriptor in descriptors:
            artifact_path(self.root, descriptor).unlink(missing_ok=True)

    def close(self) -> None:
        if self._directory is not None:
            self._directory.cleanup()
