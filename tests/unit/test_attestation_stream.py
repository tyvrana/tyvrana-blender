"""Canonical byte framing and bounded native buffer ownership."""

import array
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def attestation(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setitem(
        sys.modules,
        "bpy",
        SimpleNamespace(
            app=SimpleNamespace(version=(5, 2, 1)),
            types=SimpleNamespace(ID=type("ID", (), {})),
        ),
    )
    bindings = ModuleType("tyvrana_blender.bindings")
    bindings.project_id = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, bindings.__name__, bindings)
    path = Path(__file__).resolve().parents[2] / "src/tyvrana_blender/attestation.py"
    spec = importlib.util.spec_from_file_location(
        "tyvrana_blender._attestation_test", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chunk_boundaries_do_not_change_digest(attestation: Any) -> None:
    first, second = attestation.Hasher(), attestation.Hasher()
    first.blob(6, [b"abcdef"])
    second.blob(6, [b"ab", b"c", b"def"])
    assert first.digest.digest() == second.digest.digest()
    third = attestation.Hasher()
    third.blob(2, [b"ab"])
    third.blob(4, [b"cdef"])
    assert first.digest.digest() != third.digest.digest()
    with pytest.raises(attestation.Unqualified, match="declared byte length"):
        attestation.Hasher().blob(3, [b"ab"])


def test_native_memory_and_mapped_paths_are_identical(attestation: Any) -> None:
    class Source:
        def foreach_get(self, target: Any) -> None:
            for index in range(len(target)):
                target[index] = index / 8

    first = attestation.Hasher()
    first.native_array(Source(), 64, "f")
    attestation.DIRECT_BUFFER = 16
    second = attestation.Hasher()
    second.native_array(Source(), 64, "f")
    assert first.digest.digest() == second.digest.digest()
    attestation.MAX_BYTES = 128
    rejected = attestation.Hasher()
    with pytest.raises(attestation.Unqualified, match="stream_bytes"):
        rejected.native_array(Source(), 64, "f")
    assert rejected.diagnostics()["exceeded"] == "stream_bytes"
    assert rejected.bytes > 128
    # Rejection closes exported views and anonymous scratch without BufferError.
    attestation.MAX_BYTES = 4096
    recovered = attestation.Hasher()
    recovered.native_array(Source(), 64, "f")
    assert recovered.digest.digest() == first.digest.digest()


def test_work_bounds_identify_the_exhausted_counter(attestation: Any) -> None:
    attestation.MAX_ITEMS = 1
    h = attestation.Hasher()
    h.feed(b"one")
    with pytest.raises(attestation.Unqualified, match="stream_items"):
        h.feed(b"two")
    assert h.diagnostics()["stream_items"] == 2
    attestation.MAX_BUFFER = 8
    with pytest.raises(attestation.Unqualified, match="buffer_bytes"):
        attestation.Hasher().content(array.array("B", range(16)))
