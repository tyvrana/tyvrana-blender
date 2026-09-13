"""Native sculpt execution with explicit surface targets and temporary UI state."""

import hashlib
import logging
import math
import shutil
import tempfile
from array import array
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
from bpy_extras import view3d_utils  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from . import modifiers, multires
from .operations import OperationError
from .sculpt_models import (
    SculptStrokeArguments,
    SculptStrokeResult,
    SculptSummary,
    Symmetry,
)

logger = logging.getLogger(__name__)
ASSETS = {
    "draw": ("Draw", "DRAW"),
    "smooth": ("Smooth", "SMOOTH"),
    "inflate": ("Inflate/Deflate", "INFLATE"),
    "clay": ("Clay", "CLAY"),
    "crease": ("Crease Polish", "CREASE"),
    "flatten": ("Flatten/Contrast", "PLANE"),
}


def view_context() -> tuple[Any, Any, Any] | None:
    context = bpy.context
    if bpy.app.background or context.window is None:
        return None
    for area in context.window.screen.areas:
        if area.type == "VIEW_3D" and len(area.spaces.active.region_quadviews) == 0:
            for region in area.regions:
                if region.type == "WINDOW" and min(region.width, region.height) >= 64:
                    return area, region, area.spaces.active.region_3d
    return None


def symmetry(obj: Any) -> Symmetry:
    return Symmetry(**{axis: getattr(obj.data, "use_mirror_" + axis) for axis in "xyz"})


def inspect(obj: Any) -> SculptSummary:
    state = multires.inspect(obj)
    mod = multires.find(obj, required=False)
    level = int(mod.sculpt_levels) if mod and not mod.use_sculpt_base_mesh else 0
    count = (
        len(obj.data.loops) * (2 ** (level - 1) + 1) ** 2
        if level
        else len(obj.data.vertices)
    )
    return SculptSummary(
        object_name=obj.name,
        object_mode=str(obj.mode).lower(),
        object_scale=list(obj.scale),
        scale_applied=multires.unit_scale(obj),
        multires=state,
        effective_sculpt_level=level,
        sculpt_vertex_count=count,
        symmetry=symmetry(obj),
        view3d_available=view_context() is not None,
        mask_present=obj.data.attributes.get(".sculpt_mask") is not None,
        hidden_geometry=any(v.hide for v in obj.data.vertices)
        or any(f.hide for f in obj.data.polygons),
    )


def surface(obj: Any) -> tuple[Any, list[float], list[float], str]:
    """Snapshot the current level, releasing the evaluated Mesh on every path."""
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    try:
        data = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=graph)
        if data is None or not data.polygons:
            raise OperationError(
                "invalid_context", "Sculpting requires a polygonal surface"
            )
        modifiers.check_geometry(data)
        coordinates = array("f", [0.0]) * (3 * len(data.vertices))
        data.vertices.foreach_get("co", coordinates)
        vertices = [
            tuple(coordinates[i : i + 3]) for i in range(0, len(coordinates), 3)
        ]
        minimum = [min(v[i] for v in vertices) for i in range(3)]
        maximum = [max(v[i] for v in vertices) for i in range(3)]
        tree = BVHTree.FromPolygons(
            vertices, [tuple(f.vertices) for f in data.polygons]
        )
        return tree, minimum, maximum, hashlib.sha256(coordinates.tobytes()).hexdigest()
    finally:
        evaluated.to_mesh_clear()


def brush_asset(kind: str) -> Any:
    path = (
        Path(bpy.utils.system_resource("DATAFILES"))
        / "assets/brushes/essentials_brushes-mesh_sculpt.blend"
    )
    if not path.is_file():
        raise OperationError(
            "invalid_context", "Bundled Essentials sculpt brush library is unavailable"
        )
    name, native_type = ASSETS[kind]
    # Append fresh local data; never edit or save the user's active asset.
    # Blender reuses already-linked IDs when appending from the same library.
    # Read through a private temporary copy to obtain pristine Essentials data,
    # even if the user has unsaved changes to an active built-in brush.
    with tempfile.TemporaryDirectory(prefix="tyvrana-sculpt-brush-") as directory:
        temporary = Path(directory) / "brush.blend"
        shutil.copyfile(path, temporary)
        with bpy.data.libraries.load(str(temporary), link=False, assets_only=True) as (
            available,
            loaded,
        ):
            if name not in available.brushes:
                raise OperationError(
                    "invalid_context", "Required Essentials brush is unavailable"
                )
            loaded.brushes = [name]
    brush = loaded.brushes[0]
    if brush is None:
        raise OperationError("invalid_context", "Blender did not load the sculpt brush")
    if brush.sculpt_brush_type != native_type:
        bpy.data.brushes.remove(brush)
        raise OperationError(
            "invalid_context",
            "Essentials brush type does not match its expected behavior",
        )
    brush.name = "Temporary Sculpt Brush"
    brush.asset_mark()
    return brush


def prepare_brush(brush: Any, arguments: SculptStrokeArguments) -> None:
    brush.use_locked_size = "SCENE"
    brush.unprojected_size = arguments.radius * 2
    brush.strength = arguments.strength
    brush.use_pressure_size = False
    brush.use_pressure_strength = True
    brush.stroke_method = "DOTS"
    brush.use_space_attenuation = False
    brush.use_accumulate = True
    brush.sculpt_plane = "AREA"
    brush.falloff_shape = "SPHERE"
    brush.use_frontface = False
    brush.jitter = 0
    brush.use_smooth_stroke = False
    brush.texture = None
    brush.mask_texture = None
    for prop in brush.mesh_automasking_settings.bl_rna.properties:
        if prop.type == "BOOLEAN" and not prop.is_readonly:
            setattr(brush.mesh_automasking_settings, prop.identifier, False)


def stroke(obj: Any, arguments: SculptStrokeArguments) -> SculptStrokeResult:
    context = bpy.context
    view = view_context()
    if view is None:
        raise OperationError(
            "invalid_context",
            "Sculpt strokes require an interactive Blender window with a View3D region",
        )
    if context.mode not in {"OBJECT", "SCULPT"} or (
        context.mode == "SCULPT" and context.object != obj
    ):
        raise OperationError(
            "invalid_context", "Finish other editing modes before sculpting this object"
        )
    if not multires.unit_scale(obj):
        raise OperationError(
            "sculpt_unapplied_scale",
            "Sculpt strokes require unit object and inherited scale without shear",
        )
    mod = multires.find(obj, required=False)
    if (
        bpy.app.is_job_running("RENDER")
        or not context.scene.is_editable
        or not obj.is_editable
        or not obj.data.is_editable
        or obj.library
        or obj.override_library
        or obj.data.library
        or obj.data.override_library
        or obj.animation_data
        or obj.data.animation_data
        or obj.constraints
        or obj.data.has_custom_normals
        or obj.use_dynamic_topology_sculpting
        or obj.name not in context.view_layer.objects
        or not obj.visible_get()
        or obj.hide_select
    ):
        raise OperationError(
            "invalid_context",
            "Sculpting requires a visible local editable mesh without animation, "
            "constraints, custom normals or Dyntopo",
        )
    if obj.data.shape_keys is not None:
        raise OperationError(
            "mesh_has_shape_keys", "Shape keys require a dedicated sculpt workflow"
        )
    if any(m != mod for m in obj.modifiers):
        raise OperationError(
            "invalid_context",
            "Sculpt strokes require an otherwise empty modifier stack",
        )
    if mod and context.scene.render.use_simplify:
        raise OperationError(
            "invalid_context",
            "Disable scene Simplify before Multires strokes so evaluated targets "
            "match the sculpt level",
        )
    if mod and (
        mod.is_external
        or mod.use_sculpt_base_mesh
        or not mod.show_viewport
        or mod.sculpt_levels == 0
    ):
        raise OperationError(
            "invalid_context",
            "Enable internal Multires displacement at a positive sculpt level; "
            "Sculpt Base Mesh is not supported",
        )
    level = int(mod.sculpt_levels) if mod else 0
    multires.budget(obj, int(mod.total_levels) if mod else 0)
    if sum(o.data == obj.data for o in bpy.data.objects if o.type == "MESH") > 1:
        raise OperationError(
            "invalid_context",
            "Make the sculpt Mesh single-user before strokes; Multires "
            "subdivision isolates shared meshes",
        )
    vertices = (
        len(obj.data.loops) * (2 ** (level - 1) + 1) ** 2
        if level
        else len(obj.data.vertices)
    )
    passes = 2 ** sum(arguments.symmetry.model_dump().values())
    if vertices * len(arguments.samples) * passes > 64_000_000:
        raise OperationError(
            "invalid_context",
            "Stroke exceeds the bounded sculpt work limit; reduce samples "
            "or sculpt level",
        )
    area, region, rv = view
    window = context.window
    original_scene = context.scene
    original_layer = context.view_layer
    original_active = original_layer.objects.active
    original_mode = obj.mode
    original_brushes = {item.as_pointer() for item in bpy.data.brushes}
    original_libraries = {item.as_pointer() for item in bpy.data.libraries}
    selected = [(o, o.select_get()) for o in original_layer.objects]
    prior_symmetry = symmetry(obj)
    radial = tuple(obj.data.radial_symmetry)
    view_values = {
        key: getattr(rv, key).copy()
        if key in {"view_rotation", "view_location"}
        else getattr(rv, key)
        for key in (
            "view_rotation",
            "view_location",
            "view_distance",
            "view_perspective",
            "view_camera_zoom",
            "view_camera_offset",
        )
    }
    view_values["view_camera_offset"] = tuple(rv.view_camera_offset)
    prior_tool = context.workspace.tools.from_space_view3d_mode("SCULPT", create=False)
    tool_id = prior_tool.idname if prior_tool else "builtin.brush"
    viewport_level = mod.levels if mod else None
    temporary_scene = None
    brush = None
    started = False
    tool_restored = False
    try:
        if original_mode == "SCULPT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if mod:
            mod.levels = level
        context.view_layer.update()
        tree, minimum, maximum, before_hash = surface(obj)
        locations, normals, distances = [], [], []
        for i, sample in enumerate(arguments.samples):
            point, normal, _, distance = tree.find_nearest(Vector(sample.location))
            if point is None or distance > arguments.radius:
                raise OperationError(
                    "invalid_arguments",
                    "Stroke sample is farther than the brush radius from the "
                    "sculpt surface",
                    {"sample_index": i},
                )
            locations.append(list(point))
            normals.append(normal)
            distances.append(distance)
        if arguments.brush == "flatten" and not any(
            (Vector(location) - Vector(locations[0])).cross(normals[0]).length
            > arguments.radius * 1e-6
            for location in locations[1:]
        ):
            raise OperationError(
                "invalid_arguments", "Flatten requires motion along the sculpt surface"
            )
        brush = brush_asset(arguments.brush)
        prepare_brush(brush, arguments)
        # Paint.brush is read-only in Blender 5.2. A temporary scene owns the
        # brush reference and tool settings, including the initially-unset case.
        # Its objects and Mesh data remain the actual target, not a sculpt substitute.
        temporary_scene = original_scene.copy()
        window.scene = temporary_scene
        layer = temporary_scene.view_layers[original_layer.name]
        window.view_layer = layer
        with context.temp_override(window=window, area=area, region=region):
            for item in layer.objects:
                item.select_set(False)
            obj.select_set(True)
            layer.objects.active = obj
            bpy.ops.object.mode_set(mode="SCULPT")
            paint = context.tool_settings.sculpt
            paint.unified_paint_settings.use_unified_size = False
            paint.unified_paint_settings.use_unified_strength = False
            paint.gravity = 0
            paint.use_symmetry_feather = True
            for axis in "xyz":
                setattr(paint, "lock_" + axis, False)
                setattr(paint, "tile_" + axis, False)
                setattr(
                    obj.data, "use_mirror_" + axis, getattr(arguments.symmetry, axis)
                )
            obj.data.radial_symmetry = (1, 1, 1)
            for prop in paint.mesh_automasking_settings.bl_rna.properties:
                if prop.type == "BOOLEAN" and not prop.is_readonly:
                    setattr(paint.mesh_automasking_settings, prop.identifier, False)
            outcome = bpy.ops.brush.asset_activate(
                asset_library_type="LOCAL",
                relative_asset_identifier="Brush/" + brush.name,
            )
            if "FINISHED" not in outcome or paint.brush != brush:
                raise OperationError(
                    "invalid_context",
                    "Blender could not activate the temporary sculpt brush",
                )
            # Supply a real viewport hit to initialize SculptSession. The native
            # operator then consumes our object-local locations without ray override.
            normal_world = (obj.matrix_world.to_3x3() @ normals[0]).normalized()
            rv.view_perspective = "ORTHO"
            rv.view_rotation = normal_world.to_track_quat("Z", "Y")
            rv.view_location = obj.matrix_world @ Vector(locations[0])
            rv.view_distance = max(
                math.dist(minimum, maximum) * 2, arguments.radius * 4, 0.01
            )
            rv.update()
            projected = [
                view3d_utils.location_3d_to_region_2d(
                    region, rv, obj.matrix_world @ Vector(location)
                )
                for location in locations
            ]
            if any(mouse is None for mouse in projected):
                raise OperationError(
                    "invalid_context", "Could not initialize sculpt surface view"
                )
            elements = [
                dict(
                    name="",
                    location=location,
                    mouse=projected[i],
                    mouse_event=projected[i],
                    pressure=sample.pressure,
                    size=brush.size / 2,
                    x_tilt=0,
                    y_tilt=0,
                    time=i / 60,
                    is_start=i == 0,
                )
                for i, (location, sample) in enumerate(
                    zip(locations, arguments.samples, strict=True)
                )
            ]
            started = True
            outcome = bpy.ops.sculpt.brush_stroke(
                "EXEC_DEFAULT",
                stroke=elements,
                override_location=False,
                mode="INVERT" if arguments.invert else "NORMAL",
            )
            if "FINISHED" not in outcome:
                raise RuntimeError("Native sculpt stroke did not finish")
            bpy.ops.wm.tool_set_by_id(name=tool_id, space_type="VIEW_3D")
            tool_restored = True
            bpy.ops.object.mode_set(mode="OBJECT")  # Flush Multires displacement.
        window.scene = original_scene
        window.view_layer = original_layer
        context.view_layer.update()
        _, after_minimum, after_maximum, after_hash = surface(obj)
        return SculptStrokeResult(
            object_name=obj.name,
            brush=arguments.brush,
            sample_count=len(locations),
            radius=arguments.radius,
            strength=arguments.strength,
            invert=arguments.invert,
            symmetry=arguments.symmetry,
            multires_level=level,
            snapped_locations=locations,
            max_snap_distance=max(distances),
            bounds_before_min=minimum,
            bounds_before_max=maximum,
            bounds_after_min=after_minimum,
            bounds_after_max=after_maximum,
            changed=before_hash != after_hash,
        )
    except Exception as exc:
        if not started and isinstance(exc, OperationError):
            raise
        logger.exception(
            "Sculpt stroke failed%s",
            " after native execution began" if started else " during setup",
        )
        raise OperationError(
            "sculpt_failed",
            "Sculpt stroke failed; inspect the surface before retrying",
            {"mutation_possible": started},
        ) from exc
    finally:
        with context.temp_override(window=window, area=area, region=region):
            if obj.mode == "SCULPT":
                if temporary_scene is not None and not tool_restored:
                    bpy.ops.wm.tool_set_by_id(name=tool_id, space_type="VIEW_3D")
                bpy.ops.object.mode_set(mode="OBJECT")
            # Restore the workspace's Sculpt tool while the temporary scene still
            # owns brush selection. No user brush datablock was modified.
            window.scene = original_scene
            window.view_layer = original_layer
            for axis in "xyz":
                setattr(obj.data, "use_mirror_" + axis, getattr(prior_symmetry, axis))
            obj.data.radial_symmetry = radial
            if mod:
                mod.levels = viewport_level
            for item, value in selected:
                item.select_set(value)
            original_layer.objects.active = original_active
            if original_mode == "SCULPT":
                bpy.ops.object.mode_set(mode="SCULPT")
            for key, value in view_values.items():
                setattr(rv, key, value)
            rv.update()
        if temporary_scene is not None:
            bpy.data.scenes.remove(temporary_scene)
        # Entering Sculpt Mode can also load a default local brush. Both that
        # brush and the explicitly appended brush belong to this operation.
        for item in list(bpy.data.brushes):
            if item.as_pointer() not in original_brushes:
                bpy.data.brushes.remove(item)
        for library in list(bpy.data.libraries):
            if library.as_pointer() not in original_libraries:
                bpy.data.libraries.remove(library)
