"""Interactive viewport controls inside the main-thread adapter boundary."""

from typing import Any

import bpy  # type: ignore[import-not-found]

from .errors import OperationError
from .viewport_models import (
    ViewportFrameArguments,
    ViewportInspectArguments,
    ViewportInspection,
    ViewportState,
)


def _viewports() -> list[tuple[str, Any, Any, Any]]:
    if bpy.app.background:
        return []
    found = []
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                found.append(
                    (f"{window.as_pointer()}:{area.as_pointer()}", window, area, region)
                )
    if len(found) > 32:
        raise OperationError(
            "inspection_limit", "At most 32 open 3D viewports are supported"
        )
    return found


def _state(identifier: str, window: Any, area: Any, region: Any) -> ViewportState:
    space = area.spaces.active
    view = space.region_3d
    layer = window.view_layer
    selected = sorted(o.name for o in layer.objects if o.select_get(view_layer=layer))
    return ViewportState(
        viewport_id=identifier,
        scene_name=window.scene.name,
        view_layer_name=layer.name,
        width=region.width,
        height=region.height,
        selected_count=len(selected),
        selected_objects=selected[:64],
        active_object=layer.objects.active.name if layer.objects.active else None,
        view_location=list(view.view_location),
        view_distance=float(view.view_distance),
        perspective=view.view_perspective,
        local_view=space.local_view is not None,
        quad_view=bool(space.region_quadviews),
    )


def inspect(arguments: ViewportInspectArguments) -> ViewportInspection:
    found = _viewports()
    if arguments.viewport_id is not None:
        found = [v for v in found if v[0] == arguments.viewport_id]
        if not found:
            raise OperationError(
                "viewport_not_found", "Rediscover the intended interactive viewport"
            )
    return ViewportInspection(viewports=[_state(*v) for v in found])


def frame(arguments: ViewportFrameArguments) -> ViewportState:
    found = next((v for v in _viewports() if v[0] == arguments.viewport_id), None)
    if found is None:
        raise OperationError(
            "viewport_not_found", "An existing interactive 3D viewport is required"
        )
    identifier, window, area, region = found
    space = area.spaces.active
    view = space.region_3d
    layer = window.view_layer
    if space.region_quadviews or view.view_perspective == "CAMERA":
        raise OperationError(
            "invalid_context", "Frame requires a non-camera, non-quad 3D view"
        )
    objects = [layer.objects.get(name) for name in arguments.object_names]
    if any(o is None for o in objects):
        raise OperationError(
            "object_not_found", "Every object must exist in the viewport's view layer"
        )
    if any(
        o.hide_select or not o.visible_get(view_layer=layer, viewport=space)
        for o in objects
    ):
        raise OperationError(
            "invalid_context",
            "Every target must be visible and selectable in this viewport",
        )
    with bpy.context.temp_override(window=window, area=area, region=region):
        if bpy.context.mode != "OBJECT" or not bpy.ops.view3d.view_selected.poll():
            raise OperationError(
                "invalid_context",
                "Frame requires Object Mode and an interactive 3D view",
            )
        selected = [o for o in layer.objects if o.select_get(view_layer=layer)]
        active = layer.objects.active
        location, rotation, distance = (
            view.view_location.copy(),
            view.view_rotation.copy(),
            view.view_distance,
        )
        try:
            for obj in selected:
                obj.select_set(False, view_layer=layer)
            for obj in objects:
                obj.select_set(True, view_layer=layer)
            layer.objects.active = objects[0]
            outcome = bpy.ops.view3d.view_selected(
                "EXEC_DEFAULT", use_all_regions=False
            )
            if "FINISHED" not in outcome:
                raise RuntimeError("Native viewport framing did not finish")
            area.tag_redraw()
            return _state(identifier, window, area, region)
        except Exception as exc:
            for obj in objects:
                obj.select_set(False, view_layer=layer)
            for obj in selected:
                obj.select_set(True, view_layer=layer)
            layer.objects.active = active
            view.view_location, view.view_rotation, view.view_distance = (
                location,
                rotation,
                distance,
            )
            raise OperationError("viewport_frame_failed", str(exc)) from exc
