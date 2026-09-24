"""Bounded material RNA attestation; never writes document data or saves files."""

import array
import hashlib
import json
import math
import mmap
import os
import struct
import sys
import tempfile
import time
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from tyvrana_protocol import JsonValue

from .attestation_model import DocumentAttestationResult
from .bindings import project_id

# This identifier names canonical bytes, not unrelated implementation metadata.
# Resource closure observations below do not change the qualified document stream.
FORMAT = "blender-rna-" + ".".join(map(str, bpy.app.version)) + "-7fd96589ef405168"
RESOURCE_SCOPE = FORMAT + "-resource-closure"

MAX_ITEMS = 4000000
MAX_BYTES = 4 * 1024 * 1024 * 1024
MAX_BUFFER = 1024 * 1024 * 1024
DIRECT_BUFFER = 8 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MAX_ELEMENTS = MAX_BYTES // 4
MAX_SECONDS = 30
# RNA runtime caches, UI selection, ownership backreferences and computed handles.
SKIP = {
    "rna_type",
    "id_data",
    "original",
    "evaluated_get",
    "users",
    "use_fake_user",
    "use_extra_user",
    "is_updated",
    "is_updated_data",
    "is_updated_transform",
    "is_evaluated",
    "is_embedded_data",
    "is_missing",
    "is_runtime_data",
    "session_uid",
    "tag",
    "execution_time",
    "bindcode",
    "preview",
    "asset_data",
    "library_weak_reference",
    "is_library_indirect",
    "override_library",
    "is_editable",
    "is_editmode",
    "select",
    "select_control_point",
    "select_left_handle",
    "select_right_handle",
    "active",
    "active_index",
    "active_render",
    "active_clone",
    "active_color",
    "active_color_index",
    "render_color_index",
    "active_shape_key_index",
    "active_material_index",
    "active_uv_index",
    "is_updated_geometry",
    "is_updated_shading",
    "is_updated_time",
    "is_dirty",
    "is_loaded",
    "has_data",
    "is_stereo_3d",
    "is_multiview",
    "users_scene",
    "users_collection",
    "users_dupli_group",
    "children_recursive",
    "bound_box",
    "dimensions",
    "matrix_world",
    "matrix_local",
    "matrix_basis",
    "matrix_channel",
    "matrix",
    "display",
    "color_tag",
    "is_property_set",
    "is_property_readonly",
}
ROOT_SKIP = {
    "all_ids",
    "screens",
    "window_managers",
    "workspaces",
    "brushes",
    "palettes",
    "paint_curves",
}
UNSUPPORTED = {
    "libraries",
    "cache_files",
    "volumes",
    "movieclips",
    "sounds",
    "speakers",
    "pointclouds",
    "lightprobes",
}

# Named armature subresources form a graph, not recursively nested RNA values.
# Serialize their owning rows once; parent/handle/membership links are identities.
ARMATURE_ROWS = {
    ("Armature", "bones"),
    ("Armature", "collections_all"),
    ("Pose", "bones"),
}
ARMATURE_REFERENCES = {"Bone", "PoseBone", "BoneCollection"}
ARMATURE_SKIP = {
    "Armature": {"collections", "edit_bones"},
    "Bone": {"children"},
    "PoseBone": {"child"},
    "BoneCollection": {
        "children",  # Derived from the flat parent mapping.
        "index",  # Enumeration order, not identity.
        "child_number",
        "is_expanded",  # Editor tree expansion.
    },
}

SUPPORTED = {
    "actions",
    "armatures",
    "cameras",
    "collections",
    "curves",
    "fonts",
    "images",
    "lattices",
    "lights",
    "linestyles",
    "materials",
    "meshes",
    "metaballs",
    "node_groups",
    "objects",
    "scenes",
    "shape_keys",
    "texts",
    "textures",
    "worlds",
}


def identity() -> dict[str, str]:
    # bpy belongs to the host interpreter and is not removed by addon reload.
    value = getattr(bpy, "_tyvrana_document_session", None)
    if value is None:
        value = {"host": uuid4().hex, "document": uuid4().hex}
        bpy._tyvrana_document_session = value
    return dict(value)


def document_loaded() -> None:
    value = identity()
    value["document"] = uuid4().hex
    bpy._tyvrana_document_session = value


class Unqualified(Exception):
    pass


class Hasher:
    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.items = 0
        self.bytes = 0
        self.bulk_elements = 0
        self.peak_buffer = 0
        self.start = time.monotonic()
        self.max_seconds: float = MAX_SECONDS
        self.resource_start = self.start
        self.seen: set[int] = set()
        self.exceeded: str | None = None
        self.category = "configuration"
        self.resource_name = "color"
        self.completed = 0
        self.costs: list[dict[str, Any]] = []
        self.resource_digest: Any = None
        self.references: set[tuple[str, str]] = set()
        self.nodes: dict[tuple[str, str], tuple[str, set[tuple[str, str]]]] = {}
        self.roots: list[tuple[str, str, str, tuple[str, str]]] = []
        self.configuration = ""

    def limit(self, name: str) -> None:
        self.exceeded = name
        raise Unqualified("limit: " + name + " attestation budget exhausted")

    def checkpoint(self) -> None:
        if self.items > MAX_ITEMS:
            self.limit("stream_items")
        if self.bytes > MAX_BYTES:
            self.limit("stream_bytes")
        if self.bulk_elements > MAX_ELEMENTS:
            self.limit("bulk_elements")
        if time.monotonic() - self.start > self.max_seconds:
            self.limit("elapsed_ms")
        if time.monotonic() - self.resource_start > 20:
            self.limit("resource_elapsed_ms")

    def raw(self, value: Any) -> None:
        self.bytes += len(value)
        self.checkpoint()
        self.digest.update(value)
        if self.resource_digest is not None:
            self.resource_digest.update(value)

    def feed(self, value: bytes) -> None:
        self.items += 1
        self.raw(struct.pack("!Q", len(value)))
        self.raw(value)

    def buffer(self, size: int) -> None:
        self.peak_buffer = max(self.peak_buffer, size)
        if size > MAX_BUFFER:
            self.limit("buffer_bytes")

    def blob(self, size: int, chunks: Iterable[Any]) -> None:
        # Chunk boundaries are transport only and never enter the canonical stream.
        self.feed(b"bytes")
        self.feed(struct.pack("!Q", size))
        consumed = 0
        iterator = iter(chunks)
        try:
            for chunk in iterator:
                consumed += len(chunk)
                if consumed > size:
                    raise Unqualified("Native content exceeded declared byte length")
                self.raw(chunk)
        finally:
            close = getattr(iterator, "close", None)
            if close:
                close()
        if consumed != size:
            raise Unqualified("Native content did not match declared byte length")

    def content(self, value: Any) -> None:
        self.buffer(len(value))
        with memoryview(value).cast("B") as data:

            def chunks() -> Iterator[memoryview]:
                for offset in range(0, len(data), CHUNK_BYTES):
                    with data[offset : offset + CHUNK_BYTES] as part:
                        yield part

            self.blob(len(data), chunks())

    def file(self, path: Path) -> None:
        size = path.stat().st_size
        self.checkpoint()
        with path.open("rb") as stream:

            def chunks() -> Iterator[bytes]:
                while chunk := stream.read(CHUNK_BYTES):
                    self.buffer(len(chunk))
                    yield chunk

            self.blob(size, chunks())

    def native_array(
        self, source: Any, count: int, code: Literal["f", "i"], key: str | None = None
    ) -> None:
        size = count * array.array(code).itemsize
        self.buffer(size)
        self.bulk_elements += count
        self.checkpoint()
        self.value(("native_array", code, count))
        if not size:
            self.blob(0, ())
            return

        def read(target: Any) -> None:
            if key is None:
                source.foreach_get(target)
            else:
                source.foreach_get(key, target)

        def chunks(data: Any) -> Iterator[memoryview]:
            with memoryview(data).cast("B") as raw:
                for offset in range(0, size, CHUNK_BYTES):
                    if code == "f":
                        values = np.frombuffer(
                            raw[offset : offset + CHUNK_BYTES], dtype=np.float32
                        )
                        if not np.isfinite(values).all():
                            del values
                            raise Unqualified("Nonfinite native float")
                        del values
                    if sys.byteorder == "big" or array.array(code).itemsize == 1:
                        with raw[offset : offset + CHUNK_BYTES] as encoded:
                            yield encoded
                    else:
                        part = array.array(code)
                        part.frombytes(raw[offset : offset + CHUNK_BYTES])
                        part.byteswap()
                        with memoryview(part).cast("B") as encoded:
                            yield encoded

        if size <= DIRECT_BUFFER:
            values = array.array(code, [0]) * count
            read(values)
            self.blob(size, chunks(values))
        else:
            # RNA foreach_get requires a full contiguous target: it has no offset.
            # Disk-backed scratch avoids giant Python arrays/lists and extra copies.
            # Only endian-conversion/hash chunks are materialized in Python.
            with tempfile.TemporaryFile(prefix="tyvrana-attestation-") as scratch:
                scratch.truncate(size)
                with mmap.mmap(scratch.fileno(), size) as mapped:
                    target = np.frombuffer(
                        mapped, dtype=np.float32 if code == "f" else np.int32
                    )
                    try:
                        read(target)
                    finally:
                        del target
                    self.blob(size, chunks(mapped))
        self.checkpoint()

    @contextmanager
    def resource(self, category: str, name: str, item: Any = None) -> Iterator[None]:
        self.category, self.resource_name = category, name
        self.resource_start = time.monotonic()
        before = self.bytes, self.items, self.bulk_elements, time.monotonic()
        self.resource_digest = hashlib.sha256()
        self.references = set()
        try:
            yield
        except BaseException:
            raise
        else:
            self.completed += 1
            if item is not None:
                key = (item.bl_rna.identifier, item.name_full)
                self.nodes[key] = (self.resource_digest.hexdigest(), self.references)
                identifier = item.get("_tyvrana_resource_id")
                kind = {
                    "objects": "object",
                    "materials": "material",
                    "collections": "collection",
                }.get(category)
                if kind and isinstance(identifier, str):
                    self.roots.append((kind, identifier, name, key))
        finally:
            self.resource_digest = None
            self.references = set()
            self.costs.append(
                dict(
                    category=category,
                    resource=name[:256],
                    resources=1,
                    stream_bytes=self.bytes - before[0],
                    stream_items=self.items - before[1],
                    bulk_elements=self.bulk_elements - before[2],
                    elapsed_ms=(time.monotonic() - before[3]) * 1000,
                )
            )

    def resource_evidence(self) -> list[dict[str, Any]]:
        """Strong outgoing dependency closure; incoming users are not dependencies."""
        counts: dict[tuple[str, str], int] = {}
        for kind, identifier, _, _ in self.roots:
            counts[kind, identifier] = counts.get((kind, identifier), 0) + 1
        result: list[dict[str, Any]] = []
        for kind, identifier, name, root in sorted(self.roots):
            if any(
                r["resource_kind"] == kind and r["resource_id"] == identifier
                for r in result
            ):
                continue
            visited: set[tuple[str, str]] = set()
            pending = [root]
            missing = False
            while pending:
                key = pending.pop()
                if key in visited:
                    continue
                visited.add(key)
                self.items += 1
                self.checkpoint()
                node = self.nodes.get(key)
                if node is None:
                    missing = True
                    break
                pending.extend(node[1] - visited)
            state = (
                "ambiguous"
                if counts[kind, identifier] != 1
                else "unsupported"
                if missing
                else "present"
            )
            fingerprint = None
            if state == "present":
                content = [
                    self.configuration,
                    [(key, self.nodes[key][0]) for key in sorted(visited)],
                ]
                fingerprint = hashlib.sha256(
                    json.dumps(
                        content, separators=(",", ":"), ensure_ascii=True
                    ).encode()
                ).hexdigest()
            result.append(
                dict(
                    resource_kind=kind,
                    resource_id=identifier,
                    name=name,
                    state=state,
                    fingerprint=fingerprint,
                )
            )
        return result

    def diagnostics(self) -> dict[str, Any]:
        categories: dict[str, dict[str, Any]] = {}
        for cost in self.costs:
            total = categories.setdefault(
                cost["category"],
                dict(
                    category=cost["category"],
                    resource="",
                    resources=0,
                    stream_bytes=0,
                    stream_items=0,
                    bulk_elements=0,
                    elapsed_ms=0.0,
                ),
            )
            for field in (
                "resources",
                "stream_bytes",
                "stream_items",
                "bulk_elements",
                "elapsed_ms",
            ):
                total[field] += cost[field]
        return dict(
            exceeded=self.exceeded,
            stream_bytes=self.bytes,
            stream_items=self.items,
            bulk_elements=self.bulk_elements,
            resources_completed=self.completed,
            peak_buffer_bytes=self.peak_buffer,
            current_category=self.category,
            current_resource=self.resource_name[:256],
            limits=dict(
                stream_bytes=MAX_BYTES,
                stream_items=MAX_ITEMS,
                bulk_elements=MAX_ELEMENTS,
                buffer_bytes=MAX_BUFFER,
                resources=4096,
                elapsed_ms=int(self.max_seconds * 1000),
                resource_elapsed_ms=20000,
                nesting=40,
            ),
            categories=list(categories.values())[:32],
            heaviest=sorted(
                self.costs, key=lambda row: row["elapsed_ms"], reverse=True
            )[:8],
        )

    def value(self, value: Any, depth: int = 0) -> None:
        if depth > 40:
            self.limit("nesting")
        if value is None:
            self.feed(b"null")
        elif isinstance(value, bool):
            self.feed(b"true" if value else b"false")
        elif isinstance(value, int):
            self.feed(b"int" + str(value).encode())
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise Unqualified("Nonfinite RNA float")
            self.feed(b"float" + struct.pack("!d", value))
        elif isinstance(value, str):
            self.feed(b"str" + value.encode("utf-8", "surrogatepass"))
        elif isinstance(value, bytes):
            self.content(value)
        elif isinstance(value, bpy.types.ID):
            if isinstance(value, bpy.types.Image) and value.type in {
                "RENDER_RESULT",
                "COMPOSITING",
            }:
                raise Unqualified("Referenced transient render/compositor image")
            if value.is_embedded_data:
                self.rna(value, depth + 1)
                return
            self.references.add((value.bl_rna.identifier, value.name_full))
            self.feed(b"ID")
            self.value(value.bl_rna.identifier, depth + 1)
            self.value(value.name_full, depth + 1)
            if value.library:
                raise Unqualified(
                    "Linked library content requires independent attestation"
                )
        elif hasattr(value, "bl_rna") and hasattr(value, "as_pointer"):
            if value.bl_rna.identifier in ARMATURE_REFERENCES:
                owner = value.id_data
                key = (owner.bl_rna.identifier, owner.name_full)
                self.references.add(key)
                self.feed(b"subresource")
                self.value((key, value.bl_rna.identifier, value.name), depth + 1)
            else:
                self.rna(value, depth + 1)
        elif isinstance(value, dict) or hasattr(value, "to_dict"):
            self.feed(b"map")
            for key in sorted(value.keys()):
                self.value(str(key), depth + 1)
                self.value(value[key], depth + 1)
            self.feed(b"end_map")
        elif isinstance(value, set):
            self.value(sorted(value), depth + 1)
        elif hasattr(value, "__iter__") or (
            hasattr(value, "__len__") and hasattr(value, "__getitem__")
        ):
            self.feed(b"array")
            for item in value:
                self.value(item, depth + 1)
            self.feed(b"end")
        else:
            raise Unqualified("Unsupported value class: " + type(value).__name__)

    def bulk(
        self, collection: Any, key: str, code: Literal["f", "i"], width: int = 1
    ) -> None:
        self.value((key, len(collection), width))
        self.native_array(collection, len(collection) * width, code, key)

    def mesh(self, mesh: Any) -> None:
        self.bulk(mesh.vertices, "co", "f", 3)
        self.bulk(mesh.edges, "vertices", "i", 2)
        self.bulk(mesh.loops, "vertex_index", "i")
        self.bulk(mesh.loops, "edge_index", "i")
        for key in ["loop_start", "loop_total", "material_index", "use_smooth"]:
            self.bulk(mesh.polygons, key, "i")
        self.bulk(mesh.corner_normals, "vector", "f", 3)
        self.value("loop_triangle_polygons")
        self.bulk(mesh.loop_triangle_polygons, "value", "i")
        for vertex in mesh.vertices:
            if (
                vertex.index % 1024 == 0
                and time.monotonic() - self.start > self.max_seconds
            ):
                self.limit("elapsed_ms")
            if vertex.groups:
                self.value((vertex.index, [(g.group, g.weight) for g in vertex.groups]))
        for attribute in sorted(mesh.attributes, key=lambda a: a.name):
            self.value((attribute.name, attribute.domain, attribute.data_type))
            if not attribute.data:
                continue
            sample = attribute.data[0]
            for prop in sorted(sample.bl_rna.properties, key=lambda p: p.identifier):
                key = prop.identifier
                if key in {"rna_type", "id_data"}:
                    continue
                if prop.type in {"FLOAT", "INT", "BOOLEAN"}:
                    self.bulk(
                        attribute.data,
                        key,
                        "f" if prop.type == "FLOAT" else "i",
                        prop.array_length if prop.is_array else 1,
                    )
                elif prop.type == "STRING":
                    self.value(getattr(v, key) for v in attribute.data)
                else:
                    raise Unqualified("Unsupported mesh attribute field: " + key)
        self.value([(uv.name, uv.active_render) for uv in mesh.uv_layers])
        self.value(
            (
                mesh.color_attributes.active_color_index,
                mesh.color_attributes.render_color_index,
            )
        )

    def rna(self, owner: Any, depth: int = 0) -> None:
        if depth > 40:
            self.limit("nesting")
        kind = owner.bl_rna.identifier
        if kind == "Armature" and owner.is_editmode:
            # EditBone is an uncommitted editing facade. Never attest stale Bone
            # rows while pending authored rest edits exist in that facade.
            raise Unqualified("Armature edit mode requires committing rest edits")
        if owner.bl_rna.identifier in {
            "ShaderNodeScript",
            "GeometryNodeSimulationInput",
            "GeometryNodeSimulationOutput",
            "GeometryNodeBake",
        }:
            raise Unqualified(
                "External/procedural cache content: " + owner.bl_rna.identifier
            )
        if (
            owner.bl_rna.identifier == "Driver"
            and owner.type == "SCRIPTED"
            and not owner.is_simple_expression
        ):
            raise Unqualified(
                "Non-simple scripted driver depends on external runtime state"
            )
        if isinstance(owner, bpy.types.Node) and owner.type == "CUSTOM":
            raise Unqualified("Custom node evaluation is not attested")
        pointer = owner.as_pointer()
        if pointer in self.seen:
            self.feed(b"backreference")
            return
        self.seen.add(pointer)
        try:
            self.feed(owner.bl_rna.identifier.encode())
            for prop in sorted(owner.bl_rna.properties, key=lambda p: p.identifier):
                key = prop.identifier
                if key in ARMATURE_SKIP.get(kind, ()):
                    continue
                if kind == "Object" and key == "mode" and owner.type == "ARMATURE":
                    continue  # Object/pose editor context, not authored pose channels.
                if (kind, key) in ARMATURE_ROWS:
                    self.feed(key.encode())
                    self.feed(b"armature_rows")
                    for row in sorted(getattr(owner, key), key=lambda item: item.name):
                        self.rna(row, depth + 1)
                    self.feed(b"end_armature_rows")
                    continue
                if isinstance(owner, bpy.types.ShapeKey) and key in {"data", "points"}:
                    values = getattr(owner, key)
                    if values and values[0].bl_rna.identifier == "ShapeKeyPoint":
                        self.value(("ShapeKey", key))
                        self.bulk(values, "co", "f", 3)
                        continue
                if isinstance(owner, bpy.types.ViewLayer) and key == "depsgraph":
                    continue
                if isinstance(owner, bpy.types.ParticleEdit) and key == "object":
                    # Read-only editor target, resolved from interactive context.
                    # Keep shape_object, brush settings and authored particle data.
                    continue
                if isinstance(owner, bpy.types.Mesh) and key in {
                    "vertices",
                    "edges",
                    "loops",
                    "polygons",
                    "loop_triangles",
                    "loop_triangle_polygons",
                    "corner_normals",
                    "vertex_normals",
                    "polygon_normals",
                    "attributes",
                    "color_attributes",
                    "uv_layers",
                    "vertex_colors",
                }:
                    continue
                if (
                    key in SKIP
                    and not (
                        isinstance(owner, bpy.types.Bone) and key == "matrix_local"
                    )
                ) or (
                    isinstance(owner, bpy.types.Image)
                    and key in {"pixels", "packed_file", "packed_files"}
                ):
                    continue
                # Path and saved runtime UUID are identity/locator, not material state.
                if key in {"filepath", "filepath_raw", "name_full"}:
                    continue
                try:
                    value = getattr(owner, key)
                except (AttributeError, RuntimeError, TypeError) as exc:
                    raise Unqualified(
                        f"Unreadable {owner.bl_rna.identifier}.{key}"
                    ) from exc
                if (kind, key) in {
                    ("Bone", "collections"),
                    ("BoneCollection", "bones"),
                }:
                    value = sorted(value, key=lambda item: item.name)
                if isinstance(
                    owner, (bpy.types.Collection, bpy.types.Scene, bpy.types.ViewLayer)
                ) and key in {
                    "objects",
                    "all_objects",
                    "children",
                    "collection_objects",
                    "collection_children",
                }:

                    def order(item: Any) -> str:
                        target = (
                            getattr(item, "object", None)
                            or getattr(item, "collection", None)
                            or item
                        )
                        return str(
                            getattr(target, "name_full", getattr(target, "name", ""))
                        )

                    value = sorted(value, key=order)
                if isinstance(owner, bpy.types.NodeTree) and key == "nodes":
                    value = sorted(value, key=lambda node: node.name)
                if isinstance(owner, bpy.types.NodeTree) and key == "links":
                    value = sorted(
                        value,
                        key=lambda link: (
                            link.from_node.name,
                            link.from_socket.identifier,
                            link.to_node.name,
                            link.to_socket.identifier,
                            link.multi_input_sort_id,
                        ),
                    )
                self.feed(key.encode())
                self.value(value, depth + 1)
            if isinstance(owner, bpy.types.Mesh):
                self.mesh(owner)
            try:
                custom_keys = sorted(owner.keys())
            except TypeError:
                custom_keys = []  # This RNA type cannot own ID properties.
            if custom_keys:
                for key in custom_keys:
                    if key in {
                        "_tyvrana_document_id",
                        "_tyvrana_resource_id",
                        "_RNA_UI",
                    }:
                        continue
                    self.feed(str(key).encode())
                    self.value(owner[key], depth + 1)
        finally:
            self.seen.remove(pointer)


def inspect_steps(
    *, max_seconds: float = MAX_SECONDS
) -> Generator[dict[str, Any], None, DocumentAttestationResult]:
    start = time.monotonic()
    session = identity()
    digest = None
    omissions: list[str] = []
    resources = 0
    resource_evidence: list[dict[str, Any]] = []
    h = Hasher()
    h.max_seconds = max_seconds
    try:
        configured = os.environ.get("OCIO", "")
        h.value(("color_configuration", configured, bytes(bpy.app.build_hash)))
        color_root = Path(
            bpy.utils.system_resource("DATAFILES", path="colormanagement")
        )
        if (
            configured
            and not configured.startswith("ocio://")
            and Path(configured).resolve() != (color_root / "config.ocio").resolve()
        ):
            raise Unqualified("External color-management configuration: " + configured)
        if not configured.startswith("ocio://"):
            for color_file in sorted(color_root.rglob("*")):
                if color_file.is_file():
                    h.value(color_file.relative_to(color_root).as_posix())
                    h.file(color_file)
        if any(
            scene.render.engine not in {"BLENDER_EEVEE", "CYCLES"}
            or scene.render.use_freestyle
            for scene in bpy.data.scenes
        ):
            raise Unqualified("Unsupported render engine or scripted line rendering")
        if any(scene.rigidbody_world is not None for scene in bpy.data.scenes):
            raise Unqualified("Rigid-body cache requires independent attestation")
        h.configuration = h.digest.hexdigest()
        for prop in sorted(bpy.data.bl_rna.properties, key=lambda p: p.identifier):
            kind = prop.identifier
            if prop.type != "COLLECTION" or kind in ROOT_SKIP:
                continue
            entries = getattr(bpy.data, kind)
            if (kind in UNSUPPORTED or kind not in SUPPORTED) and any(
                x.users or x.use_fake_user for x in entries
            ):
                raise Unqualified("Unsupported document category: " + kind)
            h.feed(kind.encode())
            for item in sorted(entries, key=lambda x: x.name_full):
                if item.users == 0 and not item.use_fake_user:
                    continue
                if isinstance(item, bpy.types.Image) and item.type in {
                    "RENDER_RESULT",
                    "COMPOSITING",
                }:
                    # Output buffers; material references fail in value().
                    continue
                with h.resource(kind, item.name_full, item):
                    resources += 1
                    if resources > 4096:
                        h.limit("resources")
                    if item.library or item.override_library:
                        raise Unqualified("Linked/override resource: " + item.name_full)
                    if (
                        item.bl_rna.identifier == "VectorFont"
                        and item.filepath != "<builtin>"
                    ):
                        if item.packed_file:
                            h.buffer(item.packed_file.size)
                            h.content(item.packed_file.data)
                        else:
                            font_path = Path(bpy.path.abspath(item.filepath))
                            if (
                                not font_path.is_file()
                                or font_path.stat().st_size > MAX_BYTES
                            ):
                                raise Unqualified("Missing/oversized external font")
                            h.file(font_path)
                    if isinstance(item, bpy.types.Text):
                        if "_tyvrana_document_id" in item:
                            continue
                        h.value(item.name_full)
                        h.value(item.as_string())
                        continue
                    if isinstance(item, bpy.types.Image):
                        if item.type in {"RENDER_RESULT", "COMPOSITING"}:
                            raise Unqualified(
                                "Referenced transient render/compositor image"
                            )
                        if item.source not in {"FILE", "GENERATED"}:
                            raise Unqualified(
                                "Unsupported image source: " + item.source
                            )
                        if item.is_dirty and item.source == "FILE":
                            raise Unqualified(
                                "Unsaved image pixels require saved/packed evidence"
                            )
                        if item.packed_file:
                            h.buffer(item.packed_file.size)
                            h.content(item.packed_file.data)
                        elif item.source == "FILE":
                            path = Path(bpy.path.abspath(item.filepath))
                            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                                raise Unqualified(
                                    "Missing/oversized external image: " + item.name
                                )
                            h.file(path)
                        h.native_array(item.pixels, len(item.pixels), "f")
                    if isinstance(item, bpy.types.Object) and any(
                        m.type
                        in {
                            "FLUID",
                            "CLOTH",
                            "SOFT_BODY",
                            "PARTICLE_SYSTEM",
                            "MESH_SEQUENCE_CACHE",
                        }
                        for m in item.modifiers
                    ):
                        raise Unqualified(
                            "Simulation/cache state requires independent attestation"
                        )
                    if (
                        isinstance(item, bpy.types.Object)
                        and item.mode != "OBJECT"
                        and not (item.type == "ARMATURE" and item.mode == "POSE")
                    ):
                        raise Unqualified("Attestation requires object mode")
                    h.rna(item)
                yield {"progress": h.diagnostics()}
        resource_evidence = h.resource_evidence()
        digest = h.digest.hexdigest()
    except (Unqualified, RecursionError, ValueError, TypeError, OSError) as exc:
        omissions = [str(exc)[:256] or type(exc).__name__]
    saved = None
    if bpy.data.filepath:
        path = Path(bpy.data.filepath)
        if path.is_file() and path.stat().st_size <= MAX_BYTES:
            with path.open("rb") as stream:
                saved = hashlib.file_digest(stream, "sha256").hexdigest()
    return DocumentAttestationResult(
        {
            "host_session_id": session["host"],
            "document_session_id": session["document"],
            "project_id": project_id(),
            "algorithm": "sha256",
            "format": FORMAT,
            "digest": digest,
            "status": "complete"
            if digest
            else (
                "limit_exceeded" if omissions[0].startswith("limit:") else "unsupported"
            ),
            "resource_count": resources,
            "omissions": [str(x) for x in omissions],
            "elapsed_ms": (time.monotonic() - start) * 1000,
            "file_sha256": saved,
            "work": h.diagnostics(),
            "resources": cast(JsonValue, resource_evidence) if digest else [],
            "resource_scope": RESOURCE_SCOPE if digest else None,
        }
    )


def inspect() -> DocumentAttestationResult:
    steps = inspect_steps()
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return cast(DocumentAttestationResult, done.value)
