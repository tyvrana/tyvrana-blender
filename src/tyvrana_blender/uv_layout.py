"""Native UV snapshots, joint density-aware packing and bounded PNG artifacts."""

import math
from collections import defaultdict
from contextlib import ExitStack
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils.geometry import box_pack_2d  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from . import retopo_geometry, uv, uv_quality
from .artifacts import ArtifactSpool, ArtifactTooLarge, SpoolFull
from .errors import OperationError
from .uv_models import UVLayoutArguments, UVLayoutResult, UVPackArguments, UVPackResult


def surface(obj: Any, mesh: Any, uv_map: str | None) -> uv_quality.Surface:
    if (
        len(mesh.polygons) > uv_quality.MAX_FACES
        or len(mesh.loops) > uv_quality.MAX_TRIANGLES * 3
    ):
        raise OperationError(
            "work_limit_exceeded", "UV layout exceeds bounded face/corner capacity"
        )
    layer = uv.layer_for(mesh, uv_map)
    mesh.calc_loop_triangles()
    if len(mesh.loop_triangles) > uv_quality.MAX_TRIANGLES:
        raise OperationError(
            "work_limit_exceeded",
            f"UV layout exceeds {uv_quality.MAX_TRIANGLES} triangles",
        )
    triangles: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for triangle in mesh.loop_triangles:
        start = mesh.polygons[triangle.polygon_index].loop_start
        indices = [i - start for i in triangle.loops]
        triangles[triangle.polygon_index].append((indices[0], indices[1], indices[2]))
    faces = []
    for face in mesh.polygons:
        points = [obj.matrix_world @ mesh.vertices[i].co for i in face.vertices]
        faces.append(
            uv_quality.Face(
                face.index,
                list(face.vertices),
                [
                    (float(layer.uv[i].vector[0]), float(layer.uv[i].vector[1]))
                    for i in face.loop_indices
                ],
                [(float(p[0]), float(p[1]), float(p[2])) for p in points],
                triangles[face.index],
            )
        )
    return uv_quality.Surface(obj.name, faces)


def inspect(
    arguments: UVLayoutArguments, spool: ArtifactSpool | None
) -> tuple[UVLayoutResult, ArtifactDescriptor | None]:
    objects = [uv.mesh_object(name) for name in arguments.objects]
    if any(obj.mode != "OBJECT" for obj in objects):
        raise OperationError(
            "invalid_context", "UV layout inspection requires Object Mode"
        )
    snapshots = []
    for obj in objects:
        if arguments.evaluated:
            graph = retopo_geometry.graph(obj)
            with retopo_geometry.evaluated_mesh(obj, graph) as (mesh, _):
                snapshots.append(surface(obj, mesh, arguments.uv_map))
        else:
            snapshots.append(surface(obj, obj.data, arguments.uv_map))
    try:
        report, triangles = uv_quality.analyze(
            snapshots,
            arguments.resolution,
            arguments.evaluated,
            island_offset=arguments.island_offset,
            island_limit=arguments.island_limit,
        )
    except ValueError as exc:
        raise OperationError("uv_analysis_failed", str(exc)) from exc
    descriptor = None
    if arguments.layout_image:
        if spool is None:
            raise OperationError(
                "invalid_context", "UV artifact storage is unavailable"
            )
        try:
            with spool.reserve() as (artifact_id, path):
                path.write_bytes(
                    uv_quality.png_layout(
                        triangles,
                        arguments.image_size,
                        report.bounds_min,
                        report.bounds_max,
                    )
                )
                descriptor = spool.describe(artifact_id)
        except (SpoolFull, ArtifactTooLarge) as exc:
            raise OperationError(
                "artifact_too_large", "UV layout artifact storage limit reached"
            ) from exc
    return report, descriptor


def pack(arguments: UVPackArguments) -> UVPackResult:
    """Pack rigid island rectangles through Blender's native box packer.

    Relative linear density is normalized from world/UV areas. Rectangle packing
    deliberately protects continuity and padding; it does not fill concave holes.
    Every UV edit is prepared before publication and rolls back as one batch.
    """
    objects = [uv.mesh_object(item.object_name) for item in arguments.objects]
    if bpy.context.mode != "OBJECT":
        raise OperationError("invalid_context", "Joint packing requires Object Mode")
    entries: list[dict[str, Any]] = []
    for obj, target in zip(objects, arguments.objects, strict=True):
        data = surface(obj, obj.data, target.uv_map)
        if not data.faces:
            raise OperationError("invalid_context", "UV packing requires mapped faces")
        weight_groups = {}
        for weight in target.island_weights:
            if (
                weight.face_index >= len(data.faces)
                or weight.face_index in weight_groups
            ):
                raise OperationError(
                    "invalid_arguments",
                    "Island weight references must be distinct existing faces",
                )
            weight_groups[weight.face_index] = weight.weight
        for group in uv_quality.islands(data):
            overrides = [
                weight_groups[f.index] for f in group if f.index in weight_groups
            ]
            if len(overrides) > 1:
                raise OperationError(
                    "invalid_arguments", "Use one weight reference per UV island"
                )
            points = [p for f in group for p in f.uv]
            if any(not math.isfinite(c) for p in points for c in p):
                raise OperationError(
                    "invalid_arguments", "Packing requires finite UV coordinates"
                )
            minimum = [min(p[i] for p in points) for i in range(2)]
            maximum = [max(p[i] for p in points) for i in range(2)]
            world_area = uv_area = 0.0
            for face in group:
                for indices in face.triangles:
                    world, signed, _, _ = uv_quality.triangle_metric(
                        [face.points[i] for i in indices], [face.uv[i] for i in indices]
                    )
                    if abs(signed) <= uv_quality.EPS or world <= 1e-20:
                        raise OperationError(
                            "invalid_arguments", "Unwrap degenerate UVs before packing"
                        )
                    world_area += world
                    uv_area += abs(signed)
            density = (
                math.sqrt(world_area / uv_area)
                * target.density_weight
                * (overrides[0] if overrides else 1)
            )
            width = (maximum[0] - minimum[0]) * density
            height = (maximum[1] - minimum[1]) * density
            entries.append(
                dict(
                    object=obj,
                    target=target,
                    faces=group,
                    minimum=minimum,
                    maximum=maximum,
                    density=density,
                    width=width,
                    height=height,
                )
            )
    if (
        not entries
        or len(entries) > 512
        or sum(len(e["faces"]) for e in entries) > uv_quality.MAX_FACES
    ):
        raise OperationError(
            "work_limit_exceeded",
            f"Packing requires 1–512 islands and at most {uv_quality.MAX_FACES} faces",
        )
    extent = [
        b - a for a, b in zip(arguments.bounds_min, arguments.bounds_max, strict=True)
    ]
    for entry in entries:
        w, h = entry["width"], entry["height"]
        rotate = arguments.rotate and max(h / extent[0], w / extent[1]) < max(
            w / extent[0], h / extent[1]
        )
        entry["rotate"] = rotate
        if rotate:
            entry["width"], entry["height"] = h, w
    padding = (arguments.padding_pixels + 0.01) / arguments.resolution

    def boxes(scale: float) -> tuple[bool, list[list[float]]]:
        packed = [
            [
                0.0,
                0.0,
                (entry["width"] * scale + 2 * padding) / extent[0],
                (entry["height"] * scale + 2 * padding) / extent[1],
                float(i),
            ]
            for i, entry in enumerate(entries)
        ]
        w, h = box_pack_2d(packed)
        return w <= 1 and h <= 1, packed

    fits, chosen = boxes(0)
    if not fits:
        raise OperationError(
            "invalid_arguments", "Island padding cannot fit the requested region"
        )
    low, high = 0.0, 1.0
    while high < 1e12:
        fits, candidate = boxes(high)
        if not fits:
            break
        low = high
        chosen = candidate
        high *= 2
    for _ in range(36):
        middle = (low + high) / 2
        fits, candidate = boxes(middle)
        if fits:
            low = middle
            chosen = candidate
        else:
            high = middle
    if low <= 0:
        raise OperationError(
            "invalid_arguments", "UV islands cannot fit the requested region"
        )
    updates: dict[str, dict[int, tuple[float, float]]] = defaultdict(dict)
    for box in chosen:
        entry = entries[int(box[4])]
        origin = [
            arguments.bounds_min[i] + box[i] * extent[i] + padding for i in range(2)
        ]
        for face in entry["faces"]:
            polygon = entry["object"].data.polygons[face.index]
            for local, p in enumerate(face.uv):
                x, y = p[0] - entry["minimum"][0], p[1] - entry["minimum"][1]
                if entry["rotate"]:
                    x, y = entry["maximum"][1] - entry["minimum"][1] - y, x
                factor = entry["density"] * low
                updates[entry["object"].name][polygon.loop_start + local] = (
                    origin[0] + x * factor,
                    origin[1] + y * factor,
                )
    with ExitStack() as stack:
        for obj in objects:
            stack.enter_context(uv.mutation(obj))
        for obj, target in zip(objects, arguments.objects, strict=True):
            layer = uv.layer_for(obj.data, target.uv_map)
            for index, value in updates[obj.name].items():
                layer.uv[index].vector = value
            obj.data.update()
        result = UVPackResult(
            objects=[uv.inspect(obj) for obj in objects],
            island_count=len(entries),
            bounds_min=arguments.bounds_min,
            bounds_max=arguments.bounds_max,
            resolution=arguments.resolution,
            padding_pixels=arguments.padding_pixels,
        )
    return result
