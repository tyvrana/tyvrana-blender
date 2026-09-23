"""Canonical byte framing and bounded native buffer ownership."""

import array
import importlib.util
import math
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
    numpy = SimpleNamespace(
        float32="f",
        int32="i",
        frombuffer=lambda data, dtype: memoryview(data).cast(dtype),
        isfinite=lambda data: SimpleNamespace(
            all=lambda: all(math.isfinite(x) for x in data)
        ),
    )
    monkeypatch.setitem(sys.modules, "numpy", numpy)
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


def test_native_float_encoding_preserves_signed_zero_and_rejects_nonfinite(
    attestation: Any,
) -> None:
    class Source:
        def __init__(self, value: float):
            self.value = value

        def foreach_get(self, target: Any) -> None:
            target[0] = self.value

    positive, negative = attestation.Hasher(), attestation.Hasher()
    positive.native_array(Source(0.0), 1, "f")
    negative.native_array(Source(-0.0), 1, "f")
    assert positive.digest.digest() != negative.digest.digest()
    for value in [float("nan"), float("inf"), -float("inf")]:
        with pytest.raises(attestation.Unqualified, match="Nonfinite"):
            attestation.Hasher().native_array(Source(value), 1, "f")


def test_resource_closure_tracks_dependencies_without_incoming_users(
    attestation: Any,
) -> None:
    h = attestation.Hasher()
    h.configuration = "color-config"
    obj, mesh, material = (
        ("Object", "Base"),
        ("Mesh", "Geometry"),
        ("Material", "Surface"),
    )
    h.nodes = {
        obj: ("object-state", {mesh, material}),
        mesh: ("geometry-a", set()),
        material: ("shader-a", set()),
    }
    h.roots = [("object", "base-id", "Base", obj)]
    original = h.resource_evidence()[0]
    # A new downstream user refers to Base. This does not make it a dependency of Base.
    h.nodes["Object", "Harness"] = ("working", {obj})
    assert h.resource_evidence()[0] == original
    for dependency in (mesh, material):
        previous = h.nodes[dependency]
        h.nodes[dependency] = ("changed", set())
        assert h.resource_evidence()[0]["fingerprint"] != original["fingerprint"]
        h.nodes[dependency] = previous
    del h.nodes[mesh]
    assert h.resource_evidence()[0]["state"] == "unsupported"
    h.roots.append(("object", "base-id", "Duplicate", obj))
    assert h.resource_evidence()[0]["state"] == "ambiguous"


def test_resource_metadata_does_not_change_document_stream(attestation: Any) -> None:
    first, second = attestation.Hasher(), attestation.Hasher()
    first.feed(b"native-content")
    with second.resource("objects", "Base"):
        second.feed(b"native-content")
    assert first.digest.digest() == second.digest.digest()


def test_particle_editor_target_is_not_an_authored_reference(attestation: Any) -> None:
    types = attestation.bpy.types
    for name in (
        "ShapeKey",
        "ViewLayer",
        "Mesh",
        "Image",
        "Bone",
        "Collection",
        "Scene",
        "NodeTree",
        "Node",
    ):
        setattr(types, name, type(name, (), {}))

    class Reference(types.ID):  # type: ignore[misc, name-defined]
        is_embedded_data = False
        library = None
        bl_rna = SimpleNamespace(identifier="Object")

        def __init__(self, name: str) -> None:
            self.name_full = name

    class RNA:
        def __init__(self) -> None:
            self.bl_rna = SimpleNamespace(
                identifier=type(self).__name__,
                properties=[
                    SimpleNamespace(identifier=key)
                    for key in ("object", "shape_object")
                ],
            )
            self.object: Any = None
            self.shape_object: Any = None

        def as_pointer(self) -> int:
            return id(self)

        def keys(self) -> list[str]:
            return []

    class ParticleEdit(RNA):
        pass

    types.ParticleEdit = ParticleEdit

    def digest(owner: RNA) -> str:
        h = attestation.Hasher()
        h.rna(owner)
        return str(h.digest.hexdigest())

    editor = ParticleEdit()
    empty = digest(editor)
    editor.object = Reference("Any editor target")
    assert digest(editor) == empty
    editor.shape_object = Reference("Authored shaping boundary")
    assert digest(editor) != empty

    ordinary = RNA()
    before = digest(ordinary)
    ordinary.object = editor.object
    assert digest(ordinary) != before
