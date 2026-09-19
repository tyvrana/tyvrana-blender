"""Shared prototypes and lazy world-space geometry for native attached elements."""

import bisect
from dataclasses import dataclass, field
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from . import growth
from .errors import OperationError
from .growth_models import GrowthCreateArguments


def fail(message: str) -> Any:
    raise OperationError("geometry_instances_invalid", message)


@dataclass
class Prototype:
    name: str
    points: list[Any]
    triangles: list[tuple[int, int, int]]
    faces: list[int]

    @classmethod
    def mesh(cls, name: str, data: Any) -> "Prototype":
        data.calc_loop_triangles()
        if not data.loop_triangles or len(data.vertices) > 250000:
            fail("Instance prototype requires a bounded polygon surface")
        return cls(
            name,
            [v.co.copy() for v in data.vertices],
            [tuple(t.vertices) for t in data.loop_triangles],
            [t.polygon_index for t in data.loop_triangles],
        )


@dataclass
class Element:
    identity: str
    prototype: Prototype
    matrix: Any
    root_id: int | None = None
    family_index: int = 0
    layer: int = 0
    order: int = 0
    overlap: bool = False
    path: list[Any] = field(default_factory=list)
    normals: list[Any] = field(default_factory=list)
    tangents: list[Any] = field(default_factory=list)
    lengths: list[float] = field(default_factory=list)
    width: float = 1
    thickness: float = 1
    offsets: list[Any] = field(default_factory=list)
    _points: list[Any] | None = None

    def point(self, local: Any) -> Any:
        if not self.path:
            return self.matrix @ local
        distance = min(1, max(0, local.z)) * self.lengths[-1]
        index = min(
            len(self.path) - 2, max(0, bisect.bisect_right(self.lengths, distance) - 1)
        )
        span = self.lengths[index + 1] - self.lengths[index]
        t = (distance - self.lengths[index]) / span if span > 1e-12 else 0
        normal = self.normals[index].lerp(self.normals[index + 1], t).normalized()
        tangent = self.tangents[index].lerp(self.tangents[index + 1], t).normalized()
        position = self.path[index].lerp(self.path[index + 1], t)
        return self.matrix @ (
            position
            + normal * (local.x * self.width)
            + tangent.cross(normal) * (local.y * self.thickness)
        )

    def points(self) -> list[Any]:
        if self._points is None:
            self._points = [self.point(p) for p in self.prototype.points]
            if self.offsets:
                self._points = [
                    p + delta
                    for p, delta in zip(self._points, self.offsets, strict=True)
                ]
        return self._points

    def bounds(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        if not self.path:
            lo = [min(p[k] for p in self.prototype.points) for k in range(3)]
            hi = [max(p[k] for p in self.prototype.points) for k in range(3)]
            points = [
                self.matrix @ Vector((x, y, z))
                for x in (lo[0], hi[0])
                for y in (lo[1], hi[1])
                for z in (lo[2], hi[2])
            ]
            radius = [0.0] * 3
        else:
            points = [self.matrix @ p for p in self.path]
            local_radius = max(
                abs(p.x) * self.width + abs(p.y) * self.thickness
                for p in self.prototype.points
            )
            radius = [local_radius * self.matrix.to_3x3()[k].length for k in range(3)]
        if self.offsets:
            radius = [
                radius[k] + max(abs(p[k]) for p in self.offsets) for k in range(3)
            ]
        return (
            tuple(min(p[k] for p in points) - radius[k] for k in range(3)),
            tuple(max(p[k] for p in points) + radius[k] for k in range(3)),
        )


@dataclass
class Elements:
    object_name: str
    elements: list[Element]
    prototypes: list[Prototype]
    evaluated_path_points: int = 0

    @property
    def equivalent_vertices(self) -> int:
        return sum(len(e.prototype.points) for e in self.elements)


def collect_growth(
    name: str,
    maximum: int = 50000,
    *,
    templates_only: bool = False,
    apply_correction: bool = True,
) -> Elements:
    obj, meta, _, group = growth.owned(name)
    warnings = growth.validity(obj, meta)
    if warnings:
        fail(warnings[0])
    growth.evaluation_dependencies(obj)
    spec = GrowthCreateArguments.model_validate(meta["spec"])
    prototypes = {
        i: Prototype.mesh(
            f.template.object_name, bpy.data.objects[f.template.object_name].data
        )
        for i, f in enumerate(spec.families)
        if f.template
    }
    if not prototypes:
        if templates_only:
            return Elements(name, [], [])
        fail("Instance geometry QA requires mesh templates, not bare curves")
    result = Elements(name, [], list(prototypes.values()))
    with growth.evaluated_path(obj, group, evaluated_points=True) as data:
        if len(data.vertices) > 800000:
            fail("Native evaluated paths exceed800000 points")
        groups: dict[int, list[int]] = {}
        for i, attr in enumerate(data.attributes["growth_root_id"].data):
            groups.setdefault(attr.value, []).append(i)
        if len(groups) > maximum:
            fail("Element count exceeds max_instances")
        result.evaluated_path_points = len(data.vertices)
        for indices in groups.values():
            ci = indices[0]

            def value(key: str, index: int = ci) -> Any:
                return data.attributes["growth_" + key].data[index].value

            fi = value("family")
            if fi not in prototypes:
                if templates_only:
                    continue
                fail("Mixed curve/template systems require separate geometry queries")
            family = spec.families[fi]
            template = family.template
            assert template is not None
            points = [data.vertices[i].co.copy() for i in indices]
            normals = [
                data.attributes["growth_frame_normal"].data[i].vector.copy()
                for i in indices
            ]
            tangents = [
                data.attributes["growth_frame_tangent"].data[i].vector.copy()
                for i in indices
            ]
            lengths = [0.0]
            for a, b in zip(points[:-1], points[1:], strict=True):
                lengths.append(lengths[-1] + (b - a).length)
            root = value("root_id")
            element = Element(
                f"root:{root}",
                prototypes[fi],
                obj.matrix_world.copy(),
                root_id=root,
                family_index=fi,
                layer=value("layer"),
                order=value("order"),
                overlap=bool(value("overlap")),
            )
            if template.mode == "instances":
                z = tangents[0].normalized()
                x = (normals[0] - z * normals[0].dot(z)).normalized()
                y = z.cross(x)
                transform = (
                    Matrix(
                        (
                            x * template.width,
                            y * template.thickness,
                            z * value("evaluated_length"),
                        )
                    )
                    .transposed()
                    .to_4x4()
                )
                transform.translation = points[0]
                element.matrix = obj.matrix_world @ transform
            else:
                element.path = points
                element.normals = normals
                element.tangents = tangents
                element.lengths = lengths
                element.width = template.width
                element.thickness = template.thickness
            result.elements.append(element)
    if apply_correction:
        from . import growth_layers

        growth_layers.apply_offsets(obj, group, result)
    return result


def collect(name: str, maximum: int = 50000) -> Elements:
    obj = bpy.context.scene.objects.get(name)
    if obj is None:
        fail("Instance source is absent from the current scene")
    if growth.KEY in obj:
        return collect_growth(name, maximum)
    prototypes: dict[int, Prototype] = {}
    result = Elements(name, [], [])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for instance in depsgraph.object_instances:
        if (
            not instance.is_instance
            or not instance.parent
            or instance.parent.original != obj
        ):
            continue
        evaluated = instance.object
        if evaluated.type != "MESH":
            fail("Native instance QA requires mesh prototypes")
        key = evaluated.data.as_pointer()
        if key not in prototypes:
            prototypes[key] = Prototype.mesh(evaluated.original.name, evaluated.data)
            if sum(len(p.points) for p in prototypes.values()) > 250000:
                fail("Shared prototype vertex budget exceeded")
        identity = "native:" + ".".join(str(i) for i in instance.persistent_id)
        result.elements.append(
            Element(identity, prototypes[key], instance.matrix_world.copy())
        )
        if len(result.elements) > maximum:
            fail("Element count exceeds max_instances")
    if not result.elements:
        fail("Object has no mesh instances; use objects/pairs for realized surfaces")
    if len({e.identity for e in result.elements}) != len(result.elements):
        fail("Native instance identities are not unique under this source")
    result.prototypes = list(prototypes.values())
    return result
