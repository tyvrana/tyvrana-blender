"""Bounded points estimated from authored geometry and semantic regions."""

import hashlib
import json
import math
from typing import Any

from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from . import loft, references, surfaces
from .errors import OperationError
from .reference_models import GeometryPoint


def fail(message: str) -> None:
    raise OperationError("geometry_point_invalid", message)


def basis(obj: Any) -> str:
    if obj.type != "MESH" or len(obj.data.vertices) > 131072:
        fail("Geometry points require an authored mesh with at most131072 vertices")
    return hashlib.sha256(
        json.dumps(
            [
                surfaces.content_hash(obj.data)
                if surfaces.KEY in obj
                else loft.signature(obj.data),
                [list(row) for row in obj.matrix_world],
            ]
        ).encode()
    ).hexdigest()


def region_vertices(obj: Any, region: str | None) -> set[int]:
    if region is None:
        return set(range(len(obj.data.vertices)))
    if surfaces.KEY in obj:
        meta = surfaces.metadata(obj)
        if surfaces.content_hash(obj.data) != meta["signature"]:
            fail("Surface construction regions are stale")
        if region not in meta["regions"]:
            fail(f"Unknown surface region: {region}")
        record = meta["regions"][region]
        if record["kind"] == "patch":
            index = next(
                i + 1
                for i, p in enumerate(meta["spec"]["patches"])
                if p["id"] == region
            )
            attribute = obj.data.attributes[surfaces.PATCH_ATTRIBUTE]
        else:
            index = record["index"]
            attribute = obj.data.attributes[surfaces.REGION_ATTRIBUTE]
        return {
            v
            for face in obj.data.polygons
            if attribute.data[face.index].value == index
            for v in face.vertices
        }
    if loft.KEY in obj:
        meta = loft.metadata(obj)
        spec = meta["spec"]
        if region in {"start_cap", "end_cap"} and spec["caps"]:
            count = ((len(spec["sections"]) - 1) * spec["subdivisions"] + 1) * spec[
                "sides"
            ]
            base = 0 if region == "start_cap" else count - spec["sides"]
            ids = set(range(base, base + spec["sides"]))
            if spec["ends"]:
                extra = spec["ends"]["rings"] * spec["sides"]
                start = count + (0 if region == "start_cap" else extra)
                ids.update(range(start, start + extra))
            return ids
        for i, section in enumerate(spec["sections"]):
            if section["id"] == region:
                base = i * spec["subdivisions"] * spec["sides"]
                return set(range(base, base + spec["sides"]))
        for feature in spec["features"]:
            if feature["id"] != region:
                continue
            rings = (len(spec["sections"]) - 1) * spec["subdivisions"] + 1
            return {
                ring * spec["sides"] + side
                for ring in range(rings)
                for side in range(spec["sides"])
                if abs(ring / (rings - 1) - feature["position"])
                < feature["axial_width"]
                and abs(
                    (2 * math.pi * side / spec["sides"] - feature["angle"] + math.pi)
                    % (2 * math.pi)
                    - math.pi
                )
                < feature["angular_width"]
            }
        fail(f"Unknown loft section/feature: {region}")
    fail("Named regions require managed surface or loft construction")
    raise AssertionError("unreachable")


def local(source: GeometryPoint) -> Any:
    obj = references.object_named(source.object)
    basis(obj)
    ids = region_vertices(obj, source.region)
    if not ids:
        fail("Geometry region has no vertices")
    points = [obj.data.vertices[i].co for i in ids]
    low = Vector([min(p[k] for p in points) for k in range(3)])
    high = Vector([max(p[k] for p in points) for k in range(3)])
    point = Vector([low[k] + (high[k] - low[k]) * source.position[k] for k in range(3)])
    if source.project:
        obj.data.calc_loop_triangles()
        triangles = [
            tuple(t.vertices)
            for t in obj.data.loop_triangles
            if any(i in ids for i in t.vertices)
        ]
        if not triangles or len(triangles) > 262144:
            fail("Projection requires1..262144 authored triangles")
        tree = BVHTree.FromPolygons(
            [v.co for v in obj.data.vertices], triangles, all_triangles=True
        )
        point, normal, _, _ = tree.find_nearest(point)
        if point is None:
            fail("No nearby authored surface for this region")
        point += normal * source.offset
    return point


def resolve(source: GeometryPoint) -> Any:
    obj = references.object_named(source.object)
    return references.matrix(obj) @ local(source)
