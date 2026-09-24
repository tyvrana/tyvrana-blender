"""The Blender API boundary. All entry points execute on the main thread."""

import logging
import os
import threading
from pathlib import Path
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
    OperationSuccess,
    ProofHostControl,
    ProofHostStart,
    ProofHostStatus,
    ResourceInspectionRequest,
    ResourceInspectionResult,
)

from . import (
    instances,
    material_author,
    mesh,
    modifiers,
    multires,
    raycast,
    remesh,
    retopo,
    rig,
    sculpt,
    sculpt_regions,
    shader,
    uv,
    weights,
)
from .artifacts import ArtifactSpool
from .assembly_models import (
    AssemblyConfigureArguments,
    AssemblyCreateArguments,
    AssemblyInspectArguments,
    AssemblyResult,
)
from .attestation_model import (
    AttestationJobArguments,
    AttestationJobStatus,
    DocumentAttestationResult,
)
from .bake_models import (
    BakeImageArguments,
    BakeInspectArguments,
    BakeInspectResult,
    BakeJobStatus,
    BakeStatusArguments,
    ImageSaveArguments,
    ImageSaveResult,
)
from .binding_models import ProjectBindArguments, ProjectBindResult
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
from .cleanup_models import CleanupArguments, CleanupResult
from .compatibility import require_blender
from .constraint_models import (
    ConstraintsConfigureArguments,
    ConstraintsInspectArguments,
    ConstraintsRemoveArguments,
    ConstraintsResult,
    PoseMatchArguments,
    PoseMatchResult,
    SpaceSwitchArguments,
)
from .control_rig_models import (
    ControlRigConfigureArguments,
    ControlRigInspectArguments,
    ControlRigResult,
    ControlRigSwitchArguments,
)
from .corrective_models import (
    CaptureTargetArguments,
    CaptureTargetResult,
    DeformationCompareArguments,
    DeformationCompareResult,
    ShapeKeysEditArguments,
    ShapeKeysEditResult,
    ShapeKeysInspectArguments,
    ShapeKeysRemoveArguments,
    ShapeKeysRemoveResult,
    ShapeKeysSummary,
)
from .curve_models import (
    CurveConfigureArguments,
    CurveCreateArguments,
    CurveInspectArguments,
    CurveInspectResult,
    CurveRemoveArguments,
    CurveRemoveResult,
    CurveResult,
)
from .deformation_sweep_models import DeformationSweepArguments, DeformationSweepResult
from .delivery_models import FileAuditArguments, FileAuditResult
from .dispatch import CommandQueue
from .dynamics_models import (
    DynamicsBakeArguments,
    DynamicsCache,
    DynamicsJobArguments,
    DynamicsJobStatus,
    DynamicsObjectArguments,
)
from .errors import OperationError
from .extension_models import (
    ExtensionReloadArguments,
    ExtensionReloadResult,
    ExtensionState,
)
from .file_models import (
    FileNewArguments,
    FileOpenArguments,
    FileSaveArguments,
    FileState,
)
from .form_models import (
    FormConfigureArguments,
    FormCreateArguments,
    FormInspectArguments,
    FormJobArguments,
    FormJobStatus,
    FormResult,
    ReferenceCompareArguments,
    ReferenceCompareResult,
)
from .geometry_qa_models import GeometryInspectArguments, GeometryInspectResult
from .growth_layers_models import (
    LayerCache,
    LayerCorrectArguments,
    LayerObjectArguments,
)
from .growth_models import (
    GrowthConfigureArguments,
    GrowthCreateArguments,
    GrowthDelta,
    GrowthInspectArguments,
    GrowthInspectResult,
    GrowthRemoveArguments,
    GrowthRemoveResult,
    GrowthSampleArguments,
    GrowthSampleResult,
)
from .image_models import (
    ALPHA_MODES,
    ImageConfigureArguments,
    ImageCreateArguments,
    ImageFromArtifactArguments,
    ImageInspectResult,
    ImagePreviewArguments,
    ImagePreviewResult,
    ImageSummary,
)
from .incoming import input_path
from .inspection import page
from .instance_models import (
    MeshCreateArguments,
    SurfaceInstancesConfigureArguments,
    SurfaceInstancesCreateArguments,
    SurfaceInstancesInspectArguments,
    SurfaceInstancesSummary,
)
from .joint_models import (
    JointConfigureArguments,
    StructureInspectArguments,
    StructureSummary,
)
from .layer_models import (
    LayerCaptureArguments,
    LayerInspectArguments,
    LayerInspectResult,
    LayerReferencesResult,
    LayerRemoveArguments,
)
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
from .loft_models import (
    LoftConfigureArguments,
    LoftCreateArguments,
    LoftInspectArguments,
    LoftResult,
)
from .material_author_models import (
    AssignBatchArguments,
    AssignBatchResult,
    GraphAuthorArguments,
    MaterialAuthorArguments,
    MaterialCopyArguments,
    MaterialRemoveArguments,
    MaterialRemoveResult,
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
from .mechanics_models import ContactArguments, ContactResult, FitArguments, FitResult
from .mesh_models import (
    MeshEditResult,
    MeshInspectArguments,
    MeshNormalsArguments,
    MeshQueryArguments,
    MeshQueryResult,
    MeshSelectionArguments,
    MeshSummary,
)
from .models import (
    ConnectionConfig,
    CreateArguments,
    DeleteArguments,
    DeleteResult,
    InspectArguments,
    ObjectSummary,
    RenderArguments,
    SceneInspectArguments,
    SceneSummary,
    TransformArguments,
)
from .modifier_models import (
    EvaluatedMeshArguments,
    EvaluatedMeshSummary,
    ModifierApplyArguments,
    ModifierApplyResult,
    ModifierBatchCreateArguments,
    ModifierBatchCreateResult,
    ModifierConfigureArguments,
    ModifierCreateArguments,
    ModifierInspectArguments,
    ModifierInspectResult,
    ModifierMoveArguments,
    ModifierRemoveArguments,
    ModifierRemoveResult,
    ModifierSummary,
)
from .motion_models import (
    ActionAssignArguments,
    ActionEditArguments,
    ActionInspectArguments,
    ActionRemoveArguments,
    ActionResult,
    CouplingConfigureArguments,
    CouplingInspectArguments,
    CouplingInspectResult,
    MechanismSolution,
    MechanismSolveArguments,
    MotionNames,
    MotionRemoveArguments,
    MotionSampleArguments,
    MotionSampleResult,
    PropertiesArguments,
    TimelineArguments,
    TimelineInspectArguments,
    TimelineState,
)
from .mutation_models import MutationArguments, MutationJobStatus
from .operations import Response, execute, registration
from .organization_models import (
    CollectionConfigureArguments,
    CollectionCreateArguments,
    CollectionInspectArguments,
    CollectionInspectResult,
    CollectionRemoveArguments,
    CollectionResult,
    ObjectSetConfigureArguments,
    ObjectSetConfigureResult,
    ObjectSetCreateArguments,
    ObjectSetCreateResult,
    ObjectSetInspectArguments,
    ObjectSetInspectResult,
    ObjectSetRemoveArguments,
    ObjectSetRemoveResult,
    OrganizationRemoveResult,
)
from .placement_models import PlacementArguments, PlacementResult
from .raster import raster_size
from .reference_models import (
    ConstructionReport,
    LandmarkDeriveArguments,
    LandmarkInspectArguments,
    LandmarkInspectResult,
    LandmarkResult,
    LandmarkSetArguments,
    MeasurementArguments,
    MeasurementResult,
    NamedRemoveArguments,
    NamedRemoveResult,
    ObservationInspectArguments,
    ObservationResult,
    ObservationSetArguments,
    ObservationWriteResult,
    ReferenceCalibrateArguments,
    ReferenceCalibrateResult,
    ReferenceConfigureArguments,
    ReferenceCreateArguments,
    ReferenceInspectArguments,
    ReferenceInspectResult,
    ReferenceResult,
    RegistrationArguments,
    RegistrationResult,
    UnitsConfigureArguments,
    UnitsSummary,
)
from .remesh_models import (
    VoxelRemeshArguments,
    VoxelRemeshInspectArguments,
    VoxelRemeshResult,
    VoxelRemeshSummary,
)
from .render_models import (
    RenderDevicesResult,
    RenderJobArguments,
    RenderJobStatus,
    RenderStatusArguments,
)
from .restore_models import RestoreArguments, RestoreJobStatus
from .retopo_models import (
    RetopoCreateArguments,
    RetopoCreateResult,
    RetopoEditArguments,
    RetopoEditResult,
    RetopoInspectArguments,
    RetopoSummary,
)
from .rig_models import (
    ArmatureBindArguments,
    ArmatureCreateArguments,
    ArmatureInspectArguments,
    ArmaturePoseArguments,
    ArmatureRestArguments,
    ArmatureSummary,
    BindingSummary,
    DeformationInspectArguments,
    DeformationSummary,
)
from .sculpt_models import (
    FaceSetsAssignArguments,
    FaceSetsAssignResult,
    FaceSetsInitializeArguments,
    FaceSetsInspectArguments,
    FaceSetsSummary,
    MaskClearArguments,
    MaskInspectArguments,
    MaskInvertArguments,
    MaskStrokeArguments,
    MaskStrokeResult,
    MultiresConfigureArguments,
    MultiresCreateArguments,
    MultiresInspectArguments,
    MultiresSubdivideArguments,
    MultiresSummary,
    RaycastArguments,
    RaycastResult,
    SculptFilterArguments,
    SculptFilterResult,
    SculptInspectArguments,
    SculptMaskSummary,
    SculptStrokeArguments,
    SculptStrokeResult,
    SculptSummary,
)
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
from .surface_deform_models import (
    SurfaceBindArguments,
    SurfaceBindings,
    SurfaceInspectArguments,
)
from .surface_models import (
    SurfaceConfigureArguments,
    SurfaceCreateArguments,
    SurfaceNetworkInspectArguments,
    SurfaceResult,
)
from .topology_models import (
    MeshInsertLoopsArguments,
    TopologyInspectArguments,
    TopologySummary,
)
from .transport import WorkerProcess
from .uv_models import (
    UVCreateArguments,
    UVInspectArguments,
    UVInspectResult,
    UVLayoutArguments,
    UVLayoutResult,
    UVPackArguments,
    UVPackResult,
    UVSetActiveArguments,
    UVUnwrapArguments,
)
from .viewport_models import (
    ViewportCaptureArguments,
    ViewportCaptureResult,
    ViewportConfigureArguments,
    ViewportFrameArguments,
    ViewportInspectArguments,
    ViewportInspection,
    ViewportState,
)
from .volume_models import (
    VolumeInspectArguments,
    VolumeInspectResult,
    VolumeSnapshotArguments,
    VolumeSnapshotResult,
)
from .weight_models import (
    WeightsAssignArguments,
    WeightsAssignment,
    WeightsInspectArguments,
    WeightsSummary,
)
from .weight_transfer_models import (
    GroupsConfigureArguments,
    GroupsResult,
    WeightsTransferArguments,
    WeightsTransferResult,
)

logger = logging.getLogger(__name__)
INSTANCE_ID = f"blender-{uuid4()}"

from .deployment import identity  # noqa: E402

IMPLEMENTATION_BUILD = identity(Path(__file__).parent)


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
    assignments = [
        (str(obj.name), index)
        for obj in sorted(bpy.data.objects, key=lambda obj: obj.name)
        for index, slot in enumerate(obj.material_slots)
        if slot.material == material
    ]
    return MaterialSummary(
        name=str(material.name),
        graph=material_author.inspect(material),
        surface=surface,
        principled=values,
        assignments=[
            MaterialAssignment(object=name, slot=slot)
            for name, slot in assignments[:32]
        ],
        assignment_count=len(assignments),
        assignments_truncated=len(assignments) > 32,
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
    from .image_buffers import decoded

    with decoded(image):
        return ImageSummary(
            name=str(image.name),
            source=str(image.source).lower(),
            width=int(image.size[0]),
            height=int(image.size[1]),
            channels=int(image.channels),
            has_alpha=image.depth // (32 if image.is_float else 8) in {2, 4},
            is_float=bool(image.is_float),
            color_space=(
                None
                if image.source == "VIEWER"
                else str(image.colorspace_settings.name)
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
    def proof_host_start(self, arguments: ProofHostStart) -> ProofHostStatus:
        from . import proof_hosts

        return proof_hosts.start(arguments)

    def proof_host_status(self, arguments: ProofHostControl) -> ProofHostStatus:
        from . import proof_hosts

        return proof_hosts.status(arguments)

    def proof_host_stop(self, arguments: ProofHostControl) -> ProofHostStatus:
        from . import proof_hosts

        return proof_hosts.stop(arguments)

    def growth_layers_correct(self, arguments: LayerCorrectArguments) -> LayerCache:
        from . import growth_layers

        return growth_layers.correct(arguments)

    def growth_layers_inspect(self, arguments: LayerObjectArguments) -> LayerCache:
        from . import growth_layers

        return growth_layers.inspect(arguments)

    def growth_layers_clear(self, arguments: LayerObjectArguments) -> LayerCache:
        from . import growth_layers

        return growth_layers.clear(arguments)

    def growth_dynamics_bake(
        self, arguments: DynamicsBakeArguments
    ) -> DynamicsJobStatus:
        from . import growth_dynamics

        return growth_dynamics.start(arguments)

    def growth_dynamics_inspect(
        self, arguments: DynamicsObjectArguments
    ) -> DynamicsCache:
        from . import growth_dynamics

        return growth_dynamics.inspect(arguments)

    def growth_dynamics_status(
        self, arguments: DynamicsJobArguments
    ) -> DynamicsJobStatus:
        from . import growth_dynamics

        return growth_dynamics.status(arguments.job_id)

    def growth_dynamics_cancel(
        self, arguments: DynamicsJobArguments
    ) -> DynamicsJobStatus:
        from . import growth_dynamics

        return growth_dynamics.cancel(arguments.job_id)

    def growth_dynamics_clear(
        self, arguments: DynamicsObjectArguments
    ) -> DynamicsCache:
        from . import growth_dynamics

        return growth_dynamics.clear(arguments)

    def growth_create(self, arguments: GrowthCreateArguments) -> GrowthDelta:
        from . import growth

        return growth.create(arguments)

    def growth_configure(self, arguments: GrowthConfigureArguments) -> GrowthDelta:
        from . import growth

        return growth.configure(arguments)

    def growth_inspect(self, arguments: GrowthInspectArguments) -> GrowthInspectResult:
        from . import growth

        return growth.inspect(arguments)

    def growth_remove(self, arguments: GrowthRemoveArguments) -> GrowthRemoveResult:
        from . import growth

        return growth.remove(arguments)

    def growth_sample(self, arguments: GrowthSampleArguments) -> GrowthSampleResult:
        from . import growth_qa

        return growth_qa.sample(arguments)

    def control_rig_configure(
        self, arguments: ControlRigConfigureArguments
    ) -> ControlRigResult:
        from . import control_rig

        return control_rig.configure(arguments)

    def control_rig_inspect(
        self, arguments: ControlRigInspectArguments
    ) -> ControlRigResult:
        from . import control_rig

        return control_rig.inspect(arguments)

    def control_rig_switch(
        self, arguments: ControlRigSwitchArguments
    ) -> ControlRigResult:
        from . import control_rig

        return control_rig.switch(arguments)

    def control_rig_remove(
        self, arguments: ControlRigInspectArguments
    ) -> ControlRigResult:
        from . import control_rig

        return control_rig.remove(arguments)

    def constraint_configure(
        self, arguments: ConstraintsConfigureArguments
    ) -> ConstraintsResult:
        from . import rig_constraints

        return rig_constraints.configure(arguments)

    def constraint_inspect(
        self, arguments: ConstraintsInspectArguments
    ) -> ConstraintsResult:
        from . import rig_constraints

        return rig_constraints.inspect(arguments)

    def constraint_remove(
        self, arguments: ConstraintsRemoveArguments
    ) -> ConstraintsResult:
        from . import rig_constraints

        return rig_constraints.remove(arguments)

    def pose_match(self, arguments: PoseMatchArguments) -> PoseMatchResult:
        from . import rig_constraints

        return rig_constraints.match(arguments)

    def space_switch(self, arguments: SpaceSwitchArguments) -> ConstraintsResult:
        from . import rig_constraints

        return rig_constraints.switch_space(arguments)

    def mesh_cleanup(self, arguments: CleanupArguments) -> CleanupResult:
        from . import cleanup

        return cleanup.cleanup(arguments)

    def assembly_create(self, arguments: AssemblyCreateArguments) -> AssemblyResult:
        from . import assembly

        return assembly.create(arguments)

    def assembly_configure(
        self, arguments: AssemblyConfigureArguments
    ) -> AssemblyResult:
        from . import assembly

        return assembly.configure(arguments)

    def assembly_inspect(self, arguments: AssemblyInspectArguments) -> AssemblyResult:
        from . import assembly

        return assembly.inspect(arguments)

    def object_set_place(self, arguments: PlacementArguments) -> PlacementResult:
        from . import placement

        return placement.place(arguments)

    def surface_create(self, arguments: SurfaceCreateArguments) -> SurfaceResult:
        from . import surfaces

        return surfaces.create(arguments)

    def surface_configure(self, arguments: SurfaceConfigureArguments) -> SurfaceResult:
        from . import surfaces

        return surfaces.configure(arguments)

    def surface_inspect(
        self, arguments: SurfaceNetworkInspectArguments
    ) -> SurfaceResult:
        from . import surfaces

        return surfaces.inspect(arguments)

    def reference_compare(
        self, arguments: ReferenceCompareArguments
    ) -> tuple[ReferenceCompareResult, ArtifactDescriptor | None]:
        from . import reference_compare

        return reference_compare.compare(arguments, self.spool)

    def form_create(self, arguments: FormCreateArguments) -> FormJobStatus:
        from . import form_jobs

        return form_jobs.start(arguments)

    def form_configure(self, arguments: FormConfigureArguments) -> FormJobStatus:
        from . import form_jobs

        return form_jobs.start(arguments)

    def form_status(self, arguments: FormJobArguments) -> FormJobStatus:
        from . import form_jobs

        return form_jobs.status(arguments.job_id)

    def form_cancel(self, arguments: FormJobArguments) -> FormJobStatus:
        from . import form_jobs

        return form_jobs.cancel(arguments.job_id)

    def form_inspect(self, arguments: FormInspectArguments) -> FormResult:
        from . import forms

        return forms.inspect(arguments)

    def loft_create(self, arguments: LoftCreateArguments) -> LoftResult:
        from . import loft

        return loft.create(arguments)

    def loft_configure(self, arguments: LoftConfigureArguments) -> LoftResult:
        from . import loft

        return loft.configure(arguments)

    def loft_inspect(self, arguments: LoftInspectArguments) -> LoftResult:
        from . import loft

        return loft.inspect(arguments)

    def curve_create(self, arguments: CurveCreateArguments) -> CurveResult:
        from . import curves

        return curves.create(arguments)

    def curve_configure(self, arguments: CurveConfigureArguments) -> CurveResult:
        from . import curves

        return curves.configure(arguments)

    def curve_inspect(self, arguments: CurveInspectArguments) -> CurveInspectResult:
        from . import curves

        return curves.inspect(arguments)

    def curve_remove(self, arguments: CurveRemoveArguments) -> CurveRemoveResult:
        from . import curves

        return curves.remove(arguments)

    def armature_configure_rest(
        self, arguments: ArmatureRestArguments
    ) -> ArmatureSummary:
        from .joints import edit_rest

        return edit_rest(arguments)

    def armature_configure_joints(
        self, arguments: JointConfigureArguments
    ) -> ArmatureSummary:
        from .joints import configure

        return configure(arguments)

    def armature_inspect_structure(
        self, arguments: StructureInspectArguments
    ) -> StructureSummary:
        from .joints import inspect_structure

        return inspect_structure(arguments)

    def collection_create(
        self, arguments: CollectionCreateArguments
    ) -> CollectionResult:
        from .organization import collection_create

        return collection_create(arguments)

    def collection_configure(
        self, arguments: CollectionConfigureArguments
    ) -> CollectionResult:
        from .organization import collection_configure

        return collection_configure(arguments)

    def collection_inspect(
        self, arguments: CollectionInspectArguments
    ) -> CollectionInspectResult:
        from .organization import collection_inspect

        return collection_inspect(arguments)

    def collection_remove(
        self, arguments: CollectionRemoveArguments
    ) -> OrganizationRemoveResult:
        from .organization import collection_remove

        return collection_remove(arguments)

    def object_set_create(
        self, arguments: ObjectSetCreateArguments
    ) -> ObjectSetCreateResult:
        from .organization import object_set_create

        return object_set_create(arguments)

    def object_set_configure(
        self, arguments: ObjectSetConfigureArguments
    ) -> ObjectSetConfigureResult:
        from .organization import object_set_configure

        return object_set_configure(arguments)

    def object_set_inspect(
        self, arguments: ObjectSetInspectArguments
    ) -> ObjectSetInspectResult:
        from .organization import object_set_inspect

        return object_set_inspect(arguments)

    def object_set_remove(
        self, arguments: ObjectSetRemoveArguments
    ) -> ObjectSetRemoveResult:
        from .organization import object_set_remove

        return object_set_remove(arguments)

    def reference_create(self, arguments: ReferenceCreateArguments) -> ReferenceResult:
        main_thread()
        from . import references

        return references.create(arguments)

    def reference_configure(
        self, arguments: ReferenceConfigureArguments
    ) -> ReferenceResult:
        main_thread()
        from . import references

        return references.configure(arguments)

    def reference_inspect(
        self, arguments: ReferenceInspectArguments
    ) -> ReferenceInspectResult:
        main_thread()
        from . import references

        return references.inspect(arguments)

    def reference_remove(self, arguments: NamedRemoveArguments) -> NamedRemoveResult:
        main_thread()
        from . import references

        return references.remove(arguments, references.REFERENCE)

    def reference_calibrate(
        self, arguments: ReferenceCalibrateArguments
    ) -> ReferenceCalibrateResult:
        main_thread()
        from . import references

        return references.calibrate(arguments)

    def reference_register(
        self, arguments: RegistrationArguments
    ) -> RegistrationResult:
        from . import construction

        return construction.register(arguments)

    def reference_registration_inspect(
        self, arguments: ReferenceInspectArguments
    ) -> RegistrationResult:
        from . import construction

        return construction.registration_inspect(arguments)

    def reference_observation_set(
        self, arguments: ObservationSetArguments
    ) -> ObservationWriteResult:
        from . import construction

        return construction.set_observations(arguments)

    def reference_observation_inspect(
        self, arguments: ObservationInspectArguments
    ) -> ObservationResult:
        from . import construction

        return construction.inspect_observations(arguments)

    def reference_observation_remove(
        self, arguments: NamedRemoveArguments
    ) -> NamedRemoveResult:
        from . import construction

        return construction.remove_observations(arguments)

    def landmark_derive(self, arguments: LandmarkDeriveArguments) -> ConstructionReport:
        from . import construction

        return construction.derive(arguments)

    def landmark_set(self, arguments: LandmarkSetArguments) -> LandmarkResult:
        main_thread()
        from . import references

        return references.set_landmarks(arguments)

    def landmark_inspect(
        self, arguments: LandmarkInspectArguments
    ) -> LandmarkInspectResult:
        main_thread()
        from . import references

        return references.inspect_landmarks(arguments)

    def landmark_remove(self, arguments: NamedRemoveArguments) -> NamedRemoveResult:
        main_thread()
        from . import references

        return references.remove(arguments, references.LANDMARK)

    def measurement_inspect(self, arguments: MeasurementArguments) -> MeasurementResult:
        main_thread()
        from . import references

        return references.measure(arguments)

    def scene_configure_units(self, arguments: UnitsConfigureArguments) -> UnitsSummary:
        main_thread()
        from . import references

        return references.configure_units(arguments)

    def document_restore(
        self, arguments: RestoreArguments, request: OperationRequest
    ) -> RestoreJobStatus:
        main_thread()
        from . import mutation_jobs

        return mutation_jobs.start_restore(self, arguments, request)

    def document_restore_status(
        self, arguments: AttestationJobArguments
    ) -> RestoreJobStatus:
        main_thread()
        from . import mutation_jobs

        return RestoreJobStatus.model_validate(
            mutation_jobs.status(arguments.job_id).model_dump()
        )

    def document_mutate(
        self, arguments: MutationArguments, request: OperationRequest
    ) -> MutationJobStatus:
        main_thread()
        from . import mutation_jobs

        return mutation_jobs.start(self, arguments, request)

    def document_mutation_status(
        self, arguments: AttestationJobArguments
    ) -> MutationJobStatus:
        main_thread()
        from . import mutation_jobs

        return mutation_jobs.status(arguments.job_id)

    def document_attest(self) -> DocumentAttestationResult:
        main_thread()
        from . import attestation_jobs

        return attestation_jobs.start()

    def document_attest_status(
        self, arguments: AttestationJobArguments
    ) -> AttestationJobStatus:
        main_thread()
        from . import attestation_jobs

        return attestation_jobs.status(arguments.job_id)

    def document_attest_cancel(
        self, arguments: AttestationJobArguments
    ) -> AttestationJobStatus:
        main_thread()
        from . import attestation_jobs

        return attestation_jobs.cancel(arguments.job_id)

    def extension_inspect(self) -> ExtensionState:
        main_thread()
        from . import lifecycle
        from .attestation import identity
        from .bindings import project_id

        return ExtensionState.model_validate(
            {
                **lifecycle.inspect(),
                "host_session_id": identity()["host"],
                "document_session_id": identity()["document"],
                "host_pid": os.getpid(),
                "background": bool(bpy.app.background),
                "window_count": len(bpy.context.window_manager.windows),
                "application_version": str(bpy.app.version_string),
                "project_path": str(bpy.data.filepath) or None,
                "project_id": project_id(),
            }
        )

    def extension_reload(
        self, arguments: ExtensionReloadArguments, request_id: str
    ) -> ExtensionReloadResult:
        main_thread()
        from . import lifecycle

        try:
            return ExtensionReloadResult.model_validate(
                lifecycle.request_reload(arguments.expected_build, request_id)
            )
        except (ValueError, OSError, SyntaxError) as exc:
            raise OperationError("extension_update_invalid", str(exc)) from exc

    def file_inspect(self) -> FileState:
        main_thread()
        from . import files

        return files.inspect()

    def file_audit(self, arguments: FileAuditArguments) -> FileAuditResult:
        from . import delivery

        main_thread()
        return delivery.audit(arguments)

    def project_bind(self, arguments: ProjectBindArguments) -> ProjectBindResult:
        main_thread()
        from . import bindings

        result = bindings.bind(arguments)
        after_save()
        return result

    def resource_inspect(
        self, arguments: ResourceInspectionRequest
    ) -> ResourceInspectionResult:
        main_thread()
        from . import bindings

        return bindings.inspect(arguments)

    def file_save(self, arguments: FileSaveArguments) -> FileState:
        main_thread()
        from . import files

        return files.save(arguments)

    def viewport_configure(
        self, arguments: ViewportConfigureArguments
    ) -> ViewportState:
        main_thread()
        from . import viewport

        return viewport.configure(arguments)

    def viewport_capture(
        self, arguments: ViewportCaptureArguments
    ) -> tuple[ViewportCaptureResult, ArtifactDescriptor]:
        main_thread()
        from . import viewport

        if self.spool is None:
            raise OperationError(
                "artifact_unavailable", "Artifact spool is unavailable"
            )
        return viewport.capture(arguments, self.spool)

    def viewport_inspect(
        self, arguments: ViewportInspectArguments
    ) -> ViewportInspection:
        main_thread()
        from . import viewport

        return viewport.inspect(arguments)

    def viewport_frame(self, arguments: ViewportFrameArguments) -> ViewportState:
        main_thread()
        from . import viewport

        return viewport.frame(arguments)

    def file_new(self, arguments: FileNewArguments) -> FileState:
        global _opening_project
        main_thread()
        from . import files

        _opening_project = True
        try:
            return files.new_project(arguments)
        finally:
            _opening_project = False
            after_save()

    def file_open(self, arguments: FileOpenArguments) -> FileState:
        global _opening_project
        main_thread()
        from . import files

        # Keep the requesting worker and queue alive until the result is sent.
        _opening_project = True
        try:
            return files.open_project(arguments)
        finally:
            _opening_project = False
            after_save()

    def __init__(self, spool: ArtifactSpool | None = None) -> None:
        self.spool = spool

    def scene_raycast(self, arguments: RaycastArguments) -> RaycastResult:
        main_thread()
        return raycast.cast(arguments)

    def multires_inspect(self, arguments: MultiresInspectArguments) -> MultiresSummary:
        main_thread()
        return multires.inspect(modifiers.object_mesh(arguments.object_name))

    def multires_create(self, arguments: MultiresCreateArguments) -> MultiresSummary:
        main_thread()
        return multires.create(modifiers.object_mesh(arguments.object_name), arguments)

    def multires_subdivide(
        self, arguments: MultiresSubdivideArguments
    ) -> MultiresSummary:
        main_thread()
        return multires.subdivide(
            modifiers.object_mesh(arguments.object_name), arguments
        )

    def multires_configure(
        self, arguments: MultiresConfigureArguments
    ) -> MultiresSummary:
        main_thread()
        return multires.configure(
            modifiers.object_mesh(arguments.object_name), arguments
        )

    def retopo_create_target(
        self, arguments: RetopoCreateArguments
    ) -> RetopoCreateResult:
        main_thread()
        return retopo.create_target(arguments)

    def retopo_inspect(self, arguments: RetopoInspectArguments) -> RetopoSummary:
        main_thread()
        return retopo.inspect(arguments)

    def retopo_edit(self, arguments: RetopoEditArguments) -> RetopoEditResult:
        main_thread()
        return retopo.execute(arguments)

    def sculpt_voxel_remesh_inspect(
        self, arguments: VoxelRemeshInspectArguments
    ) -> VoxelRemeshSummary:
        main_thread()
        return remesh.inspect(modifiers.object_mesh(arguments.object_name), arguments)

    def sculpt_voxel_remesh(self, arguments: VoxelRemeshArguments) -> VoxelRemeshResult:
        main_thread()
        return remesh.execute(modifiers.object_mesh(arguments.object_name), arguments)

    def sculpt_mask_inspect(self, arguments: MaskInspectArguments) -> SculptMaskSummary:
        main_thread()
        return sculpt_regions.mask_inspect(modifiers.object_mesh(arguments.object_name))

    def sculpt_mask_clear(self, arguments: MaskClearArguments) -> SculptMaskSummary:
        main_thread()
        return sculpt.execute(modifiers.object_mesh(arguments.object_name), arguments)

    def sculpt_mask_invert(self, arguments: MaskInvertArguments) -> SculptMaskSummary:
        main_thread()
        return sculpt.execute(modifiers.object_mesh(arguments.object_name), arguments)

    def sculpt_mask_stroke(self, arguments: MaskStrokeArguments) -> MaskStrokeResult:
        main_thread()
        return sculpt.execute(modifiers.object_mesh(arguments.object_name), arguments)

    def sculpt_face_sets_inspect(
        self, arguments: FaceSetsInspectArguments
    ) -> FaceSetsSummary:
        main_thread()
        return sculpt_regions.face_sets_inspect(
            modifiers.object_mesh(arguments.object_name)
        )

    def sculpt_face_sets_assign(
        self, arguments: FaceSetsAssignArguments
    ) -> FaceSetsAssignResult:
        main_thread()
        return sculpt_regions.face_sets_assign(
            modifiers.object_mesh(arguments.object_name), arguments
        )

    def sculpt_face_sets_initialize(
        self, arguments: FaceSetsInitializeArguments
    ) -> FaceSetsSummary:
        main_thread()
        return sculpt_regions.face_sets_initialize(
            modifiers.object_mesh(arguments.object_name), arguments
        )

    def sculpt_filter(self, arguments: SculptFilterArguments) -> SculptFilterResult:
        main_thread()
        return sculpt.execute(modifiers.object_mesh(arguments.object_name), arguments)

    def sculpt_inspect(self, arguments: SculptInspectArguments) -> SculptSummary:
        main_thread()
        return sculpt.inspect(modifiers.object_mesh(arguments.object_name))

    def sculpt_stroke(self, arguments: SculptStrokeArguments) -> SculptStrokeResult:
        main_thread()
        return sculpt.stroke(modifiers.object_mesh(arguments.object_name), arguments)

    def modifier_inspect(
        self, arguments: ModifierInspectArguments
    ) -> ModifierInspectResult:
        main_thread()
        return modifiers.inspect(modifiers.object_mesh(arguments.object_name))

    def modifier_create_batch(
        self, arguments: ModifierBatchCreateArguments
    ) -> ModifierBatchCreateResult:
        main_thread()
        return modifiers.create_batch(arguments)

    def modifier_create(self, arguments: ModifierCreateArguments) -> ModifierSummary:
        main_thread()
        return modifiers.create(modifiers.object_mesh(arguments.object_name), arguments)

    def modifier_configure(
        self, arguments: ModifierConfigureArguments
    ) -> ModifierSummary:
        main_thread()
        return modifiers.configure(
            modifiers.object_mesh(arguments.object_name), arguments
        )

    def modifier_move(self, arguments: ModifierMoveArguments) -> ModifierInspectResult:
        main_thread()
        return modifiers.move(
            modifiers.object_mesh(arguments.object_name),
            arguments.modifier_name,
            arguments.index,
        )

    def modifier_remove(
        self, arguments: ModifierRemoveArguments
    ) -> ModifierRemoveResult:
        main_thread()
        return modifiers.remove(
            modifiers.object_mesh(arguments.object_name), arguments.modifier_name
        )

    def modifier_apply(self, arguments: ModifierApplyArguments) -> ModifierApplyResult:
        main_thread()
        return modifiers.apply(
            modifiers.object_mesh(arguments.object_name), arguments.modifier_name
        )

    def mesh_inspect_evaluated(
        self, arguments: EvaluatedMeshArguments
    ) -> EvaluatedMeshSummary:
        main_thread()
        return modifiers.inspect_evaluated(
            modifiers.object_mesh(arguments.object_name), arguments.uv_map
        )

    def weights_assign(self, arguments: WeightsAssignArguments) -> WeightsAssignment:
        main_thread()
        return weights.assign(arguments)

    def weights_inspect(self, arguments: WeightsInspectArguments) -> WeightsSummary:
        main_thread()
        return weights.inspect(arguments)

    def armature_create(self, arguments: ArmatureCreateArguments) -> ArmatureSummary:
        main_thread()
        return rig.create(arguments)

    def armature_inspect(self, arguments: ArmatureInspectArguments) -> ArmatureSummary:
        main_thread()
        return rig.inspect(
            rig.armature(arguments.object_name),
            arguments.bone_names,
            arguments.sample_limit,
        )

    def armature_bind(self, arguments: ArmatureBindArguments) -> BindingSummary:
        main_thread()
        return rig.bind(arguments)

    def armature_pose(self, arguments: ArmaturePoseArguments) -> ArmatureSummary:
        main_thread()
        return rig.pose(arguments)

    def timeline_inspect(self, arguments: TimelineInspectArguments) -> TimelineState:
        main_thread()
        from . import timeline

        return timeline.inspect(arguments)

    def timeline_configure(self, arguments: TimelineArguments) -> TimelineState:
        main_thread()
        from . import timeline

        return timeline.configure(arguments)

    def motion_set_properties(self, arguments: PropertiesArguments) -> MotionNames:
        main_thread()
        from . import couplings

        return couplings.set_properties(arguments)

    def coupling_configure(self, arguments: CouplingConfigureArguments) -> MotionNames:
        main_thread()
        from . import couplings

        return couplings.configure(arguments)

    def coupling_inspect(
        self, arguments: CouplingInspectArguments
    ) -> CouplingInspectResult:
        main_thread()
        from . import couplings

        return couplings.inspect(arguments)

    def coupling_solve(self, arguments: MechanismSolveArguments) -> MechanismSolution:
        main_thread()
        from . import couplings

        return couplings.solve(arguments)

    def coupling_remove(self, arguments: MotionRemoveArguments) -> MotionNames:
        main_thread()
        from . import couplings

        return couplings.remove(arguments)

    def action_edit(self, arguments: ActionEditArguments) -> ActionResult:
        main_thread()
        from . import actions

        return actions.edit(arguments)

    def action_assign(self, arguments: ActionAssignArguments) -> MotionNames:
        main_thread()
        from . import actions

        return actions.assign(arguments)

    def action_inspect(self, arguments: ActionInspectArguments) -> ActionResult:
        main_thread()
        from . import actions

        return actions.inspect(arguments)

    def action_remove(self, arguments: ActionRemoveArguments) -> MotionNames:
        main_thread()
        from . import actions

        return actions.remove(arguments)

    def motion_sample(self, arguments: MotionSampleArguments) -> MotionSampleResult:
        main_thread()
        from . import motion

        return motion.sample(arguments)

    def geometry_fit(self, arguments: FitArguments) -> FitResult:
        main_thread()
        from . import mechanics_fit

        return mechanics_fit.inspect(arguments)

    def contact_inspect(self, arguments: ContactArguments) -> ContactResult:
        main_thread()
        from . import mechanics_contact

        return mechanics_contact.inspect(arguments)

    def geometry_inspect(
        self, arguments: GeometryInspectArguments
    ) -> GeometryInspectResult:
        main_thread()
        from . import geometry_qa

        return geometry_qa.inspect(arguments)

    def volume_inspect(self, arguments: VolumeInspectArguments) -> VolumeInspectResult:
        from . import volumes

        return volumes.inspect(arguments)

    def volume_snapshot(
        self, arguments: VolumeSnapshotArguments
    ) -> VolumeSnapshotResult:
        from . import volumes

        return volumes.snapshot(arguments)

    def layer_inspect(self, arguments: LayerInspectArguments) -> LayerInspectResult:
        from . import layers

        return layers.inspect(arguments)

    def layer_capture_reference(
        self, arguments: LayerCaptureArguments
    ) -> LayerReferencesResult:
        from . import layers

        return layers.capture(arguments)

    def layer_remove_reference(
        self, arguments: LayerRemoveArguments
    ) -> LayerReferencesResult:
        from . import layers

        return layers.remove(arguments)

    def shape_keys_edit(self, arguments: ShapeKeysEditArguments) -> ShapeKeysEditResult:
        main_thread()
        from . import correctives

        return correctives.edit(arguments)

    def shape_keys_inspect(
        self, arguments: ShapeKeysInspectArguments
    ) -> ShapeKeysSummary:
        main_thread()
        from . import correctives

        return correctives.inspect(arguments)

    def shape_keys_remove(
        self, arguments: ShapeKeysRemoveArguments
    ) -> ShapeKeysRemoveResult:
        main_thread()
        from . import correctives

        return correctives.remove(arguments)

    def deformation_capture_target(
        self, arguments: CaptureTargetArguments
    ) -> CaptureTargetResult:
        main_thread()
        from . import correctives

        return correctives.capture_target(arguments)

    def deformation_compare(
        self, arguments: DeformationCompareArguments
    ) -> DeformationCompareResult:
        main_thread()
        from . import correctives

        return correctives.compare(arguments)

    def surface_deform_bind(self, arguments: SurfaceBindArguments) -> SurfaceBindings:
        main_thread()
        from . import surface_deform

        return surface_deform.bind(arguments)

    def surface_deform_inspect(
        self, arguments: SurfaceInspectArguments
    ) -> SurfaceBindings:
        main_thread()
        from . import surface_deform

        return surface_deform.inspect(arguments)

    def surface_deform_unbind(
        self, arguments: SurfaceInspectArguments
    ) -> SurfaceBindings:
        main_thread()
        from . import surface_deform

        return surface_deform.unbind(arguments)

    def vertex_groups_configure(
        self, arguments: GroupsConfigureArguments
    ) -> GroupsResult:
        main_thread()
        from . import weight_transfer

        return weight_transfer.configure(arguments)

    def weights_transfer(
        self, arguments: WeightsTransferArguments
    ) -> WeightsTransferResult:
        main_thread()
        from . import weight_transfer

        return weight_transfer.transfer(arguments)

    def deformation_sweep(
        self, arguments: DeformationSweepArguments
    ) -> DeformationSweepResult:
        main_thread()
        from . import deformation_sweep

        return deformation_sweep.execute(arguments)

    def deformation_inspect(
        self, arguments: DeformationInspectArguments
    ) -> DeformationSummary:
        main_thread()
        return rig.deformation(arguments)

    def mesh_create(self, arguments: MeshCreateArguments) -> MeshSummary:
        main_thread()
        return instances.create_mesh(arguments)

    def surface_instances_create(
        self, arguments: SurfaceInstancesCreateArguments
    ) -> SurfaceInstancesSummary:
        main_thread()
        return instances.configure(arguments.name, arguments.distribution, create=True)

    def surface_instances_configure(
        self, arguments: SurfaceInstancesConfigureArguments
    ) -> SurfaceInstancesSummary:
        main_thread()
        return instances.configure(
            arguments.object_name, arguments.distribution, create=False
        )

    def surface_instances_inspect(
        self, arguments: SurfaceInstancesInspectArguments
    ) -> SurfaceInstancesSummary:
        main_thread()
        return instances.inspect(
            instances.object_mesh(arguments.object_name), arguments.sample_limit
        )

    def mesh_inspect(self, arguments: MeshInspectArguments) -> MeshSummary:
        main_thread()
        return mesh.inspect(uv.mesh_object(arguments.object_name))

    def mesh_inspect_topology(
        self, arguments: TopologyInspectArguments
    ) -> TopologySummary:
        main_thread()
        from . import topology

        return topology.inspect(uv.mesh_object(arguments.object_name), arguments)

    def mesh_query(self, arguments: MeshQueryArguments) -> MeshQueryResult:
        main_thread()
        return mesh.query(uv.mesh_object(arguments.object_name), arguments)

    def mesh_edit(
        self,
        arguments: MeshSelectionArguments
        | MeshNormalsArguments
        | MeshInsertLoopsArguments,
    ) -> MeshEditResult:
        main_thread()
        return mesh.edit(uv.mesh_object(arguments.object_name), arguments)

    def operation_allowed(self, operation: str) -> bool:
        from . import (
            attestation_jobs,
            bake_jobs,
            form_jobs,
            growth_dynamics,
            mutation_jobs,
            render_host,
        )

        if mutation_jobs.busy():
            return operation in {
                "blender.document.mutation_status",
                "blender.document.restore_status",
                "blender.extension.inspect",
            }

        if attestation_jobs.busy():
            return operation in {
                "blender.document.attest",
                "blender.document.attest_status",
                "blender.document.attest_cancel",
                "blender.extension.inspect",
            }

        if form_jobs.busy():
            return operation in {
                "blender.form.status",
                "blender.form.cancel",
                "blender.extension.inspect",
            }

        if render_host.busy():
            return operation == "blender.extension.inspect"
        if growth_dynamics.busy():
            return operation in {
                "blender.growth.dynamics.status",
                "blender.growth.dynamics.cancel",
                "blender.extension.inspect",
            }
        return not bake_jobs.busy() or operation in {
            "blender.bake.status",
            "blender.extension.inspect",
        }

    def bake_status(self, arguments: BakeStatusArguments) -> BakeJobStatus:
        main_thread()
        from . import bake_jobs

        return bake_jobs.status(arguments.job_id)

    def bake_inspect(self, arguments: BakeInspectArguments) -> BakeInspectResult:
        main_thread()
        from . import bake

        return bake.inspect(arguments)

    def bake_image(self, arguments: BakeImageArguments) -> BakeJobStatus:
        main_thread()
        from . import bake_jobs

        return bake_jobs.start(arguments)

    def image_preview(
        self, arguments: ImagePreviewArguments
    ) -> tuple[ImagePreviewResult, ArtifactDescriptor]:
        main_thread()
        from . import image_preview

        if self.spool is None:
            raise OperationError(
                "invalid_context", "Image artifact storage is unavailable"
            )
        return image_preview.preview(arguments, self.spool)

    def image_save(
        self, arguments: ImageSaveArguments
    ) -> tuple[ImageSaveResult, ArtifactDescriptor]:
        main_thread()
        from . import image_output

        if self.spool is None:
            raise OperationError(
                "invalid_context", "Image artifact storage is unavailable"
            )
        return image_output.save(arguments, self.spool)

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

    def uv_pack(self, arguments: UVPackArguments) -> UVPackResult:
        main_thread()
        from . import uv_layout

        return uv_layout.pack(arguments)

    def uv_layout(
        self, arguments: UVLayoutArguments
    ) -> tuple[UVLayoutResult, ArtifactDescriptor | None]:
        main_thread()
        from . import uv_layout

        return uv_layout.inspect(arguments, self.spool)

    def image_inspect(
        self, arguments: InspectArguments | None = None
    ) -> ImageInspectResult:
        main_thread()
        arguments = arguments or InspectArguments()
        selected, info = page(bpy.data.images, arguments, lambda item: str(item.name))
        return ImageInspectResult(
            images=[image_summary(item) for item in selected], page=info
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
                "artifact_not_attached",
                "Image artifact is not attached",
                {"stage": "artifact_read"},
            )
        if descriptor.media_type not in {"image/png", "image/jpeg"}:
            raise OperationError(
                "unsupported_artifact_media_type",
                "Only PNG and JPEG images are supported",
                {"stage": "image_format", "media_type": descriptor.media_type},
            )
        if self.spool is None:
            raise OperationError(
                "artifact_not_found",
                "Input artifact is unavailable",
                {"stage": "artifact_read"},
            )
        path = input_path(self.spool.root, request.request_id, descriptor.artifact_id)
        if not path.is_file():
            raise OperationError(
                "artifact_not_found",
                "Input artifact is unavailable",
                {"stage": "artifact_read"},
            )
        if arguments.name is not None and any(
            image.name == arguments.name for image in bpy.data.images
        ):
            raise OperationError("invalid_arguments", "Image name already exists")
        if arguments.color_space is not None:
            validate_color_space(arguments.color_space)
        image = None
        stage = "image_header"
        try:
            size = raster_size(
                path, descriptor.media_type, expected_bytes=descriptor.byte_size
            )
            stage = "blender_decode"
            image = bpy.data.images.load(str(path), check_existing=False)
            if tuple(image.size) != size or not image.has_data or len(image.pixels) < 4:
                raise OperationError(
                    "image_decode_failed",
                    "Blender could not decode the admitted image",
                    {
                        "stage": stage,
                        "expected_width": size[0],
                        "expected_height": size[1],
                    },
                )
            stage = "blender_datablock"
            image.name = arguments.name or "Image"
            if arguments.name is not None and image.name != arguments.name:
                raise OperationError(
                    "invalid_arguments",
                    "Blender cannot store the requested image name exactly",
                )
            stage = "blender_pack"
            image.pack()
            if not image.packed_files:
                raise OperationError(
                    "image_pack_failed",
                    "Blender could not pack the input image",
                    {"stage": stage},
                )
            # Empty paths cannot accidentally reload another local file. Packed
            # bytes remain authoritative through buffer reload and .blend saving.
            image.filepath_raw = ""
            for packed in image.packed_files:
                packed.filepath = ""
            stage = "blender_datablock"
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
                raise OperationError(
                    "image_datablock_failed",
                    "Blender did not retain a packed input image",
                    {"stage": stage},
                )
            return result
        except OperationError:
            if image is not None:
                bpy.data.images.remove(image, do_unlink=True)
            raise
        except (RuntimeError, OSError, ValueError, MemoryError) as exc:
            if image is not None:
                bpy.data.images.remove(image, do_unlink=True)
            raise OperationError(
                "image_memory_exhausted"
                if isinstance(exc, MemoryError)
                else "image_pack_failed"
                if stage == "blender_pack"
                else "image_datablock_failed"
                if stage == "blender_datablock"
                else "image_decode_failed",
                "Input image import failed during " + stage,
                {"stage": stage},
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

    def image_remove(self, arguments: DeleteArguments) -> DeleteResult:
        data_mutation_context()
        image = find_image(arguments.name)
        if (
            not image.is_editable
            or image.library is not None
            or image.override_library is not None
            or image.source in {"VIEWER", "RENDER_RESULT"}
        ):
            raise OperationError(
                "invalid_context", "Image is not a local input resource"
            )
        if image.users > int(image.use_fake_user):
            raise OperationError(
                "image_in_use", "Remove image references before deletion"
            )
        bpy.data.images.remove(image)
        return DeleteResult(deleted=arguments.name)

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
        return shader.graph_summary(find_material(arguments.material_name), arguments)

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

    def material_assign_batch(
        self, arguments: AssignBatchArguments
    ) -> AssignBatchResult:
        data_mutation_context()
        with material_author.assignment_transaction(
            bpy.data,
            arguments.assignments,
            material_index_snapshot,
            bpy.context.view_layer.update,
        ):
            assigned = []
            for binding in arguments.assignments:
                result = self.material_assign(
                    MaterialAssignArguments(
                        object_name=binding.object_name,
                        slot_index=binding.slot_index,
                        material_name=arguments.material_name,
                    )
                )
                assigned.append(
                    MaterialAssignment(
                        object=result.object_name, slot=result.assigned_slot
                    )
                )
            return AssignBatchResult(
                material_name=arguments.material_name, assignments=assigned
            )

    def material_author(self, arguments: MaterialAuthorArguments) -> MaterialSummary:
        data_mutation_context()
        with material_author.staged(
            bpy.data,
            arguments.name,
            arguments.mode,
            arguments.affect_shared,
            arguments.expected_fingerprint,
        ) as (previous, candidate):
            recipe = material_author.semantic_recipe(arguments, previous)
            material_author.apply_graph(
                candidate,
                material_author.recipe_graph(arguments.name, recipe),
                bpy.data,
            )
            material_author.mark(candidate, "semantic", recipe)
            material_author.validate_users(
                candidate, previous, bpy.data, arguments.assignments
            )
            material_author.publish(
                previous, candidate, arguments.name, bpy.context.view_layer.update
            )
            with material_author.assignment_transaction(
                bpy.data,
                arguments.assignments,
                material_index_snapshot,
                bpy.context.view_layer.update,
            ):
                if arguments.assignments:
                    self.material_assign_batch(
                        AssignBatchArguments(
                            material_name=arguments.name,
                            assignments=arguments.assignments,
                        )
                    )
                return material_summary(candidate)

    def shader_author(self, arguments: GraphAuthorArguments) -> MaterialSummary:
        data_mutation_context()
        with material_author.staged(
            bpy.data,
            arguments.name,
            arguments.mode,
            arguments.affect_shared,
            arguments.expected_fingerprint,
        ) as (previous, candidate):
            if (
                previous
                and arguments.mode == "replace"
                and not material_author.owner(previous)
                and not arguments.replace_unowned
            ):
                raise OperationError(
                    "invalid_arguments",
                    "Replacing an unowned graph requires replace_unowned=true",
                )
            material_author.apply_graph(candidate, arguments, bpy.data)
            material_author.mark(candidate, "declarative")
            material_author.validate_users(
                candidate, previous, bpy.data, arguments.assignments
            )
            material_author.publish(
                previous, candidate, arguments.name, bpy.context.view_layer.update
            )
            with material_author.assignment_transaction(
                bpy.data,
                arguments.assignments,
                material_index_snapshot,
                bpy.context.view_layer.update,
            ):
                if arguments.assignments:
                    self.material_assign_batch(
                        AssignBatchArguments(
                            material_name=arguments.name,
                            assignments=arguments.assignments,
                        )
                    )
                return material_summary(candidate)

    def material_copy(self, arguments: MaterialCopyArguments) -> MaterialSummary:
        data_mutation_context()
        source = find_material(arguments.source)
        material_author.writable(source, bpy.data, affect_shared=True)
        if any(material.name == arguments.name for material in bpy.data.materials):
            raise OperationError("invalid_arguments", "Material name already exists")
        copy = source.copy()
        try:
            copy.name = arguments.name
            if copy.name != arguments.name:
                raise OperationError(
                    "operation_failed", "Blender did not retain material name"
                )
            return material_summary(copy)
        except Exception:
            bpy.data.materials.remove(copy)
            raise

    def material_remove(
        self, arguments: MaterialRemoveArguments
    ) -> MaterialRemoveResult:
        data_mutation_context()
        material = find_material(arguments.name)
        material_author.writable(material, bpy.data, affect_shared=True)
        if material.users:
            raise OperationError(
                "invalid_arguments", "Material still has users; reassign them first"
            )
        result = MaterialRemoveResult(name=arguments.name)
        bpy.data.materials.remove(material)
        return result

    def material_inspect(
        self, arguments: InspectArguments | None = None
    ) -> MaterialInspectResult:
        main_thread()
        arguments = arguments or InspectArguments()
        bpy.context.view_layer.update()
        selected, info = page(
            bpy.data.materials, arguments, lambda item: str(item.name)
        )
        return MaterialInspectResult(
            materials=[material_summary(item) for item in selected], page=info
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
        material_author.validate_uv(material, obj)
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

    def light_inspect(
        self, arguments: InspectArguments | None = None
    ) -> LightInspectResult:
        main_thread()
        arguments = arguments or InspectArguments()
        bpy.context.view_layer.update()
        selected, info = page(
            (obj for obj in bpy.context.scene.objects if obj.type == "LIGHT"),
            arguments,
            lambda item: str(item.name),
        )
        return LightInspectResult(
            lights=[light_summary(obj) for obj in selected], page=info
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

    def camera_inspect(
        self, arguments: InspectArguments | None = None
    ) -> CameraInspectResult:
        main_thread()
        arguments = arguments or InspectArguments()
        bpy.context.view_layer.update()
        selected, info = page(
            (obj for obj in bpy.context.scene.objects if obj.type == "CAMERA"),
            arguments,
            lambda item: str(item.name),
        )
        active = bpy.context.scene.camera
        return CameraInspectResult(
            active_camera=str(active.name) if active else None,
            cameras=[camera_summary(obj) for obj in selected],
            page=info,
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

    def render(self, arguments: RenderArguments, job_id: str) -> RenderJobStatus:
        main_thread()
        from .render_host import prepare

        if self.spool is None:
            raise OperationError("invalid_context", "Render storage is unavailable")
        return prepare(arguments, self.spool, job_id)

    def render_devices(self) -> RenderDevicesResult:
        main_thread()
        import _cycles  # type: ignore[import-not-found]

        from .render_devices import inspect_devices

        return inspect_devices(bpy.context, _cycles.available_devices)

    def render_status(self, arguments: RenderStatusArguments) -> RenderJobStatus:
        raise OperationError(
            "invalid_context", "Render status is owned by the networking worker"
        )

    def render_cancel(self, arguments: RenderJobArguments) -> RenderJobStatus:
        raise OperationError(
            "invalid_context", "Render cancellation is owned by the networking worker"
        )

    def render_result(
        self, arguments: RenderJobArguments
    ) -> tuple[RenderJobStatus, ArtifactDescriptor]:
        raise OperationError(
            "invalid_context", "Render retrieval is owned by the networking worker"
        )

    def inspect(self, arguments: SceneInspectArguments | None = None) -> SceneSummary:
        main_thread()
        arguments = arguments or SceneInspectArguments()
        bpy.context.view_layer.update()
        objects = bpy.context.scene.objects
        types = set(arguments.types) if arguments.types is not None else None
        selected, info = page(
            (obj for obj in objects if types is None or obj.type in types),
            arguments,
            lambda item: str(item.name),
        )
        active = bpy.context.view_layer.objects.active
        selected_names = sorted(str(obj.name) for obj in objects if obj.select_get())
        return SceneSummary(
            name=str(bpy.context.scene.name),
            filepath=str(bpy.data.filepath) or None,
            active_object=str(active.name) if active else None,
            selected_objects=selected_names[:32],
            selected_object_count=len(selected_names),
            selected_objects_truncated=len(selected_names) > 32,
            object_count=len(objects),
            objects=[object_summary(obj) for obj in selected],
            page=info,
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
        from .organization import object_set_remove

        result = object_set_remove(ObjectSetRemoveArguments(names=[arguments.name]))
        if result.error:
            raise OperationError("operation_failed", result.error, result.model_dump())
        return DeleteResult(deleted=arguments.name)


class Runtime:
    def __init__(self, config: ConnectionConfig) -> None:
        main_thread()
        self.config = config
        self.filepath = str(bpy.data.filepath)
        from .bindings import project_id
        from .proof_hosts import runtime_identity

        self.project_id = project_id()
        self.worker = WorkerProcess(
            config,
            registration(
                INSTANCE_ID,
                str(bpy.app.version_string),
                self.filepath,
                self.project_id,
                runtime_identity(),
            ),
        )
        self.queue = CommandQueue(
            self.execute_operation,
            discard=self.worker.discard,
        )
        self.status = "connecting"

    def execute_operation(self, request: OperationRequest) -> Response:
        from . import growth_dynamics, growth_layers
        from .operations import REGISTRY

        result = execute(BlenderBackend(self.worker.spool), request)
        if (
            isinstance(result, OperationSuccess)
            and REGISTRY[request.operation].contract.effect == "mutating"
        ):
            growth_layers.invalidate(
                request.operation,
                request.arguments if isinstance(request.arguments, dict) else None,
            )
            growth_dynamics.invalidate(
                request.operation,
                request.arguments if isinstance(request.arguments, dict) else None,
            )
        return result

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
                and message.event == "blender.response.sent"
            ):
                from . import lifecycle

                if isinstance(message.payload, dict):
                    lifecycle.response_sent(str(message.payload["request_id"]))
            elif (
                isinstance(message, AdapterEvent)
                and message.event == "blender.connection.state"
                and isinstance(message.payload, dict)
            ):
                self.status = str(message.payload["state"])
                if self.status != "connected":
                    self.queue.clear(
                        preserve_operations=frozenset({"blender.render.image"})
                    )
            else:
                raise ValueError("Unexpected networking worker message")
        self.queue.drain(self.worker.send)
        # Publish the prepare acknowledgement before any blocking headless frame.
        if not self.worker.output_pending:
            from . import render_host

            render_host.tick()

    def stop(self) -> None:
        main_thread()
        self.queue.close()
        self.worker.stop()
        self.status = "stopped"


_runtime: Runtime | None = None
_enabled = False
_opening_project = False
_status = "disabled"


def preferences_config() -> ConnectionConfig:
    from .proof_hosts import connection

    proof = connection()
    if proof:
        return ConnectionConfig(host=proof[0], port=proof[1])
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is None:
        return ConnectionConfig()
    return ConnectionConfig(host=addon.preferences.host, port=addon.preferences.port)


def stop() -> None:
    global _runtime
    main_thread()
    from . import (
        attestation_jobs,
        form_jobs,
        growth_dynamics,
        mutation_jobs,
        proof_hosts,
        render_host,
    )

    proof_hosts.shutdown()
    mutation_jobs.shutdown()
    attestation_jobs.shutdown()
    form_jobs.shutdown()
    render_host.shutdown()
    growth_dynamics.shutdown()
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
            from . import (
                attestation_jobs,
                form_jobs,
                growth_dynamics,
                mutation_jobs,
                proof_hosts,
            )

            proof_hosts.tick()
            mutation_jobs.tick()
            attestation_jobs.tick()
            form_jobs.tick()
            growth_dynamics.tick()
            _status = _runtime.status
    except Exception:
        logger.exception("Adapter stopped after an unexpected runtime failure")
        stop()
        _status = "error; reconnect from extension preferences"
    return 0.02


def before_load(*args: object) -> None:
    from .attestation import document_loaded

    document_loaded()
    if not _opening_project:
        stop()


def after_load(*args: object) -> None:
    if _enabled and not _opening_project:
        restart()


def after_save(*args: object) -> None:
    from .bindings import project_id
    from .proof_hosts import runtime_identity

    identity = project_id()
    if _runtime is not None and (
        _runtime.filepath != str(bpy.data.filepath) or _runtime.project_id != identity
    ):
        _runtime.filepath = str(bpy.data.filepath)
        _runtime.project_id = identity
        _runtime.worker.send(
            registration(
                INSTANCE_ID,
                str(bpy.app.version_string),
                _runtime.filepath,
                identity,
                runtime_identity(),
            )
        )


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
_registered_classes: list[Any] = []
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
    _enabled = True
    _status = "starting"
    try:
        for cls in _classes:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
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
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes.clear()
    _status = "disabled"
