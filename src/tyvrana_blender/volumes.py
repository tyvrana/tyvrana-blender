"""Inspect native swept volumes and freeze evaluated geometry for mesh workflows."""

import json
import math
import time
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import deformation_geometry as geo
from . import layer_geometry as geometry
from . import organization, retopo_geometry
from .volume_models import (
    PathSection,
    VolumeInspectArguments,
    VolumeInspectResult,
    VolumePath,
    VolumeQuery,
    VolumeSnapshotArguments,
    VolumeSnapshotResult,
    VolumeSnapshotSummary,
    VolumeSummary,
)

KEY = "_tyvrana_volume_snapshot"


def path(surface: geometry.Surface, reference: Any) -> VolumePath | None:
    curve = surface.curve
    if curve is None:
        return None
    radii = [value for row in curve.splines for value in row.radius_range]
    profile = curve.profile.model_dump()
    radius = profile.get("radius") if profile["kind"] == "circle" else None
    scale = surface.obj.matrix_world.to_scale().x
    reference_length = None
    if reference is not None:
        if reference.curve:
            reference_length = reference.curve.length
        elif KEY in reference.obj:
            raw = reference.obj[KEY]
            try:
                if not isinstance(raw, str) or len(raw) > 4096:
                    raise ValueError("Invalid snapshot provenance")
                reference_length = json.loads(raw).get("path_length")
                if reference_length is not None and (
                    not isinstance(reference_length, (float, int))
                    or not math.isfinite(reference_length)
                    or reference_length <= 0
                ):
                    raise ValueError("Invalid reference path length")
            except (ValueError, TypeError, AttributeError):
                geometry.fail(
                    "Snapshot path provenance is invalid; create a new reference"
                )
    return VolumePath(
        guide=surface.obj.name,
        length=curve.length,
        reference_length=reference_length,
        length_ratio=curve.length / reference_length
        if reference_length and reference_length > 1e-12
        else None,
        profile_kind=profile["kind"],
        control_radius_multiplier_min=min(radii),
        control_radius_multiplier_max=max(radii),
        circle_radius_min=min(radii) * radius * scale if radius is not None else None,
        circle_radius_max=max(radii) * radius * scale if radius is not None else None,
        attachment_count=len(curve.bindings),
        attachment_error_max=max((b.error or 0 for b in curve.bindings), default=0),
        sections=[
            PathSection(
                spline=row.index,
                factor=s.factor,
                position=s.position,
                radius_multiplier=s.radius,
                circle_radius=s.radius * radius * scale if radius is not None else None,
            )
            for row in curve.splines
            for s in row.samples
        ],
    )


def summary(query: VolumeQuery, cache: geometry.SurfaceCache) -> VolumeSummary:
    surface = cache.get(query.object_name)
    current = geometry.metrics(surface.points, surface.triangles)
    reference = cache.get(query.reference) if query.reference else None
    reference_volume = None
    reference_kind = None
    if reference:
        reference_volume = geometry.metrics(
            reference.points, reference.triangles
        ).volume
        reference_kind = "evaluated_reference_object"
    elif surface.obj.type == "MESH":
        data = surface.obj.data
        if len(data.vertices) > geometry.MAX_VERTICES:
            geometry.fail("Authored volume baseline exceeds 250000 vertices")
        data.calc_loop_triangles()
        if len(data.loop_triangles) > geometry.MAX_TRIANGLES:
            geometry.fail("Authored volume baseline exceeds 500000 triangles")
        points = [
            retopo_geometry.checked_point(surface.obj.matrix_world @ v.co)
            for v in data.vertices
        ]
        triangles = [tuple(t.vertices) for t in data.loop_triangles]
        if triangles:
            reference_volume = geometry.metrics(points, triangles).volume
        reference_kind = "authored_mesh"
    ratio = (
        current.volume / reference_volume
        if current.volume is not None
        and reference_volume is not None
        and reference_volume > 1e-16
        else None
    )
    return VolumeSummary(
        object_name=surface.obj.name,
        representation="profiled_curve" if surface.curve else "mesh",
        valid=True,
        evaluated=current,
        reference_volume=reference_volume,
        reference_kind=reference_kind,
        volume_ratio=ratio,
        volume_change_percent=(ratio - 1) * 100 if ratio is not None else None,
        path=path(surface, reference),
        modifiers=[m.type for m in surface.obj.modifiers],
        topology_sha256=surface.topology,
    )


def inspect(args: VolumeInspectArguments) -> VolumeInspectResult:
    start = time.perf_counter()
    with geometry.SurfaceCache(args.section_samples) as cache:
        result = [summary(query, cache) for query in args.objects]
        return VolumeInspectResult(
            volumes=result,
            evaluated_vertices=cache.vertices,
            processing_seconds=time.perf_counter() - start,
        )


def snapshot(args: VolumeSnapshotArguments) -> VolumeSnapshotResult:
    organization.idle(mutate=True)
    collections = [organization.collection_named(n) for n in args.collections]
    for collection in collections:
        organization.editable(collection)
    for spec in args.objects:
        geo.native_name(spec.name)
        if spec.name in bpy.data.objects:
            geometry.fail("Snapshot name already exists; choose an unused name")
    created = []
    datasets = []
    results = []
    with geometry.SurfaceCache() as cache:
        surfaces = [cache.get(spec.source) for spec in args.objects]
        try:
            for spec, surface in zip(args.objects, surfaces, strict=True):
                graph = bpy.context.evaluated_depsgraph_get()
                evaluated = surface.obj.evaluated_get(graph)
                data = bpy.data.meshes.new_from_object(
                    evaluated, preserve_all_data_layers=True, depsgraph=graph
                )
                datasets.append(data)
                if geo.topology(data) != surface.topology:
                    geometry.fail(
                        "Evaluation changed during snapshot; retry in stable state"
                    )
                obj = bpy.data.objects.new(spec.name, data)
                created.append(obj)
                obj.matrix_world = surface.obj.matrix_world.copy()
                # Copy group definitions so preserved mesh deform layers retain names.
                for group in surface.obj.vertex_groups:
                    (
                        obj.vertex_groups.get(group.name)
                        or obj.vertex_groups.new(name=group.name)
                    ).lock_weight = group.lock_weight
                organization.metadata(obj, args.role, args.tags)
                obj[KEY] = json.dumps(
                    dict(
                        source=surface.obj.name,
                        path_length=surface.curve.length if surface.curve else None,
                    )
                )
                for collection in collections:
                    collection.objects.link(obj)
                results.append(
                    VolumeSnapshotSummary(
                        source=spec.source,
                        object_name=obj.name,
                        vertex_count=len(data.vertices),
                        face_count=len(data.polygons),
                        topology_sha256=geo.topology(data),
                    )
                )
            bpy.context.view_layer.update()
        except BaseException:
            for obj in reversed(created):
                bpy.data.objects.remove(obj, do_unlink=True)
            for data in datasets:
                if data.users == 0:
                    bpy.data.meshes.remove(data)
            raise
    return VolumeSnapshotResult(snapshots=results)
