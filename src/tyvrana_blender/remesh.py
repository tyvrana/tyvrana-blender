"""Native voxel remeshing on staged Mesh data with explicit loss analysis."""

import math
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import mesh, modifiers, multires, sculpt_regions
from .operations import OperationError
from .remesh_models import (
    MAX_ATTRIBUTE_COMPONENTS,
    MAX_ATTRIBUTES,
    MAX_GRID_COORDINATE,
    Distribution,
    RemeshBlocker,
    RemeshDataEffect,
    VoxelMeshSummary,
    VoxelRemeshArguments,
    VoxelRemeshResult,
    VoxelRemeshSettings,
    VoxelRemeshSummary,
    estimate_grid,
)

STRUCTURAL = {"position", ".edge_verts", ".corner_vert", ".corner_edge"}
COMPONENTS = {
    "FLOAT": 1,
    "INT": 1,
    "INT8": 1,
    "BOOLEAN": 1,
    "FLOAT_VECTOR": 3,
    "FLOAT2": 2,
    "INT32_2D": 2,
    "FLOAT_COLOR": 4,
    "BYTE_COLOR": 4,
}


def distribution(values: list[float]) -> Distribution:
    mean = math.fsum(values) / len(values) if values else 0.0
    variance = (
        math.fsum((v - mean) ** 2 for v in values) / len(values) if values else 0.0
    )
    return Distribution(
        min=min(values, default=0),
        max=max(values, default=0),
        mean=mean,
        variance=variance,
        coefficient_of_variation=math.sqrt(variance) / mean if mean else 0,
    )


def geometry(obj: Any) -> VoxelMeshSummary:
    with mesh.snapshot(obj) as bm:
        return VoxelMeshSummary(
            **mesh.summary(obj, bm).model_dump(),
            edge_length=distribution([float(e.calc_length()) for e in bm.edges]),
            face_area=distribution([float(f.calc_area()) for f in bm.faces]),
        )


def attributes(data: Any) -> list[Any]:
    return [a for a in data.attributes if a.name not in STRUCTURAL]


def attribute_work(data: Any) -> int:
    return sum(len(a.data) * COMPONENTS.get(a.data_type, 16) for a in attributes(data))


def inspect(obj: Any, arguments: VoxelRemeshArguments) -> VoxelRemeshSummary:
    data = obj.data
    if data.is_editmode:
        raise OperationError(
            "invalid_context", "Leave Edit Mode to analyze voxel remeshing"
        )
    modifiers.check_geometry(data)
    if any(abs(c) > MAX_GRID_COORDINATE * 1000 for v in data.vertices for c in v.co):
        # Even the largest supported voxel cannot represent these coordinates.
        # Reject before single-precision BMesh area/normal calculations overflow.
        raise OperationError(
            "voxel_remesh_blocked",
            "Object-local coordinates exceed the voxel coordinate limit",
            {"blockers": [{"code": "voxel_coordinate_limit"}]},
        )
    state = geometry(obj)
    settings = VoxelRemeshSettings.model_validate(
        arguments.model_dump(exclude={"object_name"})
    )
    grid = estimate_grid(
        state.bounds_min or [0, 0, 0],
        state.bounds_max or [0, 0, 0],
        settings.voxel_size,
    )
    blockers: list[RemeshBlocker] = []

    def block(condition: bool, code: str, message: str) -> None:
        if condition:
            blockers.append(RemeshBlocker(code=code, message=message))

    block(
        bpy.context.mode != "OBJECT",
        "object_mode_required",
        "Voxel remeshing requires Object Mode",
    )
    block(
        bpy.app.is_job_running("RENDER"),
        "render_running",
        "Wait for the render to finish",
    )
    block(
        not bpy.context.scene.is_editable
        or not obj.is_editable
        or not data.is_editable
        or bool(
            obj.library or data.library or obj.override_library or data.override_library
        ),
        "linked_data",
        "Use local editable scene, object and Mesh data without library overrides",
    )
    block(
        data.shape_keys is not None,
        "has_shape_keys",
        "Shape keys require a separate topology-transfer workflow",
    )
    block(
        any(m.type == "MULTIRES" for m in obj.modifiers),
        "has_multires",
        "Multires displacement must not be remeshed or discarded",
    )
    block(
        any(m.type != "MULTIRES" for m in obj.modifiers),
        "has_modifiers",
        "Resolve the modifier stack explicitly before remeshing authored geometry",
    )
    block(
        not multires.unit_scale(obj),
        "nonunit_scale",
        "Require unit local and inherited scale without shear",
    )
    block(
        bool(data.animation_data or obj.animation_data),
        "mesh_animation",
        "Animated object or Mesh data requires a separate workflow",
    )
    block(
        bool(obj.constraints)
        or bool(obj.parent and obj.parent.type in {"ARMATURE", "LATTICE"})
        or any(
            c.parent == obj and c.parent_type in {"VERTEX", "VERTEX_3"}
            for c in bpy.data.objects
        )
        or any(
            (m.type == "MESH_DEFORM" and m.object == obj)
            or (m.type == "SURFACE_DEFORM" and m.target == obj)
            for other in bpy.data.objects
            if other != obj
            for m in other.modifiers
        ),
        "deformation_relationship",
        (
            "Constraints, deformation parents/targets and vertex-parented "
            "children need explicit correspondence handling"
        ),
    )
    block(
        bool(data.uv_layers) and not settings.discard_uv_maps,
        "has_uv_maps",
        (
            "UV maps are protected: reprojection cannot retain authored "
            "seams and production UV layout; discard_uv_maps explicitly removes them"
        ),
    )
    block(
        bool(obj.vertex_groups),
        "has_vertex_groups",
        (
            "Vertex group definitions and weights are protected from "
            "approximate reprojection or loss"
        ),
    )
    block(
        data.has_custom_normals,
        "has_custom_normals",
        "Authored custom normals require a separate transfer workflow",
    )
    block(
        bool(obj.use_dynamic_topology_sculpting),
        "dyntopo",
        "Disable Dyntopo before explicit blockout remeshing",
    )
    block(
        any(v.hide for v in data.vertices)
        or any(e.hide for e in data.edges)
        or any(f.hide for f in data.polygons),
        "hidden_geometry",
        "Remeshing replaces the whole surface; reveal hidden geometry explicitly first",
    )
    block(
        not state.face_count or state.face_area.min <= 0,
        "invalid_surface",
        "A nonempty surface without degenerate faces is required",
    )
    manifold = state.manifold_summary
    block(
        bool(manifold.non_manifold_edge_count or manifold.loose_vertex_count),
        "non_manifold",
        "Use closed manifold surface components without loose vertices or edges",
    )
    block(
        grid.exceeds_limit,
        "voxel_grid_limit",
        (
            "Padded grid exceeds 2,000,000 cells or 4096 cells along an "
            "axis; increase voxel size"
        ),
    )
    block(
        grid.coordinate_limit_exceeded,
        "voxel_coordinate_limit",
        (
            "Object-local voxel coordinates exceed 1,000,000; use a "
            "larger voxel size or deliberately recenter the authored mesh"
        ),
    )
    attrs = attributes(data)
    block(
        len(attrs) > MAX_ATTRIBUTES or attribute_work(data) > MAX_ATTRIBUTE_COMPONENTS,
        "attribute_limit",
        (
            "Remeshing supports at most 32 nonstructural attributes and "
            "8,000,000 attribute components"
        ),
    )
    block(
        any(a.data_type not in COMPONENTS for a in attrs),
        "unsupported_attribute",
        "An attribute type lacks a validated native reprojection contract",
    )
    try:
        sculpt_regions.mask_inspect(obj)
        sculpt_regions.face_values(obj)
    except OperationError:
        block(
            True,
            "invalid_sculpt_regions",
            "Repair malformed masks or Face Sets before remeshing",
        )
    shared = sum(o.type == "MESH" and o.data == data for o in bpy.data.objects) > 1
    effects = [
        RemeshDataEffect(
            code="topology_replaced",
            behavior="rebuilt",
            message=(
                "Whole-surface topology, element indices, selection "
                "correspondence and computed normals are rebuilt; details "
                "below voxel resolution can disappear"
            ),
        )
    ]
    discarded_uvs = (
        {layer.name for layer in data.uv_layers} if settings.discard_uv_maps else set()
    )
    if discarded_uvs:
        effects.append(
            RemeshDataEffect(
                code="uv_maps",
                behavior="discarded",
                names=sorted(discarded_uvs),
                message="All UV maps are explicitly removed from the remeshed target",
            )
        )
    for domain in ("POINT", "EDGE", "FACE", "CORNER"):
        names = sorted(
            a.name for a in attrs if a.domain == domain and a.name not in discarded_uvs
        )[:MAX_ATTRIBUTES]
        if not names:
            continue
        method = {
            "POINT": (
                "Barycentric surface sampling; masks, colors and numeric "
                "values are approximate"
            ),
            "EDGE": (
                "Nearest-source-edge sampling; seam/sharp flags do not retain "
                "original edge flow"
            ),
            "FACE": (
                "Nearest-source-face sampling; material indices and Face Set "
                "IDs can shrink or disappear with their regions"
            ),
            "CORNER": (
                "Corner values sampled onto new vertices and expanded to "
                "corners; discontinuities are not retained"
            ),
        }[domain]
        effects.append(
            RemeshDataEffect(
                code="attributes_" + domain.lower(),
                names=names,
                behavior="reprojected" if settings.preserve_attributes else "discarded",
                message=method
                if settings.preserve_attributes
                else "Native attributes on this domain are discarded",
            )
        )
    if not settings.preserve_attributes:
        effects.append(
            RemeshDataEffect(
                code="face_defaults",
                behavior="rebuilt",
                message=(
                    "All faces use material index 0 and the first source face's "
                    "smooth/flat shading; masks, Face Sets, seams and sharp edges "
                    "are removed"
                ),
            )
        )
    preserved = [
        RemeshDataEffect(
            code="object_and_material_slots",
            behavior="preserved",
            message=(
                "Object identity/transforms, Mesh and object-linked material slots "
                "and unrelated scene state remain unchanged"
            ),
        )
    ]
    if shared:
        preserved.append(
            RemeshDataEffect(
                code="shared_mesh",
                behavior="preserved",
                message=(
                    "The target receives an isolated Mesh; sibling objects retain "
                    "their original topology and data"
                ),
            )
        )
    return VoxelRemeshSummary(
        object_name=obj.name,
        mesh=state,
        settings=settings,
        effective_fix_poles=settings.fix_poles and settings.adaptivity == 0,
        grid=grid,
        shared_mesh=shared,
        blockers=blockers,
        destructive_effects=effects,
        preservable_data=preserved,
    )


def native_remesh(staged: Any, settings: VoxelRemeshSettings) -> None:
    data = staged.data
    data.remesh_voxel_size = settings.voxel_size
    data.remesh_voxel_adaptivity = settings.adaptivity
    data.use_remesh_preserve_volume = settings.preserve_volume
    data.use_remesh_fix_poles = settings.fix_poles
    data.use_remesh_preserve_attributes = settings.preserve_attributes
    with bpy.context.temp_override(
        object=staged,
        active_object=staged,
        selected_objects=[staged],
        selected_editable_objects=[staged],
    ):
        if bpy.ops.object.voxel_remesh("EXEC_DEFAULT") != {"FINISHED"}:
            raise OperationError(
                "voxel_remesh_failed",
                "Native voxel remeshing did not finish; original Mesh preserved",
            )


def validate_transfer(
    original: Any,
    candidate: Any,
    expected_attributes: set[tuple[str, str, str]] | None,
) -> None:
    if list(candidate.materials) != list(original.materials):
        raise RuntimeError("Native remesh changed material slots")
    if expected_attributes is not None:
        actual = {(a.name, a.domain, a.data_type) for a in attributes(candidate)}
        if not expected_attributes <= actual:
            raise RuntimeError("Native remesh lost requested attributes")
    if (
        len(attributes(candidate)) > MAX_ATTRIBUTES
        or attribute_work(candidate) > MAX_ATTRIBUTE_COMPONENTS
    ):
        raise RuntimeError("Output exceeds attribute capacity")
    mask = candidate.attributes.get(".sculpt_mask")
    if mask is not None:
        for point in mask.data:
            value = point.value
            if not math.isfinite(value) or not -1e-6 <= value <= 1 + 1e-6:
                raise RuntimeError("Native mask reprojection returned invalid values")
            # Native barycentric float arithmetic can overshoot by a few ULPs.
            # Normalize only that roundoff, never reinterpret the mask transfer.
            if value < 0 or value > 1:
                point.value = min(1, max(0, value))


def execute(obj: Any, arguments: VoxelRemeshArguments) -> VoxelRemeshResult:
    analysis = inspect(obj, arguments)
    if analysis.blockers:
        code = (
            "mesh_has_shape_keys"
            if obj.data.shape_keys is not None
            else "voxel_remesh_blocked"
        )
        raise OperationError(
            code,
            "Voxel remeshing blocked; inspect consequences and resolve blockers first",
            analysis.model_dump(mode="json"),
        )
    original = obj.data
    original_slots = modifiers.material_slots(obj)
    staged = candidate = None
    committed = False
    try:
        staged = obj.copy()
        candidate = original.copy()
        staged.data = candidate
        bpy.context.scene.collection.objects.link(staged)
        if analysis.settings.discard_uv_maps:
            for layer in list(candidate.uv_layers):
                candidate.uv_layers.remove(layer)
        expected_attributes = (
            {(a.name, a.domain, a.data_type) for a in attributes(candidate)}
            if analysis.settings.preserve_attributes
            else None
        )
        native_remesh(staged, analysis.settings)
        if staged.data != candidate:
            raise RuntimeError(
                "Native remesh unexpectedly replaced staged Mesh identity"
            )
        modifiers.check_geometry(candidate)
        validate_transfer(original, candidate, expected_attributes)
        if analysis.settings.discard_uv_maps and candidate.uv_layers:
            raise RuntimeError("Native remesh recreated discarded UV maps")
        if modifiers.material_slots(staged) != original_slots:
            raise RuntimeError("Native remesh changed object material slots")
        after = geometry(staged).model_copy(
            update={"object_name": obj.name, "mesh_users": 1}
        )
        if (
            not after.face_count
            or after.face_area.min <= 0
            or after.manifold_summary.non_manifold_edge_count
            or after.manifold_summary.loose_vertex_count
        ):
            raise RuntimeError("Native remesh did not produce a valid closed surface")
        result = VoxelRemeshResult(
            object_name=obj.name,
            before=analysis.mesh,
            after=after,
            settings=analysis.settings,
            effective_fix_poles=analysis.effective_fix_poles,
            grid=analysis.grid,
            isolated_shared_mesh=analysis.shared_mesh,
            lost_or_rebuilt_data=analysis.destructive_effects,
            preserved_data=analysis.preservable_data,
        )
        obj.data = candidate
        if modifiers.material_slots(obj) != original_slots:
            raise RuntimeError("Mesh publication changed object material slots")
        bpy.context.view_layer.update()
        committed = True
        return result
    except OperationError:
        raise
    except Exception as exc:
        raise OperationError(
            "voxel_remesh_failed",
            "Staged voxel remeshing failed; original Mesh preserved",
        ) from exc
    finally:
        if not committed and candidate is not None and obj.data == candidate:
            obj.data = original
            modifiers.restore_material_slots(obj, original_slots)
        if staged is not None:
            bpy.data.objects.remove(staged, do_unlink=True)
        if candidate is not None and not committed and candidate.users == 0:
            bpy.data.meshes.remove(candidate)
        if committed and original.users == 0:
            bpy.data.meshes.remove(original)
