"""Restored native Workbench contact sheets with automatic regional framing."""

import time
from collections.abc import Callable, Generator
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from . import organization, render_output
from .artifacts import ArtifactSpool, artifact_path
from .errors import OperationError
from .models import InspectionTile, RenderArguments, RenderResult

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
    choices = options.objects or [
        o.name
        for o in scene.objects
        if o.type in {"MESH", "CURVE", "SURFACE", "FONT"} and not o.hide_render
    ]
    if not choices or len(choices) > 128:
        raise OperationError(
            "inspection_limit", "Select1..128 renderable objects for inspection"
        )
    view_objects = [
        [organization.object_named(n) for n in view.objects or choices]
        for view in options.views
    ]
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
    columns = min(options.columns, len(options.views))
    rows = (len(options.views) + columns - 1) // columns
    width, height = arguments.width * columns, arguments.height * rows
    pixels = np.zeros((height, width, 4), dtype=np.float32)
    pixels[:, :, 3] = 1
    shading = scene.display.shading
    settings: list[tuple[Any, str, Any]] = [
        (scene.render, "engine", "BLENDER_WORKBENCH"),
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
    try:
        scene.collection.objects.link(camera)
        scene.camera = camera
        camera_data.type = "ORTHO"
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
            center = (low + high) / 2
            size = (high - low).length
            if size < 1e-6:
                raise OperationError("inspection_invalid", "Inspection bounds collapse")
            eye = Vector(EYES[view.orientation]).normalized()
            camera.location = center + eye * size * 2
            camera.rotation_euler = (-eye).to_track_quat("-Z", "Y").to_euler()
            rotation = camera.rotation_euler.to_matrix().transposed()
            projected = [rotation @ (p - center) for p in points]
            extents = [
                max(p[k] for p in projected) - min(p[k] for p in projected)
                for k in range(2)
            ]
            camera_data.ortho_scale = max(
                extents[1], extents[0] * arguments.height / arguments.width
            ) * (1 + 2 * options.margin)
            camera_data.clip_start = max(size * 0.0001, 0.00001)
            camera_data.clip_end = size * 5
            bpy.context.view_layer.update()
            result, artifact = yield from render_steps(
                arguments.model_copy(update={"inspection": None, "output": None}),
                spool,
                observe=observe,
                interactive=interactive,
                checkpoint=checkpoint,
            )
            path = artifact_path(spool.root, artifact)
            loaded = None
            try:
                loaded = bpy.data.images.load(str(path), check_existing=False)
                tile = np.empty(
                    arguments.width * arguments.height * 4, dtype=np.float32
                )
                loaded.pixels.foreach_get(tile)
                x = index % columns * arguments.width
                y = (rows - 1 - index // columns) * arguments.height
                pixels[y : y + arguments.height, x : x + arguments.width] = (
                    tile.reshape((arguments.height, arguments.width, 4))
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
        return RenderResult(
            width=width,
            height=height,
            color_mode=arguments.color_mode,
            color_management=result.color_management,
            inspection_tiles=tiles,
        ), descriptor
    finally:
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
