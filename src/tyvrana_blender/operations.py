"""Validated operation dispatch independent of Blender's Python module."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import TypeAdapter, ValidationError
from tyvrana_protocol import (
    AdapterRegistration,
    ArtifactDescriptor,
    JsonValue,
    OperationContract,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
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
from .camera_models import (
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraSetActiveArguments,
    CameraSummary,
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
from .extension_models import (
    ExtensionInspectArguments,
    ExtensionReloadArguments,
    ExtensionReloadResult,
    ExtensionState,
)
from .file_models import (
    FileInspectArguments,
    FileOpenArguments,
    FileSaveArguments,
    FileState,
)
from .image_models import (
    ImageConfigureArguments,
    ImageCreateArguments,
    ImageFromArtifactArguments,
    ImageInspectResult,
    ImageSummary,
)
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
from .light_models import (
    LightConfigureArguments,
    LightCreateArguments,
    LightInspectResult,
    LightSummary,
)
from .material_models import (
    MaterialAssignArguments,
    MaterialAssignResult,
    MaterialConfigureArguments,
    MaterialCreateArguments,
    MaterialInspectResult,
    MaterialSummary,
)
from .mesh_models import (
    MeshBevelArguments,
    MeshDeleteArguments,
    MeshEditResult,
    MeshExtrudeArguments,
    MeshInsetArguments,
    MeshInspectArguments,
    MeshMergeArguments,
    MeshNormalsArguments,
    MeshQueryArguments,
    MeshQueryResult,
    MeshSeamArguments,
    MeshSelectionArguments,
    MeshShadingArguments,
    MeshSubdivideArguments,
    MeshSummary,
    MeshTransformArguments,
)
from .models import (
    CreateArguments,
    DeleteArguments,
    DeleteResult,
    InspectArguments,
    Model,
    ObjectSummary,
    RenderArguments,
    RenderResult,
    SceneInspectArguments,
    SceneSummary,
    TransformArguments,
)
from .modifier_models import (
    CONFIGURE,
    CREATE,
    EvaluatedMeshArguments,
    EvaluatedMeshSummary,
    ModifierApplyArguments,
    ModifierApplyResult,
    ModifierConfigureArguments,
    ModifierCreateArguments,
    ModifierInspectArguments,
    ModifierInspectResult,
    ModifierMoveArguments,
    ModifierRemoveArguments,
    ModifierRemoveResult,
    ModifierSummary,
)
from .organization_models import (
    CollectionConfigureArguments,
    CollectionCreateArguments,
    CollectionInspectArguments,
    CollectionInspectResult,
    CollectionRemoveArguments,
    CollectionResult,
    ObjectSetConfigureArguments,
    ObjectSetCreateArguments,
    ObjectSetCreateResult,
    ObjectSetInspectArguments,
    ObjectSetInspectResult,
    ObjectSetRemoveArguments,
    ObjectSetResult,
    OrganizationRemoveResult,
)
from .reference_models import (
    LandmarkInspectArguments,
    LandmarkInspectResult,
    LandmarkResult,
    LandmarkSetArguments,
    MeasurementArguments,
    MeasurementResult,
    NamedRemoveArguments,
    NamedRemoveResult,
    ReferenceCalibrateArguments,
    ReferenceCalibrateResult,
    ReferenceConfigureArguments,
    ReferenceCreateArguments,
    ReferenceInspectArguments,
    ReferenceInspectResult,
    ReferenceResult,
    UnitsConfigureArguments,
    UnitsSummary,
)
from .remesh_models import (
    VoxelRemeshArguments,
    VoxelRemeshInspectArguments,
    VoxelRemeshResult,
    VoxelRemeshSummary,
)
from .retopo_models import (
    RetopoBridgeArguments,
    RetopoCollapseArguments,
    RetopoCreateArguments,
    RetopoCreateResult,
    RetopoEditArguments,
    RetopoEditResult,
    RetopoExtrudeArguments,
    RetopoFillArguments,
    RetopoInsertArguments,
    RetopoInspectArguments,
    RetopoProjectArguments,
    RetopoRelaxArguments,
    RetopoRotateArguments,
    RetopoSeedArguments,
    RetopoSlideArguments,
    RetopoStitchArguments,
    RetopoSubdivideArguments,
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
    RAYCAST,
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
from .topology_models import (
    MeshInsertLoopsArguments,
    TopologyInspectArguments,
    TopologySummary,
)
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
type Response = OperationSuccess | OperationFailure


class OperationError(Exception):
    def __init__(self, code: str, message: str, details: JsonValue = None) -> None:
        super().__init__(message)
        self.error = ProtocolError(code=code, message=message, details=details)


class SceneBackend(Protocol):
    def curve_create(self, arguments: CurveCreateArguments) -> CurveResult: ...

    def curve_configure(self, arguments: CurveConfigureArguments) -> CurveResult: ...

    def curve_inspect(self, arguments: CurveInspectArguments) -> CurveInspectResult: ...

    def curve_remove(self, arguments: CurveRemoveArguments) -> CurveRemoveResult: ...

    def armature_configure_rest(
        self, arguments: ArmatureRestArguments
    ) -> ArmatureSummary: ...

    def armature_configure_joints(
        self, arguments: JointConfigureArguments
    ) -> ArmatureSummary: ...

    def armature_inspect_structure(
        self, arguments: StructureInspectArguments
    ) -> StructureSummary: ...

    def collection_create(
        self, arguments: CollectionCreateArguments
    ) -> CollectionResult: ...

    def collection_configure(
        self, arguments: CollectionConfigureArguments
    ) -> CollectionResult: ...

    def collection_inspect(
        self, arguments: CollectionInspectArguments
    ) -> CollectionInspectResult: ...

    def collection_remove(
        self, arguments: CollectionRemoveArguments
    ) -> OrganizationRemoveResult: ...

    def object_set_create(
        self, arguments: ObjectSetCreateArguments
    ) -> ObjectSetCreateResult: ...

    def object_set_configure(
        self, arguments: ObjectSetConfigureArguments
    ) -> ObjectSetResult: ...

    def object_set_inspect(
        self, arguments: ObjectSetInspectArguments
    ) -> ObjectSetInspectResult: ...

    def object_set_remove(
        self, arguments: ObjectSetRemoveArguments
    ) -> OrganizationRemoveResult: ...

    def reference_create(
        self, arguments: ReferenceCreateArguments
    ) -> ReferenceResult: ...

    def reference_configure(
        self, arguments: ReferenceConfigureArguments
    ) -> ReferenceResult: ...

    def reference_inspect(
        self, arguments: ReferenceInspectArguments
    ) -> ReferenceInspectResult: ...

    def reference_remove(
        self, arguments: NamedRemoveArguments
    ) -> NamedRemoveResult: ...

    def reference_calibrate(
        self, arguments: ReferenceCalibrateArguments
    ) -> ReferenceCalibrateResult: ...

    def landmark_set(self, arguments: LandmarkSetArguments) -> LandmarkResult: ...

    def landmark_inspect(
        self, arguments: LandmarkInspectArguments
    ) -> LandmarkInspectResult: ...

    def landmark_remove(self, arguments: NamedRemoveArguments) -> NamedRemoveResult: ...

    def measurement_inspect(
        self, arguments: MeasurementArguments
    ) -> MeasurementResult: ...

    def scene_configure_units(
        self, arguments: UnitsConfigureArguments
    ) -> UnitsSummary: ...

    def weights_assign(
        self, arguments: WeightsAssignArguments
    ) -> WeightsAssignment: ...
    def weights_inspect(self, arguments: WeightsInspectArguments) -> WeightsSummary: ...

    def armature_create(
        self, arguments: ArmatureCreateArguments
    ) -> ArmatureSummary: ...
    def armature_inspect(
        self, arguments: ArmatureInspectArguments
    ) -> ArmatureSummary: ...
    def armature_bind(self, arguments: ArmatureBindArguments) -> BindingSummary: ...
    def armature_pose(self, arguments: ArmaturePoseArguments) -> ArmatureSummary: ...
    def shape_keys_edit(
        self, arguments: ShapeKeysEditArguments
    ) -> ShapeKeysEditResult: ...
    def shape_keys_inspect(
        self, arguments: ShapeKeysInspectArguments
    ) -> ShapeKeysSummary: ...
    def shape_keys_remove(
        self, arguments: ShapeKeysRemoveArguments
    ) -> ShapeKeysRemoveResult: ...
    def deformation_capture_target(
        self, arguments: CaptureTargetArguments
    ) -> CaptureTargetResult: ...
    def deformation_compare(
        self, arguments: DeformationCompareArguments
    ) -> DeformationCompareResult: ...
    def surface_deform_bind(
        self, arguments: SurfaceBindArguments
    ) -> SurfaceBindings: ...
    def surface_deform_inspect(
        self, arguments: SurfaceInspectArguments
    ) -> SurfaceBindings: ...
    def surface_deform_unbind(
        self, arguments: SurfaceInspectArguments
    ) -> SurfaceBindings: ...
    def vertex_groups_configure(
        self, arguments: GroupsConfigureArguments
    ) -> GroupsResult: ...
    def weights_transfer(
        self, arguments: WeightsTransferArguments
    ) -> WeightsTransferResult: ...

    def deformation_sweep(
        self, arguments: DeformationSweepArguments
    ) -> DeformationSweepResult: ...

    def deformation_inspect(
        self, arguments: DeformationInspectArguments
    ) -> DeformationSummary: ...

    def mesh_create(self, arguments: MeshCreateArguments) -> MeshSummary: ...
    def surface_instances_create(
        self, arguments: SurfaceInstancesCreateArguments
    ) -> SurfaceInstancesSummary: ...
    def surface_instances_configure(
        self, arguments: SurfaceInstancesConfigureArguments
    ) -> SurfaceInstancesSummary: ...
    def surface_instances_inspect(
        self, arguments: SurfaceInstancesInspectArguments
    ) -> SurfaceInstancesSummary: ...

    def extension_inspect(self) -> ExtensionState: ...
    def extension_reload(
        self, arguments: ExtensionReloadArguments, request_id: str
    ) -> ExtensionReloadResult: ...

    def file_inspect(self) -> FileState: ...
    def file_open(self, arguments: FileOpenArguments) -> FileState: ...
    def file_save(self, arguments: FileSaveArguments) -> FileState: ...

    def scene_raycast(self, arguments: RaycastArguments) -> RaycastResult: ...
    def multires_inspect(
        self, arguments: MultiresInspectArguments
    ) -> MultiresSummary: ...
    def multires_create(
        self, arguments: MultiresCreateArguments
    ) -> MultiresSummary: ...
    def multires_subdivide(
        self, arguments: MultiresSubdivideArguments
    ) -> MultiresSummary: ...
    def multires_configure(
        self, arguments: MultiresConfigureArguments
    ) -> MultiresSummary: ...
    def sculpt_mask_inspect(
        self, arguments: MaskInspectArguments
    ) -> SculptMaskSummary: ...
    def sculpt_mask_clear(self, arguments: MaskClearArguments) -> SculptMaskSummary: ...
    def sculpt_mask_invert(
        self, arguments: MaskInvertArguments
    ) -> SculptMaskSummary: ...
    def sculpt_mask_stroke(
        self, arguments: MaskStrokeArguments
    ) -> MaskStrokeResult: ...
    def sculpt_face_sets_inspect(
        self, arguments: FaceSetsInspectArguments
    ) -> FaceSetsSummary: ...
    def sculpt_face_sets_assign(
        self, arguments: FaceSetsAssignArguments
    ) -> FaceSetsAssignResult: ...
    def sculpt_face_sets_initialize(
        self, arguments: FaceSetsInitializeArguments
    ) -> FaceSetsSummary: ...
    def sculpt_filter(self, arguments: SculptFilterArguments) -> SculptFilterResult: ...
    def sculpt_voxel_remesh_inspect(
        self, arguments: VoxelRemeshInspectArguments
    ) -> VoxelRemeshSummary: ...
    def sculpt_voxel_remesh(
        self, arguments: VoxelRemeshArguments
    ) -> VoxelRemeshResult: ...
    def retopo_create_target(
        self, arguments: RetopoCreateArguments
    ) -> RetopoCreateResult: ...
    def retopo_inspect(self, arguments: RetopoInspectArguments) -> RetopoSummary: ...
    def retopo_edit(self, arguments: RetopoEditArguments) -> RetopoEditResult: ...
    def sculpt_inspect(self, arguments: SculptInspectArguments) -> SculptSummary: ...
    def sculpt_stroke(self, arguments: SculptStrokeArguments) -> SculptStrokeResult: ...

    def modifier_inspect(
        self, arguments: ModifierInspectArguments
    ) -> ModifierInspectResult: ...
    def modifier_create(
        self, arguments: ModifierCreateArguments
    ) -> ModifierSummary: ...
    def modifier_configure(
        self, arguments: ModifierConfigureArguments
    ) -> ModifierSummary: ...
    def modifier_move(
        self, arguments: ModifierMoveArguments
    ) -> ModifierInspectResult: ...
    def modifier_remove(
        self, arguments: ModifierRemoveArguments
    ) -> ModifierRemoveResult: ...
    def modifier_apply(
        self, arguments: ModifierApplyArguments
    ) -> ModifierApplyResult: ...
    def mesh_inspect_evaluated(
        self, arguments: EvaluatedMeshArguments
    ) -> EvaluatedMeshSummary: ...

    def mesh_inspect(self, arguments: MeshInspectArguments) -> MeshSummary: ...
    def mesh_inspect_topology(
        self, arguments: TopologyInspectArguments
    ) -> TopologySummary: ...

    def mesh_query(self, arguments: MeshQueryArguments) -> MeshQueryResult: ...
    def mesh_edit(
        self,
        arguments: MeshSelectionArguments
        | MeshNormalsArguments
        | MeshInsertLoopsArguments,
    ) -> MeshEditResult: ...
    def operation_allowed(self, operation: str) -> bool: ...
    def bake_status(self, arguments: BakeStatusArguments) -> BakeJobStatus: ...
    def bake_inspect(self, arguments: BakeInspectArguments) -> BakeInspectResult: ...
    def bake_image(self, arguments: BakeImageArguments) -> BakeJobStatus: ...
    def image_save(
        self, arguments: ImageSaveArguments
    ) -> tuple[ImageSaveResult, ArtifactDescriptor]: ...
    def uv_inspect(self, arguments: UVInspectArguments) -> UVInspectResult: ...
    def uv_create(self, arguments: UVCreateArguments) -> UVInspectResult: ...
    def uv_set_active(self, arguments: UVSetActiveArguments) -> UVInspectResult: ...
    def uv_unwrap(self, arguments: UVUnwrapArguments) -> UVInspectResult: ...
    def uv_pack(self, arguments: UVPackArguments) -> UVPackResult: ...
    def uv_layout(
        self, arguments: UVLayoutArguments
    ) -> tuple[UVLayoutResult, ArtifactDescriptor | None]: ...
    def image_inspect(self, arguments: InspectArguments) -> ImageInspectResult: ...
    def image_remove(self, arguments: DeleteArguments) -> DeleteResult: ...
    def image_create(self, arguments: ImageCreateArguments) -> ImageSummary: ...
    def image_from_artifact(
        self, arguments: ImageFromArtifactArguments, request: OperationRequest
    ) -> ImageSummary: ...
    def image_configure(self, arguments: ImageConfigureArguments) -> ImageSummary: ...
    def shader_inspect(
        self, arguments: ShaderInspectArguments
    ) -> ShaderGraphSummary: ...
    def shader_create(self, arguments: NodeCreateArguments) -> NodeSummary: ...
    def shader_configure(self, arguments: NodeConfigureArguments) -> NodeSummary: ...
    def shader_delete(self, arguments: NodeDeleteArguments) -> NodeDeleteResult: ...
    def shader_connect(self, arguments: ConnectArguments) -> LinkSummary: ...
    def shader_disconnect(self, arguments: DisconnectArguments) -> DisconnectResult: ...

    def material_inspect(
        self, arguments: InspectArguments
    ) -> MaterialInspectResult: ...
    def material_create(
        self, arguments: MaterialCreateArguments
    ) -> MaterialSummary: ...
    def material_configure(
        self, arguments: MaterialConfigureArguments
    ) -> MaterialSummary: ...
    def material_assign(
        self, arguments: MaterialAssignArguments
    ) -> MaterialAssignResult: ...
    def light_inspect(self, arguments: InspectArguments) -> LightInspectResult: ...
    def light_create(self, arguments: LightCreateArguments) -> LightSummary: ...
    def light_configure(self, arguments: LightConfigureArguments) -> LightSummary: ...
    def camera_inspect(self, arguments: InspectArguments) -> CameraInspectResult: ...
    def camera_create(self, arguments: CameraCreateArguments) -> CameraSummary: ...
    def camera_configure(
        self, arguments: CameraConfigureArguments
    ) -> CameraSummary: ...
    def camera_set_active(
        self, arguments: CameraSetActiveArguments
    ) -> CameraSummary: ...
    def inspect(self, arguments: SceneInspectArguments) -> SceneSummary: ...
    def create(self, arguments: CreateArguments) -> ObjectSummary: ...
    def transform(self, arguments: TransformArguments) -> ObjectSummary: ...
    def delete(self, arguments: DeleteArguments) -> DeleteResult: ...
    def render(
        self, arguments: RenderArguments
    ) -> tuple[RenderResult, ArtifactDescriptor]: ...


def failure(request: OperationRequest, error: ProtocolError) -> OperationFailure:
    return OperationFailure(
        type="operation.failure", request_id=request.request_id, error=error
    )


@dataclass(frozen=True, slots=True)
class OperationSpec:
    contract: OperationContract
    parse: Callable[[JsonValue], Model]
    invoke: Callable[
        [SceneBackend, Model, OperationRequest],
        tuple[Model, tuple[ArtifactDescriptor, ...]],
    ]


def _operation[A: Model, R: Model](
    name: str,
    arguments: type[A] | TypeAdapter[A],
    result_model: type[R] | TypeAdapter[R],
    handler: Callable[
        [SceneBackend, A, OperationRequest], R | tuple[R, ArtifactDescriptor | None]
    ],
    description: str,
    *,
    effect: Literal["read_only", "mutating", "transient", "lifecycle"],
    execution: Literal["synchronous", "job_start", "job_status", "lifecycle"],
    requires_interactive: bool = False,
    input_artifacts: Literal["none", "required"] = "none",
    output_artifacts: Literal["none", "optional", "required"] = "none",
) -> OperationSpec:
    validator = TypeAdapter(arguments) if isinstance(arguments, type) else arguments
    result_validator = (
        TypeAdapter(result_model) if isinstance(result_model, type) else result_model
    )
    contract = OperationContract(
        name=name,
        description=description,
        arguments_schema=validator.json_schema(),
        result_schema=result_validator.json_schema(mode="serialization"),
        effect=effect,
        execution=execution,
        requires_interactive=requires_interactive,
        input_artifacts=input_artifacts,
        output_artifacts=output_artifacts,
    )

    def invoke(
        backend: SceneBackend, parsed: Model, request: OperationRequest
    ) -> tuple[Model, tuple[ArtifactDescriptor, ...]]:
        value = handler(backend, cast(A, parsed), request)
        if isinstance(value, tuple):
            result, artifact = value
            artifacts = (artifact,) if artifact is not None else ()
        else:
            result, artifacts = value, ()
        return result_validator.validate_python(result), artifacts

    return OperationSpec(contract, validator.validate_python, invoke)


_DECLARATIONS = (
    _operation(
        "blender.shape_keys.edit",
        ShapeKeysEditArguments,
        ShapeKeysEditResult,
        lambda b, a, q: b.shape_keys_edit(a),
        (
            "Atomically create/edit/configure 1..16 native relative keys on "
            "an exclusive local mesh; Basis is ensured and protected. 100000 "
            "vertices, 64 keys and 2000000 stored key coordinates. Sparse "
            "deltas (65536/key) or shared region offsets use original "
            "local/world vectors, replace relative deltas or add offsets; "
            "smooth local ellipsoid falloff and native group masks reuse mesh "
            "weights. Zero replacement clears a region. Optional expected "
            "ordered topology hash guards stale indices. Keys have explicit "
            "create, range/value/mute/reference/rename; omitted fields "
            "persist. Refuse locked, absolute, animated/driven or linked data "
            "and reference cycles. Capture uses one provenance-bearing target "
            "and one unmuted unmasked Basis-relative key at zero: native "
            "inverse maps desired posed displacement, then verifies evaluated "
            "target tolerance at value1 before commit. No modifier "
            "application, posed-pose baking or automatic drivers; failure "
            "restores original keys/data and pose."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.shape_keys.inspect",
        ShapeKeysInspectArguments,
        ShapeKeysSummary,
        lambda b, a, q: b.shape_keys_inspect(a),
        (
            "Inspect relative native shape keys with ordered topology, "
            "Basis/reference, values/ranges/masks/mute/locks/drivers and "
            "locally computed affected count/max/mean displacement. Key pages "
            "default16/max32, max64 keys and 2M coordinates; optional "
            "explicit key delta page max256, none by default. Stored values "
            "are original object-local deltas relative to each key reference, "
            "independent of current influence. Reject incompatible stored "
            "topology; no coordinate dump."
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.shape_keys.remove",
        ShapeKeysRemoveArguments,
        ShapeKeysRemoveResult,
        lambda b, a, q: b.shape_keys_remove(a),
        (
            "Atomically remove 1..32 unique existing local relative keys; "
            "protect locked/animated data and references from surviving keys. "
            "Basis removal requires every key plus remove_basis=true. Other "
            "data, pose, UVs, weights and modifiers remain native; shared "
            "mesh edits require an independent copy."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.deformation.capture_target",
        CaptureTargetArguments,
        CaptureTargetResult,
        lambda b, a, q: b.deformation_capture_target(a),
        (
            "Create a named editable mesh from current evaluated geometry, "
            "preserving ordered topology and current world transform; source "
            "remains untouched. Capture provenance stores source ID, topology "
            "and current evaluated baseline, so shape_keys.edit "
            "captured_target subtracts only correction and refuses stale "
            "source pose/shape/topology. Supports bare mesh or one enabled "
            "owned linear Armature, no constructive or nonlinear modifiers, "
            "no absolute/animated keys; max100000 vertices. Target is an "
            "ordinary editable mesh with native UV/material data, no "
            "keys/modifiers. Edit through typed mesh operations; no automatic "
            "desired shape or arbitrary inversion."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.deformation.compare",
        DeformationCompareArguments,
        DeformationCompareResult,
        lambda b, a, q: b.deformation_compare(a),
        (
            "Compare 1..8 current evaluated object/target pairs with matching "
            "authored/evaluated ordered topology. Shared selectors define "
            "authored regions. Adapter computes world-space "
            "RMS/min/p05/p50/p95/p99/max distance plus default4/max16 worst "
            "indices; 100000 vertices/mesh and 1000000 evaluated "
            "samples/request. Does not change pose/keys, infer registration, "
            "certify intersections or compare unrelated vertex order."
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.surface_deform.bind",
        SurfaceBindArguments,
        SurfaceBindings,
        lambda b, a, q: b.surface_deform_bind(a),
        (
            "Bind 1..8 unmodified exclusive local driven meshes to one driver "
            "using native Surface Deform at explicit bind_state=current. One "
            "owned relation per driven mesh; up to 10000 driver vertices/20000 "
            "faces, 100000 driven vertices/mesh, 5M driven-vertex/driver-face "
            "work. Native bind requires convex nondegenerate driver faces, no "
            "duplicate vertices or edges shared by over 2 faces. Bounded "
            "falloff/strength and existing native mask group; append later "
            "subdivision after binding. Prevalidate dependencies, rollback "
            "all new modifiers on failure. Persist native binding plus "
            "authored/evaluated topology signatures; no hidden rebind. Same "
            "names are not proof of compatibility. Native surface transfer follows "
            "driver mesh deformation; subsequent object-level driver transforms "
            "are ignored. Move related objects together for assembly placement."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.surface_deform.inspect",
        SurfaceInspectArguments,
        SurfaceBindings,
        lambda b, a, q: b.surface_deform_inspect(a),
        (
            "Inspect 1..16 owned Surface Deform relations: driver/driven, "
            "native bound state, validity/reason, ordering/settings and "
            "authored/evaluated topology signatures. Unbound objects are "
            "explicit. Edited/removed driver/modifier/topology is invalid, "
            "even if native is_bound remains true. No mesh arrays; "
            "save/reopen uses native binding data."
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.surface_deform.unbind",
        SurfaceInspectArguments,
        SurfaceBindings,
        lambda b, a, q: b.surface_deform_unbind(a),
        (
            "Prevalidate and remove 1..16 owned Surface Deform "
            "modifiers/binding records, including stale topology bindings; "
            "preserve all other modifiers, authored meshes and current driver "
            "shape. Rebind explicitly to capture another baseline; does not "
            "bake evaluated geometry or reset pose."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.vertex_groups.configure",
        GroupsConfigureArguments,
        GroupsResult,
        lambda b, a, q: b.vertex_groups_configure(a),
        (
            "Atomically create/edit/rename/lock/remove up to 32 native vertex "
            "groups on an exclusive local mesh. Up to16 shared-selector "
            "constant-weight layers/group, 100000 vertices and256 total "
            "groups. Zero weight removes membership. Preserve unselected "
            "weights, keys and attributes; reject locked changes and "
            "renaming/removing groups referenced by armatures, masks or "
            "constraints. Explicitly unlock in a prior call. Compact first32 "
            "group summaries plus total; general masks use native groups, no "
            "second weight system."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.weights.transfer",
        WeightsTransferArguments,
        WeightsTransferResult,
        lambda b, a, q: b.weights_transfer(a),
        (
            "Transfer 1..128 explicitly mapped groups between authored "
            "world-rest surfaces via bounded nearest-triangle barycentric "
            "interpolation; topology may differ. Optional world-plane "
            "reflection and group mapping mirror influences, including "
            "same-object snapshot mirroring. Shared target selector preserves "
            "unselected weights; max_distance failure is atomic. Optional "
            "normalization/top4 influences, complete deform-group mapping "
            "required for a bound target; locks preserved. Up to100000 "
            "vertices/mesh,200000 source triangles,256 groups. No "
            "posed/evaluated-surface transfer or shape-key remapping: "
            "transfer before correctives. Existing armature.bind initializes "
            "target ownership; masks may transfer without a rig. Return "
            "distance/coverage summary, not per-vertex matrices."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.curve.create",
        CurveCreateArguments,
        CurveResult,
        lambda b, a, q: b.curve_create(a),
        (
            "Create 1..64 native Curve objects with shared "
            "spline/profile/material defaults; up to 8 splines/1024 points "
            "per curve, 4096 points and 128 attachments per request. POLY, "
            "BEZIER handles and rational NURBS. Local/world authored points; "
            "XYZ rotation/tilt radians; positive uniform object scale. "
            "Optional circle/custom XY profile sweeps share existing "
            "materials/profile objects. Native object/bone-frame and "
            "triangular surface attachments follow dependency-graph "
            "evaluation. Surface barycentrics require unchanged "
            "authored/evaluated connectivity; incompatible topology is "
            "reported invalid. Whole-spline binding snaps its selected point "
            "to target-frame offset and follows that frame; point binding "
            "drives one control point. Creates owned evaluation "
            "graphs/helpers with rollback; no hair Curves, grooming or "
            "simulation. sample_limit=0 suppresses curve rows."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.curve.configure",
        CurveConfigureArguments,
        CurveResult,
        lambda b, a, q: b.curve_configure(a),
        (
            "Edit 1..64 managed native curves using full spline lists or "
            "contiguous full local-space point ranges, typed settings and "
            "optional full "
            "binding replacements. Omitted settings/attachments persist. "
            "Explicit bindings=[] detaches; supplied bindings rebind at "
            "current target frames. Changed attached coordinates or spline "
            "topology require explicit rebinding. Bounds match creation; "
            "validate the complete batch and stage data/graphs with rollback."
            " Preserve shared/library data, animation, shape keys and "
            "external modifiers. Profiles/materials stay shared; no arbitrary"
            " dictionaries or scripting."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.curve.inspect",
        CurveInspectArguments,
        CurveInspectResult,
        lambda b, a, q: b.curve_inspect(a),
        (
            "Inspect managed native curves by names/prefix/collection with "
            "default 8/max64 rows. Summary evaluates spline lengths, "
            "endpoints, mesh bounds/counts, radius/tilt ranges and attachment"
            " errors without returning points by default. Optional spline "
            "filter, bounded authored-local point pages and 2..64 "
            "arc-length samples per "
            "spline. World/local output; radius and tilt remain authored "
            "local units/radians. Native minimum-twist "
            "tangent/normal/binormal includes tilt, with singular/cusp "
            "limitations. Attachment intended/evaluated positions and error "
            "use world units; surface topology changes invalidate bindings "
            "instead of claiming stable attachment. No whole meshes in "
            "results."
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.curve.remove",
        CurveRemoveArguments,
        CurveRemoveResult,
        lambda b, a, q: b.curve_remove(a),
        (
            "Remove 1..64 managed curves after dependency/ownership checks; "
            "remove their exclusively owned native Curve data, evaluation "
            "graphs and bone-anchor helpers. Retain shared materials, "
            "external profile curves and attachment targets. Reject surviving"
            " profile/attachment users and external children. Names must be "
            "unique; all targets are preflighted before removal."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.configure_rest",
        ArmatureRestArguments,
        ArmatureSummary,
        lambda b, a, q: b.armature_configure_rest(a),
        "Edit an existing unbound neutral armature using up to 128 full "
        "bone definitions/additions and unused-name renames. Rest "
        "endpoints use armature/world vectors or shared typed point "
        "sources; x_reference constructs an orthonormal frame "
        "(Y=head-tail, projected X, Z=X cross Y), otherwise roll radians. "
        "Validate complete hierarchy/connected heads and stage copied "
        "data with rollback. Reject posed channels, shared/library data, "
        "external users/bindings/control metadata and unowned "
        "constraints. Owned LOCAL XYZ limits are preserved for omitted "
        "bones and replaced by supplied definitions. No automatic weight "
        "retargeting. sample_limit=0 returns counts/hashes without bone "
        "rows.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.configure_joints",
        JointConfigureArguments,
        ArmatureSummary,
        lambda b, a, q: b.armature_configure_joints(a),
        "Configure 1..128 fixed-center rotational joints on existing "
        "bones. Native heads are centers; native rest axes are frames. "
        "Typed XYZ Euler axis limits use radians relative to rest in "
        "LOCAL space: null axis is unlimited, [0,0] locks it, null limits "
        "removes only the owned constraint. Native constraints clamp "
        "evaluated motion while requested channels remain; pose results "
        "report both. Canonical principal branch avoids multi-turn/gimbal "
        "ambiguity (|X,Z|<=pi-0.0001, |Y|<=pi/2-0.0001). Zero pose "
        "translation/unit scale required for joints. No "
        "coupling/IK/driver system. Atomic rollback; exclusive local "
        "armature with no animation/unowned constraints. May configure "
        "already bound structures without changing rest data.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.inspect_structure",
        StructureInspectArguments,
        StructureSummary,
        lambda b, a, q: b.armature_inspect_structure(a),
        "Compact rest/joint/pose QA for one armature (max 128 bones), "
        "optionally a subtree/named set. Default 16/max 128 paged rows; "
        "select rest/frame/limits/pose fields. Explicit "
        "world/armature/parent endpoint/frame space; all "
        "requested/evaluated Euler rotations are rest-relative XYZ "
        "radians. Joint center is the bone head; Y is head-tail, X/Z are "
        "rest roll axes. World frame axes require positive uniform object "
        "scale. Whole-armature validity includes unsampled bones; errors "
        "are bounded per sampled bone. Use measurement.inspect bone "
        "points for lengths/reach/angles in the existing unit system.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.collection.create_hierarchy",
        CollectionCreateArguments,
        CollectionResult,
        lambda b, a, q: b.collection_create(a),
        "Create 1..32 native local collections with explicit "
        "multi-parent hierarchy and global visibility. Null parent "
        "means scene root. Forward batch names allowed; names must be "
        "unused. Prevalidated, staged and rolled back on failure. No "
        "view-layer exclusion changes.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.collection.configure",
        CollectionConfigureArguments,
        CollectionResult,
        lambda b, a, q: b.collection_configure(a),
        "Atomically rename, replace parent memberships or set global "
        "viewport/render/select visibility for 1..32 collections. "
        "Omitted fields are preserved. Reject cycles, library/override, "
        "external-scene and instance users; names resolve before "
        "renaming.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.collection.inspect",
        CollectionInspectArguments,
        CollectionInspectResult,
        lambda b, a, q: b.collection_inspect(a),
        "Inspect current-scene collection hierarchy/subtree with "
        "optional names/prefix and pagination (default 32, max 128). "
        "Parent/child lists cap 16 with counts/truncation; includes "
        "direct and recursive object counts and global visibility, not "
        "view-layer exclusion.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.collection.remove",
        CollectionRemoveArguments,
        OrganizationRemoveResult,
        lambda b, a, q: b.collection_remove(a),
        "Remove one empty collection, or explicitly rehome its direct "
        "objects and children to a local collection/root. Reject "
        "external/instance users and cycles. Retain objects and their "
        "data. Report native deletion failure in error/remaining; no "
        "orphan purge.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.object_set.create",
        ObjectSetCreateArguments,
        ObjectSetCreateResult,
        lambda b, a, q: b.object_set_create(a),
        "Author 1..64 coherent objects: cubes, planes, UV spheres, "
        "cylinders, plain empties or copies of plain local "
        "mesh/light/camera/empty objects. Request-local keys allow "
        "forward parent/copy references. Transforms are parent-local "
        "XYZ radians. Collections default to scene root, max 16/object. "
        "Explicit linked/independent data and shared/independent "
        "materials; independent materials require independent data, "
        "nested shader groups/images remain shared. Role/tags are "
        "bounded persistent adapter-owned metadata; keys are not "
        "persistent. Reject cycles, collisions and "
        "animated/constrained/modifier/shape-key/domain-owned copy "
        "sources and nonidentity deltas. Max 250000 allocated mesh "
        "vertices. Stage all "
        "objects/data, publish together, rollback on failure; selection "
        "independent.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.object_set.configure",
        ObjectSetConfigureArguments,
        ObjectSetResult,
        lambda b, a, q: b.object_set_configure(a),
        "Atomically update 1..64 named objects: rename, exact "
        "collection memberships (link/unlink/move), plain parent "
        "changes with world transform preserved, role/tags. Omitted "
        "fields preserve values; null parent/role clears. Reject "
        "cycles, external-scene/domain-owned objects and unsupported or "
        "unrepresentable parenting (animation/constraints/bone "
        "parents/nonidentity deltas/shear on clear). Native "
        "relationships persist through "
        "rename/save/reopen; returned names are canonical, no stable "
        "UUID. Generic transforms remain object.set_transform.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.object_set.inspect",
        ObjectSetInspectArguments,
        ObjectSetInspectResult,
        lambda b, a, q: b.object_set_inspect(a),
        "Inspect objects in a collection/subtree or named set, filtered "
        "by names/prefix/role/all tags. Select hierarchy, memberships, "
        "world/local transforms, fixed metadata and data/material "
        "ownership. Pages default 32, max 128; nested lists cap 16 with "
        "counts/truncation. Native object names identify objects; keys "
        "from authoring are request-local. No mesh payload, history or "
        "arbitrary properties.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.object_set.remove",
        ObjectSetRemoveArguments,
        OrganizationRemoveResult,
        lambda b, a, q: b.object_set_remove(a),
        "Delete 1..64 named local objects after dependency preflight; "
        "default rejects surviving children, explicit unparent "
        "preserves representable world transforms. Reject other "
        "external dependencies and domain-owned objects. Retain "
        "mesh/material data, never purge orphans. Native deletion is "
        "irreversible: error and exact deleted/remaining names report "
        "partial progress. Selection independent.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.create",
        ReferenceCreateArguments,
        ReferenceResult,
        lambda b, a, q: b.reference_create(a),
        "Create 1..16 named image-empty references from existing "
        "packed/generated images. Import artifacts with "
        "image.create_from_artifact first. References share images, persist in "
        "the project and are viewport-only, not renderable geometry. New "
        "reference collections are local and removed when empty. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.configure",
        ReferenceConfigureArguments,
        ReferenceResult,
        lambda b, a, q: b.reference_configure(a),
        "Patch display/metadata for 1..16 managed references; omitted fields "
        "remain unchanged. Object transforms use object.set_transform. Does not "
        "change shared image data. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.inspect",
        ReferenceInspectArguments,
        ReferenceInspectResult,
        lambda b, a, q: b.reference_inspect(a),
        "Inspect a filtered page of managed references, pixel dimensions, local "
        "size, world corners, display settings and source labels. Default "
        "32/max 128; collections max 16 with explicit count. Pixel points use "
        "bottom-left image-edge coordinates. ",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.remove",
        NamedRemoveArguments,
        NamedRemoveResult,
        lambda b, a, q: b.reference_remove(a),
        "Remove 1..32 managed references after dependency preflight; retain "
        "shared images. Remove unused images separately with image.remove. "
        "Empty owned reference collections are cleaned up. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.calibrate",
        ReferenceCalibrateArguments,
        ReferenceCalibrateResult,
        lambda b, a, q: b.reference_calibrate(a),
        "Calibrate an unparented reference from two image-edge pixel points and "
        "a positive world distance; uniformly change display size while "
        "preserving point A in world space. Requires no attached children. "
        "Units are Blender units or meters via scene scale. No vision "
        "inference. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.landmark.set",
        LandmarkSetArguments,
        LandmarkResult,
        lambda b, a, q: b.landmark_set(a),
        "Declare 1..32 persistent named landmarks. Create or fully redefine "
        "only managed landmarks; omitted metadata resets. Object-local points "
        "follow object transforms, not mesh deformation. Omit/null object for "
        "world points. Reject name collisions and landmark-to-landmark "
        "attachments. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.landmark.inspect",
        LandmarkInspectArguments,
        LandmarkInspectResult,
        lambda b, a, q: b.landmark_inspect(a),
        "Inspect named/category/object/attachment-filtered landmark pages "
        "(default 32/max 128). Return local and world coordinates and explicit "
        "validity; missing attached objects require redefinition, never silent "
        "world-point fallback. ",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.landmark.remove",
        NamedRemoveArguments,
        NamedRemoveResult,
        lambda b, a, q: b.landmark_remove(a),
        "Remove 1..32 managed landmarks after dependency preflight. Preserve "
        "target objects and unrelated state. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.measurement.inspect",
        MeasurementArguments,
        MeasurementResult,
        lambda b, a, q: b.measurement_inspect(a),
        "Calculate 1..64 named distances, unsigned angles (degrees), or exact "
        "authored-mesh axis-aligned bounds in world or an object-local frame. "
        "Optional scalar targets include signed deviations and absolute "
        "tolerance checks. Bounds ignore modifiers/deformation, total 128000 "
        "unique mesh-object vertices per call. Meters require world frame; "
        "local coordinates may be scaled. Point sources include native bone "
        "head/tail in rest or evaluated state, transformed through the armature; "
        "bone names must be current. Distance/reach/unsigned-angle measurement "
        "reuses this operation; signed joint Euler angles are radians in pose "
        "inspection. All queries must be valid. ",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.scene.configure_units",
        UnitsConfigureArguments,
        UnitsSummary,
        lambda b, a, q: b.scene_configure_units(a),
        "Set scene display units and explicit meters per Blender unit. This "
        "does not scale geometry or change physics. Read current units with "
        "measurement.inspect. ",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.bind",
        ArmatureBindArguments,
        BindingSummary,
        lambda b, a, q: b.armature_bind(a),
        "Bind or explicitly retarget an owned Armature modifier using "
        "normalized envelope/explicit weights. Preserves unrelated groups; "
        "stages publication with rollback.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.create",
        ArmatureCreateArguments,
        ArmatureSummary,
        lambda b, a, q: b.armature_create(a),
        "Create a bounded rest-bone hierarchy with explicit head/tail/roll, "
        "parent and connection geometry. Rejects duplicate names, cycles and "
        "inconsistent connected joints. Up to 128 bones. Raw endpoints use "
        "armature/world request space; shared typed point sources resolve to "
        "world then armature space. Head is the joint center. x_reference "
        "constructs an orthonormal native frame: Y=head-tail, projected X, "
        "Z=X cross Y; alternatively roll is radians about Y. Optional typed "
        "limits create owned LOCAL XYZ rotation constraints. sample_limit=0 "
        "returns only counts/hashes; construction rolls back on failure.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.inspect",
        ArmatureInspectArguments,
        ArmatureSummary,
        lambda b, a, q: b.armature_inspect(a),
        "Inspect filtered rest/posed bones and bounded mesh-binding "
        "summaries. Bone coordinates are world-space; pose channels remain "
        "local to the rest hierarchy.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.pose",
        ArmaturePoseArguments,
        ArmatureSummary,
        lambda b, a, q: b.armature_pose(a),
        "Set a batch of local pose channels, optionally resetting the whole "
        "pose first. Keeps rest geometry unchanged; invalid bone requests do "
        "not partially apply. XYZ Euler radians relative to native rest axes. "
        "Owned joint limits clamp evaluated motion while requested channels "
        "remain; returned joint summaries distinguish both. Constrained "
        "joint requests require zero translation/unit scale and the principal "
        "branch |X,Z|<=pi-0.0001, |Y|<=pi/2-0.0001. No multi-turn or gimbal "
        "solver. sample_limit=0 suppresses bone rows; structural inspection "
        "selects evaluated endpoints, frames and limits.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.bake.image",
        BakeImageArguments,
        BakeJobStatus,
        lambda b, a, q: b.bake_image(a),
        "Start a bounded asynchronous tangent-normal bake from explicit "
        "evaluated sources to UV targets. Returns a job ID; inspect that job "
        "with bake.status.",
        effect="mutating",
        execution="job_start",
    ),
    _operation(
        "blender.bake.inspect",
        BakeInspectArguments,
        BakeInspectResult,
        lambda b, a, q: b.bake_inspect(a),
        "Inspect evaluated UV/ray/normal transfer readiness for explicit "
        "source-target sets, with bounded samples and optional existing-image"
        " QA.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.bake.status",
        BakeStatusArguments,
        BakeJobStatus,
        lambda b, a, q: b.bake_status(a),
        "Read one native bake job's pending/completed/failed state. Inspect "
        "again after useful work; native bake cancellation is not a rollback "
        "guarantee.",
        effect="read_only",
        execution="job_status",
    ),
    _operation(
        "blender.camera.configure",
        CameraConfigureArguments,
        CameraSummary,
        lambda b, a, q: b.camera_configure(a),
        "Change camera optical/projection properties by object name, "
        "isolating shared camera data. Omit unchanged properties; transforms "
        "use object.set_transform.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.camera.create",
        CameraCreateArguments,
        CameraSummary,
        lambda b, a, q: b.camera_create(a),
        "Create a camera with explicit optics and transform, optionally "
        "making it active. Orthographic requests must omit lens_mm; "
        "perspective requests must omit ortho_scale.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.camera.inspect",
        InspectArguments,
        CameraInspectResult,
        lambda b, a, q: b.camera_inspect(a),
        "Inspect a filtered, bounded page of camera objects and the scene's "
        "active-camera name. Pagination does not change the active camera.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.camera.set_active",
        CameraSetActiveArguments,
        CameraSummary,
        lambda b, a, q: b.camera_set_active(a),
        "Make the named camera the scene's active render camera.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.deformation.sweep",
        DeformationSweepArguments,
        DeformationSweepResult,
        lambda b, a, q: b.deformation_sweep(a),
        "Optional per-pose shape_values reset named key channels to zero for "
        "the baseline and each pose, then apply explicit values; all values "
        "restore even on failure. Optional same-topology targets return bounded "
        "world deviation summaries and count toward the 1M sample budget. "
        "Evaluate 1..16 deterministic reset-to-rest poses on owned bound meshes and "
        "up to 8 frozen rest-frame boxes. Return compact "
        "stretch/compression/area/corner-angle distributions, bounded worst edges, "
        "bone influence regions and closed-volume proxy. Joint limits apply; "
        "Report evaluated rotations. Up to 128 mesh/pose/region summaries, "
        "384 detail units (summary count times 1+2*sample_limit+bone_names count) "
        "and 1M evaluated vertex/pose samples. Temporarily evaluates native poses "
        "synchronously and "
        "restores every channel/mode and pose position on success or failure. "
        "Unknown controls/animation remain guarded. No self-intersection/inversion "
        "certification or automatic topology-vs-weight diagnosis.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.deformation.inspect",
        DeformationInspectArguments,
        DeformationSummary,
        lambda b, a, q: b.deformation_inspect(a),
        "Compare evaluated meshes in the current pose against temporary rest "
        "state. Reports bounded displacement/distortion samples, percentiles,"
        " bone regions and optional contact/volume proxies; restores pose "
        "mode.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.extension.inspect",
        ExtensionInspectArguments,
        ExtensionState,
        lambda b, a, q: b.extension_inspect(),
        "Inspect extension build identity, connection, lifecycle state and "
        "owned resource counts without reloading it.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.extension.reload",
        ExtensionReloadArguments,
        ExtensionReloadResult,
        lambda b, a, q: b.extension_reload(a, q.request_id),
        "Activate the already staged matching build through the extension "
        "lifecycle. Returns an acknowledgement before reconnection; changes "
        "adapter identity while preserving the host process/project.",
        effect="lifecycle",
        execution="lifecycle",
    ),
    _operation(
        "blender.file.inspect",
        FileInspectArguments,
        FileState,
        lambda b, a, q: b.file_inspect(),
        "Inspect native project path, saved/dirty flags and file size. Native"
        " dirty state is not a complete audit of every external/scripted "
        "edit.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.file.open",
        FileOpenArguments,
        FileState,
        lambda b, a, q: b.file_open(a),
        "Open a native project with an explicit discard policy. Embedded "
        "script execution is disabled; project metadata re-registers after "
        "the response drains. Wait for registration to report the resulting "
        "project_path before sending further operations.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.file.save",
        FileSaveArguments,
        FileState,
        lambda b, a, q: b.file_save(a),
        "Save the native project with explicit overwrite policy. Writes are "
        "external filesystem effects, not an atomic undo transaction; changed"
        " paths refresh adapter registration. Wait for registration to report "
        "the resulting project_path before sending further operations.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.image.configure",
        ImageConfigureArguments,
        ImageSummary,
        lambda b, a, q: b.image_configure(a),
        "Change shared image color space/alpha interpretation while "
        "preserving unsaved pixel data. Affects all users of that image.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.image.create_from_artifact",
        ImageFromArtifactArguments,
        ImageSummary,
        lambda b, a, q: b.image_from_artifact(a, q),
        "Create and pack an image from one attached PNG/JPEG artifact. The "
        "artifact ID references transferred bytes, never a shared filesystem "
        "transport path.",
        effect="mutating",
        execution="synchronous",
        input_artifacts="required",
    ),
    _operation(
        "blender.image.create_generated",
        ImageCreateArguments,
        ImageSummary,
        lambda b, a, q: b.image_create(a),
        "Create a bounded generated image resource with explicit dimensions, "
        "fill and color interpretation.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.image.inspect",
        InspectArguments,
        ImageInspectResult,
        lambda b, a, q: b.image_inspect(a),
        "Inspect a filtered, bounded page of image metadata without returning"
        " pixel arrays.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.image.remove",
        DeleteArguments,
        DeleteResult,
        lambda b, a, q: b.image_remove(a),
        "Remove the named image resource with native ownership/user guards.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.image.save",
        ImageSaveArguments,
        ImageSaveResult,
        lambda b, a, q: b.image_save(a),
        "Write and optionally pack a named image, then return a bounded "
        "preview artifact from verified file pixels. Overwrite and output "
        "format are explicit.",
        effect="mutating",
        execution="synchronous",
        output_artifacts="required",
    ),
    _operation(
        "blender.light.configure",
        LightConfigureArguments,
        LightSummary,
        lambda b, a, q: b.light_configure(a),
        "Patch typed light data while preserving unexposed settings and "
        "isolating shared data. Omit unchanged properties; transforms remain "
        "object operations.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.light.create",
        LightCreateArguments,
        LightSummary,
        lambda b, a, q: b.light_create(a),
        "Create a typed point/sun/spot/area light and transform with "
        "validated type-specific properties.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.light.inspect",
        InspectArguments,
        LightInspectResult,
        lambda b, a, q: b.light_inspect(a),
        "Inspect a filtered, bounded page of light objects and their typed "
        "data properties.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.material.assign",
        MaterialAssignArguments,
        MaterialAssignResult,
        lambda b, a, q: b.material_assign(a),
        "Assign a named shared material to one object slot. Slot assignment "
        "does not author per-face material indices.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.material.configure_principled",
        MaterialConfigureArguments,
        MaterialSummary,
        lambda b, a, q: b.material_configure(a),
        "Patch supported numeric Principled properties on a recognized "
        "material. Preserve custom graphs and shared material identity; RGB "
        "colors have three components.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.material.create_principled",
        MaterialCreateArguments,
        MaterialSummary,
        lambda b, a, q: b.material_create(a),
        "Create a reusable named Principled material with bounded numeric "
        "properties. RGB colors have three components; alpha is a separate "
        "property.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.material.inspect",
        InspectArguments,
        MaterialInspectResult,
        lambda b, a, q: b.material_inspect(a),
        "Inspect a filtered, bounded material page with compact Principled "
        "state and bounded assignment samples/counts.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.bevel_edges",
        MeshBevelArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Bevel explicitly selected authored edges on a detached mesh stage, "
        "with bounded width/segments and preservation guards.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.create",
        MeshCreateArguments,
        MeshSummary,
        lambda b, a, q: b.mesh_create(a),
        "Create one bounded original triangle/quad mesh with optional corner "
        "UVs, materials and shading. Vertex indices belong to this supplied "
        "mesh only.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.delete_elements",
        MeshDeleteArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Delete explicitly selected authored elements with explicit face "
        "deletion semantics and staged topology validation.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.extrude_faces",
        MeshExtrudeArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Extrude a selected authored face region with offset and scale in one"
        " staged operation.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.inset_faces",
        MeshInsetArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Inset selected authored faces with explicit thickness/depth and "
        "validated native topology publication.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.inspect",
        MeshInspectArguments,
        MeshSummary,
        lambda b, a, q: b.mesh_inspect(a),
        "Inspect one authored mesh's compact topology, bounds, data ownership"
        " and geometry fingerprint. Does not return full coordinate arrays.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.inspect_evaluated",
        EvaluatedMeshArguments,
        EvaluatedMeshSummary,
        lambda b, a, q: b.mesh_inspect_evaluated(a),
        "Inspect one viewport-evaluated mesh and optional UV/normal/tangent "
        "basis. Evaluated indices cannot be used as authored edit indices; "
        "temporary meshes are released.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.mark_seam",
        MeshSeamArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Set seam flags on selected authored edges without rewriting topology"
        " or discarding supported modifier stacks.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.merge_vertices",
        MeshMergeArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Merge an explicit authored vertex selection using the requested "
        "native merge mode and staged validation.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.inspect_topology",
        TopologyInspectArguments,
        TopologySummary,
        lambda b, a, q: b.mesh_inspect_topology(a),
        "Inspect a selected authored region: induced edges/faces, world-space "
        "length/area/aspect distributions, valence and bounded poles. Shared "
        "selectors discover rings/loops/components/neighborhoods and native "
        "rest-frame boxes; no model-side graph reconstruction. At most64 pole "
        "samples,100000 traversed elements. Counts are cage diagnostics, not "
        "deformation acceptance.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.insert_loops",
        MeshInsertLoopsArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Refine up to 16 disjoint quad strips with 1..16 ordered fractions each. "
        "Input seed edge/from_vertex indices share one snapshot; 4096 rails per "
        "strip, 2M staged elements. Native BMesh interpolates UVs, weights and "
        "supported custom attributes; material/seam flags retained. Owned "
        "Armature, Mirror/Subdivision/Shrinkwrap stacks remain ordered; shape "
        "keys/custom normals/unknown dependencies refused. Stage then swap; "
        "failure preserves data. Connectivity invalidates indices and curve "
        "surface attachments; explicitly reinspect/rebind and recheck deformation. "
        "No automatic weight correction.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.query",
        MeshQueryArguments,
        TypeAdapter(MeshQueryResult),
        lambda b, a, q: b.mesh_query(a),
        "Query authored vertices/edges/faces using a typed selector and "
        "bounded result limit. Reports matched count and truncation; topology"
        " changes invalidate indices. Shared selectors discover edge loops/rings/"
        "closed boundaries, components, neighborhoods, valence and rest-frame "
        "boxes. Traversal caps 100000 elements; loops stop at poles/non-quads, "
        "rings reject them. Details are bounded independently of matched count.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.recalculate_normals",
        MeshNormalsArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Recalculate authored face normals with explicit inward/outward "
        "orientation and native data-preservation guards.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.set_shading",
        MeshShadingArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Set selected authored face smooth/flat flags without smoothing "
        "geometry. Shading changes may invalidate a baked tangent basis.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.subdivide_edges",
        MeshSubdivideArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Subdivide selected authored edges with bounded cuts and smoothing, "
        "using staged topology validation.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.mesh.transform",
        MeshTransformArguments,
        MeshEditResult,
        lambda b, a, q: b.mesh_edit(a),
        "Transform an explicit authored region; optional ellipsoidal smooth "
        "falloff supports translation. Preserves ordering for coordinate-only"
        " edits and rejects unsafe data combinations.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.apply",
        ModifierApplyArguments,
        ModifierApplyResult,
        lambda b, a, q: b.modifier_apply(a),
        "Stage native application of one named modifier, validate the "
        "resulting mesh and publish it. Applying out of stack order may "
        "change final evaluated form.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.configure",
        CONFIGURE,
        ModifierSummary,
        lambda b, a, q: b.modifier_configure(a),
        "Patch one supported typed modifier while preserving "
        "omitted/unexposed native settings. The type discriminator selects "
        "its settings schema.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.create",
        CREATE,
        ModifierSummary,
        lambda b, a, q: b.modifier_create(a),
        "Corrective Smooth uses original coordinates before constructive modifiers, "
        "bounded iterations/factor/scale and optional native group mask; "
        "smoothing may lose volume and is not an authored corrective. "
        "Create a supported typed object-owned modifier. Type-specific "
        "properties belong in settings; stack order is significant.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.inspect",
        ModifierInspectArguments,
        ModifierInspectResult,
        lambda b, a, q: b.modifier_inspect(a),
        "Inspect one object's modifier stack, including unsupported native "
        "types without deleting or rebuilding them.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.move",
        ModifierMoveArguments,
        ModifierInspectResult,
        lambda b, a, q: b.modifier_move(a),
        "Move one named modifier to an explicit stack index, respecting "
        "dependency/detail ownership guards.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.remove",
        ModifierRemoveArguments,
        ModifierRemoveResult,
        lambda b, a, q: b.modifier_remove(a),
        "Remove one named modifier with explicit ownership and persistent-"
        "detail protections.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.multires.configure",
        MultiresConfigureArguments,
        MultiresSummary,
        lambda b, a, q: b.multires_configure(a),
        "Set viewport/sculpt/render levels of the owned Multires modifier "
        "without discarding stored displacement.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.multires.create",
        MultiresCreateArguments,
        MultiresSummary,
        lambda b, a, q: b.multires_create(a),
        "Create a guarded Multires detail owner on a suitable mesh, "
        "preserving shared-data ownership.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.multires.inspect",
        MultiresInspectArguments,
        MultiresSummary,
        lambda b, a, q: b.multires_inspect(a),
        "Inspect Multires levels, base topology and detail/allocation "
        "constraints on one object.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.multires.subdivide",
        MultiresSubdivideArguments,
        MultiresSummary,
        lambda b, a, q: b.multires_subdivide(a),
        "Add bounded Multires levels using the chosen native subdivision mode"
        " while preserving existing detail.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.object.create_primitive",
        CreateArguments,
        ObjectSummary,
        lambda b, a, q: b.create(a),
        "Create a native mesh primitive with one explicit object transform. "
        "Use mesh.create for bounded authored coordinates/faces.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.object.delete",
        DeleteArguments,
        DeleteResult,
        lambda b, a, q: b.delete(a),
        "Delete the named object, preserving unrelated application state and "
        "enforcing native ownership guards.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.object.set_transform",
        TransformArguments,
        ObjectSummary,
        lambda b, a, q: b.transform(a),
        "Patch object-local location/rotation/scale; omitted channels stay "
        "unchanged. This does not edit authored mesh coordinates.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.render.image",
        RenderArguments,
        RenderResult,
        lambda b, a, q: b.render(a),
        "Render a bounded PNG using current scene state or temporary "
        "Cycles/diagnostic overrides. Returns inline image bytes through "
        "artifacts; show_result requires an interactive host. Synchronous "
        "native rendering can outlast a client's deadline.",
        effect="transient",
        execution="synchronous",
        output_artifacts="required",
    ),
    _operation(
        "blender.retopo.bridge_loops",
        RetopoBridgeArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Bridge explicit target boundary loops with bounded segments/twist "
        "and source projection in one staged retopology edit.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.collapse",
        RetopoCollapseArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Collapse a selected retopology region with explicit boundary policy "
        "and source reprojection.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.create_target",
        RetopoCreateArguments,
        RetopoCreateResult,
        lambda b, a, q: b.retopo_create_target(a),
        "Create an independent empty target aligned to an explicit read-only source.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.extrude_boundary",
        RetopoExtrudeArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Grow a selected target boundary with offset/rotation/scale followed "
        "by source projection in one operation.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.fill_boundary",
        RetopoFillArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Fill a declared closed target boundary using typed corner/span "
        "controls, then project and validate the patch.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.insert_loop",
        RetopoInsertArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Insert 1..16 loops in one quad strip at factor or ordered factors, "
        "oriented by from_vertex, and source-project new topology. Existing retopo "
        "UV/weight/shape-key guards remain. Staged transaction; connectivity "
        "invalidates indices and surface attachments.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.inspect",
        RetopoInspectArguments,
        RetopoSummary,
        lambda b, a, q: b.retopo_inspect(a),
        "Inspect target topology and evaluated-source correspondence "
        "separately. Good manifold metrics alone do not establish deformation"
        " quality.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.project",
        RetopoProjectArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Project a selected target region to its explicit evaluated source "
        "with bounded distance and surface offset.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.relax",
        RetopoRelaxArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Relax a selected target region for bounded iterations while "
        "projecting to the source and optionally preserving boundaries.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.rotate_edge",
        RetopoRotateArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Rotate a declared target edge and reproject/validate the affected topology.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.seed_patch",
        RetopoSeedArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Create an oriented segmented quad patch on an explicit source "
        "surface using center/tangent dimensions.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.slide",
        RetopoSlideArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Slide a selected target region toward declared topology with bounded"
        " factor and source reprojection.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.stitch",
        RetopoStitchArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Stitch explicit compatible target boundary chains with a maximum "
        "weld distance and source projection.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.retopo.subdivide",
        RetopoSubdivideArguments,
        RetopoEditResult,
        lambda b, a, q: b.retopo_edit(a),
        "Subdivide selected target topology for bounded cuts and reproject it"
        " to the source.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.scene.inspect",
        SceneInspectArguments,
        SceneSummary,
        lambda b, a, q: b.inspect(a),
        "Inspect a filtered, bounded page of scene objects, global counts and"
        " active state. Selected-object names are also bounded; use explicit "
        "names/types instead of full-scene queries.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.scene.raycast",
        RAYCAST,
        RaycastResult,
        lambda b, a, q: b.scene_raycast(a),
        "Pick the current evaluated surface using a world ray or normalized "
        "camera image coordinates. Returns object-local hit positions; "
        "evaluated face indices are not authored edit selectors.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.face_sets.assign",
        FaceSetsAssignArguments,
        FaceSetsAssignResult,
        lambda b, a, q: b.sculpt_face_sets_assign(a),
        "Assign a persistent face-set ID to an explicit authored face region "
        "while preserving native sculpt data.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.face_sets.initialize",
        FaceSetsInitializeArguments,
        FaceSetsSummary,
        lambda b, a, q: b.sculpt_face_sets_initialize(a),
        "Initialize native face sets using the requested supported grouping mode.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.face_sets.inspect",
        FaceSetsInspectArguments,
        FaceSetsSummary,
        lambda b, a, q: b.sculpt_face_sets_inspect(a),
        "Inspect one object's authored face-set statistics. Face sets "
        "organize faces and are not an implicit Multires grid-mask selector.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.filter",
        SculptFilterArguments,
        SculptFilterResult,
        lambda b, a, q: b.sculpt_filter(a),
        "Apply a bounded native sculpt filter with preserved context and "
        "masks. Requires interactive View3D; failure after native mutation "
        "can leave partial sculpt changes.",
        effect="mutating",
        execution="synchronous",
        requires_interactive=True,
    ),
    _operation(
        "blender.sculpt.inspect",
        SculptInspectArguments,
        SculptSummary,
        lambda b, a, q: b.sculpt_inspect(a),
        "Inspect one object's sculpt readiness, geometry/detail state and "
        "applicable native limitations.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.mask.clear",
        MaskClearArguments,
        SculptMaskSummary,
        lambda b, a, q: b.sculpt_mask_clear(a),
        "Clear the native sculpt mask while preserving unrelated context and "
        "supported detail state.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.mask.inspect",
        MaskInspectArguments,
        SculptMaskSummary,
        lambda b, a, q: b.sculpt_mask_inspect(a),
        "Inspect authored/base sculpt-mask statistics; effective Multires "
        "grid masks are not fully exposed by RNA.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.mask.invert",
        MaskInvertArguments,
        SculptMaskSummary,
        lambda b, a, q: b.sculpt_mask_invert(a),
        "Invert the native sculpt mask without changing topology.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.mask.stroke",
        MaskStrokeArguments,
        MaskStrokeResult,
        lambda b, a, q: b.sculpt_mask_stroke(a),
        "Apply bounded explicit surface dabs to the native mask with local "
        "symmetry. Requires interactive sculpt context and can partially "
        "mutate on native failure.",
        effect="mutating",
        execution="synchronous",
        requires_interactive=True,
    ),
    _operation(
        "blender.sculpt.stroke",
        SculptStrokeArguments,
        SculptStrokeResult,
        lambda b, a, q: b.sculpt_stroke(a),
        "Apply bounded native brush dabs at object-local surface locations "
        "with radius/pressure/symmetry. Requires interactive View3D and unit "
        "inherited scale; native failure can leave partial displacement.",
        effect="mutating",
        execution="synchronous",
        requires_interactive=True,
    ),
    _operation(
        "blender.sculpt.voxel_remesh",
        VoxelRemeshArguments,
        VoxelRemeshResult,
        lambda b, a, q: b.sculpt_voxel_remesh(a),
        "Stage a bounded destructive voxel remesh with explicit data-loss "
        "policy and attribute reprojection limits. Reinspect afterward; all "
        "topology indices change.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.sculpt.voxel_remesh.inspect",
        VoxelRemeshInspectArguments,
        VoxelRemeshSummary,
        lambda b, a, q: b.sculpt_voxel_remesh_inspect(a),
        "Preflight voxel-remesh allocation, data blockers and explicit "
        "preservation/loss policy without remeshing.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.connect",
        ConnectArguments,
        LinkSummary,
        lambda b, a, q: b.shader_connect(a),
        "Connect explicit socket identifiers; occupied inputs require "
        "replace_existing. Reject incompatible sockets and preserve unrelated"
        " graph structure.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.disconnect",
        DisconnectArguments,
        DisconnectResult,
        lambda b, a, q: b.shader_disconnect(a),
        "Disconnect links from one explicit input socket without deleting nodes.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.inspect",
        ShaderInspectArguments,
        ShaderGraphSummary,
        lambda b, a, q: b.shader_inspect(a),
        "Inspect a bounded filtered node page and link page for one material."
        " Socket detail is bounded/optional; unsupported nodes remain "
        "inspectable.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.node.configure",
        NodeConfigureArguments,
        NodeSummary,
        lambda b, a, q: b.shader_configure(a),
        "Patch supported settings on one named node without recreating "
        "unrelated nodes or links.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.node.create",
        NodeCreateArguments,
        NodeSummary,
        lambda b, a, q: b.shader_create(a),
        "Create one supported typed shader node; name names the new node, "
        "node_type selects supported semantics. Graph connections are "
        "explicit operations.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.node.delete",
        NodeDeleteArguments,
        NodeDeleteResult,
        lambda b, a, q: b.shader_delete(a),
        "Delete a named editable shader node with guards protecting essential"
        " surface/output structure.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.surface_instances.configure",
        SurfaceInstancesConfigureArguments,
        SurfaceInstancesSummary,
        lambda b, a, q: b.surface_instances_configure(a),
        "Replace the declaration of an owned UV-bound rigid-instance "
        "distribution in one staged update. Existing arbitrary edited node "
        "graphs are not silently overwritten.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.surface_instances.create",
        SurfaceInstancesCreateArguments,
        SurfaceInstancesSummary,
        lambda b, a, q: b.surface_instances_create(a),
        "Create a bounded guided distribution of shared rigid mesh instances "
        "with UV-root/tangent attachment and deterministic variation. This is"
        " not flexible hair/feather grooming.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.surface_instances.inspect",
        SurfaceInstancesInspectArguments,
        SurfaceInstancesSummary,
        lambda b, a, q: b.surface_instances_inspect(a),
        "Inspect compact distribution/binding/frame statistics and bounded "
        "samples against the evaluated carrier, including invalid roots and "
        "maximum root error.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.uv.create_map",
        UVCreateArguments,
        UVInspectResult,
        lambda b, a, q: b.uv_create(a),
        "Create a named UV map on an isolated target mesh with explicit "
        "editing/render roles.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.uv.inspect",
        UVInspectArguments,
        UVInspectResult,
        lambda b, a, q: b.uv_inspect(a),
        "Inspect one mesh's UV maps, active roles and compact authored bounds"
        " without returning every corner coordinate.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.uv.inspect_layout",
        UVLayoutArguments,
        UVLayoutResult,
        lambda b, a, q: b.uv_layout(a),
        "Inspect bounded authored/evaluated UV density, overlap and "
        "distortion across named targets, optionally returning a layout image"
        " artifact.",
        effect="transient",
        execution="synchronous",
        output_artifacts="optional",
    ),
    _operation(
        "blender.uv.pack_islands",
        UVPackArguments,
        UVPackResult,
        lambda b, a, q: b.uv_pack(a),
        "Pack UV islands across a bounded object set with common density, "
        "explicit bounds and pixel padding.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.uv.set_active",
        UVSetActiveArguments,
        UVInspectResult,
        lambda b, a, q: b.uv_set_active(a),
        "Set a named UV map's editing/render role explicitly without changing"
        " coordinates.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.uv.unwrap",
        UVUnwrapArguments,
        UVInspectResult,
        lambda b, a, q: b.uv_unwrap(a),
        "Unwrap one mesh using explicit seams and a supported typed "
        "algorithm; preserves user mesh/UV selection. Does not invent "
        "anatomical seams.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.weights.assign",
        WeightsAssignArguments,
        WeightsAssignment,
        lambda b, a, q: b.weights_assign(a),
        "Replace weights in 1–32 ordered vertex regions using "
        "constants/gradients/normalization, up to four influences, and "
        "optional fixed-root adjacency smoothing. Unselected rows remain "
        "exact; publication is staged.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.weights.inspect",
        WeightsInspectArguments,
        WeightsSummary,
        lambda b, a, q: b.weights_inspect(a),
        "Inspect compact deform-group statistics over a typed vertex region "
        "with bounded filtered samples; no full weight matrix by default.",
        effect="read_only",
        execution="synchronous",
    ),
)
REGISTRY = {item.contract.name: item for item in _DECLARATIONS}
if len(REGISTRY) != len(_DECLARATIONS):
    raise RuntimeError("Duplicate operation registry name")
OPERATIONS = tuple(sorted(REGISTRY))


def registration(instance_id: str, version: str, filepath: str) -> AdapterRegistration:
    return AdapterRegistration(
        type="adapter.register",
        instance_id=instance_id,
        application="blender",
        application_version=version,
        project_path=filepath or None,
        operations=tuple(REGISTRY[name].contract for name in OPERATIONS),
    )


def _safe_text(value: str, limit: int) -> str:
    return value.encode("utf-8", "backslashreplace").decode("utf-8")[:limit]


def execute(backend: SceneBackend, request: OperationRequest) -> Response:
    if not backend.operation_allowed(request.operation):
        return failure(
            request,
            ProtocolError(
                code="adapter_busy",
                message=(
                    "A native bake owns temporary scene resources; "
                    "inspect its status first"
                ),
            ),
        )
    spec = REGISTRY.get(request.operation)
    if spec is None:
        return failure(
            request,
            ProtocolError(
                code="operation_unsupported", message="Operation is not advertised"
            ),
        )
    try:
        arguments = spec.parse(request.arguments)
    except ValidationError as exc:
        details: list[JsonValue] = []
        for item in exc.errors(include_context=False, include_url=False)[:8]:
            received = item.get("input")
            preview = (
                str(received)[:120]
                if isinstance(received, (str, int, float, bool)) or received is None
                else f"{type(received).__name__} with {len(received)} items"
                if isinstance(received, (list, dict))
                else type(received).__name__
            )
            details.append(
                {
                    "field": _safe_text(".".join(map(str, item["loc"])), 500),
                    "message": _safe_text(item["msg"], 500),
                    "reason": item["type"],
                    "received": _safe_text(preview, 120),
                }
            )
        return failure(
            request,
            ProtocolError(
                code="invalid_arguments",
                message=(
                    f"Invalid arguments for {request.operation} "
                    f"({exc.error_count()} errors; up to 8 shown)"
                ),
                details=details,
            ),
        )
    try:
        if spec.contract.input_artifacts == "none" and request.artifacts:
            raise OperationError(
                "invalid_arguments", "This operation accepts no input artifacts"
            )
        result, artifacts = spec.invoke(backend, arguments, request)
        return OperationSuccess(
            type="operation.success",
            request_id=request.request_id,
            result=result.model_dump(mode="json"),
            artifacts=artifacts,
        )
    except OperationError as exc:
        logger.info("Operation %s failed: %s", request.operation, exc.error.code)
        return failure(request, exc.error)
    except Exception:
        logger.exception("Unexpected failure executing %s", request.operation)
        return failure(
            request,
            ProtocolError(
                code="operation_failed",
                message="Blender operation failed; see the application log",
            ),
        )
