"""Deterministic section generation with native mesh ownership and rollback."""

import hashlib
import json
import math
import struct
import time
import uuid
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from pydantic import ValidationError

from . import organization
from .errors import OperationError, constraint_error
from .loft_models import (
    LoftBatch,
    LoftConfigureArguments,
    LoftCreateArguments,
    LoftInspectArguments,
    LoftResult,
    LoftRevision,
    LoftSpec,
    LoftSummary,
)

KEY = "tyvrana_loft"


def fail(message: str) -> None:
    raise OperationError("loft_invalid", message)


def geometry(spec: LoftSpec) -> tuple[list[list[float]], list[list[int]]]:
    centers = [Vector(s.center) for s in spec.sections]
    samples = []
    for i in range(len(centers) - 1):
        for j in range(spec.subdivisions):
            t = j / spec.subdivisions
            p0, p1, p2, p3 = (
                centers[max(0, i - 1)],
                centers[i],
                centers[i + 1],
                centers[min(len(centers) - 1, i + 2)],
            )
            if spec.interpolation == "linear":
                point = p1.lerp(p2, t)
            else:
                point = 0.5 * (
                    (2 * p1)
                    + (-p0 + p2) * t
                    + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                    + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t
                )
            a, b = spec.sections[i], spec.sections[i + 1]
            samples.append(
                (
                    point,
                    [
                        x * (1 - t) + y * t
                        for x, y in zip(a.radii, b.radii, strict=True)
                    ],
                    a.twist * (1 - t) + b.twist * t,
                )
            )
    samples.append(
        (centers[-1], list(spec.sections[-1].radii), spec.sections[-1].twist)
    )
    vertices = []
    frames = []
    reference = Vector(spec.x_reference).normalized()
    for i, (point, radii, twist) in enumerate(samples):
        tangent = samples[min(len(samples) - 1, i + 1)][0] - samples[max(0, i - 1)][0]
        if tangent.length < 1e-8:
            fail("Loft tangent collapses; separate centers or use linear interpolation")
        tangent.normalize()
        x = reference - tangent * reference.dot(tangent)
        if x.length < 1e-6:
            fail(
                "Section tangent is parallel to transported X; revise "
                "x_reference or centers"
            )
        x.normalize()
        z = x.cross(tangent).normalized()
        reference = x.copy()  # Parallel transport, before authored twist.
        tx, tz = (
            x * math.cos(twist) - z * math.sin(twist),
            x * math.sin(twist) + z * math.cos(twist),
        )
        frames.append((point, tangent.copy()))
        for side in range(spec.sides):
            theta = 2 * math.pi * side / spec.sides
            u, v = math.cos(theta), math.sin(theta)
            offset = (
                tx * u * radii[0 if u >= 0 else 1] + tz * v * radii[2 if v >= 0 else 3]
            )
            displacement = 0.0
            for feature in spec.features:
                axial = abs(i / (len(samples) - 1) - feature.position)
                angle = abs((theta - feature.angle + math.pi) % (2 * math.pi) - math.pi)
                if axial < feature.axial_width and angle < feature.angular_width:
                    axial_weight = 1 - (axial / feature.axial_width) ** 2
                    angle_weight = 1 - (angle / feature.angular_width) ** 2
                    displacement += feature.height * axial_weight**2 * angle_weight**2
            if offset.length + displacement < 1e-6:
                fail("Local feature collapses the section; reduce groove depth")
            offset += offset.normalized() * displacement
            vertices.append(list(point + offset))
    faces = []
    n = spec.sides
    for ring in range(len(samples) - 1):
        for side in range(n):
            nxt = (side + 1) % n
            faces.append(
                [
                    ring * n + side,
                    (ring + 1) * n + side,
                    (ring + 1) * n + nxt,
                    ring * n + nxt,
                ]
            )
    if spec.ends is not None:
        for end, depth in ((0, spec.ends.start_depth), (1, spec.ends.end_depth)):
            base = 0 if end == 0 else (len(samples) - 1) * n
            center, tangent = frames[0 if end == 0 else -1]
            outer = [Vector(vertices[base + side]) for side in range(n)]
            previous = [base + side for side in range(n)]
            for ring in range(1, spec.ends.rings + 1):
                radius = 1 - ring / (spec.ends.rings + 1)
                shift = (
                    tangent
                    * depth
                    * (1 if end == 0 else -1)
                    * (1 - radius * radius) ** 2
                )
                current = []
                for point in outer:
                    current.append(len(vertices))
                    vertices.append(list(center + (point - center) * radius + shift))
                for side in range(n):
                    nxt = (side + 1) % n
                    face = [previous[side], previous[nxt], current[nxt], current[side]]
                    faces.append(face if end == 0 else list(reversed(face)))
                previous = current
            faces.append(previous if end == 0 else list(reversed(previous)))
    elif spec.caps:
        faces += [
            list(range(n)),
            list(reversed(range(len(vertices) - n, len(vertices)))),
        ]
    return vertices, faces


def signature(mesh: Any) -> str:
    h = hashlib.sha256()
    for v in mesh.vertices:
        h.update(struct.pack("<3f", *v.co))
    for p in mesh.polygons:
        h.update(tuple(p.vertices).__repr__().encode())
    return h.hexdigest()


def metadata(obj: Any) -> dict[str, Any]:
    try:
        raw = obj[KEY]
        if not isinstance(raw, str) or len(raw) > 65536:
            raise ValueError
        value: dict[str, Any] = json.loads(raw)
        LoftSpec.model_validate(value["spec"])
        if obj.type != "MESH" or value["signature"] != signature(obj.data):
            fail(
                "Generated base mesh changed; preserve downstream edits and "
                "use mesh tools instead of regenerating"
            )
        return value
    except (KeyError, ValueError, TypeError) as exc:
        raise OperationError(
            "loft_invalid", "Owned loft metadata is missing or damaged"
        ) from exc


def summary(obj: Any, detail: bool = False) -> LoftSummary:
    issues = []
    meta = json.loads(obj.get(KEY, "{}"))
    try:
        metadata(obj)
    except OperationError as exc:
        issues.append(str(exc))
    coords = [v.co for v in obj.data.vertices]
    spec = LoftSpec.model_validate(meta["spec"]).model_copy(update={"name": obj.name})
    return LoftSummary(
        name=obj.name,
        component_id=meta["id"],
        vertex_count=len(coords),
        face_count=len(obj.data.polygons),
        section_count=len(spec.sections),
        bounds_min=[min(v[k] for v in coords) for k in range(3)],
        bounds_max=[max(v[k] for v in coords) for k in range(3)],
        valid=not issues,
        issues=issues,
        spec=spec if detail else None,
        revision=meta["revision"],
        region_ids=[s.id for s in spec.sections if s.id]
        + [f.id for f in spec.features]
        + (["start_cap", "end_cap"] if spec.caps else []),
    )


def revised(old: LoftSpec, edit: LoftRevision) -> LoftSpec:
    values = old.model_dump()
    values.update(
        edit.model_dump(
            exclude_unset=True, exclude={"section_edits", "expected_revision"}
        )
    )
    sections = {s["id"]: s for s in values["sections"] if s["id"] is not None}
    for change in edit.section_edits:
        if change.id not in sections:
            fail(f"Unknown section ID: {change.id}")
        sections[change.id].update(
            change.model_dump(exclude_unset=True, exclude_none=True)
        )
    try:
        return LoftSpec.model_validate(values)
    except ValidationError as exc:
        raise constraint_error("loft_invalid", exc) from exc


def native_checks(mesh: Any, spec: LoftSpec) -> dict[str, Any]:
    from .surfaces import native_checks as check

    try:
        return check(mesh)
    except OperationError as exc:
        raise OperationError(
            exc.error.code,
            f"Loft {spec.name}: invalid generated surface; reduce end depths/features "
            "or revise section curvature, spacing and sampling",
            {"component": spec.name, "cause": exc.error.message},
        ) from exc


def validate_mesh(mesh: Any, spec: LoftSpec) -> None:
    if spec.features or spec.ends:
        native_checks(mesh, spec)


def create(args: LoftCreateArguments) -> LoftResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    collections = [organization.collection_named(n) for n in args.collections]
    for c in collections:
        organization.collection_editable(c)
    prepared = []
    for spec in args.components:
        organization.named_available(spec.name, bpy.data.objects)
        prepared.append((spec, geometry(spec)))
    made = []
    meshes = []
    try:
        for spec, (vertices, faces) in prepared:
            mesh = bpy.data.meshes.new(spec.name)
            meshes.append(mesh)
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            validate_mesh(mesh, spec)
            for polygon in mesh.polygons:
                polygon.use_smooth = spec.smooth
            obj = bpy.data.objects.new(spec.name, mesh)
            made.append(obj)
            for c in collections:
                c.objects.link(obj)
            obj[KEY] = json.dumps(
                dict(
                    id=uuid.uuid4().hex,
                    revision=1,
                    spec=spec.model_dump(),
                    signature=signature(mesh),
                )
            )
        return LoftResult(
            components=[summary(o) for o in made],
            processing_seconds=time.perf_counter() - start,
        )
    except BaseException:
        for obj in reversed(made):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise


def configure(args: LoftConfigureArguments) -> LoftResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    prepared = []
    for edit in args.components:
        obj = organization.object_named(edit.name)
        organization.object_editable(obj)
        organization.editable(obj.data)
        meta = metadata(obj)
        old = LoftSpec.model_validate(meta["spec"])
        if (
            edit.expected_revision is not None
            and edit.expected_revision != meta["revision"]
        ):
            raise OperationError(
                "loft_revision_conflict", "Expected revision is not current"
            )
        spec = revised(old, edit)
        if [s.id for s in old.sections] != [s.id for s in spec.sections]:
            fail("Loft revision preserves ordered section identities")
        if (len(old.sections), old.sides, old.subdivisions, old.caps) != (
            len(spec.sections),
            spec.sides,
            spec.subdivisions,
            spec.caps,
        ):
            fail(
                "Loft revision preserves ordered topology; keep section "
                "count, sides, subdivisions and caps"
            )
        if (old.ends.rings if old.ends else None) != (
            spec.ends.rings if spec.ends else None
        ):
            fail("End support-ring count must be preserved")
        if obj.data.users != 1 or obj.data.shape_keys or obj.data.animation_data:
            fail(
                "Loft revision requires exclusive mesh without shape "
                "keys/data animation"
            )
        vertices, faces = geometry(spec)
        if spec.features or spec.ends:
            staged = bpy.data.meshes.new("LoftValidation")
            try:
                staged.from_pydata(vertices, [], faces)
                staged.update()
                validate_mesh(staged, spec)
            finally:
                bpy.data.meshes.remove(staged)
        prepared.append(
            (
                obj,
                spec,
                vertices,
                [tuple(v.co) for v in obj.data.vertices],
                str(obj[KEY]),
                [p.use_smooth for p in obj.data.polygons],
            )
        )
    LoftBatch(components=[item[1] for item in prepared])
    try:
        for obj, spec, vertices, _, _, _ in prepared:
            for vertex, co in zip(obj.data.vertices, vertices, strict=True):
                vertex.co = co
            for p in obj.data.polygons:
                p.use_smooth = spec.smooth
            obj.data.update()
            meta = json.loads(obj[KEY])
            meta.update(
                spec=spec.model_dump(),
                signature=signature(obj.data),
                revision=meta["revision"] + 1,
            )
            obj[KEY] = json.dumps(meta)
        bpy.context.view_layer.update()
        return LoftResult(
            components=[summary(o) for o, *_ in prepared],
            processing_seconds=time.perf_counter() - start,
        )
    except BaseException:
        for obj, _, _, coords, raw, smooth in prepared:
            for vertex, old_co in zip(obj.data.vertices, coords, strict=True):
                vertex.co = old_co
            for p, value in zip(obj.data.polygons, smooth, strict=True):
                p.use_smooth = value
            obj.data.update()
            obj[KEY] = raw
        raise


def inspect(args: LoftInspectArguments) -> LoftResult:
    start = time.perf_counter()
    organization.idle()
    objects = [organization.object_named(n) for n in args.names]
    if any(KEY not in o or o.type != "MESH" for o in objects):
        fail("Every target must be an owned section loft")
    if (
        args.include_sections
        and sum(len(json.loads(o[KEY])["spec"]["sections"]) for o in objects) > 1024
    ):
        fail("Detailed inspection exceeds 1024 sections; split the query")
    return LoftResult(
        components=[summary(o, args.include_sections) for o in objects],
        processing_seconds=time.perf_counter() - start,
    )
