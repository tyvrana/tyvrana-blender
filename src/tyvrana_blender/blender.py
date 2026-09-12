"""The Blender API boundary. All entry points execute on the main thread."""

import logging
import threading
from typing import Any, cast
from uuid import uuid4

import bpy  # type: ignore[import-not-found]
from mathutils import Quaternion  # type: ignore[import-not-found]
from pydantic import ValidationError
from tyvrana_protocol import (
    AdapterEvent,
    ArtifactDescriptor,
    CancelRequest,
    OperationRequest,
)

from .artifacts import ArtifactSpool
from .camera_models import (
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraProperties,
    CameraSetActiveArguments,
    CameraSummary,
    SensorFit,
    normalize_projection,
    validate_optics,
)
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


def find_object(name: str, *, mutable: bool = True) -> Any:
    main_thread()
    obj = bpy.context.scene.objects.get(name)
    if obj is None:
        raise OperationError(
            "object_not_found",
            f'Object "{name}" does not exist in the current scene',
            {"name": name},
        )
    if mutable and obj.library is not None and obj.override_library is None:
        raise OperationError(
            "invalid_context", "Linked objects cannot be modified directly"
        )
    return obj


def camera_summary(obj: Any) -> CameraSummary:
    main_thread()
    pose = object_summary(obj)
    data = obj.data
    projection = normalize_projection(str(data.type))
    return CameraSummary(
        name=pose.name,
        active=obj == bpy.context.scene.camera,
        projection=projection,
        location=pose.location,
        rotation=pose.rotation,
        scale=pose.scale,
        lens_mm=float(data.lens) if projection == "perspective" else None,
        ortho_scale=float(data.ortho_scale) if projection == "orthographic" else None,
        clip_start=float(data.clip_start),
        clip_end=float(data.clip_end),
        shift_x=float(data.shift_x),
        shift_y=float(data.shift_y),
        sensor_width_mm=float(data.sensor_width),
        sensor_height_mm=float(data.sensor_height),
        sensor_fit=cast(SensorFit, str(data.sensor_fit).lower()),
    )


def find_camera(name: str, *, mutable: bool = True) -> Any:
    obj = find_object(name, mutable=mutable)
    if obj.type != "CAMERA":
        raise OperationError(
            "object_not_camera", "Object is not a camera", {"name": name}
        )
    return obj


_CAMERA_FIELDS = {
    "projection": "type",
    "lens_mm": "lens",
    "ortho_scale": "ortho_scale",
    "clip_start": "clip_start",
    "clip_end": "clip_end",
    "shift_x": "shift_x",
    "shift_y": "shift_y",
}


def camera_values(data: Any) -> dict[str, str | float]:
    main_thread()
    return {
        key: normalize_projection(str(data.type))
        if key == "projection"
        else float(getattr(data, prop))
        for key, prop in _CAMERA_FIELDS.items()
    }


def apply_camera_values(data: Any, values: CameraProperties, fields: set[str]) -> None:
    main_thread()
    for key, prop in _CAMERA_FIELDS.items():
        if key in fields:
            value = getattr(values, key)
            if key == "projection":
                value = "PERSP" if values.projection == "perspective" else "ORTHO"
            setattr(data, prop, value)


def camera_mutation_context() -> None:
    main_thread()
    if bpy.app.is_job_running("RENDER"):
        raise OperationError("invalid_context", "Cannot change cameras during a render")
    if not bpy.context.scene.is_editable:
        raise OperationError("invalid_context", "Scene is not editable")


class BlenderBackend:
    def __init__(self, spool: ArtifactSpool | None = None) -> None:
        self.spool = spool

    def camera_inspect(self) -> CameraInspectResult:
        main_thread()
        bpy.context.view_layer.update()
        cameras = [
            camera_summary(obj)
            for obj in sorted(bpy.context.scene.objects, key=lambda obj: obj.name)
            if obj.type == "CAMERA"
        ]
        return CameraInspectResult(
            active_camera=next((obj.name for obj in cameras if obj.active), None),
            cameras=cameras,
        )

    def camera_create(self, arguments: CameraCreateArguments) -> CameraSummary:
        camera_mutation_context()
        if bpy.context.mode != "OBJECT" or not bpy.context.collection.is_editable:
            raise OperationError(
                "invalid_context", "Camera creation requires editable Object Mode"
            )
        scene = bpy.context.scene
        previous = scene.camera
        data = bpy.data.cameras.new(arguments.name or "Camera")
        obj = None
        try:
            apply_camera_values(data, arguments, set(_CAMERA_FIELDS))
            obj = bpy.data.objects.new(arguments.name or "Camera", data)
            bpy.context.collection.objects.link(obj)
            obj.location = arguments.location
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = arguments.rotation
            obj.scale = arguments.scale
            if arguments.make_active or previous is None:
                scene.camera = obj
            bpy.context.view_layer.update()
            return camera_summary(obj)
        except Exception:
            scene.camera = previous
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.cameras.remove(data)
            raise

    def camera_configure(self, arguments: CameraConfigureArguments) -> CameraSummary:
        camera_mutation_context()
        obj = find_camera(arguments.name)
        data = obj.data
        fields = arguments.model_fields_set - {"name"}
        if not data.is_editable or any(
            data.is_property_readonly(_CAMERA_FIELDS[key]) for key in fields
        ):
            raise OperationError("invalid_context", "Camera data is not editable")
        values = camera_values(data)
        values.update(arguments.model_dump(exclude={"name"}, exclude_unset=True))
        if values["projection"] not in ("perspective", "orthographic"):
            raise OperationError(
                "unsupported_projection",
                "Configure requires perspective or orthographic projection",
                {"projection": values["projection"]},
            )
        try:
            settings = CameraProperties.model_validate(values)
            validate_optics(settings.projection, fields)
        except (ValidationError, ValueError) as exc:
            # Validation errors here describe merged state, not an internal failure.
            message = (
                "; ".join(error["msg"] for error in exc.errors(include_input=False))
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            raise OperationError(
                "invalid_arguments", message, {"name": arguments.name}
            ) from exc
        if not fields:
            return camera_summary(obj)
        if data.users > 1 and obj.is_property_readonly("data"):
            raise OperationError(
                "invalid_context", "Cannot make camera data independent"
            )
        original = {
            _CAMERA_FIELDS[key]: getattr(data, _CAMERA_FIELDS[key]) for key in fields
        }
        working = data.copy() if data.users > 1 else data
        try:
            apply_camera_values(working, settings, fields)
            if working != data:
                obj.data = working
            bpy.context.view_layer.update()
            return camera_summary(obj)
        except Exception:
            if working != data:
                if obj.data != data:
                    obj.data = data
                bpy.data.cameras.remove(working)
            else:
                for prop, value in original.items():
                    setattr(data, prop, value)
            bpy.context.view_layer.update()
            raise

    def camera_set_active(self, arguments: CameraSetActiveArguments) -> CameraSummary:
        camera_mutation_context()
        obj = find_camera(arguments.name, mutable=False)
        scene = bpy.context.scene
        previous = scene.camera
        try:
            scene.camera = obj
            bpy.context.view_layer.update()
            return camera_summary(obj)
        except Exception:
            scene.camera = previous
            raise

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
