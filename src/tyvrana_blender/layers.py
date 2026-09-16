"""BVH layer diagnostics with explicit persisted material-triangle references."""

import hashlib
import json
import time
from typing import Any, Self

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.geometry import barycentric_transform  # type: ignore[import-not-found]
from pydantic import Field, ValidationError, model_validator

from . import deformation_qa, layer_math, organization
from . import layer_geometry as geometry
from .errors import OperationError
from .layer_models import (
    CurrentLayerQuery,
    LayerCaptureArguments,
    LayerDistribution,
    LayerInspectArguments,
    LayerInspectResult,
    LayerPair,
    LayerQuery,
    LayerReferencesResult,
    LayerRemoveArguments,
    LayerSummary,
    LayerWorst,
    ReferenceLayerQuery,
)
from .models import Model
from .numeric import Vector32

KEY = "_tyvrana_layer_references"
MAX_REFERENCES = 32
MAX_REFERENCE_SAMPLES = 131072
MAX_METADATA_BYTES = 24_000_000


class TrackedSample(Model):
    vertex: int = Field(ge=0, le=249999)
    triangle: tuple[int, int, int]
    barycentric: Vector32
    offset: Vector32

    @model_validator(mode="after")
    def valid(self) -> Self:
        if (
            len(set(self.triangle)) != 3
            or min(self.triangle) < 0
            or max(self.triangle) >= 250000
            or min(self.barycentric) < -1e-5
            or max(self.barycentric) > 1 + 1e-5
            or abs(sum(self.barycentric) - 1) > 1e-5
        ):
            raise ValueError("Invalid tracked triangle or barycentric coordinates")
        return self


class ReferenceData(Model):
    source_name: str = Field(min_length=1, max_length=128)
    target_name: str = Field(min_length=1, max_length=128)
    source_topology: str = Field(pattern="^[0-9a-f]{64}$")
    target_topology: str = Field(pattern="^[0-9a-f]{64}$")
    source_authored: str = Field(pattern="^[0-9a-f]{64}$")
    target_authored: str = Field(pattern="^[0-9a-f]{64}$")
    selected: int = Field(ge=1, le=250000)
    samples: list[TrackedSample] = Field(min_length=1, max_length=8192)


def records() -> dict[str, Any]:
    value = bpy.context.scene.get(KEY, "{}")
    if not isinstance(value, str) or len(value.encode()) > MAX_METADATA_BYTES:
        geometry.fail("Layer reference storage is invalid or exceeds 24 MB")
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise OperationError(
            "layer_reference_invalid", "Layer reference metadata is invalid"
        ) from exc
    if (
        not isinstance(result, dict)
        or len(result) > MAX_REFERENCES
        or any(not isinstance(k, str) or not k.strip() or len(k) > 128 for k in result)
        or any(
            not isinstance(v, dict) or not isinstance(v.get("samples"), list)
            for v in result.values()
        )
    ):
        geometry.fail("Layer reference catalog is invalid or exceeds 32 references")
    if sum(len(v["samples"]) for v in result.values()) > MAX_REFERENCE_SAMPLES:
        geometry.fail("Layer reference storage exceeds 131072 tracked samples")
    return result


def pointer_key(name: str, side: str) -> str:
    return KEY + "_" + hashlib.sha256(name.encode()).hexdigest()[:16] + "_" + side


def reference_data(value: Any) -> ReferenceData:
    try:
        return ReferenceData.model_validate_json(json.dumps(value))
    except ValidationError as exc:
        raise OperationError(
            "layer_reference_invalid",
            "Layer reference sample data is invalid; remove and recapture",
        ) from exc


def frame(surface: geometry.Surface, triangle: Any) -> tuple[Any, Any, Any]:
    if len(set(triangle)) != 3 or any(
        i < 0 or i >= len(surface.points) for i in triangle
    ):
        geometry.fail("Reference triangle has invalid vertex indices")
    a, b, c = [surface.points[i] for i in triangle]
    x = b - a
    z = x.cross(c - a)
    if x.length <= 1e-10 or z.length <= 1e-14:
        geometry.fail(
            "Tracked target triangle became degenerate; use a noncollapsed pose"
        )
    x.normalize()
    z.normalize()
    return x, z.cross(x).normalized(), z


def local_offset(vector: Any, axes: Any) -> list[float]:
    return [float(vector.dot(axis)) for axis in axes]


def selected(
    pair: LayerPair, cache: geometry.SurfaceCache
) -> tuple[Any, Any, list[int], int]:
    source, target = cache.get(pair.source), cache.get(pair.target)
    chosen = source.selected(pair.selector)
    return (
        source,
        target,
        layer_math.sample_indices(chosen, pair.sample_count),
        len(chosen),
    )


def capture(args: LayerCaptureArguments) -> LayerReferencesResult:
    organization.idle(mutate=True)
    scene = bpy.context.scene
    organization.editable(scene)
    old = records()
    updated = dict(old)
    pointers = {}
    with geometry.SurfaceCache() as cache:
        for spec in args.references:
            if spec.name in old and not spec.replace:
                geometry.fail(
                    "Reference already exists; set replace=true for an explicit "
                    "recapture"
                )
            source, target, indices, total = selected(spec, cache)
            samples = []
            for i in indices:
                near, _normal, face, _distance = target.bvh().find_nearest(
                    source.points[i]
                )
                if near is None or face is None:
                    geometry.fail("Target has no nearest nondegenerate surface")
                triangle = target.triangles[face]
                axes = frame(target, triangle)
                a, b, c = [target.points[j] for j in triangle]
                bary = barycentric_transform(
                    near,
                    a,
                    b,
                    c,
                    Vector((1, 0, 0)),
                    Vector((0, 1, 0)),
                    Vector((0, 0, 1)),
                )
                samples.append(
                    TrackedSample(
                        vertex=i,
                        triangle=triangle,
                        barycentric=list(bary),
                        offset=local_offset(source.points[i] - near, axes),
                    )
                )
            record = ReferenceData(
                source_name=source.obj.name,
                target_name=target.obj.name,
                source_topology=source.topology,
                target_topology=target.topology,
                source_authored=source.authored,
                target_authored=target.authored,
                selected=total,
                samples=samples,
            )
            updated[spec.name] = record.model_dump(mode="json")
            pointers[pointer_key(spec.name, "s")] = source.obj
            pointers[pointer_key(spec.name, "t")] = target.obj
    if (
        len(updated) > MAX_REFERENCES
        or sum(len(v["samples"]) for v in updated.values()) > MAX_REFERENCE_SAMPLES
    ):
        geometry.fail(
            "Capture exceeds 32 references/131072 tracked samples; remove "
            "unused references"
        )
    encoded = json.dumps(updated, separators=(",", ":"))
    if len(encoded.encode()) > MAX_METADATA_BYTES:
        geometry.fail("Capture metadata exceeds 24 MB")
    saved = {k: scene.get(k) for k in pointers}
    previous = scene.get(KEY)
    try:
        for key, value in pointers.items():
            scene[key] = value
        scene[KEY] = encoded
    except BaseException:
        for key, value in saved.items():
            if value is None:
                if key in scene:
                    del scene[key]
            else:
                scene[key] = value
        if previous is None:
            if KEY in scene:
                del scene[KEY]
        else:
            scene[KEY] = previous
        raise
    return LayerReferencesResult(
        names=sorted(updated), changed=[r.name for r in args.references]
    )


def remove(args: LayerRemoveArguments) -> LayerReferencesResult:
    organization.idle(mutate=True)
    scene = bpy.context.scene
    organization.editable(scene)
    value = records()
    if any(name not in value for name in args.names):
        geometry.fail(
            "Remove existing layer reference names; inspect the catalog first"
        )
    for name in args.names:
        del value[name]
    scene[KEY] = json.dumps(value, separators=(",", ":"))
    for name in args.names:
        for side in ("s", "t"):
            key = pointer_key(name, side)
            if key in scene:
                del scene[key]
    return LayerReferencesResult(names=sorted(value), changed=args.names)


def distribution(values: list[float]) -> LayerDistribution:
    return LayerDistribution(
        **deformation_qa.distribution(values).model_dump(),
        mean=sum(values) / len(values) if values else None,
    )


def inspect_one(
    query: LayerQuery,
    args: LayerInspectArguments,
    cache: geometry.SurfaceCache,
    catalog: dict[str, Any],
) -> LayerSummary:
    reference = None
    source = target = None
    try:
        if isinstance(query, CurrentLayerQuery):
            source, target, indices, total = selected(query, cache)
        else:
            if query.name not in catalog:
                geometry.fail("Layer reference is missing; inspect names or capture it")
            reference = reference_data(catalog[query.name])
            a = bpy.context.scene.get(pointer_key(query.name, "s"))
            b = bpy.context.scene.get(pointer_key(query.name, "t"))
            if a is None or b is None:
                geometry.fail("Layer reference object was removed")
            source, target = cache.get(a.name), cache.get(b.name)
            if (source.topology, target.topology, source.authored, target.authored) != (
                reference.source_topology,
                reference.target_topology,
                reference.source_authored,
                reference.target_authored,
            ):
                geometry.fail(
                    "Layer reference authored/evaluated topology changed; explicitly "
                    "recapture compatible geometry"
                )
            indices = [s.vertex for s in reference.samples]
            total = reference.selected
        distances, gaps, rays, tangents, changes = [], [], [], [], []
        worst = []
        misses = 0
        for order, i in enumerate(indices):
            if i >= len(source.points):
                geometry.fail("Reference source index exceeds current surface")
            point = source.points[i]
            near, normal, _face, distance = target.bvh().find_nearest(point)
            if near is None or normal is None or distance is None:
                geometry.fail("Target has no nearest surface")
            gap = float((point - near).dot(normal))
            distances.append(float(distance))
            gaps.append(gap)
            tangent = change = None
            if reference:
                sample = reference.samples[order]
                axes = frame(target, sample.triangle)
                anchor = sum(
                    (
                        target.points[j] * w
                        for j, w in zip(
                            sample.triangle, sample.barycentric, strict=True
                        )
                    ),
                    Vector((0, 0, 0)),
                )
                tangent, change, magnitude = layer_math.sliding(
                    local_offset(point - anchor, axes), sample.offset
                )
                tangents.append(magnitude)
                changes.append(change)
            if args.ray_direction != "none":
                direction = source.vertex_normals()[i]
                if args.ray_direction == "opposite_source_normal":
                    direction = -direction
                hit = (
                    target.bvh().ray_cast(point, direction, args.ray_limit)
                    if direction.length > 1e-10
                    else (None, None, None, None)
                )
                if hit[3] is None:
                    misses += 1
                else:
                    rays.append(float(hit[3]))
            worst.append(
                LayerWorst(
                    vertex=i,
                    position_world=list(point),
                    nearest_world=list(near),
                    separation=distance,
                    oriented_normal_gap=gap,
                    tangential_delta=tangent,
                    normal_change=change,
                )
            )
        worst.sort(
            key=lambda w: (
                w.oriented_normal_gap
                if w.oriented_normal_gap < -args.normal_epsilon
                else 0,
                w.separation,
                w.vertex,
            )
        )
        return LayerSummary(
            source=source.obj.name,
            target=target.obj.name,
            reference=query.name if isinstance(query, ReferenceLayerQuery) else None,
            selected_vertices=total,
            sampled_vertices=len(indices),
            source_topology_sha256=source.topology,
            target_topology_sha256=target.topology,
            separation=distribution(distances),
            oriented_normal_gap=distribution(gaps),
            contact_samples=sum(d <= args.contact_distance for d in distances),
            below_minimum_samples=sum(d < args.minimum_separation for d in distances),
            negative_side_samples=sum(g < -args.normal_epsilon for g in gaps),
            negative_side_depth_max=max((max(0, -g) for g in gaps), default=0),
            normal_ray_distance=distribution(rays)
            if args.ray_direction != "none"
            else None,
            normal_ray_misses=misses,
            tangential_movement=distribution(tangents) if reference else None,
            normal_change=distribution(changes) if reference else None,
            worst=worst[: args.worst_limit],
        )
    except OperationError as exc:
        if isinstance(query, CurrentLayerQuery):
            raise
        return LayerSummary(
            source=source.obj.name
            if source
            else (reference.source_name if reference else None),
            target=target.obj.name
            if target
            else (reference.target_name if reference else None),
            reference=query.name,
            valid=False,
            reason=str(exc)[:512],
        )


def inspect(args: LayerInspectArguments) -> LayerInspectResult:
    start = time.perf_counter()
    catalog = records()
    with geometry.SurfaceCache() as cache:
        rows = [inspect_one(query, args, cache, catalog) for query in args.queries]
        return LayerInspectResult(
            layers=rows,
            references=sorted(catalog),
            evaluated_vertices=cache.vertices,
            processing_seconds=time.perf_counter() - start,
        )
