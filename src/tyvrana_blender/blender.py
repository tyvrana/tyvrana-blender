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
    JsonValue,
    OperationRequest,
)

from . import shader, uv
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
from .compatibility import require_blender
from .dispatch import CommandQueue
from .image_models import (
    ALPHA_MODES,
    ImageConfigureArguments,
    ImageCreateArguments,
    ImageFromArtifactArguments,
    ImageInspectResult,
    ImageSummary,
)
from .incoming import input_path
from .light_models import (
    COMMON_FIELDS,
    LIGHT_TYPES,
    TYPE_FIELDS,
    LightConfigureArguments,
    LightCreateArguments,
    LightInspectResult,
    LightSummary,
    light_state,
)
from .material_models import (
    PRINCIPLED_SOCKETS,
    MaterialAssignArguments,
    MaterialAssignment,
    MaterialAssignResult,
    MaterialConfigureArguments,
    MaterialCreateArguments,
    MaterialInspectResult,
    MaterialSummary,
    PrincipledSummary,
    Surface,
)
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
from .raster import RasterError, raster_size
from .shader_models import (
    ConnectArguments,
    DisconnectArguments,
    DisconnectResult,
    LinkSummary,
    NodeConfigureArguments,
    NodeCreateArguments,
    NodeDeleteArguments,
    NodeDeleteResult,
    NodeSummary,
    ShaderGraphSummary,
    ShaderInspectArguments,
)
from .transport import WorkerProcess
from .uv_models import (
    UVCreateArguments,
    UVInspectArguments,
    UVInspectResult,
    UVPackArguments,
    UVSetActiveArguments,
    UVUnwrapArguments,
)

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


def data_mutation_context() -> None:
    main_thread()
    if bpy.app.is_job_running("RENDER"):
        raise OperationError(
            "invalid_context", "Cannot change scene data during a render"
        )
    if not bpy.context.scene.is_editable:
        raise OperationError("invalid_context", "Scene is not editable")


def light_values(data: Any) -> dict[str, JsonValue]:
    main_thread()
    kind = LIGHT_TYPES[str(data.type)]
    values: dict[str, JsonValue] = {}
    for key in COMMON_FIELDS | TYPE_FIELDS[kind]:
        value = getattr(data, _LIGHT_FIELDS[key])
        if key == "color":
            color: list[JsonValue] = [float(value[i]) for i in range(3)]
            values[key] = color
        elif key == "shape":
            values[key] = str(value).lower()
        elif key in {"normalize", "use_shadow"}:
            values[key] = bool(value)
        else:
            values[key] = float(value)
    return values


_LIGHT_FIELDS = {
    "color": "color",
    "energy": "energy",
    "exposure": "exposure",
    "normalize": "normalize",
    "use_shadow": "use_shadow",
    "radius": "shadow_soft_size",
    "angle": "angle",
    "spot_size": "spot_size",
    "spot_blend": "spot_blend",
    "shape": "shape",
    "size": "size",
    "size_y": "size_y",
}


def apply_light_values(data: Any, values: dict[str, JsonValue]) -> None:
    main_thread()
    for key, value in values.items():
        setattr(
            data, _LIGHT_FIELDS[key], str(value).upper() if key == "shape" else value
        )


def light_summary(obj: Any) -> LightSummary:
    main_thread()
    pose = object_summary(obj)
    state = light_state(LIGHT_TYPES[str(obj.data.type)], light_values(obj.data), set())
    return LightSummary.model_validate(
        {
            **state.model_dump(mode="json"),
            **pose.model_dump(mode="json", exclude={"type", "dimensions", "selected"}),
        }
    )


def shader_socket(sockets: Any, identifier: str) -> Any:
    """Use the built-in semantic identifier, independent of node/socket labels."""
    return next(socket for socket in sockets if socket.identifier == identifier)


def principled_surface(material: Any) -> tuple[Surface, Any]:
    main_thread()
    tree = material.node_tree
    if tree is None or not tree.nodes:
        return "none", None
    outputs = [
        node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"
    ]
    if not outputs:
        return "none", None
    active = [node for node in outputs if node.is_active_output]
    if (
        len(active) != 1
        or active[0].target != "ALL"
        or tree.animation_data is not None
        or material.is_grease_pencil
    ):
        return "custom", None
    output = active[0]
    if output.mute or any(
        tree.get_output_node(target) != output for target in ("ALL", "EEVEE", "CYCLES")
    ):
        return "custom", None
    surface = shader_socket(output.inputs, "Surface")
    if not surface.is_linked:
        return "none", None
    if len(surface.links) != 1:
        return "custom", None
    link = surface.links[0]
    node = link.from_node
    if (
        not link.is_valid
        or link.is_muted
        or link.from_socket.identifier != "BSDF"
        or node.bl_idname != "ShaderNodeBsdfPrincipled"
        or node.mute
        or any(socket.is_linked for socket in node.inputs)
        or any(socket.is_linked for socket in output.inputs if socket != surface)
    ):
        return "custom", None
    return "principled", node


def principled_values(node: Any) -> PrincipledSummary:
    main_thread()
    values: dict[str, JsonValue] = {}
    for field, identifier in PRINCIPLED_SOCKETS.items():
        value = shader_socket(node.inputs, identifier).default_value
        if field in {"base_color", "emission_color", "subsurface_radius"}:
            values[field] = [float(value[i]) for i in range(3)]
        else:
            values[field] = float(value)
    return PrincipledSummary.model_validate(values)


def material_summary(material: Any) -> MaterialSummary:
    main_thread()
    surface, node = principled_surface(material)
    values = None
    if node is not None:
        try:
            values = principled_values(node)
        except ValidationError:
            # Non-finite externally authored state cannot be represented as JSON.
            surface = "custom"
    return MaterialSummary(
        name=str(material.name),
        surface=surface,
        principled=values,
        assignments=[
            MaterialAssignment(object=str(obj.name), slot=index)
            for obj in sorted(bpy.data.objects, key=lambda obj: obj.name)
            for index, slot in enumerate(obj.material_slots)
            if slot.material == material
        ],
    )


def find_material(name: str) -> Any:
    main_thread()
    matches = [material for material in bpy.data.materials if material.name == name]
    if not matches:
        raise OperationError(
            "material_not_found", "Material does not exist", {"name": name}
        )
    if len(matches) != 1:
        raise OperationError(
            "invalid_arguments",
            "Material name is ambiguous across libraries",
            {"name": name},
        )
    return matches[0]


def apply_principled_values(node: Any, values: dict[str, JsonValue]) -> None:
    main_thread()
    for field, value in values.items():
        socket = shader_socket(node.inputs, PRINCIPLED_SOCKETS[field])
        if field in {"base_color", "emission_color"}:
            assert isinstance(value, list)
            # RGB shader colors have an unused fourth socket component, not Alpha.
            socket.default_value = [*value, float(socket.default_value[3])]
        else:
            socket.default_value = value


MATERIAL_OBJECT_TYPES = frozenset(
    {"MESH", "CURVE", "SURFACE", "FONT", "CURVES", "POINTCLOUD", "VOLUME"}
)


def assignment_result(obj: Any, index: int, material: Any) -> MaterialAssignResult:
    return MaterialAssignResult(
        object_name=str(obj.name),
        assigned_slot=index,
        material_name=str(material.name),
        slots=[
            str(slot.material.name) if slot.material else None
            for slot in obj.material_slots
        ],
    )


def material_index_snapshot(data: Any) -> list[tuple[Any, str, int]]:
    """Preserve authored indices when rolling back Blender's slot-removal remap."""
    if isinstance(data, bpy.types.Mesh):
        return [
            (face, "material_index", int(face.material_index)) for face in data.polygons
        ]
    if isinstance(data, bpy.types.Curve):
        characters = data.body_format if isinstance(data, bpy.types.TextCurve) else ()
        return [
            (item, "material_index", int(item.material_index))
            for item in (*data.splines, *characters)
        ]
    return []


def image_summary(image: Any) -> ImageSummary:
    main_thread()
    return ImageSummary(
        name=str(image.name),
        source=str(image.source).lower(),
        width=int(image.size[0]),
        height=int(image.size[1]),
        channels=int(image.channels),
        has_alpha=image.depth // (32 if image.is_float else 8) in {2, 4},
        is_float=bool(image.is_float),
        color_space=(
            None if image.source == "VIEWER" else str(image.colorspace_settings.name)
        ),
        alpha_mode=next(
            key for key, value in ALPHA_MODES.items() if value == image.alpha_mode
        ),
        packed=bool(image.packed_files),
        users=int(image.users),
        dirty=bool(image.is_dirty),
        generated_type=image.generated_type.lower()
        if image.source == "GENERATED"
        else None,
    )


def find_image(name: str) -> Any:
    main_thread()
    matches = [image for image in bpy.data.images if image.name == name]
    if not matches:
        raise OperationError("image_not_found", "Image does not exist", {"name": name})
    if len(matches) != 1:
        raise OperationError(
            "invalid_arguments", "Image name is ambiguous across libraries"
        )
    return matches[0]


def validate_color_space(name: str) -> None:
    main_thread()
    names = {
        item.identifier
        for item in bpy.types.ColorManagedInputColorspaceSettings.bl_rna.properties[
            "name"
        ].enum_items
    }
    if name not in names:
        raise OperationError(
            "invalid_arguments",
            "Color space is unavailable in the active OCIO configuration",
            {"color_space": name, "available": sorted(names)},
        )


class BlenderBackend:
    def __init__(self, spool: ArtifactSpool | None = None) -> None:
        self.spool = spool

    def uv_inspect(self, arguments: UVInspectArguments) -> UVInspectResult:
        main_thread()
        return uv.inspect(uv.mesh_object(arguments.object_name))

    def uv_create(self, arguments: UVCreateArguments) -> UVInspectResult:
        main_thread()
        return uv.create(uv.mesh_object(arguments.object_name), arguments)

    def uv_set_active(self, arguments: UVSetActiveArguments) -> UVInspectResult:
        main_thread()
        return uv.set_active(uv.mesh_object(arguments.object_name), arguments)

    def uv_unwrap(self, arguments: UVUnwrapArguments) -> UVInspectResult:
        main_thread()
        return uv.unwrap(uv.mesh_object(arguments.object_name), arguments)

    def uv_pack(self, arguments: UVPackArguments) -> UVInspectResult:
        main_thread()
        return uv.pack(uv.mesh_object(arguments.object_name), arguments)

    def image_inspect(self) -> ImageInspectResult:
        main_thread()
        return ImageInspectResult(
            images=[
                image_summary(image)
                for image in sorted(bpy.data.images, key=lambda image: image.name)
            ]
        )

    def image_from_artifact(
        self, arguments: ImageFromArtifactArguments, request: OperationRequest
    ) -> ImageSummary:
        data_mutation_context()
        descriptor = next(
            (
                item
                for item in request.artifacts
                if item.artifact_id == arguments.artifact_id
            ),
            None,
        )
        if descriptor is None:
            raise OperationError(
                "artifact_not_attached", "Image artifact is not attached"
            )
        if descriptor.media_type not in {"image/png", "image/jpeg"}:
            raise OperationError(
                "unsupported_artifact_media_type",
                "Only PNG and JPEG images are supported",
            )
        if self.spool is None:
            raise OperationError("artifact_not_found", "Input artifact is unavailable")
        path = input_path(self.spool.root, request.request_id, descriptor.artifact_id)
        if not path.is_file():
            raise OperationError("artifact_not_found", "Input artifact is unavailable")
        if arguments.name is not None and any(
            image.name == arguments.name for image in bpy.data.images
        ):
            raise OperationError("invalid_arguments", "Image name already exists")
        if arguments.color_space is not None:
            validate_color_space(arguments.color_space)
        image = None
        try:
            size = raster_size(path, descriptor.media_type)
            image = bpy.data.images.load(str(path), check_existing=False)
            if (
                tuple(image.size) not in {size, tuple(reversed(size))}
                or not image.has_data
                or len(image.pixels) < 4
            ):
                raise RasterError("Blender could not decode the input image")
            image.name = arguments.name or "Image"
            if arguments.name is not None and image.name != arguments.name:
                raise OperationError(
                    "invalid_arguments",
                    "Blender cannot store the requested image name exactly",
                )
            image.pack()
            if not image.packed_files:
                raise RasterError("Blender could not pack the input image")
            # Empty paths cannot accidentally reload another local file. Packed
            # bytes remain authoritative through buffer reload and .blend saving.
            image.filepath_raw = ""
            for packed in image.packed_files:
                packed.filepath = ""
            if arguments.color_space is not None:
                image.colorspace_settings.name = arguments.color_space
            if arguments.alpha_mode is not None:
                image.alpha_mode = ALPHA_MODES[arguments.alpha_mode]
            result = image_summary(image)
            if (
                not result.packed
                or result.source != "file"
                or not result.width
                or not result.height
            ):
                raise RasterError("Blender did not retain a packed input image")
            return result
        except OperationError:
            if image is not None:
                bpy.data.images.remove(image, do_unlink=True)
            raise
        except (RasterError, RuntimeError, OSError, ValueError) as exc:
            if image is not None:
                bpy.data.images.remove(image, do_unlink=True)
            raise OperationError(
                "artifact_decode_failed", "Cannot decode and pack the input image"
            ) from exc
        except BaseException:
            if image is not None:
                bpy.data.images.remove(image, do_unlink=True)
            raise

    def image_create(self, arguments: ImageCreateArguments) -> ImageSummary:
        data_mutation_context()
        if arguments.name is not None and any(
            image.name == arguments.name for image in bpy.data.images
        ):
            raise OperationError("invalid_arguments", "Image name already exists")
        if arguments.color_space is not None:
            validate_color_space(arguments.color_space)
        image = bpy.data.images.new(
            arguments.name or "Image",
            width=arguments.width,
            height=arguments.height,
            alpha=arguments.alpha,
            float_buffer=arguments.float_buffer,
        )
        try:
            if arguments.name is not None and image.name != arguments.name:
                raise OperationError(
                    "invalid_arguments",
                    "Blender cannot store the requested image name exactly",
                )
            image.generated_type = arguments.generated_type.upper()
            if arguments.generated_type == "blank":
                image.generated_color = arguments.color
            if arguments.color_space is not None:
                image.colorspace_settings.name = arguments.color_space
            image.update()
            result = image_summary(image)
            if (
                result.width != arguments.width
                or result.height != arguments.height
                or result.generated_type != arguments.generated_type
                or result.has_alpha != arguments.alpha
                or result.is_float != arguments.float_buffer
                or (
                    arguments.color_space is not None
                    and result.color_space != arguments.color_space
                )
            ):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested image settings",
                )
            return result
        except Exception:
            bpy.data.images.remove(image, do_unlink=True)
            raise

    def image_configure(self, arguments: ImageConfigureArguments) -> ImageSummary:
        data_mutation_context()
        image = find_image(arguments.name)
        if not image.is_editable:
            raise OperationError("invalid_context", "Image is not editable")
        if image.source == "VIEWER" and arguments.model_fields_set - {"name"}:
            raise OperationError(
                "invalid_context",
                "Render/compositor buffers have no input image metadata",
            )
        if arguments.color_space is not None:
            validate_color_space(arguments.color_space)
        changes: list[tuple[Any, str, Any, Any]] = []
        if arguments.color_space is not None:
            changes.append(
                (
                    image.colorspace_settings,
                    "name",
                    image.colorspace_settings.name,
                    arguments.color_space,
                )
            )
        if arguments.alpha_mode is not None:
            changes.append(
                (
                    image,
                    "alpha_mode",
                    image.alpha_mode,
                    ALPHA_MODES[arguments.alpha_mode],
                )
            )
        changes = [change for change in changes if change[2] != change[3]]
        if image.is_dirty and any(
            key == "name" or image.source != "GENERATED"
            for owner, key, old, new in changes
        ):
            raise OperationError(
                "invalid_context",
                "Save image pixel edits before changing buffer-reloading metadata",
            )
        if any(owner.is_property_readonly(key) for owner, key, old, new in changes):
            raise OperationError("invalid_context", "Image metadata is not editable")
        try:
            for owner, key, _old, new in changes:
                setattr(owner, key, new)
            if any(getattr(owner, key) != new for owner, key, old, new in changes):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested image metadata",
                )
            return image_summary(image)
        except Exception:
            for owner, key, old, _new in reversed(changes):
                setattr(owner, key, old)
            raise

    def shader_inspect(self, arguments: ShaderInspectArguments) -> ShaderGraphSummary:
        main_thread()
        bpy.context.view_layer.update()
        return shader.graph_summary(find_material(arguments.material_name))

    def shader_create(self, arguments: NodeCreateArguments) -> NodeSummary:
        data_mutation_context()
        tree = shader.editable_tree(find_material(arguments.material_name))
        return shader.create_node(
            tree, arguments, bpy.data.images, bpy.context.view_layer.update
        )

    def shader_configure(self, arguments: NodeConfigureArguments) -> NodeSummary:
        data_mutation_context()
        tree = shader.editable_tree(find_material(arguments.material_name))
        return shader.configure_node(
            shader.find_node(tree, arguments.node_name),
            arguments,
            bpy.data.images,
            bpy.context.view_layer.update,
        )

    def shader_delete(self, arguments: NodeDeleteArguments) -> NodeDeleteResult:
        data_mutation_context()
        tree = shader.editable_tree(find_material(arguments.material_name))
        return shader.delete_node(tree, arguments)

    def shader_connect(self, arguments: ConnectArguments) -> LinkSummary:
        data_mutation_context()
        tree = shader.editable_tree(find_material(arguments.material_name))
        return shader.connect(tree, arguments, bpy.context.view_layer.update)

    def shader_disconnect(self, arguments: DisconnectArguments) -> DisconnectResult:
        data_mutation_context()
        tree = shader.editable_tree(find_material(arguments.material_name))
        return shader.disconnect(tree, arguments)

    def material_inspect(self) -> MaterialInspectResult:
        main_thread()
        bpy.context.view_layer.update()
        return MaterialInspectResult(
            materials=[
                material_summary(material)
                for material in sorted(
                    bpy.data.materials, key=lambda material: material.name
                )
            ]
        )

    def material_create(self, arguments: MaterialCreateArguments) -> MaterialSummary:
        data_mutation_context()
        if arguments.name is not None and any(
            material.name == arguments.name for material in bpy.data.materials
        ):
            raise OperationError(
                "invalid_arguments",
                "Material name already exists",
                {"name": arguments.name},
            )
        material = bpy.data.materials.new(arguments.name or "Material")
        try:
            if arguments.name is not None and material.name != arguments.name:
                raise OperationError(
                    "invalid_arguments",
                    "Blender cannot store the requested name exactly",
                )
            # Blender 5.2 creates precisely the native Principled -> Output graph.
            surface, node = principled_surface(material)
            if surface != "principled":
                raise OperationError(
                    "operation_failed", "Blender did not create a Principled surface"
                )
            requested = arguments.model_dump(
                mode="json", exclude={"name"}, exclude_unset=True
            )
            apply_principled_values(node, requested)
            bpy.context.view_layer.update()
            stored = principled_values(node).model_dump(mode="json")
            if any(stored[key] != value for key, value in requested.items()):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested shader values",
                )
            return material_summary(material)
        except Exception:
            bpy.data.materials.remove(material, do_unlink=True)
            raise

    def material_configure(
        self, arguments: MaterialConfigureArguments
    ) -> MaterialSummary:
        data_mutation_context()
        material = find_material(arguments.name)
        surface, node = principled_surface(material)
        if surface != "principled":
            raise OperationError(
                "unsupported_material_graph",
                "Material has no supported constant Principled surface",
                {"name": arguments.name},
            )
        requested = arguments.model_dump(
            mode="json", exclude={"name"}, exclude_unset=True
        )
        if (
            not material.is_editable
            or not material.node_tree.is_editable
            or any(
                shader_socket(
                    node.inputs, PRINCIPLED_SOCKETS[key]
                ).is_property_readonly("default_value")
                for key in requested
            )
        ):
            raise OperationError(
                "invalid_context", "Material shader data is not editable"
            )
        try:
            original = principled_values(node).model_dump(mode="json")
        except ValidationError as exc:
            raise OperationError(
                "unsupported_material_graph",
                "Material contains unsupported non-finite shader values",
            ) from exc
        # Validate the complete state before writing any socket.
        PrincipledSummary.model_validate({**original, **requested})
        try:
            apply_principled_values(node, requested)
            bpy.context.view_layer.update()
            stored = principled_values(node).model_dump(mode="json")
            if any(stored[key] != value for key, value in requested.items()):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested shader values",
                )
            return material_summary(material)
        except Exception:
            apply_principled_values(node, {key: original[key] for key in requested})
            bpy.context.view_layer.update()
            raise

    def material_assign(
        self, arguments: MaterialAssignArguments
    ) -> MaterialAssignResult:
        data_mutation_context()
        obj = find_object(arguments.object_name, mutable=False)
        if (
            obj.type not in MATERIAL_OBJECT_TYPES
            or obj.data is None
            or not hasattr(obj.data, "materials")
        ):
            raise OperationError(
                "object_not_material_capable",
                "Object does not support these material slots",
                {"name": arguments.object_name},
            )
        material = find_material(arguments.material_name)
        if material.is_grease_pencil:
            raise OperationError(
                "invalid_arguments",
                "Grease Pencil materials require a Grease Pencil assignment API",
            )
        if bpy.context.mode != "OBJECT" or not obj.is_editable:
            raise OperationError(
                "invalid_context",
                "Material assignment requires an editable object in Object Mode",
            )
        data = obj.data
        count = len(obj.material_slots)
        try:
            index = arguments.checked_slot(count)
        except ValueError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        append = index == count
        if append and (not data.is_editable or obj.is_property_readonly("data")):
            raise OperationError(
                "invalid_context", "Cannot extend this object's material slots"
            )
        if not append:
            slot = obj.material_slots[index]
            if slot.is_property_readonly("link") or (
                slot.link == "OBJECT" and slot.is_property_readonly("material")
            ):
                raise OperationError("invalid_context", "Material slot is not editable")
        # Replacements are object overrides. Extending shared geometry must not
        # change another object's slot count or face-index interpretation.
        working = data.copy() if append and data.users > 1 else data
        indices = material_index_snapshot(data) if append and working == data else []
        old_slot = (
            None
            if append
            else (obj.material_slots[index].link, obj.material_slots[index].material)
        )
        previous_active = obj.active_material_index
        try:
            if working != data:
                obj.data = working
            if append:
                working.materials.append(None)
            slot = obj.material_slots[index]
            slot.link = "OBJECT"
            # A DATA-linked slot on linked geometry becomes writable only after
            # switching this local object to its own material override.
            if slot.is_property_readonly("material"):
                raise OperationError("invalid_context", "Material slot is not editable")
            slot.material = material
            bpy.context.view_layer.update()
            if (
                len(obj.material_slots) != count + int(append)
                or slot.link != "OBJECT"
                or slot.material != material
            ):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested assignment",
                )
            return assignment_result(obj, index, material)
        except Exception:
            if working != data:
                obj.data = data
                bpy.data.batch_remove(ids=(working,))
            elif append:
                if len(working.materials) > count:
                    if count == 0:
                        # pop's last-slot path does not sync object slot lengths.
                        working.materials.clear()
                    else:
                        working.materials.pop(index=count)
                    for item, property_name, value in indices:
                        setattr(item, property_name, value)
            elif old_slot is not None:
                slot = obj.material_slots[index]
                slot.link = old_slot[0]
                if slot.material != old_slot[1]:
                    slot.material = old_slot[1]
            obj.active_material_index = previous_active
            bpy.context.view_layer.update()
            raise

    def light_inspect(self) -> LightInspectResult:
        main_thread()
        bpy.context.view_layer.update()
        return LightInspectResult(
            lights=[
                light_summary(obj)
                for obj in sorted(bpy.context.scene.objects, key=lambda obj: obj.name)
                if obj.type == "LIGHT"
            ]
        )

    def light_create(self, arguments: LightCreateArguments) -> LightSummary:
        data_mutation_context()
        if bpy.context.mode != "OBJECT" or not bpy.context.collection.is_editable:
            raise OperationError(
                "invalid_context", "Light creation requires editable Object Mode"
            )
        if arguments.name is not None and arguments.name in bpy.data.objects:
            raise OperationError(
                "invalid_arguments",
                "Object name already exists",
                {"name": arguments.name},
            )
        settings = arguments.settings_state().writable_values()
        data = bpy.data.lights.new(arguments.name or "Light", arguments.type.upper())
        obj = None
        try:
            apply_light_values(data, settings)
            obj = bpy.data.objects.new(arguments.name or "Light", data)
            if arguments.name is not None and obj.name != arguments.name:
                raise OperationError(
                    "invalid_arguments",
                    "Blender cannot store the requested name exactly",
                )
            bpy.context.collection.objects.link(obj)
            obj.location = arguments.location
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = arguments.rotation
            obj.scale = arguments.scale
            bpy.context.view_layer.update()
            stored = light_values(data)
            if any(stored[key] != value for key, value in settings.items()):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested light settings",
                )
            return light_summary(obj)
        except Exception:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(data)
            raise

    def light_configure(self, arguments: LightConfigureArguments) -> LightSummary:
        data_mutation_context()
        obj = find_object(arguments.name, mutable=False)
        if obj.type != "LIGHT":
            raise OperationError(
                "object_not_light", "Object is not a light", {"name": arguments.name}
            )
        if obj.library is not None and obj.override_library is None:
            raise OperationError(
                "invalid_context", "Linked objects cannot be modified directly"
            )
        data = obj.data
        fields = arguments.model_fields_set - {"name"}
        original = light_values(data)
        values = {
            **original,
            **arguments.model_dump(mode="json", exclude={"name"}, exclude_unset=True),
        }
        try:
            settings = light_state(
                LIGHT_TYPES[str(data.type)], values, fields
            ).writable_values()
        except ValueError as exc:
            message = (
                "; ".join(error["msg"] for error in exc.errors(include_input=False))
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            raise OperationError(
                "invalid_arguments", message, {"name": arguments.name}
            ) from exc
        if not data.is_editable or any(
            data.is_property_readonly(_LIGHT_FIELDS[key]) for key in fields
        ):
            raise OperationError("invalid_context", "Light data is not editable")
        if not fields:
            return light_summary(obj)
        if data.users > 1 and obj.is_property_readonly("data"):
            raise OperationError(
                "invalid_context", "Cannot make light data independent"
            )
        requested = {key: settings[key] for key in fields}
        working = data.copy() if data.users > 1 else data
        try:
            apply_light_values(working, requested)
            if working != data:
                obj.data = working
            bpy.context.view_layer.update()
            stored = light_values(working)
            if any(stored[key] != value for key, value in requested.items()):
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested light settings",
                )
            return light_summary(obj)
        except Exception:
            if working != data:
                if obj.data != data:
                    obj.data = data
                bpy.data.lights.remove(working)
            else:
                apply_light_values(data, {key: original[key] for key in fields})
            bpy.context.view_layer.update()
            raise

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
        data_mutation_context()
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
        data_mutation_context()
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
        data_mutation_context()
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
    require_blender(bpy.app.version)
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
