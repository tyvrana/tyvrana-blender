"""Validated operation dispatch independent of Blender's Python module."""

from typing import Protocol

from pydantic import TypeAdapter
from tyvrana_protocol import (
    AdapterRegistration,
    AdapterRuntime,
    ArtifactDescriptor,
    OperationRequest,
    ProofHostControl,
    ProofHostStart,
    ProofHostStatus,
    ResourceInspectionRequest,
    ResourceInspectionResult,
)

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
    CameraSetActiveArguments,
    CameraSummary,
)
from .cleanup_models import CleanupArguments, CleanupResult
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
from .dynamics_models import (
    DynamicsBakeArguments,
    DynamicsCache,
    DynamicsJobArguments,
    DynamicsJobStatus,
    DynamicsObjectArguments,
)
from .errors import OperationError as OperationError
from .extension_models import (
    ExtensionInspectArguments,
    ExtensionReloadArguments,
    ExtensionReloadResult,
    ExtensionState,
)
from .file_models import (
    FileInspectArguments,
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
    ImageConfigureArguments,
    ImageCreateArguments,
    ImageFromArtifactArguments,
    ImageInspectResult,
    ImagePreviewArguments,
    ImagePreviewResult,
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
from .layer_models import (
    LayerCaptureArguments,
    LayerInspectArguments,
    LayerInspectResult,
    LayerReferencesResult,
    LayerRemoveArguments,
)
from .light_models import (
    LightConfigureArguments,
    LightCreateArguments,
    LightInspectResult,
    LightSummary,
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
    MaterialAssignArguments,
    MaterialAssignResult,
    MaterialConfigureArguments,
    MaterialCreateArguments,
    MaterialInspectResult,
    MaterialSummary,
)
from .mechanics_models import ContactArguments, ContactResult, FitArguments, FitResult
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
    ObjectSummary,
    RenderArguments,
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
from .operation_dispatch import (
    OperationSpec as OperationSpec,
)
from .operation_dispatch import (
    Response as Response,
)
from .operation_dispatch import (
    _operation,
)
from .operation_dispatch import (
    argument_schema as argument_schema,
)
from .operation_dispatch import (
    execute as dispatch,
)
from .operation_dispatch import (
    failure as failure,
)
from .operation_dispatch import (
    logger as logger,
)
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
    RenderDevicesArguments,
    RenderDevicesResult,
    RenderJobArguments,
    RenderJobStatus,
    RenderStatusArguments,
)
from .render_operations import DECLARATIONS as _RENDER_DECLARATIONS
from .restore_models import RestoreArguments, RestoreJobStatus
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


class SceneBackend(Protocol):
    def proof_host_start(self, arguments: ProofHostStart) -> ProofHostStatus: ...
    def proof_host_status(self, arguments: ProofHostControl) -> ProofHostStatus: ...
    def proof_host_stop(self, arguments: ProofHostControl) -> ProofHostStatus: ...

    def mesh_cleanup(self, arguments: CleanupArguments) -> CleanupResult: ...

    def assembly_create(self, arguments: AssemblyCreateArguments) -> AssemblyResult: ...
    def assembly_configure(
        self, arguments: AssemblyConfigureArguments
    ) -> AssemblyResult: ...
    def assembly_inspect(
        self, arguments: AssemblyInspectArguments
    ) -> AssemblyResult: ...
    def object_set_place(self, arguments: PlacementArguments) -> PlacementResult: ...

    def growth_layers_correct(self, arguments: LayerCorrectArguments) -> LayerCache: ...

    def growth_layers_inspect(self, arguments: LayerObjectArguments) -> LayerCache: ...

    def growth_layers_clear(self, arguments: LayerObjectArguments) -> LayerCache: ...

    def growth_dynamics_bake(
        self, arguments: DynamicsBakeArguments
    ) -> DynamicsJobStatus: ...

    def growth_dynamics_inspect(
        self, arguments: DynamicsObjectArguments
    ) -> DynamicsCache: ...

    def growth_dynamics_status(
        self, arguments: DynamicsJobArguments
    ) -> DynamicsJobStatus: ...

    def growth_dynamics_cancel(
        self, arguments: DynamicsJobArguments
    ) -> DynamicsJobStatus: ...

    def growth_dynamics_clear(
        self, arguments: DynamicsObjectArguments
    ) -> DynamicsCache: ...

    def growth_create(self, arguments: GrowthCreateArguments) -> GrowthDelta: ...

    def growth_configure(self, arguments: GrowthConfigureArguments) -> GrowthDelta: ...

    def growth_inspect(
        self, arguments: GrowthInspectArguments
    ) -> GrowthInspectResult: ...

    def growth_remove(self, arguments: GrowthRemoveArguments) -> GrowthRemoveResult: ...

    def growth_sample(self, arguments: GrowthSampleArguments) -> GrowthSampleResult: ...

    def control_rig_configure(
        self, arguments: ControlRigConfigureArguments
    ) -> ControlRigResult: ...

    def control_rig_inspect(
        self, arguments: ControlRigInspectArguments
    ) -> ControlRigResult: ...

    def control_rig_switch(
        self, arguments: ControlRigSwitchArguments
    ) -> ControlRigResult: ...

    def control_rig_remove(
        self, arguments: ControlRigInspectArguments
    ) -> ControlRigResult: ...

    def constraint_configure(
        self, arguments: ConstraintsConfigureArguments
    ) -> ConstraintsResult: ...

    def constraint_inspect(
        self, arguments: ConstraintsInspectArguments
    ) -> ConstraintsResult: ...

    def constraint_remove(
        self, arguments: ConstraintsRemoveArguments
    ) -> ConstraintsResult: ...

    def pose_match(self, arguments: PoseMatchArguments) -> PoseMatchResult: ...

    def space_switch(self, arguments: SpaceSwitchArguments) -> ConstraintsResult: ...

    def surface_create(self, arguments: SurfaceCreateArguments) -> SurfaceResult: ...

    def surface_configure(
        self, arguments: SurfaceConfigureArguments
    ) -> SurfaceResult: ...

    def surface_inspect(
        self, arguments: SurfaceNetworkInspectArguments
    ) -> SurfaceResult: ...

    def reference_compare(
        self, arguments: ReferenceCompareArguments
    ) -> tuple[ReferenceCompareResult, ArtifactDescriptor | None]: ...

    def form_create(self, arguments: FormCreateArguments) -> FormJobStatus: ...

    def form_configure(self, arguments: FormConfigureArguments) -> FormJobStatus: ...

    def form_status(self, arguments: FormJobArguments) -> FormJobStatus: ...

    def form_cancel(self, arguments: FormJobArguments) -> FormJobStatus: ...

    def form_inspect(self, arguments: FormInspectArguments) -> FormResult: ...

    def loft_create(self, arguments: LoftCreateArguments) -> LoftResult: ...

    def loft_configure(self, arguments: LoftConfigureArguments) -> LoftResult: ...

    def loft_inspect(self, arguments: LoftInspectArguments) -> LoftResult: ...

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
    ) -> ObjectSetConfigureResult: ...

    def object_set_inspect(
        self, arguments: ObjectSetInspectArguments
    ) -> ObjectSetInspectResult: ...

    def object_set_remove(
        self, arguments: ObjectSetRemoveArguments
    ) -> ObjectSetRemoveResult: ...

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

    def reference_register(
        self, arguments: RegistrationArguments
    ) -> RegistrationResult: ...

    def reference_registration_inspect(
        self, arguments: ReferenceInspectArguments
    ) -> RegistrationResult: ...

    def reference_observation_set(
        self, arguments: ObservationSetArguments
    ) -> ObservationWriteResult: ...

    def reference_observation_inspect(
        self, arguments: ObservationInspectArguments
    ) -> ObservationResult: ...

    def reference_observation_remove(
        self, arguments: NamedRemoveArguments
    ) -> NamedRemoveResult: ...

    def landmark_derive(
        self, arguments: LandmarkDeriveArguments
    ) -> ConstructionReport: ...

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
    def timeline_inspect(
        self, arguments: TimelineInspectArguments
    ) -> TimelineState: ...

    def timeline_configure(self, arguments: TimelineArguments) -> TimelineState: ...

    def motion_set_properties(self, arguments: PropertiesArguments) -> MotionNames: ...

    def coupling_configure(
        self, arguments: CouplingConfigureArguments
    ) -> MotionNames: ...

    def coupling_inspect(
        self, arguments: CouplingInspectArguments
    ) -> CouplingInspectResult: ...

    def coupling_remove(self, arguments: MotionRemoveArguments) -> MotionNames: ...

    def coupling_solve(
        self, arguments: MechanismSolveArguments
    ) -> MechanismSolution: ...

    def action_edit(self, arguments: ActionEditArguments) -> ActionResult: ...

    def action_assign(self, arguments: ActionAssignArguments) -> MotionNames: ...

    def action_inspect(self, arguments: ActionInspectArguments) -> ActionResult: ...

    def action_remove(self, arguments: ActionRemoveArguments) -> MotionNames: ...

    def motion_sample(self, arguments: MotionSampleArguments) -> MotionSampleResult: ...

    def geometry_fit(self, arguments: FitArguments) -> FitResult: ...

    def contact_inspect(self, arguments: ContactArguments) -> ContactResult: ...

    def geometry_inspect(
        self, arguments: GeometryInspectArguments
    ) -> GeometryInspectResult: ...

    def volume_inspect(
        self, arguments: VolumeInspectArguments
    ) -> VolumeInspectResult: ...

    def volume_snapshot(
        self, arguments: VolumeSnapshotArguments
    ) -> VolumeSnapshotResult: ...

    def layer_inspect(self, arguments: LayerInspectArguments) -> LayerInspectResult: ...

    def layer_capture_reference(
        self, arguments: LayerCaptureArguments
    ) -> LayerReferencesResult: ...

    def layer_remove_reference(
        self, arguments: LayerRemoveArguments
    ) -> LayerReferencesResult: ...

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
    def document_restore(
        self, arguments: RestoreArguments, request: OperationRequest
    ) -> RestoreJobStatus: ...

    def document_restore_status(
        self, arguments: AttestationJobArguments
    ) -> RestoreJobStatus: ...

    def document_mutate(
        self, arguments: MutationArguments, request: OperationRequest
    ) -> MutationJobStatus: ...

    def document_mutation_status(
        self, arguments: AttestationJobArguments
    ) -> MutationJobStatus: ...

    def document_attest(self) -> DocumentAttestationResult: ...

    def document_attest_status(
        self, arguments: AttestationJobArguments
    ) -> AttestationJobStatus: ...

    def document_attest_cancel(
        self, arguments: AttestationJobArguments
    ) -> AttestationJobStatus: ...

    def extension_reload(
        self, arguments: ExtensionReloadArguments, request_id: str
    ) -> ExtensionReloadResult: ...

    def file_inspect(self) -> FileState: ...

    def file_audit(self, arguments: FileAuditArguments) -> FileAuditResult: ...
    def file_new(self, arguments: FileNewArguments) -> FileState: ...

    def viewport_configure(
        self, arguments: ViewportConfigureArguments
    ) -> ViewportState: ...

    def viewport_capture(
        self, arguments: ViewportCaptureArguments
    ) -> tuple[ViewportCaptureResult, ArtifactDescriptor]: ...

    def viewport_inspect(
        self, arguments: ViewportInspectArguments
    ) -> ViewportInspection: ...

    def viewport_frame(self, arguments: ViewportFrameArguments) -> ViewportState: ...

    def file_open(self, arguments: FileOpenArguments) -> FileState: ...
    def project_bind(self, arguments: ProjectBindArguments) -> ProjectBindResult: ...
    def resource_inspect(
        self, arguments: ResourceInspectionRequest
    ) -> ResourceInspectionResult: ...
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
    def modifier_create_batch(
        self, arguments: ModifierBatchCreateArguments
    ) -> ModifierBatchCreateResult: ...

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
    def image_preview(
        self, arguments: ImagePreviewArguments
    ) -> tuple[ImagePreviewResult, ArtifactDescriptor]: ...
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

    def material_author(
        self, arguments: MaterialAuthorArguments
    ) -> MaterialSummary: ...
    def shader_author(self, arguments: GraphAuthorArguments) -> MaterialSummary: ...
    def material_copy(self, arguments: MaterialCopyArguments) -> MaterialSummary: ...
    def material_remove(
        self, arguments: MaterialRemoveArguments
    ) -> MaterialRemoveResult: ...
    def material_assign_batch(
        self, arguments: AssignBatchArguments
    ) -> AssignBatchResult: ...

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
    def render(self, arguments: RenderArguments, job_id: str) -> RenderJobStatus: ...
    def render_devices(self) -> RenderDevicesResult: ...
    def render_status(self, arguments: RenderStatusArguments) -> RenderJobStatus: ...
    def render_cancel(self, arguments: RenderJobArguments) -> RenderJobStatus: ...
    def render_result(
        self, arguments: RenderJobArguments
    ) -> tuple[RenderJobStatus, ArtifactDescriptor]: ...


_DECLARATIONS = (
    _operation(
        "blender.proof_host.start",
        ProofHostStart,
        ProofHostStatus,
        lambda b, a, q: b.proof_host_start(a),
        "Core-internal lifecycle: start an owned independent proof host "
        "from an exact durable artifact.",
        tags=("proof_host_start",),
        effect="lifecycle",
        execution="job_start",
    ),
    _operation(
        "blender.proof_host.status",
        ProofHostControl,
        ProofHostStatus,
        lambda b, a, q: b.proof_host_status(a),
        "Core-internal lifecycle: inspect readiness of an owned proof process.",
        tags=("proof_host_status",),
        effect="read_only",
        execution="job_status",
    ),
    _operation(
        "blender.proof_host.stop",
        ProofHostControl,
        ProofHostStatus,
        lambda b, a, q: b.proof_host_stop(a),
        "Core-internal lifecycle: release a proof lease "
        "and terminate only its owned process.",
        tags=("proof_host_stop",),
        effect="lifecycle",
        execution="lifecycle",
    ),
    _operation(
        "blender.mesh.cleanup",
        CleanupArguments,
        CleanupResult,
        lambda b, a, q: b.mesh_cleanup(a),
        "Atomically clean up1..32 meshes: explicit merge distance, "
        "duplicate/degenerate "
        "and loose removal, normal consistency; optional bounded hole filling, "
        "small-island "
        "removal and ngon triangulation. Preview stages the same work without "
        "publishing. "
        "Preserve openings by default. Reject remaining non-manifold/self-intersecting "
        "geometry; require_closed optionally enforces closed shells. Not automatic "
        "retopology or arbitrary shape repair.2M total mesh elements; protected native "
        "dependencies and construction metadata require explicit resolution. Clean "
        "managed meshes are no-ops; actual repair needs explicit detach_construction "
        "and invalidates generator revision. Object/resource identity persists.",
        tags=("modeling", "topology", "repair", "cleanup", "batched"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.assembly.create",
        AssemblyCreateArguments,
        AssemblyResult,
        lambda b, a, q: b.assembly_create(a),
        "Create mixed loft/surface/constructive-form families with stable identities. "
        "Up to64 components,16 templates,32 families and256KiB intent. Default131072 "
        "vertices; explicit max_vertices supports bounded detailed assemblies. "
        "Paths place members by arc length. Morph corresponding shape handles through "
        "related templates, then apply indexed exceptions and bilateral reflection. "
        "Shape variation is distinct from scale/rotation; preserve discrete topology "
        "settings for corresponding loft cages. Native volume forms may retessellate. "
        "Atomic construction; inspect compact inventory and revise with "
        "assembly.configure.",
        tags=(
            "modeling",
            "structural",
            "assembly",
            "family",
            "repeat",
            "mirror",
            "batched",
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.assembly.configure",
        AssemblyConfigureArguments,
        AssemblyResult,
        lambda b, a, q: b.assembly_configure(a),
        "Revise selected assembly templates/family rules or indexed member exceptions. "
        "Omitted rules persist; expected_revision guards stale edits. Keep "
        "membership/counts "
        "and semantic IDs. Geometry-only changes preserve ordered connectivity/data; "
        "changed tessellation requires topology_policy=rebuild and no protected "
        "downstream "
        "data. Externally edited base meshes are protected. Atomic staged rollback. "
        "Use members.shape for section/feature/opening/thickness/form-part changes; "
        "refresh_placements re-evaluates saved landmark/interface rules. "
        "Returns totals, all changed names and at most16 changed member rows; "
        "unchanged inventory is omitted.",
        tags=("modeling", "structural", "assembly", "family", "revision", "batched"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.assembly.inspect",
        AssemblyInspectArguments,
        AssemblyResult,
        lambda b, a, q: b.assembly_inspect(a),
        "Compact assembly inventory: stable member/resource IDs, family/side "
        "provenance, "
        "bounds/dimensions, counts, named regions and construction/placement "
        "freshness. "
        "Default16/max64 member rows; limit=0 totals only. Filter families. "
        "Full sparse specification is opt-in; never generated vertex arrays. "
        "Construction validity is not evaluated-modifier or visual acceptance.",
        tags=("structural", "assembly", "inventory", "inspection"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.object_set.place",
        PlacementArguments,
        PlacementResult,
        lambda b, a, q: b.object_set_place(a),
        "Seating mode solves 2..8 simultaneous point/frame/regional-surface interfaces "
        "for one rigid assembly (up to256 members including descendants), with "
        "selected clearance/penetration guards, bounded translation/rotation, "
        "mirrored initial estimates and preview/apply. No scale/deformation; "
        "non-SOLVED results leave all transforms intact. Compact residuals and "
        "explicit numerical/work bounds; local solve, not global feasibility. "
        "Alternatively atomically place/fit up to64 mesh components: between "
        "named landmarks/object "
        "points, align a local anchor/axis to a target frame, fit selected local "
        "dimensions, "
        "reflect a source transform, or align local origin/unit-axis points to four "
        "measured datums (including mirrored/scaled orthogonal frames). Math runs "
        "in Blender; no caller matrix or Euler conversion is needed. "
        "Between fits bounding end planes, not arbitrary curved centerline length; "
        "dimensions are local-axis extents, world scene units. Preserve geometry and "
        "resource IDs. refresh re-evaluates stored rules. Static constraints, not live "
        "drivers; reject cycles, stale landmarks, singular axes and shear. "
        "Mirror placement positions an existing independently authored component; "
        "assembly families can create mirrored geometry with provenance.",
        tags=("structural", "assembly", "placement", "landmark", "fit", "batched"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.timeline.inspect",
        TimelineInspectArguments,
        TimelineState,
        lambda b, a, q: b.timeline_inspect(a),
        (
            "Read native scene frame/subframe, animation and preview ranges, "
            "FPS and effective FPS. Integer frames plus subframe [0,1). No "
            "playback UI."
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.timeline.configure",
        TimelineArguments,
        TimelineState,
        lambda b, a, q: b.timeline_configure(a),
        (
            "Patch scene time/ranges/FPS; omitted fields preserved. frame_set"
            " evaluates native animation and drivers in background mode. No "
            "playback, simulation bake or action assignment."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.motion.set_properties",
        PropertiesArguments,
        MotionNames,
        lambda b, a, q: b.motion_set_properties(a),
        (
            "Create/update 1..64 owned finite scalar controls on local "
            "objects, at most 64/object. Explicit value/range; no arbitrary "
            "custom property path. Existing keyed/driven controls are "
            "protected. Use kind=property channels to animate or couple these"
            " values."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.coupling.configure",
        CouplingConfigureArguments,
        MotionNames,
        lambda b, a, q: b.coupling_configure(a),
        (
            "Configure scalar couplings and/or up to8 named mixed closed mechanisms "
            "(16/scene). Mechanisms reuse native local rotation/location channels, "
            "fixed native links/frames and point closures; solve with coupling.solve "
            "or motion.sample.mechanism. Scalar couplings:1..64 drivers,256/scene. "
            "Typed weighted sources→linear/remap/piecewise→target: object/pose-bone "
            "transforms, relative shape values, existing constraint "
            "influence, owned scalar controls. Transform sources use "
            "evaluated LOCAL_SPACE including constraints; destinations use "
            "raw local channels. XYZ radians, native units; principal "
            "constrained joints. No arbitrary expressions, RNA paths, self, "
            "namespaces, Python callbacks or polling. Native simple "
            "expressions work with script auto-run disabled. Linear "
            "scale/offset and explicit output clamp; remap may invert output,"
            " clamps by default. Up to eight weighted source channels are added "
            "before mapping; piecewise knots use constant or linear extrapolation. "
            "Finite range targets require output bounds. "
            "Obvious channel/parent cycles, unsupported external "
            "dependencies, linked data, key/driver conflicts rejected before "
            "publication; batch rollback. Non-native curve length/volume "
            "metrics are not persistent sources. Unknown external drivers are"
            " preserved."
        ),
        tags=("recovery_replay", "rigging", "mapping", "coupled", "batched"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.coupling.solve",
        MechanismSolveArguments,
        MechanismSolution,
        lambda b, a, q: b.coupling_solve(a),
        "Solve a named bounded mixed revolute/prismatic closed coupling using "
        "projected damped least squares on existing local typed channels and "
        "world endpoint closures. At most12 solved variables,12 closures,64 "
        "iterations and4096 evaluations. Explicit rank/conditioning, limits, "
        "residual and failure status. apply=false restores all state; apply=true "
        "commits only a qualified solution. No expression or trajectory tables. "
        "Use motion.sample.mechanism for continuation across a bounded range.",
        tags=("mechanics", "closed_chain", "prismatic", "solver"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.coupling.inspect",
        CouplingInspectArguments,
        CouplingInspectResult,
        lambda b, a, q: b.coupling_inspect(a),
        (
            "Paged scalar and closed-mechanism relationships, definitions/variables/"
            "ranges, "
            "current values and closure/limit residuals; last committed numerical "
            "condition/status is exposed only while its state fingerprint matches. "
            "Scalar summaries: typed source/target/mapping,"
            " current source, mapped value, raw and evaluated target, "
            "absolute mapping/constrained error, saturation, validity and "
            "upstream relationships. No raw expression/FCurve dump. Native "
            "object pointers survive rename/reopen; missing/edited paths are "
            "invalid, never silently rebound."
        ),
        tags=("rigging", "mapping", "coupled"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.coupling.remove",
        MotionRemoveArguments,
        MotionNames,
        lambda b, a, q: b.coupling_remove(a),
        (
            "Remove 1..64 scalar or closed coupling records and exact owned drivers "
            "after whole-batch ownership "
            "validation. Retain unrelated drivers/actions and current scalar "
            "values. Externally modified native drivers are protected; no "
            "orphan purge."
        ),
        tags=("rigging", "mapping", "coupled", "batched"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.action.edit",
        ActionEditArguments,
        ActionResult,
        lambda b, a, q: b.action_edit(a),
        (
            "Create or edit one owned slotted action without implicit "
            "assignment. Up to 128 channels,512 integer-frame "
            "keys/channel,8192 total keys/changes. Typed channels share "
            "coupling semantics. Upsert/remove exact frames or replace/remove"
            " one channel; preserve other channels. CONSTANT/LINEAR/BEZIER "
            "with AUTO_CLAMPED handles; constant/linear extrapolation. Native"
            " slots per owner ID, one layer/keyframe strip. Stage copy and "
            "publish atomically to existing declared users; "
            "unknown/shared/NLA users and driven targets protected. "
            "Unassigned actions persist with fake user. No arbitrary paths or"
            " animation DSL."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.action.assign",
        ActionAssignArguments,
        MotionNames,
        lambda b, a, q: b.action_assign(a),
        (
            "Assign owned action slots to all declared owners with REPLACE "
            "blending, influence 1, HOLD action extrapolation. replace=true "
            "required for existing different active actions, which remain "
            "retained. detach=true removes only matching assignment and "
            "retains action/current values. Reject driver conflicts/NLA; "
            "assignment rollback."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.action.inspect",
        ActionInspectArguments,
        ActionResult,
        lambda b, a, q: b.action_inspect(a),
        (
            "Compact owned action counts, frame range and paged typed "
            "channels with interpolation/extrapolation, key counts, "
            "assignment, current/evaluated values and driver conflicts. Keys "
            "only by explicit key_limit; max 1024 detailed keys/response. "
            "Native slots/ownership validated; no full FCurve/handle dump."
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.action.remove",
        ActionRemoveArguments,
        MotionNames,
        lambda b, a, q: b.action_remove(a),
        (
            "Delete one unused owned action; detach first. Refuse "
            "shared/external/NLA users. No deletion of unrelated user "
            "actions."
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.motion.sample",
        MotionSampleArguments,
        MotionSampleResult,
        lambda b, a, q: b.motion_sample(a),
        (
            "Optionally solve one mixed closed mechanism with warm-start branch "
            "continuity at each sampled frame before diagnostics. Aggregate solved/"
            "failed/uncertain counts, closure/limits/conditioning and bounded worst "
            "samples;50000 total solve evaluations maximum. Unsolved frames skip "
            "downstream geometry/contact QA. Evaluate active actions over1..128 "
            "explicit/range "
            "integer frames in one call. Never assigns actions. Reuse "
            "joint/deformation/target/volume/layer diagnostics and 32 typed "
            "distance/angle measurements (world Blender units/degrees), including "
            "closure residuals and comparison violations; meshes "
            "compare with evaluated reference_frame (default first sample), "
            "not implicit rest. Aggregate min/max/mean/p 05/p 50/p 95 with worst"
            " frames, thresholds, coupling error/saturation, "
            "constrained/requested XYZ radians and world joint endpoints. At "
            "most 1024 metrics, 2M evaluated vertex samples, 8 detail frames, "
            "2048 detailed scalars, 384 KiB JSON output, 64 "
            "returned violations, 256 selected/dependent mechanics objects. "
            "Unrelated static scene objects do not count. Scoped contacts "
            "aggregate envelope classifications/worst frames; max2M contact tests"
            " across the call. Off-scope animated channels restore within a "
            "separate8192-channel budget. Unverified frame callbacks are "
            "rejected. layers.worst_limit must "
            "be 0. Restore frame/subframe, transforms, shape/control values "
            "and constraint influences even on failure. No continuous-extrema"
            " guarantee, hidden polling, physical simulation or arbitrary "
            "code; non-native metrics remain read-only diagnostics."
        ),
        effect="transient",
        execution="synchronous",
    ),
    _operation(
        "blender.geometry.fit",
        FitArguments,
        FitResult,
        lambda b, a, q: b.geometry_fit(a),
        "Fit 1..8 sphere/circle/cylinder/plane or landmark frames from "
        "bounded mesh selectors, named construction features and typed "
        "points. Normalized least squares and rank/conditioning/residual "
        "gates; ambiguous evidence returns UNCERTAIN without a fabricated "
        "frame. Cylinder axes require coherent surface normals or directed "
        "point evidence. A sphere alone has no axis. Optional typed "
        "axis/secondary evidence avoids manual roll math. Return center, "
        "axis, radius, errors, support, source fingerprint and ready-to-use "
        "world-space rest frame head/tail/x_reference. Roll without secondary"
        " evidence is explicitly conventional. expected_sha256 detects "
        "changed source geometry/points; stale fits provide no consumable "
        "frame. No anatomy inference or persistent mutation. Max8192 "
        "samples/fit,1M evaluated vertices.",
        effect="read_only",
        execution="synchronous",
        tags=("mechanics", "joint", "fit", "frame", "landmark"),
    ),
    _operation(
        "blender.contact.inspect",
        ContactArguments,
        ContactResult,
        lambda b, a, q: b.contact_inspect(a),
        "Classify 1..8 selected regional contact envelopes: SEPARATED, "
        "PERMITTED_CONTACT, INVALID_PENETRATION or UNCERTAIN. Caller declares"
        " minimum/maximum signed gap. Closed solids use winding and signed "
        "nearest distance; oriented open patches require explicit allowed "
        "side, interior boundary support and coherent normals. Rim/ambiguous "
        "projections are UNCERTAIN, never hidden by exemptions. Bounded "
        "vertex sampling, counts/fraction, signed gaps, penetration metric, "
        "worst-N, source fingerprints and reasons. Patch depth is a local "
        "unilateral metric, not intersection volume or minimum translation. "
        "No continuous collision or full-scene physics. Up to2048 "
        "samples/interface and2M tests; read-only.",
        effect="read_only",
        execution="synchronous",
        tags=("mechanics", "contact", "clearance", "regional"),
    ),
    _operation(
        "blender.geometry.inspect",
        GeometryInspectArguments,
        GeometryInspectResult,
        lambda b, a, q: b.geometry_inspect(a),
        (
            "Bounded world-space triangle surface clearance/contact, component "
            "containment, nonadjacent self-contact, degeneration and reference-local "
            "normal/area and rank-aware local affine Jacobian proxies. Batch "
            "up to128 objects or pairs in one call, or instances, over fractional "
            "frames or an adaptive range; "
            "restore frame/subframe. Face-pair exemptions exclude intended contacts. "
            "Branch-and-bound triangle distances; fail if work budget exceeded. "
            "Normal reversal is not inversion proof; signed volume reversal is global "
            "orientation only. Sampled motion, never continuous collision "
            "certification. "
            "Instance queries reuse mesh prototypes and test bounded candidates; "
            "owned growth templates use native evaluated paths and stable root IDs. "
            "Coverage reports gaps/unrefined risky intervals. Planar one-rings have "
            "no volume Jacobian; negative proxies are not volumetric-element proof. "
            "Closed-surface containment assumes no self intersections. "
            "At most 128 summaries, 256 details, 1M vertex samples, 250k "
            "triangles/sample."
        ),
        tags=("collision", "clearance", "self_intersection", "deformation", "sampled"),
        effect="transient",
        execution="synchronous",
    ),
    _operation(
        "blender.volume.inspect",
        VolumeInspectArguments,
        VolumeInspectResult,
        lambda b, a, q: b.volume_inspect(a),
        (
            "Inspect 1..16 evaluated mesh or owned profiled-curve volumes in world "
            "units. Reuse curve.create/configure for capped POLY/BEZIER/NURBS "
            "sweeps with variable control radius, circle/custom profiles and "
            "object/bone/triangle attachments. Reports closed consistently wound "
            "signed-sum magnitude, area, surface centroid, bounds, guide length, "
            "radius and attachment error. Open surfaces return null volume; self-"
            "intersection and mixed component winding are not solid-union "
            "validation. Ratios use an explicit evaluated reference object, or "
            "authored mesh geometry; curves have no implicit rest reference. "
            "volume.snapshot freezes a reference. Optional section samples are per "
            "spline, capped at 256 total. Limits 250000 vertices/500000 triangles "
            "per object and 1000000 evaluated vertices/call; details at most 32 "
            "samples/spline. No automatic volume preservation. "
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.volume.snapshot",
        VolumeSnapshotArguments,
        VolumeSnapshotResult,
        lambda b, a, q: b.volume_snapshot(a),
        (
            "Freeze 1..16 evaluated local mesh/owned profiled-curve sources as new "
            "editable native meshes. Source state is preserved; output has no live "
            "guide/driver or modifier relationship. Preserves native evaluated "
            "material/UV/deform layers and group names; shared "
            "collections/role/tags. Reuse Armature weights, Surface Deform and "
            "shape keys for subsequent deformation. Snapshot path-length provenance "
            "supports fixed volume/path references. All-or-nothing creation; unused "
            "unique names and editable collections required. Same surface budgets "
            "as volume.inspect. No source modifier application. "
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.layer.inspect",
        LayerInspectArguments,
        LayerInspectResult,
        lambda b, a, q: b.layer_inspect(a),
        (
            "Measure up to 8 evaluated mesh/owned profiled-curve layer pairs in "
            "world units using BVH, not all-pairs scans. Empty queries list saved "
            "reference names. Select current evaluated source topology using shared "
            "mesh selectors; evenly sample sorted vertex-index ranks (not surface-"
            "area uniform), 1..8192 per pair. Separation is nearest Euclidean "
            "surface distance; contact counts distance <= contact_distance. "
            "Oriented gap is dot(source-nearest, target face normal); negative-side "
            "count/depth is a local normal-side penetration proxy, not closed-solid "
            "containment or collision. Optional ray along +/- source normal reports "
            "normal-ray distance and misses, not universal thickness. Named "
            "references track source vertex against captured target triangle "
            "barycentric point advected in its current orthonormal frame: "
            "X=vertex0->1, Z=triangle normal, Y=Z cross X. Delta from captured "
            "frame offset yields signed tangent XY, normal change and tangent "
            "magnitude; common rigid motion cancels. Not geodesic slip, friction or "
            "simulation. Authored/evaluated ordered topology changes or missing "
            "dependencies invalidate saved references explicitly. Maximum 32 worst "
            "locations/pair, 250000 vertices/500000 triangles/object and 1000000 "
            "vertices/call. "
        ),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.layer.capture_reference",
        LayerCaptureArguments,
        LayerReferencesResult,
        lambda b, a, q: b.layer_capture_reference(a),
        (
            "Capture 1..8 named layer QA references from current evaluated "
            "geometry. Freeze sampled source vertex indices and target nearest "
            "triangle vertex triples, barycentric coordinates and local-frame "
            "offset; same selector/sampling and surface limits as layer.inspect. "
            "Scene owns native object pointers (rename-safe) plus bounded metadata, "
            "saved in blend files. Explicit replace required to recapture. Maximum "
            "32 references, 131072 stored samples and 24 MB metadata. Connectivity "
            "changes invalidate, coordinates/poses remain trackable. No deformation "
            "binding or solver is created. "
        ),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.layer.remove_reference",
        LayerRemoveArguments,
        LayerReferencesResult,
        lambda b, a, q: b.layer_remove_reference(a),
        (
            "Remove 1..32 named scene-owned layer QA references and their object "
            "pointers. Prevalidates all names; source meshes, curves, modifiers and "
            "deformation relationships remain intact. "
        ),
        effect="mutating",
        execution="synchronous",
    ),
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
            "restores original keys/data and pose. After bound rest revision, "
            "inspect and supply acknowledge_rest_sha256 before edits; recapture "
            "stale targets and revalidate motion."
        ),
        tags=("deformation", "corrective", "rest_revision"),
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
            "topology; no coordinate dump. Reports the rest revision signature "
            "and whether corrective editing still requires acknowledgement."
        ),
        tags=("deformation", "corrective", "inspection"),
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
        tags=("deformation", "corrective", "capture"),
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
        "blender.growth.layers.correct",
        LayerCorrectArguments,
        LayerCache,
        lambda b, a, q: b.growth_layers_correct(a),
        (
            "Correct broad attached mesh templates with bounded deterministic "
            "root-pinned lift. Direction is in growth-object space; process "
            "layer/order/root sequence, check full triangle clearance against "
            "earlier elements and explicit mesh colliders, plus upper-side "
            "vertex/centroid probes for overlap intent. Coordinate conservative "
            "per-element lift across uniform fractional-time samples; preserve "
            "roots and publish offsets atomically only if "
            "all samples solve. Unresolved conflict returns valid=false without "
            "mutation. replace=true recomputes. Native output realizes corrected "
            "templates; prototypes remain shared. Authored Path output is "
            "unchanged. No physics or continuous collision guarantee: verify "
            "interpolated motion with adaptive geometry.inspect."
        ),
        tags=("layers", "collision", "cache", "deformation"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.layers.inspect",
        LayerObjectArguments,
        LayerCache,
        lambda b, a, q: b.growth_layers_inspect(a),
        (
            "Inspect broad-template correction cache range, settings, bounds, "
            "displacement, work, hash, stale state and unresolved conflicts. "
            "Outside its range authored growth is used. Saved offset cache persists "
            "in the native document; motion edits invalidate it."
        ),
        tags=("layers", "collision", "cache", "deformation"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.layers.clear",
        LayerObjectArguments,
        LayerCache,
        lambda b, a, q: b.growth_layers_clear(a),
        (
            "Remove owned broad-template correction offsets and restore authored "
            "native output. Preserve externally shared resources; clear before "
            "growth revision/removal."
        ),
        tags=("layers", "collision", "cache", "deformation"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.dynamics.bake",
        DynamicsBakeArguments,
        DynamicsJobStatus,
        lambda b, a, q: b.growth_dynamics_bake(a),
        "Start bounded native rod secondary dynamics on attached growth paths. "
        "Native Hair Dynamics XPBD is experimental. Explicit 2..128 frames, "
        "solver/point/time budgets; roots stay attached. Optional mesh/surface "
        "collision. Publishes durable positions atomically; status reports "
        "progress/result; cancel between native frame evaluations. Existing "
        "growth templates follow the simulated path. No self-collision or "
        "continuous-collision guarantee; inspect sampled growth/geometry QA. "
        "replace=true replaces an owned cache. ",
        tags=("dynamics", "collision", "cache", "secondary_motion"),
        effect="mutating",
        execution="job_start",
    ),
    _operation(
        "blender.growth.dynamics.inspect",
        DynamicsObjectArguments,
        DynamicsCache,
        lambda b, a, q: b.growth_dynamics_inspect(a),
        "Inspect owned secondary cache identity, settings, frame range, hash, "
        "root error, displacement and stale state. Native physics approximates "
        "rods; broad sheets need a suitable separate deformation model. Outside "
        "the frame range authored growth is used. ",
        tags=("dynamics", "collision", "cache", "secondary_motion"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.dynamics.status",
        DynamicsJobArguments,
        DynamicsJobStatus,
        lambda b, a, q: b.growth_dynamics_status(a),
        "Inspect bounded secondary-motion job progress and terminal result/error. "
        "Latest eight jobs persist until host/reload. Cancellation is cooperative "
        "between bounded native frames. ",
        tags=("dynamics", "collision", "cache", "secondary_motion"),
        effect="read_only",
        execution="job_status",
    ),
    _operation(
        "blender.growth.dynamics.cancel",
        DynamicsJobArguments,
        DynamicsJobStatus,
        lambda b, a, q: b.growth_dynamics_cancel(a),
        "Cancel a secondary-motion job between native frame evaluations, discard "
        "staging resources and restore the original frame. Retain the previous "
        "cache on failure/cancellation. ",
        tags=("dynamics", "collision", "cache", "secondary_motion"),
        effect="transient",
        execution="job_status",
    ),
    _operation(
        "blender.growth.dynamics.clear",
        DynamicsObjectArguments,
        DynamicsCache,
        lambda b, a, q: b.growth_dynamics_clear(a),
        "Remove an owned secondary-motion cache and restore authored surface- "
        "bound growth. Preserve shared or externally modified resources; inspect "
        "first. ",
        tags=("dynamics", "collision", "cache", "secondary_motion"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.create",
        GrowthCreateArguments,
        GrowthDelta,
        lambda b, a, q: b.growth_create(a),
        (
            "Create surface-rooted native Hair Curves guides, regions/families and "
            "dense grouped interpolation in one staged transaction. Use normal "
            "curve.* for named structural paths/profiles. Local Object Mode mesh with "
            "non-overlapping UVs required. Enables shared native surface rest- "
            "position generation; armatures/shape keys deform growth. Flow projects "
            "into root tangent plane; normalized family shape defines guides. "
            "Optional UV direction/length fields and named ordered root rows "
            "support spacing, mirrored orientation and layer/overlap intent. "
            "Rows require guides=children=0; roots stay inside the selected faces. "
            "children=0 outputs guides, otherwise exactly children curves per region. "
            "Static template local Z[0,1]: instances share rigid geometry; deform "
            "maps realized geometry along minimum-twist guide frames. Stable root IDs "
            "scoped by system_id; native rest/UV/family/region/pin attributes persist "
            "in .blend. One owned graph/carrier, no per-root objects. Limits: 10000 "
            "guides, 50000 output curves, 800000 points, 8 families, 16 regions, 350 "
            "nodes, 2000000 equivalent template vertices. No dynamics or arbitrary "
            "node/property escape hatch."
        ),
        tags=("growth", "attachment", "templates"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.configure",
        GrowthConfigureArguments,
        GrowthDelta,
        lambda b, a, q: b.growth_configure(a),
        (
            "Transactionally replace supplied family/region recipes or batch up to "
            "256 root-ID guide edits on the same owned system. Omitted recipes "
            "persist. Surviving roots retain IDs; manually edited shapes persist "
            "through style/density changes. Child output families follow region "
            "defaults; row name/side/sequence keeps identity through path/field "
            "edits and list reordering. Edited shapes follow revised roots. "
            "Changed seed/selection or stale base geometry/topology/UV "
            "requires explicit rebind=true, rebuilding rest guides with new IDs. "
            "Shared/user-modified resources and disabled/extra modifiers are guarded. "
            "Same creation budgets apply. Returns compact counts/delta; no full "
            "geometry."
        ),
        tags=("growth", "attachment", "templates"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.inspect",
        GrowthInspectArguments,
        GrowthInspectResult,
        lambda b, a, q: b.growth_inspect(a),
        (
            "Compact owned growth counts, budgets, attachment validity, warnings and "
            "sampled root/orientation/clearance QA. Default excludes points/recipe; "
            "opt-in guide pages up to32 with offset. qa_samples=0 skips spatial QA, "
            "include_rows reports rest spacing/order; field_samples queries up to32 "
            "rest-surface UV directions/length factors. "
            "retaining evaluated counts. Stale base geometry/topology/UV reports "
            "invalid_roots and explicit rebind recovery. Signed nearest-surface "
            "clearance is sampled diagnostic evidence, not exact collision proof; "
            "root_exclusion applies to guide segments. Optional template_samples "
            "samples shared templates lazily and includes template roots. "
            "Tangent-flow compares configured rest flow in world space; frame normals "
            "come from actual native evaluation."
        ),
        tags=("growth", "attachment", "qa"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.remove",
        GrowthRemoveArguments,
        GrowthRemoveResult,
        lambda b, a, q: b.growth_remove(a),
        (
            "Remove owned growth object, guides, root carrier and graph after "
            "checking external users. Preserve shared source mesh, templates, "
            "materials and enabled native source rest-position generation. Reject "
            "user-created Hair Curves, changed owned graph/attributes, shared data "
            "and ambiguous ownership. Invalidated source geometry may still be "
            "cleaned up."
        ),
        tags=("growth", "ownership"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.growth.sample",
        GrowthSampleArguments,
        GrowthSampleResult,
        lambda b, a, q: b.growth_sample(a),
        (
            "Evaluate up to16 named bone/shape-key poses or timeline frames with "
            "compact growth attachment/orientation/clearance QA. Restores native "
            "frame, transforms, keys and pose state even on failure. Requires "
            "armature_object for bone poses. Maximum4096 root samples per sweep; no "
            "guide/recipe dumps. Same sampled clearance limitations as "
            "growth.inspect; optional template vertex sampling uses shared "
            "prototypes and native paths. Does not simulate dynamics."
        ),
        tags=("growth", "attachment", "qa", "sampled"),
        effect="transient",
        execution="synchronous",
    ),
    _operation(
        "blender.control_rig.configure",
        ControlRigConfigureArguments,
        ControlRigResult,
        lambda b, a, q: b.control_rig_configure(a),
        (
            "Construct an owned two-segment FK/IK network over six existing "
            "matching-rest bones and independent target/pole controls. Two "
            "copy-transform branches drive deform bones; native two-bone IK "
            "drives the IK branch. Starts in FK. No anatomical preset or "
            "generated topology. "
        ),
        tags=("rigging", "ik_fk", "matching", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.control_rig.inspect",
        ControlRigInspectArguments,
        ControlRigResult,
        lambda b, a, q: b.control_rig_inspect(a),
        (
            "Inspect a named control network, definition, mode, constraint "
            "integrity and measured output/control error. "
        ),
        tags=("rigging", "ik_fk", "matching", "controls"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.control_rig.switch",
        ControlRigSwitchArguments,
        ControlRigResult,
        lambda b, a, q: b.control_rig_switch(a),
        (
            "Match and switch an owned two-segment FK/IK network with bounded "
            "native pole matching. Measure full world-matrix error; rollback "
            "when tolerance cannot be met. Optional keying extends the active "
            "owned action atomically with hold keys; unlocked destination "
            "controls must not be driven. Timeline inspection derives the active mode. "
        ),
        tags=("rigging", "ik_fk", "matching", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.control_rig.remove",
        ControlRigInspectArguments,
        ControlRigResult,
        lambda b, a, q: b.control_rig_remove(a),
        (
            "Remove a validated owned control network and its constraints; "
            "retain native bones and target/pole objects. Refuse externally "
            "modified or animated resources. "
        ),
        tags=("rigging", "ik_fk", "matching", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.constraint.configure",
        ConstraintsConfigureArguments,
        ConstraintsResult,
        lambda b, a, q: b.constraint_configure(a),
        (
            "Create or replace owned typed native copy "
            "transforms/rotation/location, translation limits, damped "
            "tracking, IK/poles, child-of spaces and floor contact constraints "
            "in bounded batches. Explicit endpoints/spaces; dependency cycle "
            "checks and rollback. Configure before owner animation. No "
            "property bags or expressions."
        ),
        tags=("recovery_replay", "rigging", "constraints", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.constraint.inspect",
        ConstraintsInspectArguments,
        ConstraintsResult,
        lambda b, a, q: b.constraint_inspect(a),
        (
            "Inspect up to 64 owned native constraints, integrity, current "
            "influence and evaluated world transforms. Optional typed "
            "definitions; external modifications remain visible failures."
        ),
        tags=("rigging", "constraints", "controls"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.constraint.remove",
        ConstraintsRemoveArguments,
        ConstraintsResult,
        lambda b, a, q: b.constraint_remove(a),
        (
            "Remove exact owned constraints after whole-batch validation; "
            "preserve external constraints and active animation."
        ),
        tags=("rigging", "constraints", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.match",
        PoseMatchArguments,
        PoseMatchResult,
        lambda b, a, q: b.pose_match(a),
        (
            "Match up to 64 unconstrained object or FK bone world transforms "
            "to evaluated source endpoints. Sources sampled before mutation; "
            "explicit tolerance and optional transactional keying into owned "
            "active actions. Preserve poses/actions on failure; driven destinations "
            "are protected. No automatic IK solution."
        ),
        tags=("rigging", "constraints", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.constraint.switch_space",
        SpaceSwitchArguments,
        ConstraintsResult,
        lambda b, a, q: b.space_switch(a),
        (
            "Switch an owned child-of constraint target to an explicit "
            "object/bone space, preserving evaluated transform by updating its "
            "inverse. With keying, retain fixed targets in up to 16 owned branches, "
            "key influences and compensate controls without changing earlier target "
            "identities. Validate world-pose error; "
            "restore action/constraints on failure."
        ),
        tags=("rigging", "constraints", "controls"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.surface.create",
        SurfaceCreateArguments,
        SurfaceResult,
        lambda b, a, q: b.surface_create(a),
        (
            "Author 1..4 source-independent connected shells from sparse named "
            "nodes/curves "
            "and four-boundary patches. Shared curves stitch branches/junctions; "
            "explicit UV "
            "openings retain support loops; local bulge/depression, ridge/groove "
            "and rim features "
            "plus varying thickness are tessellated application-side. No source "
            "mesh or vertex/face "
            "arrays. Closed orientable polygon topology, positional continuity, "
            "triangles or "
            "quad-dominant (not all-quad/CAD tangency). Reject self-"
            "intersection/impossible junctions "
            "atomically. Per object: 128 nodes, 96 curves, 32 patches, 32 openings, 64"
            " features,"
            "65536 generated vertices/131072 faces; batch 64 patches/131072 vertices. "
            "Stable handles and compact QA persist; revise with surface.configure."
        ),
        tags=(
            "modeling",
            "surface",
            "shell",
            "patch",
            "contour",
            "opening",
            "junction",
            "batched",
        ),
        execution="synchronous",
        effect="mutating",
    ),
    _operation(
        "blender.surface.configure",
        SurfaceConfigureArguments,
        SurfaceResult,
        lambda b, a, q: b.surface_configure(a),
        (
            "Atomically revise 1..4 owned surface networks by existing "
            "node/curve/patch/opening/feature "
            "IDs and expected_revision. Change contour positions, branch "
            "direction, opening size, "
            "feature strength or thickness without generated geometry payloads. "
            "Default preserves "
            "ordered connectivity/data; topology-changing tessellation requires "
            "explicit rebuild "
            "and pristine unbound mesh data. Object/semantic IDs persist; report "
            "topology_revision. "
            "External base edits block regeneration; use mesh tools for "
            "downstream refinement."
        ),
        tags=("modeling", "surface", "revision", "contour", "opening", "batched"),
        execution="synchronous",
        effect="mutating",
    ),
    _operation(
        "blender.surface.inspect",
        SurfaceNetworkInspectArguments,
        SurfaceResult,
        lambda b, a, q: b.surface_inspect(a),
        (
            "Inspect 1..8 managed surface identities/revisions, bounds, thickness,"
            " patch/opening/junction "
            "counts, topology and integrity of stored QA. Bounded named regions "
            "(default 12/max 64); "
            "optional constraints for one object. No generated coordinate arrays."
            " External edits "
            "mark QA invalid; inspect current detailed geometry with existing "
            "mesh/geometry tools."
        ),
        tags=("modeling", "surface", "topology", "inspection"),
        execution="synchronous",
        effect="read_only",
    ),
    _operation(
        "blender.form.create",
        FormCreateArguments,
        FormJobStatus,
        lambda b, a, q: b.form_create(a),
        "Build bounded editable irregular forms from calibrated section "
        "masks or orthographic silhouettes, existing typed loft/surface "
        "intent and named local features. Smoothly fuse branches, cut "
        "openings/recesses and blend interfaces using native volume "
        "meshing. Silhouettes do not recover hidden concavities: use "
        "section evidence and local refinement. Optional surface_fit aligns "
        "local shape to complementary planar contours within a movement bound. "
        "No explicit mesh payload; "
        "inspect closeups and compare references before acceptance. "
        "Regeneration changes connectivity; finish form before production "
        "topology. Returns an incremental atomic job; inspect form.status until "
        "completed before using geometry. Cancel leaves no partial batch.",
        tags=("modeling", "organic", "reference", "form", "blending"),
        execution="job_start",
        effect="mutating",
    ),
    _operation(
        "blender.form.configure",
        FormConfigureArguments,
        FormJobStatus,
        lambda b, a, q: b.form_configure(a),
        "Atomically replace/add/remove named constructive parts or change "
        "sampling/reference fitting while preserving object identities. "
        "Expected revision "
        "required. Regeneration changes connectivity and rejects "
        "downstream mesh edits, modifiers, groups, UVs, shape keys and "
        "shared data. Use local features to refine visible reference "
        "mismatches, then review and compare again. Returns an atomic incremental "
        "job; inspect form.status until completed. Cancellation preserves "
        "original forms.",
        tags=("modeling", "organic", "reference", "form", "blending"),
        execution="job_start",
        effect="mutating",
    ),
    _operation(
        "blender.form.status",
        FormJobArguments,
        FormJobStatus,
        lambda b, a, q: b.form_status(a),
        "Read incremental form job progress/result/error. Latest four jobs persist "
        "until reload. One job at a time; only status/cancel/extension inspection "
        "are available while preparing an atomic batch. Allow useful elapsed time "
        "between checks; no partial geometry is committed.",
        tags=("modeling", "form", "job", "inspection"),
        execution="job_status",
        effect="read_only",
    ),
    _operation(
        "blender.form.cancel",
        FormJobArguments,
        FormJobStatus,
        lambda b, a, q: b.form_cancel(a),
        "Cancel an active form job at its next native step and discard uncommitted "
        "meshes. Original forms remain intact; completed jobs cannot be rolled back "
        "by cancellation.",
        tags=("modeling", "form", "job"),
        execution="synchronous",
        effect="mutating",
    ),
    _operation(
        "blender.form.inspect",
        FormInspectArguments,
        FormResult,
        lambda b, a, q: b.form_inspect(a),
        "Inspect compact form identity/revision, bounds, part handles, "
        "sampling work and reference/mesh freshness. Optional full "
        "semantic specification; no generated coordinates. Mesh integrity "
        "does not establish visual reference fidelity.",
        tags=("modeling", "organic", "reference", "form", "blending"),
        execution="synchronous",
        effect="read_only",
    ),
    _operation(
        "blender.loft.create",
        LoftCreateArguments,
        LoftResult,
        lambda b, a, q: b.loft_create(a),
        (
            "Create up to64 editable section lofts with named sections, radial "
            "process/ridge/groove features and shaped convex/concave end caps. "
            "Transported "
            "frames, variable four-sided radii, twist, linear/Catmull-Rom "
            "centerlines and deterministic quad topology. Native meshes support "
            "downstream modifiers, binding and mesh refinement; use "
            "curve.create for ordinary native curve sweeps. At most 1024 "
            "sections/131072 vertices per batch; rollback on failure."
        ),
        tags=("structural", "organic", "sections", "batched"),
        execution="synchronous",
        effect="mutating",
    ),
    _operation(
        "blender.loft.configure",
        LoftConfigureArguments,
        LoftResult,
        lambda b, a, q: b.loft_configure(a),
        (
            "Patch named section positions/radii/twist, local features and end depths "
            "(or supply full sections) in one atomic "
            "batch while preserving objects, UUIDs, ordered topology, groups "
            "and modifiers. Keep section count, sides, subdivisions and caps "
            "unchanged, including end support rings. Optional expected_revision. "
            "Reject shared data, shape keys and externally edited "
            "base meshes; use mesh tools for downstream freeform refinement."
        ),
        tags=("structural", "organic", "sections", "batched"),
        execution="synchronous",
        effect="mutating",
    ),
    _operation(
        "blender.loft.inspect",
        LoftInspectArguments,
        LoftResult,
        lambda b, a, q: b.loft_inspect(a),
        (
            "Inspect compact owned loft identities, bounds, vertex/face/section "
            "counts and base-mesh integrity. Optional full section specs "
            "limited to 1024 sections; no generated vertex dump."
        ),
        tags=("structural", "organic", "sections", "batched"),
        execution="synchronous",
        effect="read_only",
    ),
    _operation(
        "blender.curve.create",
        CurveCreateArguments,
        CurveResult,
        lambda b, a, q: b.curve_create(a),
        (
            "Author swept structural geometry or procedural guides as 1..64 native "
            "Curve objects with shared "
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
        "Revise rest frames with staged rollback; preview reports bindings, "
        "actions and corrective targets. Default reject policy requires an "
        "unbound neutral rig. preserve policy accepts owned bindings/actions, "
        "retains bone identities/hierarchy/weights and requires current "
        "expected_rest_sha256 on commit. It marks captured targets stale and "
        "shape keys unacknowledged; revalidate motion afterward. No weight "
        "retargeting. Shared data, NLA and unverified external dependencies "
        "are rejected. Endpoints use typed points in armature/world space; "
        "x_reference constructs local axes. sample_limit=0 omits bone rows.",
        tags=("rigging", "rest_revision", "dependencies"),
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
        "translation/unit scale required for joints. Optional ik replaces "
        "native per-axis locks/limits/stiffness and stretch for IK solving. "
        "Atomic rollback; exclusive local "
        "armature with no animation/unowned constraints. May configure "
        "already bound structures without changing rest data.",
        tags=("rigging", "limits", "ik"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.armature.inspect_structure",
        StructureInspectArguments,
        StructureSummary,
        lambda b, a, q: b.armature_inspect_structure(a),
        "Inspect articulation data, not anatomical/physical geometry acceptance. "
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
        tags=("rigging", "structure", "limits"),
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
        "Author explicit physical components or structural approximations in "
        "1..64 coherent objects: cubes, planes, UV spheres, "
        "cylinders, plain empties or copies of plain local "
        "mesh/light/camera/empty objects. Request-local keys allow "
        "forward parent/copy references. Transforms are parent-local "
        "XYZ radians. Collections default to scene root, max 16/object. "
        "Explicit linked/independent data and shared/independent "
        "materials; independent materials require independent data, "
        "nested shader groups/images remain shared. Distinguish domain structure, "
        "control/proxy and production surface with role/tags. These are "
        "bounded persistent adapter-owned metadata; keys are not "
        "persistent. Reject cycles, collisions and "
        "animated/constrained/modifier/shape-key/domain-owned copy "
        "sources and nonidentity deltas. Max 250000 allocated mesh "
        "vertices. Stage all "
        "objects/data, publish together, rollback on failure; selection "
        "independent.",
        effect="mutating",
        execution="synchronous",
        tags=("recovery_replay",),
    ),
    _operation(
        "blender.object_set.configure",
        ObjectSetConfigureArguments,
        ObjectSetConfigureResult,
        lambda b, a, q: b.object_set_configure(a),
        "Atomically update 1..64 named objects: rename, exact "
        "collection memberships (link/unlink/move), plain parent "
        "changes with world transform preserved, visibility and role/tags. Omitted "
        "fields preserve values; null parent/role clears. Owned objects permit "
        "memberships, visibility and role/tags; rename/parent require their typed "
        "operations. Reject cycles, external-scene objects and unsupported or "
        "unrepresentable parenting (animation/constraints/bone "
        "parents/nonidentity deltas/shear on clear). Native "
        "relationships persist through "
        "rename/save/reopen. Return counts and changed names/fields only; "
        "inspect exact state lazily with object_set.inspect. Use "
        "project.bind for saved resource IDs. Generic transforms remain "
        "object.set_transform.",
        tags=("organization", "ownership", "visibility", "batched"),
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
        "world/local transforms, visibility, fixed metadata and data/material "
        "ownership. Pages default 32, max 128; nested lists cap 16 with "
        "counts/truncation. Native object names identify objects; keys "
        "from authoring are request-local. No mesh payload, history or "
        "arbitrary properties.",
        tags=("organization", "ownership", "visibility"),
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.object_set.remove",
        ObjectSetRemoveArguments,
        ObjectSetRemoveResult,
        lambda b, a, q: b.object_set_remove(a),
        "Delete 1..256 named local objects after dependency preflight; "
        "assembly roots expand to their owned groups/members (256 total). "
        "Standalone forms, lofts and surfaces are supported. Assembly members "
        "require their root; specialized cleanup domains retain their removal tools. "
        "default rejects surviving children, explicit unparent "
        "preserves representable world transforms. Reject other "
        "external dependencies. Retain "
        "mesh/material data, never purge orphans. Native deletion is "
        "irreversible: error and exact deleted/remaining names report "
        "partial progress. Selection independent.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.compare",
        ReferenceCompareArguments,
        ReferenceCompareResult,
        lambda b, a, q: b.reference_compare(a),
        "Compare evaluated meshes with registered foreground masks: "
        "orthographic silhouettes or planar cross-sections. Return compact "
        "overlap and bidirectional boundary deviations plus an optional "
        "overlay artifact. Sampling uncertainty is explicit; "
        "calibration/source error remains conditional. Perspective "
        "photographs are qualitative evidence, never metric proof. Closed "
        "surfaces required for section occupancy; actual selected "
        "dependencies and ray/triangle work remain bounded.",
        tags=("reference", "shape", "comparison", "quality", "silhouette"),
        execution="synchronous",
        effect="read_only",
        output_artifacts="optional",
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
        "blender.reference.register",
        RegistrationArguments,
        RegistrationResult,
        lambda b, a, q: b.reference_register(a),
        "Register 1..16 packed references as metric planes or orthographic "
        "projections: source-pixel scale/origin and signed axes in a rigid "
        "world/object frame. Aligns native reference empties and persists "
        "calibration/provenance. Perspective requires camera calibration "
        "and is rejected. Source assumptions must be justified; display "
        "mode does not establish projection. Updates stale dependent "
        "landmarks.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.registration.inspect",
        ReferenceInspectArguments,
        RegistrationResult,
        lambda b, a, q: b.reference_registration_inspect(a),
        "Inspect registered reference calibration, frame, source identity, "
        "revision and current/stale basis. Names/pagination follow "
        "reference inspection. A stale registration must be explicitly "
        "re-established.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.observation.set",
        ObservationSetArguments,
        ObservationWriteResult,
        lambda b, a, q: b.reference_observation_set(a),
        "Persist/redefine 1..128 named source-pixel observations with "
        "reference identity, packed-source hash, dimensions, revision and "
        "optional pixel uncertainty. Pixels remain in source coordinates "
        "after plane movement. 2048 observations/2 MiB per scene; batch "
        "returns IDs only. No 3D inference; occluded observations do not "
        "constrain a solve.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.observation.inspect",
        ObservationInspectArguments,
        ObservationResult,
        lambda b, a, q: b.reference_observation_inspect(a),
        "Inspect bounded source observations by IDs in names, reference or "
        "page. Optional mapped_points includes reference-local and world "
        "display-plane points, not inferred 3D positions. Source changes "
        "report stale; reference transforms do not change stored pixels.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.reference.observation.remove",
        NamedRemoveArguments,
        NamedRemoveResult,
        lambda b, a, q: b.reference_observation_remove(a),
        "Remove 1..32 source observation IDs atomically. Existing derived "
        "landmarks become stale and cannot be consumed as valid measurement"
        " points until explicitly rederived.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.landmark.derive",
        LandmarkDeriveArguments,
        ConstructionReport,
        lambda b, a, q: b.landmark_derive(a),
        "Derive 1..32 existing-system landmarks from persisted observations"
        " on registered planes/orthographic views or reflection of an "
        "existing current landmark. Optional known-axis planes constrain "
        "missing components in a rigid construction frame. Application "
        "computes XYZ. Rank-deficient, stale or inconsistent inputs return "
        "explicit per-point statuses; no guessed geometry. Only solved "
        "points are written; failed re-solves invalidate old derived "
        "outputs. Malformed requests roll back the batch. Saves provenance;"
        " compact counts/residuals/conditional uncertainty and worst-N "
        "(default8). Inspect details with landmark.inspect. No perspective "
        "triangulation or automatic correspondence.",
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
        "attachments. Literal redefinition clears derivation provenance; "
        "use landmark.derive for registered source observations. ",
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
        "world-point fallback. Derived landmarks report stale input bases; "
        "detail=summary omits coordinates, detail=provenance returns at "
        "most8 records with explicit truncation. Construction statistics "
        "cover the selected page. ",
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
        "Create an articulation/deformation armature, not anatomical bone or "
        "physical component geometry. Bounded rest-bone hierarchy with explicit "
        "head/tail/roll, parent and connected endpoints. Rejects duplicate names, "
        "cycles and "
        "inconsistent connected joints. Up to 128 bones. Raw endpoints use "
        "armature/world request space; shared typed point sources resolve to "
        "world then armature space. Head is the joint center. x_reference "
        "constructs an orthonormal native frame: Y=head-tail, projected X, "
        "Z=X cross Y; alternatively roll is radians about Y. Optional typed "
        "limits create owned LOCAL XYZ rotation constraints. sample_limit=0 "
        "returns only counts/hashes; construction rolls back on failure.",
        effect="mutating",
        execution="synchronous",
        tags=("recovery_replay",),
    ),
    _operation(
        "blender.armature.inspect",
        ArmatureInspectArguments,
        ArmatureSummary,
        lambda b, a, q: b.armature_inspect(a),
        "Inspect filtered rest/posed bones and bounded mesh-binding "
        "summaries. Bone coordinates are world-space; pose channels remain "
        "local to the rest hierarchy.",
        tags=("rigging", "bindings", "rest_revision"),
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
        "with bake.status. Requires the visible application event loop; "
        "background hosts support bake.inspect but cannot start this job.",
        effect="mutating",
        execution="job_start",
        requires_interactive=True,
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
        "Optional volumes (16) and layers (8 pairs) evaluate current geometry "
        "at each pose using volume.inspect/layer.inspect definitions. Objects "
        "may be empty for diagnostic-only sweeps. Up to 128 volume/layer pose "
        "summaries and 256 layer worst details; shared evaluated surfaces count "
        "toward the 1M vertex/pose budget. Layer selectors are current-pose, "
        "while named references retain captured vertex indices. "
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
        "mode. Requires an unanimated rig; use motion.sample for active "
        "actions/drivers and an explicit evaluated reference frame.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.document.restore",
        RestoreArguments,
        RestoreJobStatus,
        lambda b, a, q: b.document_restore(a, q),
        "Core-only trusted artifact restore. Uses explicit discard authorization, "
        "exact current identity/content, target file hash and strong post-load proof. "
        "Use project.restore; ordinary file.open retains normal divergence guards.",
        effect="mutating",
        execution="job_start",
        tags=("document_restore",),
    ),
    _operation(
        "blender.document.restore_status",
        AttestationJobArguments,
        RestoreJobStatus,
        lambda b, a, q: b.document_restore_status(a),
        "Observe trusted artifact restore and its content-qualified terminal receipt.",
        effect="read_only",
        execution="job_status",
        tags=("document_restore_status",),
    ),
    _operation(
        "blender.document.mutate",
        MutationArguments,
        MutationJobStatus,
        lambda b, a, q: b.document_mutate(a, q),
        "Core-orchestrated guarded execution of one advertised typed mutation. "
        "Matches the authorized working head and returns a strong before/after "
        "receipt. "
        "Use the original authoring operation; Core supplies this internal contract.",
        effect="mutating",
        execution="job_start",
        tags=("document_mutation",),
    ),
    _operation(
        "blender.document.mutation_status",
        AttestationJobArguments,
        MutationJobStatus,
        lambda b, a, q: b.document_mutation_status(a),
        "Observe a guarded mutation and its terminal receipt, with bounded backoff.",
        effect="read_only",
        execution="job_status",
        tags=("document_mutation_status",),
    ),
    _operation(
        "blender.document.attest",
        ExtensionInspectArguments,
        DocumentAttestationResult,
        lambda b, a, q: b.document_attest(),
        (
            "Hash bounded material document content inside Blender; "
            "stable process/document sessions. "
            "Incomplete coverage returns no digest. No project mutation "
            "or serialized geometry. Returns a native job immediately. Inspect status "
            "after poll_after_seconds (0.5s), backing off to 2s; completed status "
            "contains final evidence. Four records persist until reload. "
            "Mutations are blocked during hashing; detected external edits fail closed."
        ),
        effect="read_only",
        execution="job_start",
        tags=("document_attestation",),
    ),
    _operation(
        "blender.document.attest_status",
        AttestationJobArguments,
        AttestationJobStatus,
        lambda b, a, q: b.document_attest_status(a),
        "Observe compact attestation progress and final evidence. "
        "Poll after 0.5s, backing off to 2s. No arrays are returned.",
        effect="read_only",
        execution="job_status",
        tags=("document_attestation_status",),
    ),
    _operation(
        "blender.document.attest_cancel",
        AttestationJobArguments,
        AttestationJobStatus,
        lambda b, a, q: b.document_attest_cancel(a),
        "Cancel incremental attestation and release native work/guards "
        "without changing document content.",
        effect="transient",
        execution="job_status",
        tags=("document_attestation_cancel",),
    ),
    _operation(
        "blender.extension.inspect",
        ExtensionInspectArguments,
        ExtensionState,
        lambda b, a, q: b.extension_inspect(),
        "Inspect adapter/build identity, host PID, background flag, window count, "
        "application version, project path/UUID, connection and lifecycle counts. "
        "Interactive means background=false with windows; it does not prove "
        "physical monitor visibility. Read before selecting a real-work target.",
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
        "blender.file.audit",
        FileAuditArguments,
        FileAuditResult,
        lambda b, a, q: b.file_audit(a),
        "Audit saved document identity, packed/external images, linked paths, "
        "actions and cache persistence. Verify explicitly required files and optional "
        "checksums with bounded work. Return totals and worst-N dependencies; "
        "unknown sequence/simulation coverage remains unverified. Presence is not "
        "visual or motion acceptance. Read-only; never packs, saves or rewrites paths.",
        tags=("file", "delivery", "dependencies", "verification"),
        effect="read_only",
        execution="synchronous",
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
        "blender.project.bind",
        ProjectBindArguments,
        ProjectBindResult,
        lambda b, a, q: b.project_bind(a),
        "Establish saved document UUID and stable IDs for up to64 named local "
        "objects/materials/collections. Explicit identity metadata mutation; "
        "save the file to persist. Names are used only for initial selection. "
        "Existing identities are reused; duplicate IDs fail instead of silently "
        "rebinding. renew_resource_ids explicitly renews selected IDs to repair "
        "duplicates; prior bindings need reconciliation. fork_project=true "
        "assigns a new document UUID for a deliberate "
        "independent copy; ordinary save-as preserves lineage. Wait for adapter "
        "registration to report project_id before semantic attachment. Core owns "
        "project meaning. No geometry changes.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.resource.inspect",
        ResourceInspectionRequest,
        ResourceInspectionResult,
        lambda b, a, q: b.resource_inspect(a),
        "Resolve up to64 saved resource IDs in the expected document UUID. "
        "Reports present/missing/ambiguous/unsupported and current names, without "
        "assigning IDs or changing Blender. A different file identity is rejected. "
        "Structural fingerprints have an explicit bounded scope, not complete "
        "geometry/material/animation validation. Reopening preserves IDs; copied "
        "resources with duplicate custom IDs remain ambiguous.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.file.new",
        FileNewArguments,
        FileState,
        lambda b, a, q: b.file_new(a),
        "Discard the current document and load an empty unsaved factory project. "
        "Requires discard_current=true; preserves host process, preferences and "
        "UI layout. Clears document data/path/identity, without saving. "
        "Wait for registration to report null project_path/project_id afterward.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.viewport.configure",
        ViewportConfigureArguments,
        ViewportState,
        lambda b, a, q: b.viewport_configure(a),
        "Set an explicit interactive non-quad viewport to canonical or custom world "
        "direction/up, target, distance and orthographic/perspective projection. "
        "Coordinates describe a view, not a scene camera. Returns actual quaternion "
        "and view state. Use viewport.frame to fit objects.",
        tags=("orientation", "checkpoint"),
        effect="mutating",
        execution="synchronous",
        requires_interactive=True,
    ),
    _operation(
        "blender.viewport.capture",
        ViewportCaptureArguments,
        ViewportCaptureResult,
        lambda b, a, q: b.viewport_capture(a),
        "Capture the actual specified interactive 3D editor framebuffer as a PNG "
        "artifact. Includes viewport overlays; excludes editor headers. No background "
        "render. Optional "
        "1..6 canonical/custom views form one contact sheet (row-major, top-left "
        "metadata coordinates). Native redraw precedes capture; view state restores "
        "on success/failure. Reports each view, dimensions and UTC time. Explicit "
        "pixel budget up to 32M; artifact transport, no binary JSON.",
        tags=("screenshot", "multiview", "checkpoint", "artifact"),
        effect="transient",
        execution="synchronous",
        requires_interactive=True,
        output_artifacts="required",
    ),
    _operation(
        "blender.viewport.inspect",
        ViewportInspectArguments,
        ViewportInspection,
        lambda b, a, q: b.viewport_inspect(a),
        "Inspect up to32 interactive 3D viewports with session-local IDs, "
        "scene/layer, selection (first64 plus count), active object and framing. "
        "Background hosts return no viewports. IDs expire on UI/file changes; "
        "rediscover after reload. Window presence does not prove monitor visibility.",
        effect="read_only",
        execution="synchronous",
    ),
    _operation(
        "blender.viewport.frame",
        ViewportFrameArguments,
        ViewportState,
        lambda b, a, q: b.viewport_frame(a),
        "Select 1..64 named objects (first active) and frame them in the explicit "
        "interactive viewport for a visible checkpoint. Requires Object Mode, "
        "visible/selectable objects in that view layer, non-camera/non-quad view. "
        "Does not unhide objects, change geometry, or raise an OS window. Selection "
        "is shared by views of the same layer; returns resulting framing/state.",
        effect="mutating",
        execution="synchronous",
        requires_interactive=True,
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
        tags=("document_open",),
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
        tags=("document_save",),
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
        "Create and pack unchanged bytes from one attached PNG/8-bit DCT JPEG "
        "(baseline/progressive, RGB/grayscale/CMYK). Limits: 64 MiB encoded, "
        "16384 pixels per side, 33554432 total pixels, checked before native "
        "decode. No resize/re-encoding; EXIF orientation is not applied. "
        "Failures identify format, truncation, byte/pixel limits, native decode "
        "or packing stage; no image remains on failure. Returns metadata only. "
        "Artifact IDs reference transferred bytes, never shared filesystem paths.",
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
        "blender.image.preview",
        ImagePreviewArguments,
        ImagePreviewResult,
        lambda b, a, q: b.image_preview(a),
        "Visually inspect up to16 loaded reference images in one bounded artifact. "
        "Preserves source color interpretation, files, packing and calibration. "
        "sRGB, linear Rec.709 and data images are supported; "
        "row/column metadata identifies tiles.",
        effect="read_only",
        execution="synchronous",
        output_artifacts="required",
        tags=("reference", "image", "visual", "preview"),
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
        "Write and optionally pack a non-color data image, then return a bounded "
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
        "blender.material.author",
        MaterialAuthorArguments,
        MaterialSummary,
        lambda b, a, q: b.material_author(a),
        "Author or patch an owned Principled material with coherent textures, "
        "coordinates, variation, advanced shading and optional assignments. Shared "
        "updates require explicit intent. Staged rollback.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.shader.author",
        GraphAuthorArguments,
        MaterialSummary,
        lambda b, a, q: b.shader_author(a),
        "Create, patch or explicitly replace a bounded typed shader graph. Up to 64 "
        "nodes, 128 links and 16 images; no groups or scripting. A replacement "
        "requires the current fingerprint. Staged rollback.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.material.copy",
        MaterialCopyArguments,
        MaterialSummary,
        lambda b, a, q: b.material_copy(a),
        "Copy a local material with an independent node tree and shared images; no "
        "inheritance. Assign the copy explicitly.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.material.remove",
        MaterialRemoveArguments,
        MaterialRemoveResult,
        lambda b, a, q: b.material_remove(a),
        "Remove an unused local material, preserving referenced images and node "
        "groups. Refuse materials with users.",
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.material.assign_batch",
        AssignBatchArguments,
        AssignBatchResult,
        lambda b, a, q: b.material_assign_batch(a),
        "Assign a shared material to up to 64 object slots atomically, preserving "
        "unrelated slots and face indices. Extending shared geometry isolates its "
        "slots.",
        effect="mutating",
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
        "Author explicit structural/component or surface geometry as one bounded "
        "original triangle/quad mesh with optional corner "
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
        tags=("recovery_replay",),
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
        "blender.modifier.create_batch",
        ModifierBatchCreateArguments,
        ModifierBatchCreateResult,
        lambda b, a, q: b.modifier_create_batch(a),
        "Atomically add one typed modifier to each of up to64 mesh objects. "
        "Reuse modifier.create settings for smoothing/polish, subdivision, "
        "solidify and other supported native modifiers. Each object retains "
        "its own settings; any failure removes every modifier added by this "
        "batch. Prefer this for a shared finishing intent across components.",
        tags=("modifier", "smooth", "polish", "batch", "modeling"),
        effect="mutating",
        execution="synchronous",
    ),
    _operation(
        "blender.modifier.create",
        CREATE,
        ModifierSummary,
        lambda b, a, q: b.modifier_create(a),
        "Corrective Smooth uses original coordinates before constructive modifiers; "
        "only_smooth instead fairs the evaluated surface without restoring detail. "
        "bounded iterations/factor/scale and optional native group mask; "
        "smoothing may lose volume and is not an authored corrective. "
        "Create a supported typed object-owned modifier. Type-specific "
        "properties belong in settings; stack order is significant."
        " For multiple objects use modifier.create_batch.",
        tags=("modifier", "modeling"),
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
        tags=("recovery_replay",),
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
        tags=("recovery_replay",),
    ),
    _operation(
        "blender.object.set_transform",
        TransformArguments,
        ObjectSummary,
        lambda b, a, q: b.transform(a),
        "Patch object-local location/rotation/scale; omitted channels stay "
        "unchanged. This does not edit authored mesh coordinates."
        " For measured dimensions/alignment across objects, use object_set.place.",
        effect="mutating",
        execution="synchronous",
        tags=("recovery_replay",),
    ),
    _operation(
        "blender.render.devices",
        RenderDevicesArguments,
        RenderDevicesResult,
        lambda b, a, q: b.render_devices(),
        "Inspect Cycles' selected compute backend, its available devices and "
        "saved enable flags, supported backend kinds and current scene device. "
        "Does not refresh/change preferences or render. Unknown enable flags "
        "are null; scene_uses_gpu also requires Cycles and scene GPU selection. "
        "Device page caps at 128 with full count. Availability is not a render test.",
        effect="read_only",
        execution="synchronous",
    ),
    *_RENDER_DECLARATIONS,
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
        "Apply a bounded sculpt filter. Native modes require interactive "
        "View3D and may leave partial changes on failure. Fair mode works "
        "in Object Mode without View3D: atomic local base-mesh fairing, "
        "explicit displacement/work bounds, native masks, pinned boundaries "
        "and sharp edges, thickness/contact checks. Apply existing modifiers "
        "explicitly first. Scope with fairing.regions, a named vertex_group "
        "or an existing local sculpt mask; protect_vertex_group pins features.",
        effect="mutating",
        execution="synchronous",
        requires_interactive=False,
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
        "Apply bounded native brush dabs at object-local surface locations or "
        "an image_path using an inspection tile's captured view projection "
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
        "distortion across named targets. Global metrics and optional layout "
        "image cover every island; island detail defaults to 16, max 64 per "
        "page using island_offset/island_limit and next_island_offset.",
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


def registration(
    instance_id: str,
    version: str,
    filepath: str,
    project_id: str | None = None,
    runtime: AdapterRuntime | None = None,
) -> AdapterRegistration:
    return AdapterRegistration(
        type="adapter.register",
        instance_id=instance_id,
        application="blender",
        runtime=runtime,
        application_version=version,
        project_path=filepath or None,
        project_id=project_id,
        resource_inspection="blender.resource.inspect",
        operations=tuple(REGISTRY[name].contract for name in OPERATIONS),
    )


def execute(backend: SceneBackend, request: OperationRequest) -> Response:
    return dispatch(backend, request, REGISTRY)
