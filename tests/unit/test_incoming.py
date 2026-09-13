import hashlib
import time
from pathlib import Path
from uuid import uuid4

import pytest
from tyvrana_protocol import (
    ArtifactBegin,
    ArtifactChunk,
    ArtifactDescriptor,
    OperationRequest,
)

from tyvrana_blender import incoming
from tyvrana_blender.incoming import InputError, InputStore, input_path


def begin(data: bytes, request_id: str = "request", **changes: object) -> ArtifactBegin:
    descriptor = ArtifactDescriptor(
        artifact_id=uuid4().hex,
        name="Texture",
        media_type="image/png",
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return ArtifactBegin(
        type="artifact.begin",
        request_id=request_id,
        transfer_id=uuid4().hex,
        descriptor=descriptor,
    ).model_copy(update=changes)


def request(*messages: ArtifactBegin, request_id: str = "request") -> OperationRequest:
    return OperationRequest(
        type="operation.request",
        request_id=request_id,
        operation="blender.image.create_from_artifact",
        arguments={},
        artifacts=tuple(item.descriptor for item in messages),
    )


@pytest.mark.parametrize("size", [0, 1, 65536, 131089])
def test_exact_input_storage_claim_and_cleanup(tmp_path: Path, size: int) -> None:
    data = (bytes(range(256)) * (size // 256 + 1))[:size]
    message = begin(data, "request:with.dots")
    store = InputStore(tmp_path)
    store.begin(message)
    with pytest.raises(InputError, match="incomplete"):
        store.claim(request(message, request_id=message.request_id))
    for offset in range(0, size, 65536):
        store.write(
            ArtifactChunk(message.transfer_id, offset, data[offset : offset + 65536])
        )
    store.complete(message.transfer_id)
    path = input_path(tmp_path, message.request_id, message.descriptor.artifact_id)
    assert path.parent == tmp_path and path.read_bytes() == data
    store.claim(request(message, request_id=message.request_id))
    assert not store.expired(time.monotonic() + 100)
    with pytest.raises(InputError):
        store.claim(request(message, request_id=message.request_id))
    store.discard_request(message.request_id)
    assert not store.entries and store.reserved_bytes == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "failure", ["offset", "size", "hash", "incomplete", "unknown", "complete_twice"]
)
def test_integrity_failures(tmp_path: Path, failure: str) -> None:
    store = InputStore(tmp_path)
    message = begin(b"abc")
    store.begin(message)
    with pytest.raises(InputError):
        if failure == "offset":
            store.write(ArtifactChunk(message.transfer_id, 1, b"a"))
        elif failure == "size":
            store.write(ArtifactChunk(message.transfer_id, 0, b"abcd"))
        elif failure == "unknown":
            store.write(ArtifactChunk(uuid4().hex, 0, b"a"))
        elif failure == "hash":
            store.write(ArtifactChunk(message.transfer_id, 0, b"abd"))
            store.complete(message.transfer_id)
        elif failure == "incomplete":
            store.complete(message.transfer_id)
        else:
            store.write(ArtifactChunk(message.transfer_id, 0, b"abc"))
            store.complete(message.transfer_id)
            store.complete(message.transfer_id)
    store.clear()
    assert not list(tmp_path.iterdir())


def test_two_inputs_descriptor_matching_and_repeated_use(tmp_path: Path) -> None:
    store = InputStore(tmp_path)
    first, second = begin(b"a"), begin(b"b")
    for item, data in [(first, b"a"), (second, b"b")]:
        store.begin(item)
        store.write(ArtifactChunk(item.transfer_id, 0, data))
        store.complete(item.transfer_id)
    with pytest.raises(InputError):
        store.claim(request(first))
    changed = second.model_copy(
        update={"descriptor": second.descriptor.model_copy(update={"name": "changed"})}
    )
    with pytest.raises(InputError):
        store.claim(request(first, changed))
    store.claim(request(second, first))
    third = first.model_copy(update={"request_id": "other", "transfer_id": uuid4().hex})
    store.begin(third)
    store.write(ArtifactChunk(third.transfer_id, 0, b"a"))
    store.complete(third.transfer_id)
    store.claim(request(third, request_id="other"))
    store.discard_request("request")
    assert len(store.entries) == 1
    assert (
        input_path(tmp_path, "other", third.descriptor.artifact_id).read_bytes() == b"a"
    )
    store.clear()


def test_duplicate_ids_never_replace_bytes(tmp_path: Path) -> None:
    store = InputStore(tmp_path)
    message = begin(b"a")
    store.begin(message)
    with pytest.raises(ValueError, match="collision"):
        store.begin(message)
    with pytest.raises(InputError, match="duplicated"):
        store.begin(message.model_copy(update={"transfer_id": uuid4().hex}))
    store.write(ArtifactChunk(message.transfer_id, 0, b"a"))
    store.complete(message.transfer_id)
    store.claim(request(message))
    with pytest.raises(InputError):
        store.begin(begin(b"b"))
    store.clear()


@pytest.mark.parametrize(
    "bound",
    [
        "MAX_INPUT_BYTES",
        "MAX_INPUT_STORAGE",
        "MAX_INPUT_ENTRIES",
        "MAX_INPUT_TRANSFERS",
    ],
)
def test_limits_reserve_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    monkeypatch.setattr(incoming, bound, 1)
    store = InputStore(tmp_path)
    first = begin(b"a")
    store.begin(first)
    with pytest.raises(InputError, match="limit"):
        store.begin(begin(b"aa", "another"))
    assert len(list(tmp_path.iterdir())) == 1 and store.reserved_bytes == 1
    store.clear()


def test_attachment_limit_and_idle_expiry(tmp_path: Path) -> None:
    store = InputStore(tmp_path)
    for _ in range(8):
        item = begin(b"")
        store.begin(item)
        store.complete(item.transfer_id)
    with pytest.raises(InputError, match="limit"):
        store.begin(begin(b""))
    assert len(store.expired(time.monotonic() + 31)) == 1
    store.clear()
    assert not store.expired(time.monotonic() + 31)


def test_no_inputs_need_no_storage(tmp_path: Path) -> None:
    store = InputStore(tmp_path)
    store.claim(request())
    assert not store.entries
