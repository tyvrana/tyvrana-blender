"""Interactive viewport controls inside the main-thread adapter boundary."""

import math
from datetime import UTC, datetime
from typing import Any

import bpy  # type: ignore[import-not-found]
import gpu  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from .artifacts import ArtifactSpool
from .errors import OperationError
from .viewport_models import (
    ViewDirection,
    ViewportCapture,
    ViewportCaptureArguments,
    ViewportCaptureResult,
    ViewportConfigureArguments,
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
        view_rotation=tuple(view.view_rotation),
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


def target(identifier: str) -> tuple[str, Any, Any, Any]:
    found = next((v for v in _viewports() if v[0] == identifier), None)
    if found is None:
        raise OperationError(
            "viewport_not_found", "Rediscover the intended 3D viewport"
        )
    if found[2].spaces.active.region_quadviews:
        raise OperationError("invalid_context", "Select a non-quad 3D viewport")
    return found


def configure(arguments: ViewportConfigureArguments) -> ViewportState:
    found = target(arguments.viewport_id)
    view = found[2].spaces.active.region_3d
    if isinstance(arguments.orientation, ViewDirection):
        direction = Vector(arguments.orientation.direction)
        up = Vector(arguments.orientation.up)
    else:
        direction = Vector(
            {
                "front": (0, 1, 0),
                "rear": (0, -1, 0),
                "left": (1, 0, 0),
                "right": (-1, 0, 0),
                "top": (0, 0, -1),
                "bottom": (0, 0, 1),
                "oblique": (-1, 1, -0.7),
            }[arguments.orientation]
        )
        up = Vector(
            (0, 1, 0) if arguments.orientation in {"top", "bottom"} else (0, 0, 1)
        )
    z = -direction.normalized()
    x = up.cross(z).normalized()
    y = z.cross(x).normalized()
    view.view_rotation = Matrix((x, y, z)).transposed().to_quaternion()
    view.view_perspective = arguments.perspective
    if arguments.target is not None:
        view.view_location = arguments.target
    if arguments.distance is not None:
        view.view_distance = arguments.distance
    view.update()
    found[2].tag_redraw()
    return _state(*found)


def framebuffer(window: Any, area: Any, region: Any) -> Any:
    """Read the composited window back buffer, including shaded geometry and UI.

    POST_PIXEL handlers see an overlay attachment, not the final viewport image.
    Two swaps refresh both window buffers before reading the composited back buffer.
    """
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            area.tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
            x, y, width, height = gpu.state.viewport_get()
            if (x, y, width, height) != (0, 0, window.width, window.height):
                raise RuntimeError(
                    "Redraw did not leave the intended window framebuffer"
                )
            buffer = gpu.state.active_framebuffer_get().read_color(
                region.x, region.y, region.width, region.height, 4, 0, "UBYTE"
            )
            values = np.asarray(buffer, dtype=np.uint8).copy()
            values[:, :, 3] = 255
            return values.astype(np.float32) / 255
    except Exception as exc:
        raise OperationError(
            "viewport_capture_failed", "Cannot read composited viewport: " + str(exc)
        ) from exc


def capture(
    arguments: ViewportCaptureArguments, spool: ArtifactSpool
) -> tuple[ViewportCaptureResult, ArtifactDescriptor]:
    found = target(arguments.viewport_id)
    _, window, area, region = found
    view = area.spaces.active.region_3d
    saved = (
        view.view_location.copy(),
        view.view_rotation.copy(),
        view.view_distance,
        view.view_perspective,
    )
    count = max(1, len(arguments.views))
    columns, rows = min(3, count), math.ceil(count / min(3, count))
    width, height = region.width, region.height
    if width * height * rows * columns > arguments.max_pixels:
        raise OperationError(
            "viewport_capture_limit",
            "Capture exceeds max_pixels; reduce views/window size or "
            "explicitly raise budget",
        )
    captures = []
    sheet = np.zeros((rows * height, columns * width, 4), dtype=np.float32)
    sheet[:, :, 3] = 1
    try:
        for index in range(count):
            if arguments.views:
                configure(
                    ViewportConfigureArguments(
                        viewport_id=arguments.viewport_id,
                        **arguments.views[index].model_dump(),
                    )
                )
            values = framebuffer(window, area, region)
            if values.shape != (height, width, 4):
                raise OperationError(
                    "viewport_capture_failed", "Viewport resized during capture; retry"
                )
            x, y = (index % columns) * width, (index // columns) * height
            bottom = rows * height - y - height
            sheet[bottom : bottom + height, x : x + width] = values
            captures.append(
                ViewportCapture(
                    state=_state(*found),
                    captured_at=datetime.now(UTC).isoformat(),
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                )
            )
        with spool.reserve() as (identifier, path):
            image = bpy.data.images.new(
                "Tyvrana viewport evidence",
                width=columns * width,
                height=rows * height,
                alpha=True,
            )
            try:
                image.colorspace_settings.name = "Non-Color"
                image.pixels.foreach_set(sheet.ravel())
                image.update()
                image.file_format = "PNG"
                image.filepath_raw = str(path)
                image.save()
            finally:
                bpy.data.images.remove(image)
            descriptor = spool.describe(identifier).model_copy(
                update={"name": "viewport.png"}
            )
            return ViewportCaptureResult(
                width=columns * width,
                height=rows * height,
                captures=captures,
                restored=True,
            ), descriptor
    finally:
        (
            view.view_location,
            view.view_rotation,
            view.view_distance,
            view.view_perspective,
        ) = saved
        view.update()
        area.tag_redraw()


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
