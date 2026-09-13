"""Request-scoped input storage owned by the networking worker."""

import hashlib
import time
from pathlib import Path
from typing import BinaryIO

from tyvrana_protocol import ArtifactBegin, ArtifactChunk, OperationRequest

MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_INPUT_STORAGE = 512 * 1024 * 1024
MAX_INPUT_ENTRIES = 128
MAX_INPUT_TRANSFERS = 4
INPUT_IDLE_TIMEOUT = 30.0


class InputError(Exception):
    """A sanitized failure that never includes an internal path."""


def input_path(root: Path, request_id: str, artifact_id: str) -> Path:
    # Request IDs are general identifiers, not path components. Artifact IDs have
    # already passed the protocol's lowercase hexadecimal validation.
    request_key = hashlib.sha256(request_id.encode()).hexdigest()
    return root / f"input-{request_key}-{artifact_id}.complete"


class _Input:
    def __init__(self, begin: ArtifactBegin, path: Path, stream: BinaryIO) -> None:
        self.begin = begin
        self.path = path
        self.stream: BinaryIO | None = stream
        self.digest = hashlib.sha256()
        self.offset = 0
        self.complete = False
        self.claimed = False
        self.updated = time.monotonic()


class InputStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: dict[str, _Input] = {}

    @property
    def reserved_bytes(self) -> int:
        return sum(entry.begin.descriptor.byte_size for entry in self.entries.values())

    def request_for(self, transfer_id: str) -> str | None:
        entry = self.entries.get(transfer_id)
        return entry.begin.request_id if entry is not None else None

    def _touch(self, request_id: str) -> None:
        now = time.monotonic()
        for entry in self.entries.values():
            if entry.begin.request_id == request_id:
                entry.updated = now

    def begin(self, message: ArtifactBegin) -> None:
        if message.transfer_id in self.entries:
            raise ValueError("Input transfer ID collision")
        related = [
            entry
            for entry in self.entries.values()
            if entry.begin.request_id == message.request_id
        ]
        if any(
            entry.claimed
            or entry.begin.descriptor.artifact_id == message.descriptor.artifact_id
            for entry in related
        ):
            raise InputError("Input is duplicated or operation already started")
        if (
            message.descriptor.byte_size > MAX_INPUT_BYTES
            or self.reserved_bytes + message.descriptor.byte_size > MAX_INPUT_STORAGE
            or len(self.entries) >= MAX_INPUT_ENTRIES
            or sum(not entry.complete for entry in self.entries.values())
            >= MAX_INPUT_TRANSFERS
            or len(related) >= 8
        ):
            raise InputError("Input artifact storage limit exceeded")
        path = input_path(
            self.root, message.request_id, message.descriptor.artifact_id
        ).with_suffix(".partial")
        try:
            stream = path.open("xb")
        except OSError as exc:
            raise InputError("Cannot create temporary input") from exc
        self.entries[message.transfer_id] = _Input(message, path, stream)
        self._touch(message.request_id)

    def write(self, chunk: ArtifactChunk) -> None:
        entry = self.entries.get(chunk.transfer_id)
        if entry is None or entry.stream is None or entry.complete:
            raise InputError("Input transfer is unavailable")
        if chunk.offset != entry.offset or (
            entry.offset + len(chunk.payload) > entry.begin.descriptor.byte_size
        ):
            raise InputError("Input offset or size does not match")
        try:
            entry.stream.write(chunk.payload)
        except OSError as exc:
            raise InputError("Cannot write temporary input") from exc
        entry.offset += len(chunk.payload)
        entry.digest.update(chunk.payload)
        self._touch(entry.begin.request_id)

    def complete(self, transfer_id: str) -> None:
        entry = self.entries.get(transfer_id)
        if entry is None or entry.complete or entry.stream is None:
            raise InputError("Input transfer is unavailable")
        descriptor = entry.begin.descriptor
        if (
            entry.offset != descriptor.byte_size
            or entry.digest.hexdigest() != descriptor.sha256
        ):
            raise InputError("Input integrity check failed")
        try:
            entry.stream.close()
            entry.stream = None
            final = input_path(
                self.root, entry.begin.request_id, descriptor.artifact_id
            )
            entry.path.replace(final)
            entry.path = final
        except OSError as exc:
            raise InputError("Cannot complete temporary input") from exc
        entry.complete = True
        self._touch(entry.begin.request_id)

    def claim(self, request: OperationRequest) -> None:
        related = [
            entry
            for entry in self.entries.values()
            if entry.begin.request_id == request.request_id
        ]
        actual = {
            entry.begin.descriptor.artifact_id: entry.begin.descriptor
            for entry in related
        }
        expected = {
            descriptor.artifact_id: descriptor for descriptor in request.artifacts
        }
        if actual != expected or any(
            not entry.complete or entry.claimed for entry in related
        ):
            raise InputError("Operation inputs are incomplete or do not match")
        for entry in related:
            entry.claimed = True

    def expired(self, now: float) -> tuple[tuple[str, str], ...]:
        # One transfer ID per request is enough to correlate a late abort at core.
        return tuple(
            {
                entry.begin.request_id: transfer_id
                for transfer_id, entry in self.entries.items()
                if not entry.claimed and now - entry.updated >= INPUT_IDLE_TIMEOUT
            }.items()
        )

    def discard_request(self, request_id: str) -> None:
        for transfer_id, entry in list(self.entries.items()):
            if entry.begin.request_id == request_id:
                del self.entries[transfer_id]
                try:
                    if entry.stream is not None:
                        entry.stream.close()
                finally:
                    entry.path.unlink(missing_ok=True)

    def clear(self) -> None:
        for request_id in {entry.begin.request_id for entry in self.entries.values()}:
            self.discard_request(request_id)
