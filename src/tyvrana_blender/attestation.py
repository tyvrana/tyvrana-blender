"""Bounded material RNA attestation; never writes document data or saves files."""

import array
import hashlib
import math
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy  # type: ignore[import-not-found]

from .attestation_model import DocumentAttestationResult
from .bindings import project_id

FORMAT = (
    "blender-rna-"
    + ".".join(map(str, bpy.app.version))
    + "-"
    + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
)
MAX_ITEMS = 4000000
MAX_BYTES = 512 * 1024 * 1024
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
        self.start = time.monotonic()
        self.seen: set[int] = set()

    def feed(self, value: bytes) -> None:
        self.items += 1
        self.bytes += len(value)
        if self.items > MAX_ITEMS or self.bytes > MAX_BYTES:
            raise Unqualified("limit: document exceeds attestation work budget")
        if time.monotonic() - self.start > MAX_SECONDS:
            raise Unqualified("limit: document attestation deadline exceeded")
        self.digest.update(struct.pack("!Q", len(value)))
        self.digest.update(value)

    def value(self, value: Any, depth: int = 0) -> None:
        if depth > 40:
            raise Unqualified("limit: RNA nesting exceeds 40")
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
            self.feed(b"bytes" + value)
        elif isinstance(value, bpy.types.ID):
            if isinstance(value, bpy.types.Image) and value.type in {
                "RENDER_RESULT",
                "COMPOSITING",
            }:
                raise Unqualified("Referenced transient render/compositor image")
            if value.is_embedded_data:
                self.rna(value, depth + 1)
                return
            self.feed(b"ID")
            self.value(value.bl_rna.identifier, depth + 1)
            self.value(value.name_full, depth + 1)
            if value.library:
                raise Unqualified(
                    "Linked library content requires independent attestation"
                )
        elif hasattr(value, "bl_rna") and hasattr(value, "as_pointer"):
            self.rna(value, depth + 1)
        elif hasattr(value, "to_dict"):
            self.value(value.to_dict(), depth + 1)
        elif isinstance(value, dict):
            self.feed(b"map")
            for key in sorted(value):
                self.value(str(key), depth + 1)
                self.value(value[key], depth + 1)
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

    def bulk(self, collection: Any, key: str, code: str, width: int = 1) -> None:
        count = len(collection) * width
        if count > MAX_BYTES // 8:
            raise Unqualified("limit: native array exceeds attestation budget")
        values = array.array(code, [0]) * count
        collection.foreach_get(key, values)
        if sys.byteorder != "big":
            values.byteswap()
        self.value((key, code, len(collection), width, values.tobytes()))

    def mesh(self, mesh: Any) -> None:
        self.bulk(mesh.vertices, "co", "f", 3)
        self.bulk(mesh.edges, "vertices", "i", 2)
        self.bulk(mesh.loops, "vertex_index", "i")
        self.bulk(mesh.loops, "edge_index", "i")
        for key in ["loop_start", "loop_total", "material_index", "use_smooth"]:
            self.bulk(mesh.polygons, key, "i")
        self.bulk(mesh.corner_normals, "vector", "f", 3)
        for vertex in mesh.vertices:
            if vertex.index % 1024 == 0 and time.monotonic() - self.start > MAX_SECONDS:
                raise Unqualified("limit: mesh weight scan deadline exceeded")
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
                    self.value([getattr(v, key) for v in attribute.data])
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
                if isinstance(owner, bpy.types.Mesh) and key in {
                    "vertices",
                    "edges",
                    "loops",
                    "polygons",
                    "loop_triangles",
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


def inspect() -> DocumentAttestationResult:
    start = time.monotonic()
    session = identity()
    digest = None
    omissions: list[str] = []
    resources = 0
    h = Hasher()
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
                    if color_file.stat().st_size > MAX_BYTES:
                        raise Unqualified("limit: color configuration exceeds budget")
                    h.value(
                        (
                            color_file.relative_to(color_root).as_posix(),
                            color_file.read_bytes(),
                        )
                    )
        if any(
            scene.render.engine not in {"BLENDER_EEVEE", "CYCLES"}
            or scene.render.use_freestyle
            for scene in bpy.data.scenes
        ):
            raise Unqualified("Unsupported render engine or scripted line rendering")
        if any(scene.rigidbody_world is not None for scene in bpy.data.scenes):
            raise Unqualified("Rigid-body cache requires independent attestation")
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
                resources += 1
                if resources > 4096:
                    raise Unqualified("limit: more than 4096 resources")
                if item.library or item.override_library:
                    raise Unqualified("Linked/override resource: " + item.name_full)
                if (
                    item.bl_rna.identifier == "VectorFont"
                    and item.filepath != "<builtin>"
                ):
                    if item.packed_file:
                        h.value(bytes(item.packed_file.data))
                    else:
                        font_path = Path(bpy.path.abspath(item.filepath))
                        if (
                            not font_path.is_file()
                            or font_path.stat().st_size > MAX_BYTES
                        ):
                            raise Unqualified("Missing/oversized external font")
                        h.value(font_path.read_bytes())
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
                        raise Unqualified("Unsupported image source: " + item.source)
                    if item.is_dirty and item.source == "FILE":
                        raise Unqualified(
                            "Unsaved image pixels require saved/packed evidence"
                        )
                    if item.packed_file:
                        h.value(bytes(item.packed_file.data))
                    elif item.source == "FILE":
                        path = Path(bpy.path.abspath(item.filepath))
                        if not path.is_file() or path.stat().st_size > MAX_BYTES:
                            raise Unqualified(
                                "Missing/oversized external image: " + item.name
                            )
                        h.value(path.read_bytes())
                    pixel_count = len(item.pixels)
                    if pixel_count > MAX_BYTES // 4:
                        raise Unqualified(
                            "limit: image pixels exceed attestation budget"
                        )
                    pixels = array.array("f", [0]) * pixel_count
                    item.pixels.foreach_get(pixels)
                    if sys.byteorder != "big":
                        pixels.byteswap()
                    h.value(pixels.tobytes())
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
                if isinstance(item, bpy.types.Object) and item.mode != "OBJECT":
                    raise Unqualified("Attestation requires object mode")
                h.rna(item)
        digest = h.digest.hexdigest()
    except (Unqualified, RecursionError, ValueError, TypeError) as exc:
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
        }
    )
