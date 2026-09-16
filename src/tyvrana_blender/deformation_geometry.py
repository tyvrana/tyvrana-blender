"Bounded geometry mechanics shared by corrections and deformation transfer."

import hashlib
import math
import struct
from typing import Any, Never

from . import mesh, modifiers, retopo_geometry
from .mesh_models import MeshElementSelector
from .mesh_selectors import SelectionError, select
from .operations import OperationError

MAX_VERTICES = 100_000
MAX_KEY_COORDINATES = 2_000_000


def fail(message: str) -> Never:
    raise OperationError("deformation_invalid", message)


def native_name(name: str) -> None:
    if not name or "\x00" in name or len(name.encode("utf-8")) > 63:
        fail("Native names must be nonempty and fit 63 UTF-8 bytes")


def topology(data: Any) -> str:
    digest = hashlib.sha256()
    digest.update(
        struct.pack("<III", len(data.vertices), len(data.edges), len(data.polygons))
    )
    for edge in data.edges:
        digest.update(struct.pack("<2I", *edge.vertices))
    for face in data.polygons:
        digest.update(struct.pack("<I", len(face.vertices)))
        digest.update(struct.pack("<" + "I" * len(face.vertices), *face.vertices))
    return digest.hexdigest()


def coordinates(points: Any) -> str:
    digest = hashlib.sha256()
    for p in points:
        digest.update(struct.pack("<3f", *p))
    return digest.hexdigest()


def context(name: str, *, edit: bool = False) -> Any:
    obj = modifiers.object_mesh(name)
    if not 0 < len(obj.data.vertices) <= MAX_VERTICES:
        fail("Deformation authoring requires 1..100000 authored vertices")
    mesh.check_budget(modifiers.data_size(obj.data))
    if edit:
        modifiers.mutable(obj)
        if obj.data.library or obj.data.override_library or not obj.data.is_editable:
            fail("Use a local editable mesh datablock")
        if obj.data.users != 1:
            fail("Make an independent mesh copy before editing deformation data")
        if obj.data.animation_data or (
            obj.data.shape_keys and obj.data.shape_keys.animation_data
        ):
            fail("Preserve animated/driven shape data; use an animation-aware workflow")
        if obj.show_only_shape_key:
            fail("Disable pinned shape-key display before deformation authoring")
    return obj


def selection(obj: Any, selector: MeshElementSelector) -> set[int]:
    with mesh.snapshot(obj) as bm:
        try:
            result = select(bm, selector, obj)
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        if selector.domain == "vertex":
            chosen = {v.index for v in result}
        else:
            chosen = {v.index for element in result for v in element.verts}
    if not chosen:
        fail("Deformation region is empty; revise its selection")
    return chosen


def evaluated(obj: Any, *, authored: bool = True) -> tuple[list[Any], str]:
    graph = retopo_geometry.graph(obj)
    with retopo_geometry.evaluated_mesh(obj, graph) as (data, _):
        if len(data.vertices) > MAX_VERTICES:
            fail("Evaluated deformation exceeds 100000 vertices; reduce subdivision")
        signature = topology(data)
        if authored and signature != topology(obj.data):
            fail(
                "Evaluated ordered topology differs from authored mesh; disable "
                "constructive modifiers"
            )
        points = [v.co.copy() for v in data.vertices]
        if any(not math.isfinite(c) or abs(c) > 1e6 for p in points for c in p):
            fail("Evaluated coordinates must be finite and within 1000000")
        return points, signature
