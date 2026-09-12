"""The Blender API boundary. All entry points execute on the main thread."""

import logging
import threading
from typing import Any
from uuid import uuid4

import bpy  # type: ignore[import-not-found]
from mathutils import Quaternion  # type: ignore[import-not-found]
from tyvrana_protocol import (
    AdapterEvent,
    ArtifactDescriptor,
    CancelRequest,
    OperationRequest,
)

from .artifacts import ArtifactSpool
from .dispatch import CommandQueue
from .models import (
    ConnectionConfig,
    CreateArguments,
    DeleteArguments,
    DeleteResult,
    ObjectSummary,
    RenderArguments,
    RenderResult,
    SceneSummary,
    TransformArguments,
)
from .operations import OperationError, execute, registration
from .transport import WorkerProcess

logger = logging.getLogger(__name__)
INSTANCE_ID = f"blender-{uuid4()}"


def main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Blender API access requires the main thread")


def vector(value: Any) -> list[float]:
    return [float(value[i]) for i in range(3)]


def object_summary(obj: Any) -> ObjectSummary:
    main_thread()
    if obj.rotation_mode == "QUATERNION":
        rotation = obj.rotation_quaternion.to_euler("XYZ")
    elif obj.rotation_mode == "AXIS_ANGLE":
        angle, *axis = obj.rotation_axis_angle
        rotation = Quaternion(axis, angle).to_euler("XYZ")
    else:
        rotation = obj.rotation_euler.to_quaternion().to_euler("XYZ")
    return ObjectSummary(
        name=str(obj.name),
        type=str(obj.type),
        location=vector(obj.location),
        rotation=vector(rotation),
        scale=vector(obj.scale),
        dimensions=vector(obj.dimensions),
        visible=bool(obj.visible_get()),
        hide_viewport=bool(obj.hide_viewport),
        hide_render=bool(obj.hide_render),
        selected=bool(obj.select_get()),
        parent=str(obj.parent.name) if obj.parent else None,
    )


def find_object(name: str) -> Any:
    main_thread()
    obj = bpy.context.scene.objects.get(name)
    if obj is None:
        raise OperationError(
            "object_not_found",
            f'Object "{name}" does not exist in the current scene',
            {"name": name},
        )
    if obj.library is not None and obj.override_library is None:
        raise OperationError(
            "invalid_context", "Linked objects cannot be modified directly"
        )
    return obj


class BlenderBackend:
    def __init__(self, spool: ArtifactSpool | None = None) -> None:
        self.spool = spool

    def render(
        self, arguments: RenderArguments
    ) -> tuple[RenderResult, ArtifactDescriptor]:
        main_thread()
        from .render import render_image

        if self.spool is None:
            raise OperationError("invalid_context", "Render storage is unavailable")
        return render_image(arguments, self.spool)

    def inspect(self) -> SceneSummary:
        main_thread()
        bpy.context.view_layer.update()
        objects = [
            object_summary(obj)
            for obj in sorted(bpy.context.scene.objects, key=lambda item: item.name)
        ]
        active = bpy.context.view_layer.objects.active
        return SceneSummary(
            name=str(bpy.context.scene.name),
            filepath=str(bpy.data.filepath) or None,
            active_object=str(active.name) if active else None,
            selected_objects=sorted(obj.name for obj in objects if obj.selected),
            object_count=len(objects),
            objects=objects,
        )

    def create(self, arguments: CreateArguments) -> ObjectSummary:
        main_thread()
        if bpy.context.mode != "OBJECT":
            raise OperationError(
                "invalid_context", "Primitive creation requires Object Mode"
            )
        operators = {
            "cube": bpy.ops.mesh.primitive_cube_add,
            "plane": bpy.ops.mesh.primitive_plane_add,
            "uv_sphere": bpy.ops.mesh.primitive_uv_sphere_add,
            "ico_sphere": bpy.ops.mesh.primitive_ico_sphere_add,
            "cylinder": bpy.ops.mesh.primitive_cylinder_add,
            "cone": bpy.ops.mesh.primitive_cone_add,
            "torus": bpy.ops.mesh.primitive_torus_add,
        }
        outcome = operators[arguments.primitive](
            location=arguments.location, rotation=arguments.rotation
        )
        if "FINISHED" not in outcome:
            raise OperationError(
                "operation_failed", "Blender did not create the primitive"
            )
        obj = bpy.context.view_layer.objects.active
        if arguments.name is not None:
            obj.name = arguments.name
        obj.scale = arguments.scale
        bpy.context.view_layer.update()
        return object_summary(obj)

    def transform(self, arguments: TransformArguments) -> ObjectSummary:
        main_thread()
        obj = find_object(arguments.name)
        if arguments.location is not None:
            obj.location = arguments.location
        if arguments.rotation is not None:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = arguments.rotation
        if arguments.scale is not None:
            obj.scale = arguments.scale
        bpy.context.view_layer.update()
        return object_summary(obj)

    def delete(self, arguments: DeleteArguments) -> DeleteResult:
        main_thread()
        if bpy.context.mode != "OBJECT":
            raise OperationError(
                "invalid_context", "Object deletion requires Object Mode"
            )
        obj = find_object(arguments.name)
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.view_layer.update()
        return DeleteResult(deleted=arguments.name)


class Runtime:
    def __init__(self, config: ConnectionConfig) -> None:
        main_thread()
        self.config = config
        self.filepath = str(bpy.data.filepath)
        self.worker = WorkerProcess(
            config,
            registration(INSTANCE_ID, str(bpy.app.version_string), self.filepath),
        )
        self.queue = CommandQueue(
            lambda request: execute(BlenderBackend(self.worker.spool), request),
            discard=self.worker.discard,
        )
        self.status = "connecting"

    def tick(self) -> None:
        main_thread()
        for message in self.worker.poll():
            if isinstance(message, OperationRequest):
                rejected = self.queue.submit(message)
                if rejected is not None:
                    self.worker.send(rejected)
            elif isinstance(message, CancelRequest):
                self.queue.cancel(message.request_id)
            elif (
                isinstance(message, AdapterEvent)
                and message.event == "blender.connection.state"
                and isinstance(message.payload, dict)
            ):
                self.status = str(message.payload["state"])
                if self.status != "connected":
                    self.queue.clear()
            else:
                raise ValueError("Unexpected networking worker message")
        self.queue.drain(self.worker.send)

    def stop(self) -> None:
        main_thread()
        self.queue.close()
        self.worker.stop()
        self.status = "stopped"


_runtime: Runtime | None = None
_enabled = False
_status = "disabled"


def preferences_config() -> ConnectionConfig:
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is None:
        return ConnectionConfig()
    return ConnectionConfig(host=addon.preferences.host, port=addon.preferences.port)


def stop() -> None:
    global _runtime
    main_thread()
    if _runtime is not None:
        _runtime.stop()
        _runtime = None


def restart() -> None:
    global _runtime, _status
    main_thread()
    config = preferences_config()
    stop()
    _runtime = Runtime(config)
    _status = "connecting"


def pump() -> float | None:
    """Timer callback, also used explicitly by blocking background scripts."""
    global _status
    main_thread()
    if not _enabled:
        return None
    try:
        if _runtime is None and _status == "starting":
            restart()
        if _runtime is not None:
            _runtime.tick()
            _status = _runtime.status
    except Exception:
        logger.exception("Adapter stopped after an unexpected runtime failure")
        stop()
        _status = "error; reconnect from extension preferences"
    return 0.02


def before_load(*args: object) -> None:
    stop()


def after_load(*args: object) -> None:
    if _enabled:
        restart()


def after_save(*args: object) -> None:
    if _runtime is not None and _runtime.filepath != str(bpy.data.filepath):
        restart()


def before_exit(*args: object) -> None:
    unregister()


for _handler in (before_load, after_load, after_save, before_exit):
    bpy.app.handlers.persistent(_handler)


class TyvranaPreferences(bpy.types.AddonPreferences):  # type: ignore[misc]
    bl_idname = __package__
    host: str
    port: int
    __annotations__ = {
        "host": bpy.props.StringProperty(
            name="Core host",
            default="127.0.0.1",
            description="Numeric loopback IP address",
        ),
        "port": bpy.props.IntProperty(name="Core port", default=8765, min=1, max=65535),
    }

    def draw(self, context: Any) -> None:
        self.layout.prop(self, "host")
        self.layout.prop(self, "port")
        self.layout.label(text=f"Connection: {_status}")
        self.layout.operator("tyvrana.reconnect")


class TyvranaReconnect(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "tyvrana.reconnect"
    bl_label = "Apply and reconnect"

    def execute(self, context: Any) -> set[str]:
        try:
            restart()
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


_classes = (TyvranaPreferences, TyvranaReconnect)
_handlers = (
    (bpy.app.handlers.load_pre, before_load),
    (bpy.app.handlers.load_post, after_load),
    (bpy.app.handlers.load_post_fail, after_load),
    (bpy.app.handlers.save_post, after_save),
    (bpy.app.handlers.exit_pre, before_exit),
)


def register() -> None:
    global _enabled, _status
    main_thread()
    if _enabled:
        return
    for cls in _classes:
        bpy.utils.register_class(cls)
    _enabled = True
    _status = "starting"
    try:
        # register() runs under Blender's restricted data/context wrapper.
        # Defer scene capture and networking until the first application tick.
        bpy.app.timers.register(pump, first_interval=0.02, persistent=True)
        for handlers, callback in _handlers:
            handlers.append(callback)
    except Exception:
        unregister()
        raise


def unregister() -> None:
    global _enabled, _status
    main_thread()
    if not _enabled:
        return
    _enabled = False
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)
    for handlers, callback in _handlers:
        if callback in handlers:
            handlers.remove(callback)
    stop()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _status = "disabled"
