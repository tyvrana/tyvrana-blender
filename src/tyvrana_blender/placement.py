"""Deterministic batched landmark placement, fitting and reflected transforms."""

import json
import math
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Quaternion, Vector  # type: ignore[import-not-found]
from pydantic import TypeAdapter

from . import organization, references
from .errors import OperationError
from .placement_models import (
    PlacementArguments,
    PlacementEdit,
    PlacementResult,
    PlacementRule,
    PlacementSummary,
)

KEY = "tyvrana_placement"


def same_matrix(a: Any, b: Any) -> bool:
    return bool(max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)) < 1e-6)


def fail(message: str) -> None:
    raise OperationError("placement_invalid", message)


def bounds(obj: Any) -> tuple[Any, Any]:
    if obj.type != "MESH" or not obj.data.vertices:
        fail("Placement/fitting requires a nonempty native mesh")
    points = [v.co for v in obj.data.vertices]
    return (
        Vector([min(p[k] for p in points) for k in range(3)]),
        Vector([max(p[k] for p in points) for k in range(3)]),
    )


def dependencies(rule: PlacementRule) -> set[str]:
    if rule.kind == "mirror":
        return {rule.source}
    points = (
        [rule.start, rule.end]
        if rule.kind == "between"
        else [rule.target]
        if rule.kind == "frame"
        else []
    )
    return {
        p.name if p.kind == "landmark" else p.object
        for p in points
        if p.kind in {"landmark", "object", "bone", "geometry"}
    }


def point(source: Any, matrices: dict[str, Any]) -> Any:
    if source.kind == "geometry" and source.object in matrices:
        from .geometry_points import local

        return matrices[source.object] @ local(source)
    if source.kind == "object" and source.object in matrices:
        return matrices[source.object] @ Vector(source.point)
    if source.kind == "landmark":
        obj = references.owned(source.name, references.LANDMARK)
        if not references.landmark_summary(obj).valid:
            fail("Placement requires current landmarks; rederive stale inputs")
        if obj.parent and obj.parent.name in matrices:
            return matrices[obj.parent.name] @ obj.matrix_local.translation
    return references.resolve(source)


def frame(direction: Any, up: Any) -> Any:
    if direction.length < 1e-8:
        fail("Placement endpoints/direction must be distinct")
    y = direction.normalized()
    x = y.cross(up)
    if x.length < 1e-8:
        fail("Placement up vector is parallel to its axis; supply an independent up")
    x.normalize()
    return Matrix((x, y, x.cross(y))).transposed()


def solve(obj: Any, rule: PlacementRule, matrices: dict[str, Any] | None = None) -> Any:
    matrices = matrices or {}
    if rule.kind == "mirror":
        source = organization.object_named(rule.source)
        normal = Vector(rule.plane_normal).normalized()
        reflection = Matrix.Identity(4)
        for i in range(3):
            for j in range(3):
                reflection[i][j] -= 2 * normal[i] * normal[j]
        reflection.translation = 2 * normal.dot(Vector(rule.plane_point)) * normal
        return reflection @ matrices.get(source.name, source.matrix_world)
    low, high = bounds(obj)
    size = high - low
    world = matrices.get(obj.name, obj.matrix_world).copy()
    location, rotation, scale = world.decompose()
    reconstructed = Matrix.LocRotScale(location, rotation, scale)
    if (
        max(abs(world[i][j] - reconstructed[i][j]) for i in range(4) for j in range(4))
        > 1e-5
    ):
        fail("Placement does not support sheared transforms")
    if rule.dimensions:
        for k, dimension in enumerate(rule.dimensions):
            if dimension is not None:
                if size[k] < 1e-8:
                    fail("Cannot fit a collapsed dimension")
                scale[k] = dimension / size[k] * (-1 if scale[k] < 0 else 1)
    if rule.kind == "fit":
        return Matrix.LocRotScale(location, rotation, scale)
    axis = "XYZ".index(rule.axis)
    if rule.kind == "between":
        target = point(rule.start, matrices)
        direction = point(rule.end, matrices) - target
        anchor = (low + high) / 2
        anchor[axis] = low[axis]
        if rule.fit_length:
            if size[axis] < 1e-8:
                fail("Cannot fit a collapsed longitudinal dimension")
            if rule.dimensions and rule.dimensions[axis] is not None:
                fail("Between placement derives length; omit its explicit dimension")
            scale[axis] = direction.length / size[axis]
    else:
        target = point(rule.target, matrices)
        direction = Vector(rule.direction)
        if isinstance(rule.local_anchor, list):
            anchor = Vector(rule.local_anchor)
        else:
            from .geometry_points import local

            if rule.local_anchor.object != obj.name:
                fail("The local geometry anchor must refer to the placed component")
            anchor = local(rule.local_anchor)
    local_direction = Vector([1.0 if k == axis else 0.0 for k in range(3)])
    local_up = Vector([0, 1, 0] if axis == 2 else [0, 0, 1])
    orient = (
        frame(direction, Vector(rule.up))
        @ frame(local_direction, local_up).transposed()
    )
    orient = Quaternion(direction.normalized(), rule.roll).to_matrix() @ orient
    result = orient.to_4x4() @ Matrix.Diagonal((*scale, 1.0))
    result.translation = target - result.to_3x3() @ anchor
    if any(not math.isfinite(v) or abs(v) > 1e8 for row in result for v in row):
        fail("Placement exceeds supported finite scene scale")
    return result


def summary(obj: Any) -> PlacementSummary:
    meta = json.loads(obj[KEY])
    rule: PlacementRule = TypeAdapter(PlacementRule).validate_python(meta["rule"])
    try:
        intended = solve(obj, rule)
        valid = (
            max(
                abs(intended[i][j] - obj.matrix_world[i][j])
                for i in range(4)
                for j in range(4)
            )
            < 1e-5
        )
    except OperationError:
        valid = False
    low, high = bounds(obj)
    scale = obj.matrix_world.to_scale()
    return PlacementSummary(
        name=obj.name,
        revision=meta["revision"],
        rule=rule,
        dimensions=[float((high[k] - low[k]) * abs(scale[k])) for k in range(3)],
        world_matrix=[list(row) for row in obj.matrix_world],
        valid=valid,
    )


def place(args: PlacementArguments) -> PlacementResult:
    organization.idle(mutate=True)
    edits = list(args.placements)
    for name in args.refresh:
        obj = organization.object_named(name)
        if KEY not in obj:
            fail(f"No stored placement rule: {name}")
        edits.append(PlacementEdit(name=name, rule=json.loads(obj[KEY])["rule"]))
    by_name = {edit.name: edit for edit in edits}
    graph = {}
    for name, edit in by_name.items():
        related = dependencies(edit.rule)
        for dep in list(related):
            obj = bpy.data.objects.get(dep)
            while obj is not None and obj.parent:
                related.add(obj.parent.name)
                obj = obj.parent
        if name in related:
            fail("Placement cannot depend on its own component/frame")
        graph[name] = related & by_name.keys()
    order = organization.ordered(graph)
    planned: dict[str, Any] = {}
    objects = {}
    for name in order:
        obj = organization.object_named(name)
        organization.object_editable(obj)
        organization.simple_transform(obj)
        # A parent's placement would change unlisted descendants. Require explicit
        # child placement and derive parent-local transforms only at commit.
        if any(
            c.name not in by_name and not c.get(references.LANDMARK)
            for c in obj.children
        ):
            fail("Include every non-landmark child when placing a hierarchy")
        if obj.parent and obj.parent.name in by_name:
            fail("Place parent frames separately from constrained mesh children")
        planned[name] = solve(obj, by_name[name].rule, planned)
        objects[name] = obj
    backups = [(o, o.matrix_world.copy(), o.get(KEY)) for o in objects.values()]
    try:
        for name in order:
            obj = objects[name]
            previous = json.loads(obj[KEY]) if KEY in obj else None
            rule = by_name[name].rule.model_dump(mode="json")
            unchanged = (
                previous is not None
                and previous["rule"] == rule
                and same_matrix(obj.matrix_world, planned[name])
            )
            obj.matrix_world = planned[name]
            if not unchanged:
                obj[KEY] = json.dumps(
                    {
                        "revision": previous["revision"] + 1 if previous else 1,
                        "rule": rule,
                    }
                )
        bpy.context.view_layer.update()
        return PlacementResult(placements=[summary(objects[e.name]) for e in edits])
    except BaseException:
        for obj, transform, raw in backups:
            obj.matrix_world = transform
            if raw is None:
                if KEY in obj:
                    del obj[KEY]
            else:
                obj[KEY] = raw
        bpy.context.view_layer.update()
        raise
