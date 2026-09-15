"""Native selected-to-active baking on disposable evaluated scene snapshots."""

import hashlib
import math
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from . import modifiers, retopo_geometry, uv, uv_layout, uv_quality
from .bake_models import (
    BakeImageArguments,
    BakeImageResult,
    BakeInspectArguments,
    BakeInspectResult,
    BakeRaySummary,
    BakeTarget,
    BakeTiming,
    NormalImageQA,
)
from .operations import OperationError

EVALUATION = (
    "frozen current evaluated meshes; matching viewport/render modifier settings"
)
LIMITATIONS = [
    (
        "Distance and ray statistics sample vertices and native "
        "triangle centroids; they do not count every bake ray."
    ),
    (
        "Ray origins use interpolated cage vertex normals and "
        "explicit source sets; nearest-surface distance alone does "
        "not prove a valid directional hit."
    ),
    (
        "Normal maps change shading, not silhouette, source "
        "intersections, subdivision or future export tangents."
    ),
]


def settings() -> tuple[str, list[str]]:
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs is None:
        raise OperationError("render_engine_unavailable", "Cycles is unavailable")
    p = prefs.preferences
    return str(p.compute_device_type), [
        f"{d.type}:{d.name}" for d in p.devices if d.use
    ]


def uv_hash(mesh: Any, name: str) -> str:
    layer = uv.layer_for(mesh, name)
    values = np.empty(len(layer.uv) * 2, dtype=np.float32)
    layer.uv.foreach_get("vector", values)
    return hashlib.sha256(values.astype("<f4", copy=False).tobytes()).hexdigest()


def seam_hash(mesh: Any) -> str:
    return hashlib.sha256(bytes(e.use_seam for e in mesh.edges)).hexdigest()


@contextmanager
def snapshots(targets: list[BakeTarget]) -> Iterator[dict[str, tuple[Any, Any]]]:
    if bpy.context.mode != "OBJECT" or bpy.app.is_job_running("RENDER"):
        raise OperationError("invalid_context", "Baking requires idle Object Mode")
    names = list(dict.fromkeys(n for t in targets for n in [t.target, *t.sources]))
    meshes: dict[str, tuple[Any, Any]] = {}
    try:
        for name in names:
            obj = uv.mesh_object(name)
            if any(m.show_viewport != m.show_render for m in obj.modifiers):
                raise OperationError(
                    "bake_evaluation_mismatch",
                    "Viewport and render modifier visibility must agree",
                )
            if any(
                m.type in {"SUBSURF", "MULTIRES"} and m.levels != m.render_levels
                for m in obj.modifiers
            ):
                raise OperationError(
                    "bake_evaluation_mismatch",
                    "Viewport and render subdivision levels must agree",
                )
            graph = retopo_geometry.graph(obj)
            evaluated = obj.evaluated_get(graph)
            data = bpy.data.meshes.new_from_object(
                evaluated, preserve_all_data_layers=True, depsgraph=graph
            )
            meshes[name] = (data, evaluated.matrix_world.copy())
            modifiers.check_geometry(data)
            data.calc_loop_triangles()
        for t in targets:
            uv.layer_for(meshes[t.target][0], t.uv_map)
        yield meshes
    finally:
        for mesh, _ in meshes.values():
            bpy.data.meshes.remove(mesh)


def tree(meshes: dict[str, tuple[Any, Any]], names: list[str]) -> Any:
    points: list[Any] = []
    faces: list[tuple[int, ...]] = []
    for name in names:
        mesh, matrix = meshes[name]
        offset = len(points)
        points.extend(matrix @ v.co for v in mesh.vertices)
        faces.extend(tuple(offset + i for i in t.vertices) for t in mesh.loop_triangles)
    if not faces:
        raise OperationError("bake_empty_source", "Bake sources have no triangles")
    return BVHTree.FromPolygons(points, faces, all_triangles=True)


def analyze_target(
    t: BakeTarget, meshes: dict[str, tuple[Any, Any]], limit: int
) -> BakeRaySummary:
    mesh, matrix = meshes[t.target]
    source = tree(meshes, t.sources)
    normal_matrix = matrix.inverted().transposed().to_3x3()
    samples = []
    # Sample both vertices and triangle interiors, retaining face references.
    for v in list(mesh.vertices)[
        :: max(1, math.ceil(len(mesh.vertices) / (limit // 2)))
    ]:
        samples.append((v.co.copy(), v.normal.copy(), -1))
    for tri in list(mesh.loop_triangles)[
        :: max(1, math.ceil(len(mesh.loop_triangles) / (limit // 2)))
    ]:
        verts = [mesh.vertices[i] for i in tri.vertices]
        n = (
            sum((v.normal for v in verts), Vector()).normalized()
            if t.use_cage or tri.use_smooth
            else tri.normal.copy()
        )
        samples.append((sum((v.co for v in verts), Vector()) / 3, n, tri.polygon_index))
    if not samples:
        raise OperationError("bake_empty_target", "Bake target has no geometry")
    distances, angles, hits, bad = [], [], [], []
    for co, normal, _face in samples:
        _, sn, _, dist = source.find_nearest(matrix @ co)
        if dist is None:
            raise OperationError("bake_empty_source", "No source surface")
        distances.append(float(dist))
        angles.append(math.degrees((normal_matrix @ normal).normalized().angle(sn, 0)))
    scales = [abs(float(v)) for v in matrix.to_scale()]
    if min(scales) < 1e-8 or matrix.determinant() <= 0:
        raise OperationError(
            "bake_transform_invalid", "Use a nonsingular positive target transform"
        )
    suggested = max(max(distances) * 1.5, 0.000001) / min(scales)
    suggested_reach = suggested * max(scales) * 3
    extrusion = t.cage_extrusion if t.cage_extrusion is not None else suggested
    reach = t.max_ray_distance if t.max_ray_distance is not None else suggested_reach
    misses = back = 0
    for co, normal, face in samples:
        start = matrix @ (co + normal * extrusion)
        direction = -(normal_matrix @ normal).normalized()
        _, sn, _, dist = source.ray_cast(start, direction, reach)
        if dist is None:
            misses += 1
            if face >= 0:
                bad.append(face)
        else:
            hits.append(float(dist))
            if sn.dot(direction) > 0:
                back += 1
                if face >= 0:
                    bad.append(face)
    original = uv.mesh_object(t.target).data
    return BakeRaySummary(
        target=t.target,
        sources=t.sources,
        evaluated_vertices=len(mesh.vertices),
        evaluated_triangles=len(mesh.loop_triangles),
        sampled_points=len(samples),
        nearest_distance=uv_quality.stats(distances),
        source_normal_angle_degrees=uv_quality.stats(angles),
        suggested_extrusion=suggested,
        suggested_max_ray_distance=suggested_reach,
        tested_extrusion=extrusion,
        tested_max_ray_distance=reach,
        ray_misses=misses,
        backface_hits=back,
        hit_distance=uv_quality.stats(hits),
        worst_faces=list(dict.fromkeys(bad))[:32],
        authored_uv_sha256=uv_hash(original, t.uv_map),
        evaluated_uv_sha256=uv_hash(mesh, t.uv_map),
        seam_sha256=seam_hash(original),
    )


def image_pixels(image: Any) -> Any:
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape((image.size[1], image.size[0], 4))


def uv_mask(mesh: Any, name: str, width: int, height: int) -> Any:
    layer = uv.layer_for(mesh, name)
    mask = np.zeros((height, width), dtype=bool)
    for tri in mesh.loop_triangles:
        p = np.array([layer.uv[i].vector[:] for i in tri.loops]) * [width, height]
        lo = np.maximum(np.floor(p.min(axis=0) - 0.5).astype(int), 0)
        hi = np.minimum(
            np.ceil(p.max(axis=0) - 0.5).astype(int), [width - 1, height - 1]
        )
        if (hi < lo).any():
            continue
        x, y = np.meshgrid(
            np.arange(lo[0], hi[0] + 1) + 0.5, np.arange(lo[1], hi[1] + 1) + 0.5
        )
        den = (p[1, 1] - p[2, 1]) * (p[0, 0] - p[2, 0]) + (p[2, 0] - p[1, 0]) * (
            p[0, 1] - p[2, 1]
        )
        if abs(den) < 1e-12:
            continue
        a = (
            (p[1, 1] - p[2, 1]) * (x - p[2, 0]) + (p[2, 0] - p[1, 0]) * (y - p[2, 1])
        ) / den
        b = (
            (p[2, 1] - p[0, 1]) * (x - p[2, 0]) + (p[0, 0] - p[2, 0]) * (y - p[2, 1])
        ) / den
        mask[lo[1] : hi[1] + 1, lo[0] : hi[0] + 1] |= (a >= 0) & (b >= 0) & (a + b <= 1)
    return mask


def array_stats(a: Any) -> Any:
    if not a.size:
        return uv_quality.stats([])
    values = np.quantile(a, [0, 0.05, 0.5, 0.95, 1]).tolist()
    return dict(
        zip(["minimum", "p05", "median", "p95", "maximum"], values, strict=True)
    )


def image_qa(
    name: str, targets: list[BakeTarget], meshes: dict[str, tuple[Any, Any]]
) -> NormalImageQA:
    image = bpy.data.images.get(name)
    if (
        image is None
        or image.channels != 4
        or not all(image.size)
        or max(image.size) > 4096
    ):
        raise OperationError(
            "bake_image_invalid",
            "Normal QA requires a loaded RGBA image up to 4096 square",
        )
    pixels = image_pixels(image)
    covered = np.zeros(pixels.shape[:2], dtype=bool)
    per_target: dict[str, dict[str, int | float]] = {}
    for t in targets:
        mask = uv_mask(meshes[t.target][0], t.uv_map, *image.size)
        if (covered & mask).any():
            raise OperationError("bake_uv_overlap", "Target UV interiors overlap")
        covered |= mask
        rgb = pixels[mask, :3] * 2 - 1
        lengths = np.linalg.norm(rgb, axis=1)
        per_target[t.target] = {
            "covered_texels": int(mask.sum()),
            "uncovered_alpha_texels": int((pixels[mask, 3] < 0.5).sum()),
            "invalid_normal_texels": int(((lengths < 0.9) | (lengths > 1.1)).sum()),
            "negative_z_texels": int((rgb[:, 2] < 0).sum()),
        }
    p = pixels[covered]
    normals = p[:, :3] * 2 - 1
    lengths = np.linalg.norm(normals, axis=1)
    finite = np.isfinite(normals).all(axis=1)
    tilt = np.degrees(
        np.arccos(
            np.clip(normals[finite, 2] / np.maximum(lengths[finite], 1e-12), -1, 1)
        )
    )
    return NormalImageQA(
        image=name,
        resolution=list(image.size),
        color_space=image.colorspace_settings.name,
        float_buffer=image.is_float,
        covered_texels=int(covered.sum()),
        coverage_fraction=float(covered.mean()),
        uncovered_alpha_texels=int((p[:, 3] < 0.5).sum()),
        invalid_normal_texels=int(((lengths < 0.9) | (lengths > 1.1)).sum()),
        negative_z_texels=int((normals[:, 2] < 0).sum()),
        nonfinite_components=int((~np.isfinite(p)).sum()),
        length=array_stats(lengths[finite]),
        tilt_degrees=array_stats(tilt),
        per_target=per_target,
        limitations=[
            (
                "Coverage rasterizes native target triangles at pixel "
                "centers; subpixel boundary sampling may differ from native "
                "baking."
            ),
            (
                "Alpha/length detect invalid interior data; bake margin can "
                "fill tiny ray misses. Sampled geometric ray analysis and "
                "rendered comparison remain required."
            ),
            (
                "Negative tangent Z and high tilt are diagnostics, not "
                "automatically incorrect on overhangs; inspect localized "
                "surfaces."
            ),
        ],
    )


def inspect(arguments: BakeInspectArguments) -> BakeInspectResult:
    backend, devices = settings()
    with snapshots(arguments.targets) as meshes:
        targets = [
            analyze_target(t, meshes, arguments.sample_limit) for t in arguments.targets
        ]
        qa = (
            image_qa(arguments.image, arguments.targets, meshes)
            if arguments.image
            else None
        )
    return BakeInspectResult(
        supported_types=["normal"],
        target_evaluation=EVALUATION,
        normal_convention="OpenGL +X +Y +Z, native tangent basis",
        compute_backend=backend,
        enabled_devices=devices,
        targets=targets,
        image_qa=qa,
        limitations=LIMITATIONS,
    )


def execute_native(**arguments: Any) -> Any:
    return bpy.ops.object.bake("EXEC_DEFAULT", **arguments)


def bake_image(arguments: BakeImageArguments) -> BakeImageResult:
    started = time.monotonic()
    if bpy.data.images.get(arguments.name) is not None:
        raise OperationError(
            "image_exists",
            "Choose a new image name; existing maps are never overwritten by baking",
        )
    backend, devices = settings()
    if arguments.device == "gpu" and (
        backend == "NONE" or not any(not d.startswith("CPU:") for d in devices)
    ):
        raise OperationError(
            "bake_device_unavailable",
            "No enabled Cycles GPU; configure the host or explicitly request CPU",
        )
    image = scene = material = None
    proxies = []
    success = False
    timings = []
    try:
        with ExitStack() as resources:
            meshes = resources.enter_context(snapshots(arguments.targets))
            surfaces = [
                uv_layout.surface(
                    uv.mesh_object(t.target), meshes[t.target][0], t.uv_map
                )
                for t in arguments.targets
            ]
            report, _ = uv_quality.analyze(surfaces, arguments.resolution, True)
            if (
                report.overlap_pair_count
                or report.degenerate_face_count
                or report.out_of_unit_face_count
                or report.flipped_face_count
            ):
                raise OperationError(
                    "bake_uv_invalid",
                    (
                        "Bake atlas must be non-overlapping, positive, nondegenerate "
                        "and inside tile 1001"
                    ),
                )
            if arguments.margin and (
                (
                    report.minimum_island_gap_pixels is not None
                    and report.minimum_island_gap_pixels < arguments.margin * 2
                )
                or (
                    report.minimum_tile_border_pixels is not None
                    and report.minimum_tile_border_pixels < arguments.margin
                )
            ):
                raise OperationError(
                    "bake_margin_invalid",
                    "Requested dilation exceeds inspected atlas clearance",
                )
            image = bpy.data.images.new(
                arguments.name,
                width=arguments.resolution,
                height=arguments.resolution,
                alpha=True,
                float_buffer=True,
            )
            image.colorspace_settings.name = "Non-Color"
            image.generated_color = (0.5, 0.5, 1, 0)
            material = bpy.data.materials.new("Bake target temporary")
            resources.callback(bpy.data.materials.remove, material)
            node = material.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            material.node_tree.nodes.active = node
            scene = bpy.data.scenes.new("Bake temporary scene")
            resources.callback(bpy.data.scenes.remove, scene)
            scene.render.engine = "CYCLES"
            scene.cycles.device = arguments.device.upper()
            scene.cycles.samples = arguments.samples
            scene.cycles.use_denoising = False
            for name, (mesh, matrix) in meshes.items():
                obj = bpy.data.objects.new("Bake temporary " + name, mesh)
                proxies.append(obj)
                resources.callback(bpy.data.objects.remove, obj, do_unlink=True)
                obj.matrix_world = matrix
                scene.collection.objects.link(obj)
            by_name = dict(zip(meshes, proxies, strict=True))
            layer = scene.view_layers[0]
            with bpy.context.temp_override(scene=scene, view_layer=layer):
                for t in arguments.targets:
                    for obj in proxies:
                        obj.select_set(False, view_layer=layer)
                        obj.hide_render = True
                    target = by_name[t.target]
                    target.data.materials.clear()
                    target.data.materials.append(material)
                    for f in target.data.polygons:
                        f.material_index = 0
                    target.data.uv_layers.active = target.data.uv_layers[t.uv_map]
                    target.data.uv_layers[t.uv_map].active_render = True
                    for obj in [target, *[by_name[n] for n in t.sources]]:
                        obj.hide_render = False
                        obj.select_set(True, view_layer=layer)
                    layer.objects.active = target
                    layer.update()
                    begin = time.monotonic()
                    selected = [target, *[by_name[n] for n in t.sources]]
                    with bpy.context.temp_override(
                        active_object=target,
                        object=target,
                        selected_objects=selected,
                        selected_editable_objects=selected,
                    ):
                        outcome = execute_native(
                            type="NORMAL",
                            normal_space="TANGENT",
                            normal_r="POS_X",
                            normal_g="POS_Y",
                            normal_b="POS_Z",
                            use_selected_to_active=True,
                            use_clear=False,
                            use_cage=t.use_cage,
                            cage_extrusion=t.cage_extrusion,
                            max_ray_distance=t.max_ray_distance,
                            cage_object="",
                            uv_layer=t.uv_map,
                            margin=arguments.margin,
                            margin_type=arguments.margin_type.upper(),
                            target="IMAGE_TEXTURES",
                            save_mode="INTERNAL",
                        )
                    timings.append(
                        BakeTiming(target=t.target, seconds=time.monotonic() - begin)
                    )
                    if "FINISHED" not in outcome:
                        raise OperationError(
                            "bake_failed",
                            "Native selected-to-active bake did not finish",
                        )
            # Retain the image; all scene snapshots are temporary.
            qa = image_qa(image.name, arguments.targets, meshes)
            image.use_fake_user = True
            result = BakeImageResult(
                image=image.name,
                resolution=arguments.resolution,
                normal_convention="OpenGL +X +Y +Z",
                target_evaluation=EVALUATION,
                device=arguments.device,
                compute_backend=backend,
                enabled_devices=devices,
                bake_seconds=sum(t.seconds for t in timings),
                total_seconds=time.monotonic() - started,
                targets=timings,
                qa=qa,
            )
            success = True
            return result
    finally:
        if image is not None and not success:
            bpy.data.images.remove(image)
