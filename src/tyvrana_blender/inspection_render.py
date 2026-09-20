"""Restored native Workbench contact sheets with automatic regional framing."""

import re
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from . import organization, render_output
from .artifacts import ArtifactSpool, artifact_path
from .errors import OperationError
from .models import (
    InspectionTile,
    RenderArguments,
    RenderResult,
    RenderViewProjection,
    WireframeRegion,
    WireframeRenderOptions,
)
from .review_packet import Packet

EYES = {
    "left": (-1, 0, 0),
    "right": (1, 0, 0),
    "front": (0, 1, 0),
    "rear": (0, -1, 0),
    "top": (0, 0, 1),
    "bottom": (0, 0, -1),
    "oblique": (1, -1.4, 0.9),
    "reverse_oblique": (-1, 1.4, -0.8),
}


def steps(
    arguments: RenderArguments,
    spool: ArtifactSpool,
    *,
    observe: Callable[[str, float], None] | None,
    interactive: bool,
    checkpoint: Callable[[], None] | None,
) -> Generator[None, None, tuple[RenderResult, ArtifactDescriptor]]:
    from .render import render_steps

    options = arguments.inspection
    assert options is not None
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    needs_default = any(not view.objects for view in options.views)
    choices = options.objects or (
        [
            o.name
            for o in scene.objects
            if o.type in {"MESH", "CURVE", "SURFACE", "FONT"} and not o.hide_render
        ]
        if needs_default
        else []
    )
    if needs_default and (not choices or len(choices) > options.max_objects):
        raise OperationError(
            "inspection_limit",
            f"Select1..{options.max_objects} renderable objects for inspection",
        )
    view_objects = [
        [organization.object_named(n) for n in view.objects or choices]
        for view in options.views
    ]
    if any(
        not objects or len(objects) > options.max_objects for objects in view_objects
    ):
        raise OperationError(
            "inspection_limit", "Per-view selection exceeds max_objects"
        )
    all_objects = {o for objects in view_objects for o in objects}
    if any(o.type not in {"MESH", "CURVE", "SURFACE", "FONT"} for o in all_objects):
        raise OperationError(
            "inspection_invalid", "Inspection requires renderable surface objects"
        )
    if any(o.name not in bpy.context.view_layer.objects for o in all_objects):
        raise OperationError(
            "inspection_invalid",
            "Requested object is excluded from the current view layer",
        )
    if any(
        view.wireframe and (len(objects) > 32 or any(o.type != "MESH" for o in objects))
        for view, objects in zip(options.views, view_objects, strict=True)
    ):
        raise OperationError(
            "inspection_limit", "Wireframe views require1..32 selected meshes"
        )
    columns = min(options.columns, len(options.views))
    rows = (len(options.views) + columns - 1) // columns
    scale = (
        min(1, options.packet.preview_size / max(arguments.width, arguments.height))
        if options.packet
        else 1
    )
    tile_width, tile_height = (
        max(1, round(arguments.width * scale)),
        max(1, round(arguments.height * scale)),
    )
    width, height = tile_width * columns, tile_height * rows
    pixels = np.zeros((height, width, 4), dtype=np.float32)
    pixels[:, :, 3] = 1
    shading = scene.display.shading
    settings: list[tuple[Any, str, Any]] = [
        (scene.render, "engine", "BLENDER_WORKBENCH"),
        (scene.render, "resolution_x", arguments.width),
        (scene.render, "resolution_y", arguments.height),
        (scene.render, "resolution_percentage", 100),
        (scene.render, "pixel_aspect_x", 1),
        (scene.render, "pixel_aspect_y", 1),
        (scene.render, "film_transparent", False),
        (scene.render, "use_compositing", False),
        (scene.view_settings, "view_transform", "Standard"),
        (scene.view_settings, "look", "None"),
        (scene.view_settings, "exposure", 0),
        (scene.view_settings, "gamma", 1),
        (shading, "light", "STUDIO"),
        (shading, "color_type", "SINGLE"),
        (shading, "single_color", (0.65, 0.69, 0.75)),
        (shading, "background_type", "VIEWPORT"),
        (shading, "background_color", (0.035, 0.045, 0.055)),
        (shading, "show_shadows", True),
        (shading, "show_cavity", True),
        (shading, "cavity_type", "BOTH"),
        (shading, "show_specular_highlight", True),
    ]
    saved = [
        (
            obj,
            key,
            tuple(getattr(obj, key)) if key.endswith("color") else getattr(obj, key),
        )
        for obj, key, _ in settings
    ]
    visibility = [(o, o.hide_render) for o in scene.objects]
    collections = [(c, c.hide_render) for c in organization.collections()]
    camera_before = scene.camera
    camera_data = bpy.data.cameras.new("InspectionCamera")
    camera = bpy.data.objects.new("InspectionCamera", camera_data)
    sheet = None
    started = time.monotonic()
    tiles = []
    packet = None
    try:
        if options.packet:
            packet = Packet(
                Path(bpy.path.abspath(options.packet.directory)),
                overwrite=options.packet.overwrite,
                maximum=arguments.budget.max_artifact_bytes,
            )
        scene.collection.objects.link(camera)
        scene.camera = camera
        camera_data.sensor_fit = "HORIZONTAL"
        for obj, key, value in settings:
            setattr(obj, key, value)
        for collection, _ in collections:
            collection.hide_render = False
        for index, (view, objects) in enumerate(
            zip(options.views, view_objects, strict=True)
        ):
            if checkpoint:
                checkpoint()
            if time.monotonic() - started > arguments.budget.max_seconds:
                raise OperationError(
                    "render_budget_exceeded",
                    "Inspection deadline exceeded between views",
                )
            selected = set(objects)
            for obj, _ in visibility:
                obj.hide_render = obj not in selected
            bpy.context.view_layer.update()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            points = [
                o.matrix_world @ Vector(c)
                for obj in objects
                for o in [obj.evaluated_get(depsgraph)]
                for c in o.bound_box
            ]
            low = Vector([min(p[k] for p in points) for k in range(3)])
            high = Vector([max(p[k] for p in points) for k in range(3)])
            full_size = (high - low).length
            if view.focus:
                span = high - low
                region_low = Vector(
                    [low[k] + span[k] * view.focus.minimum[k] for k in range(3)]
                )
                region_high = Vector(
                    [low[k] + span[k] * view.focus.maximum[k] for k in range(3)]
                )
                points = [
                    Vector((x, y, z))
                    for x in (region_low.x, region_high.x)
                    for y in (region_low.y, region_high.y)
                    for z in (region_low.z, region_high.z)
                ]
                low, high = region_low, region_high
            center = (low + high) / 2
            size = (high - low).length
            if size < 1e-6:
                raise OperationError("inspection_invalid", "Inspection bounds collapse")
            eye = Vector(EYES[view.orientation]).normalized()
            camera_data.type = "ORTHO" if view.projection == "orthographic" else "PERSP"
            camera.location = center + eye * full_size * 2
            camera.rotation_euler = (-eye).to_track_quat("-Z", "Y").to_euler()
            rotation = camera.rotation_euler.to_matrix().transposed()
            projected = [rotation @ (p - center) for p in points]
            extents = [
                max(p[k] for p in projected) - min(p[k] for p in projected)
                for k in range(2)
            ]
            camera_data.ortho_scale = max(
                extents[0], extents[1] * arguments.width / arguments.height
            ) * (1 + 2 * options.margin)
            if view.projection == "perspective":
                frame = camera_data.view_frame(scene=scene)
                sx = max(abs(p.x / p.z) for p in frame)
                sy = max(abs(p.y / p.z) for p in frame)
                distance = max(
                    max(abs(p.x) / sx, abs(p.y) / sy) * (1 + 2 * options.margin) + p.z
                    for p in projected
                )
                camera.location = center + eye * max(distance, size * 0.1)
            camera_data.clip_start = max(size * 0.0001, 0.00001)
            camera_data.clip_end = (camera.location - center).length + full_size * 2
            bpy.context.view_layer.update()
            wire = (
                WireframeRenderOptions(
                    objects=[o.name for o in objects],
                    thickness=min(1, max(1e-6, size * 0.0008)),
                    max_edges=view.wire_edge_limit,
                    region=(
                        WireframeRegion(minimum=list(low), maximum=list(high))
                        if view.focus
                        else None
                    ),
                )
                if view.wireframe
                else None
            )
            if view.wireframe:
                shading.single_color = (0.95, 0.95, 0.95)
            result, artifact = yield from render_steps(
                arguments.model_copy(
                    update={"inspection": None, "output": None, "wireframe": wire}
                ),
                spool,
                observe=observe,
                interactive=interactive,
                checkpoint=checkpoint,
            )
            path = artifact_path(spool.root, artifact)
            shading.single_color = (0.65, 0.69, 0.75)
            loaded = None
            filename = None
            framing = RenderViewProjection(
                width=arguments.width,
                height=arguments.height,
                camera_world=[list(row) for row in camera.matrix_world],
                projection_matrix=[
                    list(row)
                    for row in camera.calc_matrix_camera(
                        depsgraph, x=arguments.width, y=arguments.height
                    )
                ],
            )
            try:
                if packet:
                    slug = (
                        re.sub(r"[^a-zA-Z0-9_-]+", "_", view.name).strip("_") or "view"
                    )
                    filename = packet.add(
                        path,
                        f"{index + 1:02}_{slug}.png",
                        name=view.name,
                        orientation=view.orientation,
                        projection=view.projection,
                        wireframe=view.wireframe,
                        focus=view.focus.model_dump() if view.focus else None,
                        objects=[o.name for o in objects],
                        width=arguments.width,
                        height=arguments.height,
                        camera_world=framing.camera_world,
                        projection_matrix=framing.projection_matrix,
                    )
                loaded = bpy.data.images.load(str(path), check_existing=False)
                if (tile_width, tile_height) != (arguments.width, arguments.height):
                    loaded.scale(tile_width, tile_height)
                tile = np.empty(tile_width * tile_height * 4, dtype=np.float32)
                loaded.pixels.foreach_get(tile)
                rgb = tile.reshape((-1, 4))[:, :3]
                rgb[:] = np.where(
                    rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4
                )
                x = index % columns * tile_width
                y = (rows - 1 - index // columns) * tile_height
                pixels[y : y + tile_height, x : x + tile_width] = tile.reshape(
                    (tile_height, tile_width, 4)
                )
            finally:
                if loaded is not None:
                    bpy.data.images.remove(loaded)
                path.unlink(missing_ok=True)
            tiles.append(
                InspectionTile(
                    name=view.name,
                    orientation=view.orientation,
                    column=index % columns,
                    row=index // columns,
                    object_count=len(objects),
                    projection=view.projection,
                    wireframe=view.wireframe,
                    filename=filename,
                    view=framing,
                )
            )
            if interactive:
                yield
        sheet = bpy.data.images.new(
            "InspectionSheet", width=width, height=height, alpha=True, float_buffer=True
        )
        sheet.pixels.foreach_set(pixels.ravel())
        sheet.update()
        with spool.reserve("image/png") as (artifact_id, path):
            with render_output.output_settings(
                scene, bpy.context.view_layer, arguments
            ):
                sheet.save_render(filepath=str(path), scene=scene)
            descriptor = spool.describe(
                artifact_id,
                maximum=arguments.budget.max_artifact_bytes,
                name="inspection.png",
            )
            if packet:
                packet.add(
                    path,
                    "00_overview.png",
                    width=width,
                    height=height,
                    tiles=[tile.model_dump() for tile in tiles],
                )
                packet.publish()
        return RenderResult(
            width=width,
            height=height,
            color_mode=arguments.color_mode,
            color_management=result.color_management,
            inspection_tiles=tiles,
            review_directory=str(packet.directory) if packet else None,
        ), descriptor
    finally:
        if packet:
            packet.close()
        scene.camera = camera_before
        for obj, key, value in saved:
            setattr(obj, key, value)
        for obj, hidden in visibility:
            obj.hide_render = hidden
        for collection, hidden in collections:
            collection.hide_render = hidden
        if sheet is not None:
            bpy.data.images.remove(sheet)
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(camera_data)
        bpy.context.view_layer.update()
