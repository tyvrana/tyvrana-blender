"""Bounded evaluated surfaces shared by volume and sampled layer diagnostics."""

import math
from dataclasses import dataclass
from typing import Any, Never

import bmesh  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from . import (
    curves,
    deformation_geometry,
    deformation_qa,
    mesh,
    modifiers,
    organization,
    retopo_geometry,
)
from .curve_models import CurveInspectArguments
from .errors import OperationError
from .mesh_models import MeshElementSelector
from .mesh_selectors import SelectionError, select
from .volume_models import VolumeMetrics

MAX_VERTICES = 250_000
MAX_TRIANGLES = 500_000
MAX_WORK = 1_000_000


def fail(message: str) -> Never:
    raise OperationError("layer_geometry_invalid", message)


def authored_signature(obj: Any) -> str:
    if obj.type == "MESH":
        return deformation_geometry.topology(obj.data)
    return curves.digest(
        [[s.type, len(curves.points(s)), s.use_cyclic_u] for s in obj.data.splines]
    )


def metrics(points: Any, triangles: Any) -> VolumeMetrics:
    volume = deformation_qa.volume(points, triangles)
    area = 0.0
    center = Vector((0, 0, 0))
    for i, j, k in triangles:
        weight = (points[j] - points[i]).cross(points[k] - points[i]).length / 2
        area += weight
        center += (points[i] + points[j] + points[k]) * (weight / 3)
    if area <= 1e-16:
        fail("Surface has no nondegenerate area")
    center /= area
    return VolumeMetrics(
        vertex_count=len(points),
        triangle_count=len(triangles),
        closed_consistent=volume is not None,
        volume=volume,
        surface_area=area,
        bounds_min=[min(p[i] for p in points) for i in range(3)],
        bounds_max=[max(p[i] for p in points) for i in range(3)],
        surface_centroid=list(center),
    )


@dataclass
class Surface:
    obj: Any
    points: list[Any]
    triangles: list[tuple[int, int, int]]
    topology: str
    authored: str
    bm: Any
    curve: Any
    triangle_faces: list[int]
    tree: Any = None
    normals: Any = None

    def bvh(self) -> Any:
        if self.tree is None:
            self.tree = BVHTree.FromPolygons(
                self.points, self.triangles, all_triangles=True
            )
        return self.tree

    def selected(self, selector: MeshElementSelector) -> list[int]:
        try:
            chosen = list(select(self.bm, selector, self.obj))
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        values = (
            {v.index for v in chosen}
            if selector.domain == "vertex"
            else {v.index for e in chosen for v in e.verts}
        )
        if not values:
            fail("Layer region selects no evaluated vertices")
        return sorted(values)

    def vertex_normals(self) -> Any:
        if self.normals is None:
            self.normals = [Vector((0, 0, 0)) for _ in self.points]
            for i, j, k in self.triangles:
                normal = (self.points[j] - self.points[i]).cross(
                    self.points[k] - self.points[i]
                )
                for index in (i, j, k):
                    self.normals[index] += normal
            for normal in self.normals:
                normal.normalize()
        return self.normals


class SurfaceCache:
    def __init__(self, section_samples: int = 0, max_work: int | None = None) -> None:
        self.surfaces: dict[str, Surface] = {}
        self.vertices = 0
        self.max_work = MAX_WORK if max_work is None else min(MAX_WORK, max_work)
        self.section_samples = section_samples
        self.sections = 0

    def __enter__(self) -> "SurfaceCache":
        organization.idle()
        return self

    def __exit__(self, *_: Any) -> None:
        for surface in self.surfaces.values():
            surface.bm.free()
        self.surfaces.clear()

    def get(self, name: str) -> Surface:
        if name in self.surfaces:
            return self.surfaces[name]
        obj = organization.object_named(name)
        if obj.type not in {"MESH", "CURVE"}:
            fail("Volume/layer diagnostics require Mesh or managed profiled Curve")
        curve = None
        if obj.type == "CURVE":
            curves.object_curve(name)
            meta, _ = curves.metadata(obj)
            if meta["settings"]["profile"]["kind"] == "none":
                fail(
                    "A curve surface requires a sweep profile; configure a capped "
                    "profile for closed volume"
                )
            sections = len(obj.data.splines) * self.section_samples
            if self.sections + sections > 256:
                fail(
                    "Volume section output exceeds 256 samples; reduce section_samples"
                )
            self.sections += sections
            curve = curves.summary(
                obj, CurveInspectArguments(samples=self.section_samples)
            )
            if not curve.valid:
                fail(
                    "Curve attachment/profile is invalid: "
                    + "; ".join(curve.issues[:2])
                )
        graph = retopo_geometry.graph(obj)
        evaluated = obj.evaluated_get(graph)
        bm = None
        try:
            data = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
            if data is None or not data.polygons:
                fail("Evaluated object has no polygon surface")
            mesh.check_budget(modifiers.data_size(data))
            data.calc_loop_triangles()
            count = len(data.vertices)
            if (
                count > MAX_VERTICES
                or len(data.loop_triangles) > MAX_TRIANGLES
                or self.vertices + count > self.max_work
            ):
                fail(
                    "Surface budget: 250000 vertices/500000 triangles per object, "
                    "1000000 evaluated vertices per query, or remaining sweep budget"
                )
            transform = retopo_geometry.matrix(evaluated)
            points = [
                retopo_geometry.checked_point(transform @ v.co) for v in data.vertices
            ]
            triangles = [tuple(t.vertices) for t in data.loop_triangles]
            if any(not math.isfinite(c) for p in points for c in p):
                fail("Evaluated surface contains nonfinite coordinates")
            bm = bmesh.new()
            bm.from_mesh(data)
            mesh.refresh(bm)
            result = Surface(
                obj,
                points,
                triangles,
                deformation_geometry.topology(data),
                authored_signature(obj),
                bm,
                curve,
                [t.polygon_index for t in data.loop_triangles],
            )
            self.surfaces[name] = result
            self.vertices += count
            return result
        except BaseException:
            if bm is not None:
                bm.free()
            raise
        finally:
            evaluated.to_mesh_clear()
