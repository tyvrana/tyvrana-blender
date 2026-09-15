"""Compact local-space surface signatures on disposable native mesh snapshots."""

import hashlib
import math
import struct
from array import array
from typing import Any

from .modifier_models import MeshSurfaceBasis, TangentRepeatability
from .operations import OperationError


def float_bytes(values: Any) -> bytes:
    values = tuple(values)
    if not all(math.isfinite(v) for v in values):
        raise OperationError("invalid_context", "Surface basis contains nonfinite data")
    return struct.pack("<" + "f" * len(values), *values)


def inspect(data: Any, geometry_sha256: str, uv_map: str | None) -> MeshSurfaceBasis:
    flags = hashlib.sha256()
    flags.update(bytes(bool(f.use_smooth) for f in data.polygons))
    flags.update(bytes(bool(e.use_edge_sharp) for e in data.edges))
    flags.update(bytes(bool(e.use_seam) for e in data.edges))
    creased = []
    for name, count in [
        ("crease_edge", len(data.edges)),
        ("crease_vert", len(data.vertices)),
    ]:
        attribute = data.attributes.get(name)
        values = [v.value for v in attribute.data] if attribute else [0.0] * count
        flags.update(float_bytes(values))
        creased.append(sum(v != 0 for v in values))
    normals = hashlib.sha256()
    for normal in data.corner_normals:
        normals.update(float_bytes(normal.vector))
    uv_hash = tangent_hash = None
    negative = zero = None
    repeatability = None
    if uv_map is not None:
        layer = data.uv_layers.get(uv_map)
        if layer is None:
            raise OperationError(
                "uv_map_not_found", "Requested surface UV map does not exist"
            )
        uv = hashlib.sha256()
        for value in layer.uv:
            uv.update(float_bytes(value.vector))
        uv_hash = uv.hexdigest()
        if any(len(f.vertices) > 4 for f in data.polygons):
            raise OperationError(
                "invalid_context", "Tangent inspection requires triangles or quads"
            )
        data.calc_tangents(uvmap=uv_map)
        try:
            tangent = hashlib.sha256()
            original = array("f")
            negative = zero = 0
            for loop in data.loops:
                tangent_values = (*loop.tangent, loop.bitangent_sign)
                tangent.update(float_bytes(tangent_values))
                original.extend(tangent_values)
                negative += loop.bitangent_sign < 0
                zero += loop.tangent.length_squared < 1e-12
            tangent_hash = tangent.hexdigest()
        finally:
            data.free_tangents()
        # Native MikkTSpace uses parallel atomic float accumulation above 10,000
        # faces. Exact hashes can differ on identical inputs; measure the actual
        # component and handedness differences without rounding or hiding them.
        data.calc_tangents(uvmap=uv_map)
        try:
            repeated = hashlib.sha256()
            maximum_delta = 0.0
            changed = handedness_changes = 0
            for i, loop in enumerate(data.loops):
                tangent_values = (*loop.tangent, loop.bitangent_sign)
                repeated.update(float_bytes(tangent_values))
                previous = original[i * 4 : i * 4 + 4]
                delta = max(
                    abs(a - b)
                    for a, b in zip(tangent_values[:3], previous[:3], strict=True)
                )
                maximum_delta = max(maximum_delta, delta)
                changed += delta != 0 or tangent_values[3] != previous[3]
                handedness_changes += tangent_values[3] != previous[3]
            repeatability = TangentRepeatability(
                repeated_sha256=repeated.hexdigest(),
                maximum_component_delta=maximum_delta,
                changed_corner_count=changed,
                handedness_change_count=handedness_changes,
            )
        finally:
            data.free_tangents()
    return MeshSurfaceBasis(
        geometry_sha256=geometry_sha256,
        shading_flags_sha256=flags.hexdigest(),
        corner_normals_sha256=normals.hexdigest(),
        uv_map=uv_map,
        uv_sha256=uv_hash,
        tangents_sha256=tangent_hash,
        tangent_repeatability=repeatability,
        smooth_face_count=sum(f.use_smooth for f in data.polygons),
        sharp_edge_count=sum(e.use_edge_sharp for e in data.edges),
        seam_edge_count=sum(e.use_seam for e in data.edges),
        has_custom_normals=data.has_custom_normals,
        creased_edge_count=creased[0],
        creased_vertex_count=creased[1],
        negative_bitangent_count=negative,
        zero_tangent_count=zero,
    )
