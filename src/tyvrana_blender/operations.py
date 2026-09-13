"""Validated operation dispatch independent of Blender's Python module."""

import logging
from typing import Protocol

from pydantic import ValidationError
from tyvrana_protocol import (
    AdapterRegistration,
    ArtifactDescriptor,
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
)

from .camera_models import (
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraSetActiveArguments,
    CameraSummary,
)
from .image_models import (
    ImageConfigureArguments,
    ImageCreateArguments,
    ImageFromArtifactArguments,
    ImageInspectResult,
    ImageSummary,
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
    SceneSummary,
    TransformArguments,
)
from .modifier_models import (
    CONFIGURE,
    CREATE,
    ConfigureBase,
    CreateBase,
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
from .remesh_models import (
    VoxelRemeshArguments,
    VoxelRemeshInspectArguments,
    VoxelRemeshResult,
    VoxelRemeshSummary,
)
from .retopo_models import (
    RetopoBridgeArguments,
    RetopoCreateArguments,
    RetopoCreateResult,
    RetopoEditArguments,
    RetopoEditResult,
    RetopoExtrudeArguments,
    RetopoInspectArguments,
    RetopoProjectArguments,
    RetopoRelaxArguments,
    RetopoSeedArguments,
    RetopoSummary,
)
from .sculpt_models import (
    RAYCAST,
    CameraRayArguments,
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
    WorldRayArguments,
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
from .uv_models import (
    UVCreateArguments,
    UVInspectArguments,
    UVInspectResult,
    UVPackArguments,
    UVSetActiveArguments,
    UVUnwrapArguments,
)

logger = logging.getLogger(__name__)
OPERATIONS = (
    "blender.camera.configure",
    "blender.camera.create",
    "blender.camera.inspect",
    "blender.camera.set_active",
    "blender.image.configure",
    "blender.image.create_from_artifact",
    "blender.image.create_generated",
    "blender.image.inspect",
    "blender.light.configure",
    "blender.light.create",
    "blender.light.inspect",
    "blender.material.assign",
    "blender.material.configure_principled",
    "blender.material.create_principled",
    "blender.material.inspect",
    "blender.mesh.bevel_edges",
    "blender.mesh.delete_elements",
    "blender.mesh.extrude_faces",
    "blender.mesh.inset_faces",
    "blender.mesh.inspect",
    "blender.mesh.inspect_evaluated",
    "blender.mesh.mark_seam",
    "blender.mesh.merge_vertices",
    "blender.mesh.query",
    "blender.mesh.recalculate_normals",
    "blender.mesh.subdivide_edges",
    "blender.mesh.transform",
    "blender.modifier.apply",
    "blender.modifier.configure",
    "blender.modifier.create",
    "blender.modifier.inspect",
    "blender.modifier.move",
    "blender.modifier.remove",
    "blender.multires.configure",
    "blender.multires.create",
    "blender.multires.inspect",
    "blender.multires.subdivide",
    "blender.object.create_primitive",
    "blender.object.delete",
    "blender.object.set_transform",
    "blender.render.image",
    "blender.retopo.bridge_loops",
    "blender.retopo.create_target",
    "blender.retopo.extrude_boundary",
    "blender.retopo.inspect",
    "blender.retopo.project",
    "blender.retopo.relax",
    "blender.retopo.seed_patch",
    "blender.scene.inspect",
    "blender.scene.raycast",
    "blender.sculpt.face_sets.assign",
    "blender.sculpt.face_sets.initialize",
    "blender.sculpt.face_sets.inspect",
    "blender.sculpt.filter",
    "blender.sculpt.inspect",
    "blender.sculpt.mask.clear",
    "blender.sculpt.mask.inspect",
    "blender.sculpt.mask.invert",
    "blender.sculpt.mask.stroke",
    "blender.sculpt.stroke",
    "blender.sculpt.voxel_remesh",
    "blender.sculpt.voxel_remesh.inspect",
    "blender.shader.connect",
    "blender.shader.disconnect",
    "blender.shader.inspect",
    "blender.shader.node.configure",
    "blender.shader.node.create",
    "blender.shader.node.delete",
    "blender.uv.create_map",
    "blender.uv.inspect",
    "blender.uv.pack_islands",
    "blender.uv.set_active",
    "blender.uv.unwrap",
)
type Response = OperationSuccess | OperationFailure


def registration(instance_id: str, version: str, filepath: str) -> AdapterRegistration:
    return AdapterRegistration(
        type="adapter.register",
        instance_id=instance_id,
        application="blender",
        application_version=version,
        project_path=filepath or None,
        operations=OPERATIONS,
    )


class OperationError(Exception):
    def __init__(self, code: str, message: str, details: JsonValue = None) -> None:
        super().__init__(message)
        self.error = ProtocolError(code=code, message=message, details=details)


class SceneBackend(Protocol):
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
    def mesh_query(self, arguments: MeshQueryArguments) -> MeshQueryResult: ...
    def mesh_edit(
        self, arguments: MeshSelectionArguments | MeshNormalsArguments
    ) -> MeshEditResult: ...
    def uv_inspect(self, arguments: UVInspectArguments) -> UVInspectResult: ...
    def uv_create(self, arguments: UVCreateArguments) -> UVInspectResult: ...
    def uv_set_active(self, arguments: UVSetActiveArguments) -> UVInspectResult: ...
    def uv_unwrap(self, arguments: UVUnwrapArguments) -> UVInspectResult: ...
    def uv_pack(self, arguments: UVPackArguments) -> UVInspectResult: ...
    def image_inspect(self) -> ImageInspectResult: ...
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

    def material_inspect(self) -> MaterialInspectResult: ...
    def material_create(
        self, arguments: MaterialCreateArguments
    ) -> MaterialSummary: ...
    def material_configure(
        self, arguments: MaterialConfigureArguments
    ) -> MaterialSummary: ...
    def material_assign(
        self, arguments: MaterialAssignArguments
    ) -> MaterialAssignResult: ...
    def light_inspect(self) -> LightInspectResult: ...
    def light_create(self, arguments: LightCreateArguments) -> LightSummary: ...
    def light_configure(self, arguments: LightConfigureArguments) -> LightSummary: ...
    def camera_inspect(self) -> CameraInspectResult: ...
    def camera_create(self, arguments: CameraCreateArguments) -> CameraSummary: ...
    def camera_configure(
        self, arguments: CameraConfigureArguments
    ) -> CameraSummary: ...
    def camera_set_active(
        self, arguments: CameraSetActiveArguments
    ) -> CameraSummary: ...
    def inspect(self) -> SceneSummary: ...
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


def execute(backend: SceneBackend, request: OperationRequest) -> Response:
    try:
        arguments: (
            RaycastArguments
            | MultiresInspectArguments
            | MultiresCreateArguments
            | MultiresSubdivideArguments
            | MultiresConfigureArguments
            | RetopoCreateArguments
            | RetopoInspectArguments
            | RetopoEditArguments
            | VoxelRemeshArguments
            | VoxelRemeshInspectArguments
            | MaskInspectArguments
            | MaskClearArguments
            | MaskInvertArguments
            | MaskStrokeArguments
            | FaceSetsInspectArguments
            | FaceSetsAssignArguments
            | FaceSetsInitializeArguments
            | SculptFilterArguments
            | SculptInspectArguments
            | SculptStrokeArguments
            | InspectArguments
            | CreateArguments
            | TransformArguments
            | DeleteArguments
            | RenderArguments
            | CameraCreateArguments
            | CameraConfigureArguments
            | CameraSetActiveArguments
            | LightCreateArguments
            | LightConfigureArguments
            | MaterialCreateArguments
            | MaterialConfigureArguments
            | MaterialAssignArguments
            | ImageCreateArguments
            | ImageFromArtifactArguments
            | ImageConfigureArguments
            | ShaderInspectArguments
            | NodeCreateArguments
            | NodeConfigureArguments
            | NodeDeleteArguments
            | ConnectArguments
            | DisconnectArguments
            | UVInspectArguments
            | UVCreateArguments
            | UVSetActiveArguments
            | UVUnwrapArguments
            | UVPackArguments
            | ModifierCreateArguments
            | ModifierConfigureArguments
            | ModifierMoveArguments
            | ModifierRemoveArguments
            | ModifierApplyArguments
            | ModifierInspectArguments
            | EvaluatedMeshArguments
            | MeshInspectArguments
        )
        match request.operation:
            case "blender.retopo.bridge_loops":
                arguments = RetopoBridgeArguments.model_validate(request.arguments)
            case "blender.retopo.create_target":
                arguments = RetopoCreateArguments.model_validate(request.arguments)
            case "blender.retopo.extrude_boundary":
                arguments = RetopoExtrudeArguments.model_validate(request.arguments)
            case "blender.retopo.inspect":
                arguments = RetopoInspectArguments.model_validate(request.arguments)
            case "blender.retopo.project":
                arguments = RetopoProjectArguments.model_validate(request.arguments)
            case "blender.retopo.relax":
                arguments = RetopoRelaxArguments.model_validate(request.arguments)
            case "blender.retopo.seed_patch":
                arguments = RetopoSeedArguments.model_validate(request.arguments)
            case "blender.scene.raycast":
                arguments = RAYCAST.validate_python(request.arguments)
            case "blender.multires.inspect":
                arguments = MultiresInspectArguments.model_validate(request.arguments)
            case "blender.multires.create":
                arguments = MultiresCreateArguments.model_validate(request.arguments)
            case "blender.multires.subdivide":
                arguments = MultiresSubdivideArguments.model_validate(request.arguments)
            case "blender.multires.configure":
                arguments = MultiresConfigureArguments.model_validate(request.arguments)
            case "blender.sculpt.voxel_remesh.inspect":
                arguments = VoxelRemeshInspectArguments.model_validate(
                    request.arguments
                )
            case "blender.sculpt.voxel_remesh":
                arguments = VoxelRemeshArguments.model_validate(request.arguments)
            case "blender.sculpt.mask.inspect":
                arguments = MaskInspectArguments.model_validate(request.arguments)
            case "blender.sculpt.mask.clear":
                arguments = MaskClearArguments.model_validate(request.arguments)
            case "blender.sculpt.mask.invert":
                arguments = MaskInvertArguments.model_validate(request.arguments)
            case "blender.sculpt.mask.stroke":
                arguments = MaskStrokeArguments.model_validate(request.arguments)
            case "blender.sculpt.face_sets.inspect":
                arguments = FaceSetsInspectArguments.model_validate(request.arguments)
            case "blender.sculpt.face_sets.assign":
                arguments = FaceSetsAssignArguments.model_validate(request.arguments)
            case "blender.sculpt.face_sets.initialize":
                arguments = FaceSetsInitializeArguments.model_validate(
                    request.arguments
                )
            case "blender.sculpt.filter":
                arguments = SculptFilterArguments.model_validate(request.arguments)
            case "blender.sculpt.inspect":
                arguments = SculptInspectArguments.model_validate(request.arguments)
            case "blender.sculpt.stroke":
                arguments = SculptStrokeArguments.model_validate(request.arguments)
            case "blender.modifier.create":
                arguments = CREATE.validate_python(request.arguments)
            case "blender.modifier.configure":
                arguments = CONFIGURE.validate_python(request.arguments)
            case "blender.modifier.inspect":
                arguments = ModifierInspectArguments.model_validate(request.arguments)
            case "blender.modifier.move":
                arguments = ModifierMoveArguments.model_validate(request.arguments)
            case "blender.modifier.remove":
                arguments = ModifierRemoveArguments.model_validate(request.arguments)
            case "blender.modifier.apply":
                arguments = ModifierApplyArguments.model_validate(request.arguments)
            case "blender.mesh.inspect_evaluated":
                arguments = EvaluatedMeshArguments.model_validate(request.arguments)
            case "blender.mesh.inspect":
                arguments = MeshInspectArguments.model_validate(request.arguments)
            case "blender.mesh.query":
                arguments = MeshQueryArguments.model_validate(request.arguments)
            case "blender.mesh.transform":
                arguments = MeshTransformArguments.model_validate(request.arguments)
            case "blender.mesh.extrude_faces":
                arguments = MeshExtrudeArguments.model_validate(request.arguments)
            case "blender.mesh.inset_faces":
                arguments = MeshInsetArguments.model_validate(request.arguments)
            case "blender.mesh.bevel_edges":
                arguments = MeshBevelArguments.model_validate(request.arguments)
            case "blender.mesh.subdivide_edges":
                arguments = MeshSubdivideArguments.model_validate(request.arguments)
            case "blender.mesh.delete_elements":
                arguments = MeshDeleteArguments.model_validate(request.arguments)
            case "blender.mesh.merge_vertices":
                arguments = MeshMergeArguments.model_validate(request.arguments)
            case "blender.mesh.mark_seam":
                arguments = MeshSeamArguments.model_validate(request.arguments)
            case "blender.mesh.recalculate_normals":
                arguments = MeshNormalsArguments.model_validate(request.arguments)
            case "blender.uv.inspect":
                arguments = UVInspectArguments.model_validate(request.arguments)
            case "blender.uv.create_map":
                arguments = UVCreateArguments.model_validate(request.arguments)
            case "blender.uv.set_active":
                arguments = UVSetActiveArguments.model_validate(request.arguments)
            case "blender.uv.unwrap":
                arguments = UVUnwrapArguments.model_validate(request.arguments)
            case "blender.uv.pack_islands":
                arguments = UVPackArguments.model_validate(request.arguments)
            case "blender.image.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.image.create_generated":
                arguments = ImageCreateArguments.model_validate(request.arguments)
            case "blender.image.create_from_artifact":
                arguments = ImageFromArtifactArguments.model_validate(request.arguments)
            case "blender.image.configure":
                arguments = ImageConfigureArguments.model_validate(request.arguments)
            case "blender.shader.inspect":
                arguments = ShaderInspectArguments.model_validate(request.arguments)
            case "blender.shader.node.create":
                arguments = NodeCreateArguments.model_validate(request.arguments)
            case "blender.shader.node.configure":
                arguments = NodeConfigureArguments.model_validate(request.arguments)
            case "blender.shader.node.delete":
                arguments = NodeDeleteArguments.model_validate(request.arguments)
            case "blender.shader.connect":
                arguments = ConnectArguments.model_validate(request.arguments)
            case "blender.shader.disconnect":
                arguments = DisconnectArguments.model_validate(request.arguments)

            case "blender.material.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.material.create_principled":
                arguments = MaterialCreateArguments.model_validate(request.arguments)
            case "blender.material.configure_principled":
                arguments = MaterialConfigureArguments.model_validate(request.arguments)
            case "blender.material.assign":
                arguments = MaterialAssignArguments.model_validate(request.arguments)
            case "blender.light.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.light.create":
                arguments = LightCreateArguments.model_validate(request.arguments)
            case "blender.light.configure":
                arguments = LightConfigureArguments.model_validate(request.arguments)
            case "blender.camera.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.camera.create":
                arguments = CameraCreateArguments.model_validate(request.arguments)
            case "blender.camera.configure":
                arguments = CameraConfigureArguments.model_validate(request.arguments)
            case "blender.camera.set_active":
                arguments = CameraSetActiveArguments.model_validate(request.arguments)
            case "blender.scene.inspect":
                arguments = InspectArguments.model_validate(request.arguments)
            case "blender.object.create_primitive":
                arguments = CreateArguments.model_validate(request.arguments)
            case "blender.object.set_transform":
                arguments = TransformArguments.model_validate(request.arguments)
            case "blender.object.delete":
                arguments = DeleteArguments.model_validate(request.arguments)
            case "blender.render.image":
                arguments = RenderArguments.model_validate(request.arguments)
            case _:
                return failure(
                    request,
                    ProtocolError(
                        code="operation_unsupported",
                        message="Operation is not advertised",
                    ),
                )
    except ValidationError as exc:
        details: list[JsonValue] = [
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in exc.errors(
                include_input=False, include_context=False, include_url=False
            )
        ]
        return failure(
            request,
            ProtocolError(
                code="invalid_arguments",
                message="Invalid operation arguments",
                details=details,
            ),
        )
    try:
        result: Model
        artifacts: tuple[ArtifactDescriptor, ...] = ()
        if isinstance(arguments, (WorldRayArguments, CameraRayArguments)):
            result = backend.scene_raycast(arguments)
        elif isinstance(arguments, MultiresCreateArguments):
            result = backend.multires_create(arguments)
        elif isinstance(arguments, MultiresSubdivideArguments):
            result = backend.multires_subdivide(arguments)
        elif isinstance(arguments, MultiresConfigureArguments):
            result = backend.multires_configure(arguments)
        elif isinstance(arguments, MultiresInspectArguments):
            result = backend.multires_inspect(arguments)
        elif isinstance(arguments, SculptStrokeArguments):
            result = backend.sculpt_stroke(arguments)
        elif isinstance(
            arguments,
            RetopoSeedArguments
            | RetopoProjectArguments
            | RetopoExtrudeArguments
            | RetopoBridgeArguments,
        ):
            result = backend.retopo_edit(arguments)
        elif isinstance(arguments, RetopoInspectArguments):
            result = backend.retopo_inspect(arguments)
        elif isinstance(arguments, RetopoCreateArguments):
            result = backend.retopo_create_target(arguments)
        elif isinstance(arguments, VoxelRemeshInspectArguments):
            result = backend.sculpt_voxel_remesh_inspect(arguments)
        elif isinstance(arguments, VoxelRemeshArguments):
            result = backend.sculpt_voxel_remesh(arguments)
        elif isinstance(arguments, MaskInspectArguments):
            result = backend.sculpt_mask_inspect(arguments)
        elif isinstance(arguments, MaskClearArguments):
            result = backend.sculpt_mask_clear(arguments)
        elif isinstance(arguments, MaskInvertArguments):
            result = backend.sculpt_mask_invert(arguments)
        elif isinstance(arguments, MaskStrokeArguments):
            result = backend.sculpt_mask_stroke(arguments)
        elif isinstance(arguments, FaceSetsInspectArguments):
            result = backend.sculpt_face_sets_inspect(arguments)
        elif isinstance(arguments, FaceSetsAssignArguments):
            result = backend.sculpt_face_sets_assign(arguments)
        elif isinstance(arguments, FaceSetsInitializeArguments):
            result = backend.sculpt_face_sets_initialize(arguments)
        elif isinstance(arguments, SculptFilterArguments):
            result = backend.sculpt_filter(arguments)
        elif isinstance(arguments, SculptInspectArguments):
            result = backend.sculpt_inspect(arguments)
        elif isinstance(arguments, InspectArguments):
            if request.operation == "blender.image.inspect":
                result = backend.image_inspect()
            elif request.operation == "blender.material.inspect":
                result = backend.material_inspect()
            elif request.operation == "blender.light.inspect":
                result = backend.light_inspect()
            elif request.operation == "blender.camera.inspect":
                result = backend.camera_inspect()
            else:
                result = backend.inspect()
        elif isinstance(arguments, CreateBase):
            result = backend.modifier_create(arguments)
        elif isinstance(arguments, ConfigureBase):
            result = backend.modifier_configure(arguments)
        elif isinstance(arguments, ModifierMoveArguments):
            result = backend.modifier_move(arguments)
        elif isinstance(arguments, ModifierRemoveArguments):
            result = backend.modifier_remove(arguments)
        elif isinstance(arguments, ModifierApplyArguments):
            result = backend.modifier_apply(arguments)
        elif isinstance(arguments, ModifierInspectArguments):
            result = backend.modifier_inspect(arguments)
        elif isinstance(arguments, EvaluatedMeshArguments):
            result = backend.mesh_inspect_evaluated(arguments)
        elif isinstance(arguments, MeshQueryArguments):
            result = backend.mesh_query(arguments)
        elif isinstance(arguments, (MeshSelectionArguments, MeshNormalsArguments)):
            result = backend.mesh_edit(arguments)
        elif isinstance(arguments, MeshInspectArguments):
            result = backend.mesh_inspect(arguments)
        elif isinstance(arguments, UVCreateArguments):
            result = backend.uv_create(arguments)
        elif isinstance(arguments, UVSetActiveArguments):
            result = backend.uv_set_active(arguments)
        elif isinstance(arguments, UVUnwrapArguments):
            result = backend.uv_unwrap(arguments)
        elif isinstance(arguments, UVPackArguments):
            result = backend.uv_pack(arguments)
        elif isinstance(arguments, UVInspectArguments):
            result = backend.uv_inspect(arguments)
        elif isinstance(arguments, ImageCreateArguments):
            result = backend.image_create(arguments)
        elif isinstance(arguments, ImageFromArtifactArguments):
            result = backend.image_from_artifact(arguments, request)
        elif isinstance(arguments, ImageConfigureArguments):
            result = backend.image_configure(arguments)
        elif isinstance(arguments, ShaderInspectArguments):
            result = backend.shader_inspect(arguments)
        elif isinstance(arguments, NodeCreateArguments):
            result = backend.shader_create(arguments)
        elif isinstance(arguments, NodeConfigureArguments):
            result = backend.shader_configure(arguments)
        elif isinstance(arguments, NodeDeleteArguments):
            result = backend.shader_delete(arguments)
        elif isinstance(arguments, ConnectArguments):
            result = backend.shader_connect(arguments)
        elif isinstance(arguments, DisconnectArguments):
            result = backend.shader_disconnect(arguments)
        elif isinstance(arguments, MaterialCreateArguments):
            result = backend.material_create(arguments)
        elif isinstance(arguments, MaterialConfigureArguments):
            result = backend.material_configure(arguments)
        elif isinstance(arguments, MaterialAssignArguments):
            result = backend.material_assign(arguments)
        elif isinstance(arguments, LightCreateArguments):
            result = backend.light_create(arguments)
        elif isinstance(arguments, LightConfigureArguments):
            result = backend.light_configure(arguments)
        elif isinstance(arguments, CameraCreateArguments):
            result = backend.camera_create(arguments)
        elif isinstance(arguments, CameraConfigureArguments):
            result = backend.camera_configure(arguments)
        elif isinstance(arguments, CameraSetActiveArguments):
            result = backend.camera_set_active(arguments)
        elif isinstance(arguments, CreateArguments):
            result = backend.create(arguments)
        elif isinstance(arguments, TransformArguments):
            result = backend.transform(arguments)
        elif isinstance(arguments, DeleteArguments):
            result = backend.delete(arguments)
        else:
            result, descriptor = backend.render(arguments)
            artifacts = (descriptor,)
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
