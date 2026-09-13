"""Dedicated native Multires management; displacement belongs to the Mesh."""

import logging
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import mesh, modifiers
from .operations import OperationError
from .sculpt_models import (
    MAX_MULTIRES_LEVEL,
    SCALE_TOLERANCE,
    BaseTopology,
    MultiresConfigureArguments,
    MultiresCreateArguments,
    MultiresSubdivideArguments,
    MultiresSummary,
    SculptDiagnostic,
)

logger = logging.getLogger(__name__)
LEVEL_FIELDS = {
    "viewport_level": "levels",
    "sculpt_level": "sculpt_levels",
    "render_level": "render_levels",
}


def find(obj: Any, name: str | None = None, *, required: bool = True) -> Any:
    found = [m for m in obj.modifiers if m.type == "MULTIRES"]
    if len(found) > 1:
        raise OperationError(
            "invalid_context", "Multiple Multires modifiers are not supported"
        )
    if name is not None:
        named = modifiers.find(obj, name)
        if named.type != "MULTIRES":
            raise OperationError(
                "modifier_type_unsupported", "Named modifier is not Multires"
            )
    if not found and required:
        raise OperationError("modifier_not_found", "Object has no Multires modifier")
    return found[0] if found else None


def unit_scale(obj: Any) -> bool:
    # Include inherited scale/shear: local unit scale alone is insufficient.
    matrix = obj.matrix_world.to_3x3()
    return (
        all(abs(v - 1) <= SCALE_TOLERANCE for v in obj.scale)
        and all(abs(c.length - 1) <= SCALE_TOLERANCE for c in matrix.col)
        and all(
            abs(matrix.col[i].dot(matrix.col[j])) <= SCALE_TOLERANCE
            for i, j in ((0, 1), (0, 2), (1, 2))
        )
        and matrix.determinant() > 0
    )


def inspect(obj: Any) -> MultiresSummary:
    mod = find(obj, required=False)
    with mesh.snapshot(obj) as bm:
        base = BaseTopology(
            vertex_count=len(bm.verts),
            edge_count=len(bm.edges),
            face_count=len(bm.faces),
            quad_face_count=sum(len(f.verts) == 4 for f in bm.faces),
            non_quad_face_count=sum(len(f.verts) != 4 for f in bm.faces),
            non_manifold_edge_count=sum(not e.is_manifold for e in bm.edges),
        )
    diagnostics = []
    for condition, code, severity, message in (
        (
            bool(base.non_quad_face_count),
            "non_quad_base",
            "warning",
            "Clean quads are preferred for predictable sculpt subdivision",
        ),
        (
            bool(base.non_manifold_edge_count),
            "non_manifold_base",
            "warning",
            "Base has boundary or non-manifold edges; inspect surface quality",
        ),
        (
            obj.data.shape_keys is not None,
            "shape_keys",
            "blocker",
            "Shape keys require a dedicated sculpt workflow",
        ),
        (
            not unit_scale(obj),
            "unapplied_scale",
            "warning",
            "Apply object and inherited scale before sculpt strokes",
        ),
        (
            any(m != mod for m in obj.modifiers),
            "modifier_stack",
            "blocker",
            "This Multires workflow requires an otherwise empty modifier stack",
        ),
        (
            mod is not None and mod.total_levels > MAX_MULTIRES_LEVEL,
            "level_limit",
            "blocker",
            "Stored detail exceeds the supported total-level limit",
        ),
    ):
        if condition:
            diagnostics.append(
                SculptDiagnostic(
                    code=code,
                    severity="blocker" if severity == "blocker" else "warning",
                    message=message,
                )
            )
    return MultiresSummary(
        object_name=obj.name,
        present=mod is not None,
        modifier_name=mod.name if mod else None,
        total_levels=mod.total_levels if mod else None,
        viewport_level=mod.levels if mod else None,
        sculpt_level=mod.sculpt_levels if mod else None,
        render_level=mod.render_levels if mod else None,
        base_mesh=base,
        diagnostics=diagnostics,
    )


def editable(obj: Any, mod: Any = None) -> None:
    modifiers.mutable(obj)
    data = obj.data
    if data.shape_keys is not None:
        raise OperationError(
            "mesh_has_shape_keys",
            "Multires edits require a dedicated shape-key workflow",
        )
    if (
        not data.is_editable
        or data.library
        or data.override_library
        or data.animation_data
        or data.has_custom_normals
    ):
        raise OperationError(
            "invalid_context",
            "Multires requires local unanimated Mesh data without authored custom "
            "normals",
        )
    if any(m != mod for m in obj.modifiers):
        raise OperationError(
            "invalid_context",
            "Resolve other modifiers explicitly before using the dedicated "
            "Multires workflow",
        )
    if mod is not None and (mod.is_external or mod.use_sculpt_base_mesh):
        raise OperationError(
            "invalid_context",
            "External displacement and Sculpt Base Mesh require a separate workflow",
        )
    modifiers.check_geometry(data)


def budget(obj: Any, total: int) -> None:
    if total > MAX_MULTIRES_LEVEL:
        raise OperationError(
            "invalid_context", "Multires supports at most six total subdivision levels"
        )
    mesh.check_budget(modifiers.data_size(obj.data) * (2 * 4**total if total else 1))
    if total:
        # Sculpt grids duplicate boundary vertices, unlike evaluated Mesh data.
        mesh.check_budget(len(obj.data.loops) * (2 ** (total - 1) + 1) ** 2)


def create(obj: Any, arguments: MultiresCreateArguments) -> MultiresSummary:
    if find(obj, required=False) is not None:
        raise OperationError("invalid_context", "Object already has Multires")
    editable(obj)
    if arguments.name is not None and len(arguments.name.encode("utf-8")) > 63:
        raise OperationError(
            "invalid_arguments", "Modifier name must fit Blender's 63-byte limit"
        )
    mod = obj.modifiers.new(arguments.name or "Multires", "MULTIRES")
    try:
        return inspect(obj)
    except BaseException:
        obj.modifiers.remove(mod)
        raise


def subdivide(obj: Any, arguments: MultiresSubdivideArguments) -> MultiresSummary:
    mod = find(obj, arguments.modifier_name)
    editable(obj, mod)
    budget(obj, mod.total_levels + arguments.levels)
    with mesh.snapshot(obj) as bm:
        if not bm.faces or any(f.calc_area() <= 1e-15 for f in bm.faces):
            raise OperationError(
                "invalid_context", "Subdivision requires nondegenerate base faces"
            )
        if any(len(e.link_faces) > 2 for e in bm.edges):
            raise OperationError(
                "invalid_context",
                "Subdivision does not support edges shared by more than two faces",
            )
    # Native subdivision updates Mesh displacement, including sibling Multires
    # modifiers sharing that Mesh. Isolate object users before that boundary.
    original = obj.data
    copied = None
    started = False
    try:
        if sum(o.data == original for o in bpy.data.objects if o.type == "MESH") > 1:
            copied = original.copy()
            obj.data = copied
        with bpy.context.temp_override(
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            for _ in range(arguments.levels):
                previous = mod.total_levels
                started = True
                outcome = bpy.ops.object.multires_subdivide(
                    modifier=mod.name, mode=arguments.mode.upper()
                )
                if "FINISHED" not in outcome or mod.total_levels != previous + 1:
                    raise RuntimeError(
                        "Native subdivision did not add the requested level"
                    )
        return inspect(obj)
    except Exception as exc:
        if not started and copied is not None:
            obj.data = original
            bpy.data.meshes.remove(copied)
        logger.exception("Multires subdivision failed")
        raise OperationError(
            "multires_failed",
            "Subdivision failed; existing detail may have gained levels; "
            "reinspect before retrying",
            {"mutation_possible": started},
        ) from exc


def configure(obj: Any, arguments: MultiresConfigureArguments) -> MultiresSummary:
    mod = find(obj, arguments.modifier_name)
    editable(obj, mod)
    budget(obj, mod.total_levels)
    patch = {
        native: getattr(arguments, public)
        for public, native in LEVEL_FIELDS.items()
        if public in arguments.model_fields_set
    }
    if any(value > mod.total_levels for value in patch.values()):
        raise OperationError(
            "invalid_arguments", "Requested level exceeds the existing total levels"
        )
    previous = {key: getattr(mod, key) for key in patch}
    try:
        for key, value in patch.items():
            setattr(mod, key, value)
        return inspect(obj)
    except BaseException:
        for key, value in previous.items():
            setattr(mod, key, value)
        raise
