"""Main-thread evaluated scene picking using Blender camera projection matrices."""

import math
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

from . import modifiers
from .errors import OperationError
from .sculpt_models import CameraRayArguments, RaycastArguments, RaycastResult


def unit(value: Any) -> Any:
    length = math.hypot(*value)
    if not math.isfinite(length) or length == 0:
        raise OperationError("invalid_context", "Ray or surface normal is degenerate")
    return Vector([component / length for component in value])


def graph() -> Any:
    context = bpy.context
    if context.mode not in {"OBJECT", "SCULPT"} or bpy.app.is_job_running("RENDER"):
        raise OperationError(
            "invalid_context",
            "Picking requires Object or Sculpt Mode outside rendering",
        )
    if len(context.view_layer.objects) > 256:
        raise OperationError("invalid_context", "Picking exceeds the 256-object limit")
    # A scene raycast evaluates the view layer, not just the eventual hit object.
    for obj in context.view_layer.objects:
        if obj.type == "MESH" and obj.visible_get():
            modifiers.check_dependencies(obj, None, {})
            modifiers.budget(obj, modifiers.stack(obj), strict=True)
    return context.evaluated_depsgraph_get()


def camera_ray(
    arguments: CameraRayArguments, depsgraph: Any
) -> tuple[Any, Any, float, float]:
    scene = bpy.context.scene
    camera = (
        modifiers.scene_object(arguments.camera_name)
        if arguments.camera_name is not None
        else scene.camera
    )
    if camera is None:
        raise OperationError("no_camera", "The current scene has no camera")
    if camera.type != "CAMERA":
        raise OperationError("object_not_camera", "The named object is not a camera")
    evaluated = camera.evaluated_get(depsgraph)
    data = evaluated.data
    if data.type not in {"PERSP", "ORTHO"}:
        raise OperationError(
            "unsupported_projection",
            "Picking supports perspective and orthographic cameras",
        )
    render = scene.render
    width = arguments.width or render.resolution_x
    height = arguments.height or render.resolution_y
    projection = evaluated.calc_matrix_camera(
        depsgraph,
        x=width,
        y=height,
        scale_x=render.pixel_aspect_x,
        scale_y=render.pixel_aspect_y,
    )
    try:
        inverse = projection.inverted()
        matrix = evaluated.matrix_world.normalized()
        matrix.inverted()  # Reject singular camera transforms before projecting.
    except ValueError as exc:
        raise OperationError("invalid_context", "Camera transform is singular") from exc
    near = inverse @ Vector((2 * arguments.u - 1, 1 - 2 * arguments.v, -1, 1))
    if near.w == 0:
        raise OperationError("invalid_context", "Camera projection is degenerate")
    point = near.xyz / near.w
    if data.type == "PERSP":
        origin = matrix.translation.copy()
        direction = unit(matrix.to_3x3() @ unit(point))
        factor = math.hypot(*point) / abs(point.z)
    else:
        origin = matrix @ Vector((point.x, point.y, 0))
        direction = unit(matrix.to_3x3() @ Vector((0, 0, -1)))
        factor = 1.0
    return origin, direction, data.clip_start * factor, data.clip_end * factor


def cast(arguments: RaycastArguments) -> RaycastResult:
    depsgraph = graph()
    offset = 0.0
    maximum = arguments.max_distance
    if isinstance(arguments, CameraRayArguments):
        origin, direction, offset, clip_end = camera_ray(arguments, depsgraph)
        maximum = min(maximum, clip_end)
    else:
        origin, direction = Vector(arguments.origin), unit(arguments.direction)
    if maximum <= offset:
        return RaycastResult(hit=False)
    hit, location, normal, index, obj, matrix = bpy.context.scene.ray_cast(
        depsgraph, origin + direction * offset, direction, distance=maximum - offset
    )
    if not hit:
        return RaycastResult(hit=False)
    try:
        local = matrix.inverted() @ location
    except ValueError as exc:
        raise OperationError(
            "invalid_context", "Hit object transform is singular"
        ) from exc
    return RaycastResult(
        hit=True,
        object_name=obj.original.name,
        object_type=str(obj.type).lower(),
        location_world=list(location),
        normal_world=list(unit(normal)),
        location_object=list(local),
        normal_object=list(unit(matrix.to_3x3().transposed() @ normal)),
        distance=math.hypot(*(location - origin)),
        evaluated_face_index=index if index >= 0 else None,
    )
