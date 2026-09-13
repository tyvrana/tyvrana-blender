import logging

import pytest
from tyvrana_protocol import (
    ArtifactDescriptor,
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

from tyvrana_blender.camera_models import (
    CameraConfigureArguments,
    CameraCreateArguments,
    CameraInspectResult,
    CameraSetActiveArguments,
    CameraSummary,
)
from tyvrana_blender.image_models import (
    ImageConfigureArguments,
    ImageCreateArguments,
    ImageFromArtifactArguments,
    ImageInspectResult,
    ImageSummary,
)
from tyvrana_blender.light_models import (
    LightConfigureArguments,
    LightCreateArguments,
    LightInspectResult,
    LightSummary,
    light_state,
)
from tyvrana_blender.material_models import (
    MaterialAssignArguments,
    MaterialAssignResult,
    MaterialConfigureArguments,
    MaterialCreateArguments,
    MaterialInspectResult,
    MaterialSummary,
)
from tyvrana_blender.mesh_models import (
    EdgeQueryResult,
    ElementCounts,
    ElementSelection,
    FaceQueryResult,
    ManifoldSummary,
    MeshEditResult,
    MeshInspectArguments,
    MeshNormalsArguments,
    MeshQueryArguments,
    MeshQueryResult,
    MeshSelectionArguments,
    MeshSummary,
    VertexQueryResult,
)
from tyvrana_blender.models import (
    CreateArguments,
    DeleteArguments,
    DeleteResult,
    ObjectSummary,
    RenderArguments,
    RenderResult,
    SceneSummary,
    TransformArguments,
)
from tyvrana_blender.modifier_models import (
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
from tyvrana_blender.operations import OperationError, execute
from tyvrana_blender.shader_models import (
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
from tyvrana_blender.uv_models import (
    UVCreateArguments,
    UVInspectArguments,
    UVInspectResult,
    UVPackArguments,
    UVSetActiveArguments,
    UVUnwrapArguments,
)


class Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def modifier_inspect(
        self, arguments: ModifierInspectArguments
    ) -> ModifierInspectResult:
        self.calls.append("modifier_inspect")
        return ModifierInspectResult(object_name=arguments.object_name, modifiers=[])

    def modifier_create(self, arguments: ModifierCreateArguments) -> ModifierSummary:
        self.calls.append("modifier_create")
        return ModifierSummary(
            name="Surface",
            index=0,
            type=arguments.type,
            supported=True,
            enabled_viewport=True,
            enabled_render=True,
            show_in_editmode=True,
            show_on_cage=False,
            settings=None,
        )

    def modifier_configure(
        self, arguments: ModifierConfigureArguments
    ) -> ModifierSummary:
        self.calls.append("modifier_configure")
        return ModifierSummary(
            name="Surface",
            index=0,
            type=arguments.type,
            supported=True,
            enabled_viewport=True,
            enabled_render=True,
            show_in_editmode=True,
            show_on_cage=False,
            settings=None,
        )

    def modifier_move(self, arguments: ModifierMoveArguments) -> ModifierInspectResult:
        self.calls.append("modifier_move")
        return ModifierInspectResult(object_name=arguments.object_name, modifiers=[])

    def modifier_remove(
        self, arguments: ModifierRemoveArguments
    ) -> ModifierRemoveResult:
        self.calls.append("modifier_remove")
        return ModifierRemoveResult(
            object_name=arguments.object_name,
            removed=arguments.modifier_name,
            modifiers=[],
        )

    def modifier_apply(self, arguments: ModifierApplyArguments) -> ModifierApplyResult:
        self.calls.append("modifier_apply")
        return ModifierApplyResult(
            object_name=arguments.object_name,
            applied=arguments.modifier_name,
            modifiers=[],
            mesh=self.mesh_inspect(
                MeshInspectArguments(object_name=arguments.object_name)
            ),
        )

    def mesh_inspect_evaluated(
        self, arguments: EvaluatedMeshArguments
    ) -> EvaluatedMeshSummary:
        self.calls.append("mesh_inspect_evaluated")
        return EvaluatedMeshSummary(
            object_name=arguments.object_name,
            source_mesh_name="Mesh",
            vertex_count=0,
            edge_count=0,
            face_count=0,
            loop_count=0,
            bounds_min=None,
            bounds_max=None,
            manifold_summary=ManifoldSummary(
                boundary_edge_count=0,
                manifold_edge_count=0,
                non_manifold_edge_count=0,
                loose_vertex_count=0,
                loose_edge_count=0,
            ),
            modifier_count=0,
            modifier_stack=[],
        )

    def mesh_inspect(self, arguments: MeshInspectArguments) -> MeshSummary:
        self.calls.append("mesh_inspect")
        return MeshSummary(
            object_name=arguments.object_name,
            mesh_name="Mesh",
            mesh_users=1,
            vertex_count=0,
            edge_count=0,
            face_count=0,
            loop_count=0,
            bounds_min=None,
            bounds_max=None,
            material_slot_count=0,
            uv_map_count=0,
            has_shape_keys=False,
            manifold_summary=ManifoldSummary(
                boundary_edge_count=0,
                manifold_edge_count=0,
                non_manifold_edge_count=0,
                loose_vertex_count=0,
                loose_edge_count=0,
            ),
        )

    def mesh_query(self, arguments: MeshQueryArguments) -> MeshQueryResult:
        self.calls.append("mesh_query")
        match arguments.selector.domain:
            case "vertex":
                return VertexQueryResult(
                    object_name=arguments.object_name,
                    matched_count=0,
                    truncated=False,
                    elements=[],
                )
            case "edge":
                return EdgeQueryResult(
                    object_name=arguments.object_name,
                    matched_count=0,
                    truncated=False,
                    elements=[],
                )
            case "face":
                return FaceQueryResult(
                    object_name=arguments.object_name,
                    matched_count=0,
                    truncated=False,
                    elements=[],
                )

    def mesh_edit(
        self, arguments: MeshSelectionArguments | MeshNormalsArguments
    ) -> MeshEditResult:
        state = self.mesh_inspect(arguments)
        self.calls[-1] = "mesh_edit"
        return MeshEditResult(
            object_name=arguments.object_name,
            selected=ElementSelection(
                domain="face"
                if isinstance(arguments, MeshNormalsArguments)
                else arguments.selector.domain,
                count=1,
            ),
            created=ElementCounts(vertices=0, edges=0, faces=0),
            removed=ElementCounts(vertices=0, edges=0, faces=0),
            mesh=state,
        )

    def uv_inspect(self, arguments: UVInspectArguments) -> UVInspectResult:
        self.calls.append("uv_inspect")
        return UVInspectResult(
            object_name=arguments.object_name,
            active_map=None,
            active_render_map=None,
            mesh_users=1,
            maps=[],
        )

    def uv_create(self, arguments: UVCreateArguments) -> UVInspectResult:
        result = self.uv_inspect(arguments)
        self.calls[-1] = "uv_create"
        return result

    def uv_set_active(self, arguments: UVSetActiveArguments) -> UVInspectResult:
        result = self.uv_inspect(arguments)
        self.calls[-1] = "uv_set_active"
        return result

    def uv_unwrap(self, arguments: UVUnwrapArguments) -> UVInspectResult:
        result = self.uv_inspect(arguments)
        self.calls[-1] = "uv_unwrap"
        return result

    def uv_pack(self, arguments: UVPackArguments) -> UVInspectResult:
        result = self.uv_inspect(arguments)
        self.calls[-1] = "uv_pack"
        return result

    def image_inspect(self) -> ImageInspectResult:
        self.calls.append("image_inspect")
        return ImageInspectResult(images=[])

    def image_create(self, arguments: ImageCreateArguments) -> ImageSummary:
        self.calls.append("image_create")
        return image_summary(arguments.name or "Image")

    def image_from_artifact(
        self, arguments: ImageFromArtifactArguments, request: OperationRequest
    ) -> ImageSummary:
        self.calls.append("image_from_artifact")
        return image_summary(arguments.name or "Image")

    def image_configure(self, arguments: ImageConfigureArguments) -> ImageSummary:
        self.calls.append("image_configure")
        return image_summary(arguments.name)

    def shader_inspect(self, arguments: ShaderInspectArguments) -> ShaderGraphSummary:
        self.calls.append("shader_inspect")
        return ShaderGraphSummary(
            material_name=arguments.material_name,
            node_tree_present=True,
            nodes=[],
            links=[],
        )

    def shader_create(self, arguments: NodeCreateArguments) -> NodeSummary:
        self.calls.append("shader_create")
        return node_summary(arguments.name or "Node")

    def shader_configure(self, arguments: NodeConfigureArguments) -> NodeSummary:
        self.calls.append("shader_configure")
        return node_summary(arguments.node_name)

    def shader_delete(self, arguments: NodeDeleteArguments) -> NodeDeleteResult:
        self.calls.append("shader_delete")
        return NodeDeleteResult(
            material_name=arguments.material_name, deleted=arguments.node_name
        )

    def shader_connect(self, arguments: ConnectArguments) -> LinkSummary:
        self.calls.append("shader_connect")
        return LinkSummary(
            from_node=arguments.from_node,
            from_socket=arguments.from_socket,
            to_node=arguments.to_node,
            to_socket=arguments.to_socket,
            valid=True,
            muted=False,
        )

    def shader_disconnect(self, arguments: DisconnectArguments) -> DisconnectResult:
        self.calls.append("shader_disconnect")
        return DisconnectResult(
            material_name=arguments.material_name,
            to_node=arguments.to_node,
            to_socket=arguments.to_socket,
            removed=0,
        )

    def material_inspect(self) -> MaterialInspectResult:
        self.calls.append("material_inspect")
        return MaterialInspectResult(materials=[])

    def material_create(self, arguments: MaterialCreateArguments) -> MaterialSummary:
        self.calls.append("material_create")
        return MaterialSummary(
            name=arguments.name or "Material",
            surface="none",
            principled=None,
            assignments=[],
        )

    def material_configure(
        self, arguments: MaterialConfigureArguments
    ) -> MaterialSummary:
        self.calls.append("material_configure")
        return MaterialSummary(
            name=arguments.name, surface="none", principled=None, assignments=[]
        )

    def material_assign(
        self, arguments: MaterialAssignArguments
    ) -> MaterialAssignResult:
        self.calls.append("material_assign")
        return MaterialAssignResult(
            object_name=arguments.object_name,
            assigned_slot=0,
            material_name=arguments.material_name,
            slots=[arguments.material_name],
        )

    def light_inspect(self) -> LightInspectResult:
        self.calls.append("light_inspect")
        return LightInspectResult(lights=[])

    def light_create(self, arguments: LightCreateArguments) -> LightSummary:
        self.calls.append("light_create")
        return light_summary(arguments.name or "Light")

    def light_configure(self, arguments: LightConfigureArguments) -> LightSummary:
        self.calls.append("light_configure")
        return light_summary(arguments.name)

    def camera_inspect(self) -> CameraInspectResult:
        self.calls.append("camera_inspect")
        return CameraInspectResult(active_camera=None, cameras=[])

    def camera_create(self, arguments: CameraCreateArguments) -> CameraSummary:
        self.calls.append("camera_create")
        return camera_summary(arguments.name or "Camera")

    def camera_configure(self, arguments: CameraConfigureArguments) -> CameraSummary:
        self.calls.append("camera_configure")
        return camera_summary(arguments.name)

    def camera_set_active(self, arguments: CameraSetActiveArguments) -> CameraSummary:
        self.calls.append("camera_set_active")
        return camera_summary(arguments.name)

    def render(
        self, arguments: RenderArguments
    ) -> tuple[RenderResult, ArtifactDescriptor]:
        self.calls.append("render")
        return RenderResult(
            width=arguments.width, height=arguments.height
        ), ArtifactDescriptor(
            artifact_id="1" * 32, media_type="image/png", byte_size=1, sha256="0" * 64
        )

    def inspect(self) -> SceneSummary:
        self.calls.append("inspect")
        return SceneSummary(
            name="Scene",
            filepath=None,
            active_object=None,
            selected_objects=[],
            object_count=0,
            objects=[],
        )

    def create(self, arguments: CreateArguments) -> ObjectSummary:
        self.calls.append("create")
        return ObjectSummary(
            name=arguments.name or "Cube",
            type="MESH",
            location=arguments.location,
            rotation=arguments.rotation,
            scale=arguments.scale,
            dimensions=[2, 2, 2],
            visible=True,
            hide_viewport=False,
            hide_render=False,
            selected=True,
            parent=None,
        )

    def transform(self, arguments: TransformArguments) -> ObjectSummary:
        raise OperationError(
            "object_not_found", "Object is missing", {"name": arguments.name}
        )

    def delete(self, arguments: DeleteArguments) -> DeleteResult:
        self.calls.append("delete")
        return DeleteResult(deleted=arguments.name)


def image_summary(name: str) -> ImageSummary:
    return ImageSummary(
        name=name,
        source="generated",
        width=4,
        height=4,
        channels=4,
        has_alpha=True,
        is_float=False,
        color_space="sRGB",
        alpha_mode="straight",
        packed=False,
        users=0,
        dirty=False,
        generated_type="blank",
    )


def node_summary(name: str) -> NodeSummary:
    return NodeSummary(
        node_name=name,
        node_type="ShaderNodeMapping",
        label="",
        muted=False,
        inputs=[],
        outputs=[],
        settings=None,
    )


def call(
    backend: Backend, operation: str, arguments: JsonValue
) -> OperationSuccess | OperationFailure:
    return execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="request-a",
            operation=operation,
            arguments=arguments,
        ),
    )


def light_summary(name: str) -> LightSummary:
    return LightSummary.model_validate(
        {
            **light_state("point", {}, set()).model_dump(mode="json"),
            "name": name,
            "location": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
            "visible": True,
            "hide_viewport": False,
            "hide_render": False,
            "parent": None,
        }
    )


def camera_summary(name: str) -> CameraSummary:
    return CameraSummary(
        name=name,
        active=True,
        projection="perspective",
        location=[0, 0, 0],
        rotation=[0, 0, 0],
        scale=[1, 1, 1],
        lens_mm=50,
        ortho_scale=None,
        clip_start=0.1,
        clip_end=1000,
        shift_x=0,
        shift_y=0,
        sensor_width_mm=36,
        sensor_height_mm=24,
        sensor_fit="auto",
    )


@pytest.mark.parametrize(
    ("operation", "arguments", "method"),
    [
        ("blender.image.inspect", {}, "image_inspect"),
        ("blender.image.create_generated", {"width": 4, "height": 4}, "image_create"),
        (
            "blender.image.configure",
            {"name": "Image", "color_space": "Non-Color"},
            "image_configure",
        ),
        ("blender.shader.inspect", {"material_name": "M"}, "shader_inspect"),
        (
            "blender.shader.node.create",
            {"material_name": "M", "node_type": "mapping"},
            "shader_create",
        ),
        (
            "blender.shader.node.configure",
            {"material_name": "M", "node_name": "N", "scale": [2, 2, 2]},
            "shader_configure",
        ),
        (
            "blender.shader.node.delete",
            {"material_name": "M", "node_name": "N"},
            "shader_delete",
        ),
        (
            "blender.shader.connect",
            {
                "material_name": "M",
                "from_node": "A",
                "from_socket": "Color",
                "to_node": "B",
                "to_socket": "Base Color",
            },
            "shader_connect",
        ),
        (
            "blender.shader.disconnect",
            {"material_name": "M", "to_node": "B", "to_socket": "Base Color"},
            "shader_disconnect",
        ),
        ("blender.material.inspect", {}, "material_inspect"),
        ("blender.material.create_principled", {}, "material_create"),
        (
            "blender.material.configure_principled",
            {"name": "Material", "roughness": 0.5},
            "material_configure",
        ),
        (
            "blender.material.assign",
            {"object_name": "Cube", "material_name": "Material"},
            "material_assign",
        ),
        ("blender.light.inspect", {}, "light_inspect"),
        ("blender.light.create", {"type": "point"}, "light_create"),
        ("blender.light.configure", {"name": "Light", "energy": 20}, "light_configure"),
        ("blender.camera.inspect", {}, "camera_inspect"),
        ("blender.camera.create", {}, "camera_create"),
        (
            "blender.camera.configure",
            {"name": "Camera", "lens_mm": 80},
            "camera_configure",
        ),
        ("blender.camera.set_active", {"name": "Camera"}, "camera_set_active"),
        ("blender.scene.inspect", {}, "inspect"),
        ("blender.render.image", {}, "render"),
        (
            "blender.object.create_primitive",
            {"primitive": "cube", "name": "Example"},
            "create",
        ),
        ("blender.object.delete", {"name": "Example"}, "delete"),
    ],
)
def test_dispatch_calls_only_the_selected_handler(
    operation: str, arguments: JsonValue, method: str
) -> None:
    backend = Backend()
    result = call(backend, operation, arguments)
    assert isinstance(result, OperationSuccess)
    assert result.request_id == "request-a"
    assert backend.calls == [method]


@pytest.mark.parametrize(
    "arguments",
    [None, [], {}, {"primitive": "duck"}, {"primitive": "cube", "location": [1, 2]}],
)
def test_invalid_arguments_do_not_call_blender(arguments: JsonValue) -> None:
    backend = Backend()
    result = call(backend, "blender.object.create_primitive", arguments)
    assert isinstance(result, OperationFailure)
    assert result.error.code == "invalid_arguments"
    assert result.error.details
    assert backend.calls == []


def test_known_errors_preserve_details_without_stack_traces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = call(Backend(), "blender.object.set_transform", {"name": "missing"})
    assert isinstance(result, OperationFailure)
    assert result.error.code == "object_not_found"
    assert result.error.details == {"name": "missing"}
    assert all(record.exc_info is None for record in caplog.records)


def test_unsupported_operation() -> None:
    result = call(Backend(), "blender.not_implemented", {})
    assert isinstance(result, OperationFailure)
    assert result.error.code == "operation_unsupported"


@pytest.mark.parametrize("bad_model", [True, False])
def test_internal_failures_are_logged_and_sanitized(
    bad_model: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend = Backend()

    def broken() -> SceneSummary:
        if bad_model:
            return SceneSummary.model_validate({"private": "internal diagnostic"})
        raise RuntimeError("internal diagnostic")

    monkeypatch.setattr(backend, "inspect", broken)
    result = call(backend, "blender.scene.inspect", {})
    assert isinstance(result, OperationFailure)
    assert result.error.code == "operation_failed"
    assert "internal diagnostic" not in result.model_dump_json()
    assert (
        len([record for record in caplog.records if record.levelno >= logging.ERROR])
        == 1
    )
