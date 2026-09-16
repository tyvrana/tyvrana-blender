"""Main-thread modifier edits, bounded evaluation, and staged native application."""

import math
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

from . import mesh
from .errors import OperationError
from .modifier_models import (
    MAX_MODIFIERS,
    MAX_SUBDIVISION_LEVEL,
    BooleanSettings,
    CorrectiveSmoothSettings,
    EvaluatedMeshSummary,
    MirrorSettings,
    ModifierApplyResult,
    ModifierConfigureArguments,
    ModifierCreateArguments,
    ModifierInspectResult,
    ModifierRemoveResult,
    ModifierSettings,
    ModifierStackEntry,
    ModifierSummary,
    ProjectionSettings,
    ShrinkwrapSettings,
    SolidifySettings,
    SubdivisionSettings,
    TriangulateSettings,
)

TYPES = {
    "corrective_smooth": "CORRECTIVE_SMOOTH",
    "mirror": "MIRROR",
    "subdivision_surface": "SUBSURF",
    "shrinkwrap": "SHRINKWRAP",
    "boolean": "BOOLEAN",
    "solidify": "SOLIDIFY",
    "triangulate": "TRIANGULATE",
}
SEMANTIC_TYPES = {native: name for name, native in TYPES.items()}
METHODS = {
    "nearest_surface": "NEAREST_SURFACEPOINT",
    "project": "PROJECT",
    "target_normal_project": "TARGET_PROJECT",
}
COMMON = {
    "enabled_viewport": "show_viewport",
    "enabled_render": "show_render",
    "show_in_editmode": "show_in_editmode",
}
FIELDS = {
    "corrective_smooth": {
        "factor": "factor",
        "iterations": "iterations",
        "scale": "scale",
        "smooth_type": "smooth_type",
        "pin_boundaries": "use_pin_boundary",
        "vertex_group": "vertex_group",
    },
    "triangulate": {
        "quad_method": "quad_method",
        "ngon_method": "ngon_method",
        "min_vertices": "min_vertices",
        "keep_custom_normals": "keep_custom_normals",
    },
    "mirror": {
        "uv_flip_u": "use_mirror_u",
        "uv_flip_v": "use_mirror_v",
        "uv_flip_per_tile": "use_mirror_udim",
        "uv_flip_offset_u": "mirror_offset_u",
        "uv_flip_offset_v": "mirror_offset_v",
        "uv_offset_u": "offset_u",
        "uv_offset_v": "offset_v",
        "axes": "use_axis",
        "bisect_axes": "use_bisect_axis",
        "bisect_flip_axes": "use_bisect_flip_axis",
        "clipping": "use_clip",
        "merge": "use_mirror_merge",
        "merge_threshold": "merge_threshold",
        "mirror_object": "mirror_object",
    },
    "subdivision_surface": {
        "mode": "subdivision_type",
        "levels": "levels",
        "render_levels": "render_levels",
        "uv_smooth": "uv_smooth",
        "boundary_smooth": "boundary_smooth",
        "use_creases": "use_creases",
        "show_only_control_edges": "show_only_control_edges",
    },
    "shrinkwrap": {
        "target": "target",
        "method": "wrap_method",
        "mode": "wrap_mode",
        "offset": "offset",
    },
    "boolean": {
        "operand_object": "object",
        "operation": "operation",
        "solver": "solver",
    },
    "solidify": {
        "thickness": "thickness",
        "offset": "offset",
        "even_thickness": "use_even_offset",
        "rim": "use_rim",
        "rim_only": "use_rim_only",
        "quality_normals": "use_quality_normals",
    },
}
PROJECTION = {
    "positive": "use_positive_direction",
    "negative": "use_negative_direction",
    "cull_face": "cull_face",
    "invert_cull": "use_invert_cull",
    "limit": "project_limit",
}
ENUMS = {
    "smooth_type",
    "quad_method",
    "ngon_method",
    "subdivision_type",
    "uv_smooth",
    "boundary_smooth",
    "wrap_mode",
    "operation",
    "solver",
}
DEFAULTS: dict[str, Any] = {
    "rest_source": "ORCO",
    "vertex_group": "",
    "show_viewport": True,
    "show_render": True,
    "show_in_editmode": True,
    "use_axis": (True, False, False),
    "mirror_object": None,
    "target": None,
    "object": None,
    "auxiliary_target": None,
    "levels": 1,
    "render_levels": 2,
    "use_adaptive_subdivision": False,
    "wrap_method": "NEAREST_SURFACEPOINT",
    "use_positive_direction": True,
    "use_negative_direction": False,
    "operand_type": "OBJECT",
    "solver": "EXACT",
    "solidify_mode": "EXTRUDE",
    "use_rim": True,
    "use_rim_only": False,
    "subsurf_levels": 0,
}

# These native types cannot increase topology. They remain inspection-only; no
# configuration API for them is implied. Other unknown generators are unbounded.
SAME_TOPOLOGY = {
    "ARMATURE",
    "CAST",
    "CURVE",
    "DISPLACE",
    "HOOK",
    "LAPLACIANDEFORM",
    "LATTICE",
    "MESH_DEFORM",
    "SIMPLE_DEFORM",
    "SMOOTH",
    "CORRECTIVE_SMOOTH",
    "LAPLACIANSMOOTH",
    "SURFACE_DEFORM",
    "WARP",
    "WAVE",
    "NORMAL_EDIT",
    "WEIGHTED_NORMAL",
    "UV_PROJECT",
    "UV_WARP",
    "VERTEX_WEIGHT_EDIT",
    "VERTEX_WEIGHT_MIX",
    "VERTEX_WEIGHT_PROXIMITY",
    "DATA_TRANSFER",
}


def scene_object(name: str) -> Any:
    matches = [obj for obj in bpy.context.scene.objects if obj.name == name]
    if not matches:
        raise OperationError(
            "object_not_found", "Object does not exist in the current scene"
        )
    if len(matches) != 1:
        raise OperationError(
            "invalid_context", "Object name is ambiguous across libraries"
        )
    return matches[0]


def object_mesh(name: str) -> Any:
    obj = scene_object(name)
    if obj.type != "MESH":
        raise OperationError(
            "object_not_mesh", "Modifier operations require a Mesh object"
        )
    return obj


def mutable(obj: Any) -> None:
    if (
        bpy.context.mode != "OBJECT"
        or obj.data.is_editmode
        or bpy.app.is_job_running("RENDER")
        or not bpy.context.scene.is_editable
        or not obj.is_editable
        or obj.library is not None
        or obj.override_library is not None
    ):
        raise OperationError(
            "invalid_context",
            "Modifier edits require Object Mode and a local editable object",
        )
    if obj.animation_data is not None:
        raise OperationError(
            "invalid_context",
            "Animated object state requires an animation-aware modifier workflow",
        )


def find(obj: Any, name: str) -> Any:
    mod = obj.modifiers.get(name)
    if mod is None:
        raise OperationError(
            "modifier_not_found",
            "Named modifier does not exist on this object",
            {"modifier_name": name},
        )
    return mod


def semantic(mod: Any) -> str:
    return SEMANTIC_TYPES.get(str(mod.type), str(mod.type).lower())


def axes(values: Any) -> list[str]:
    return [axis for axis, enabled in zip("xyz", values, strict=True) if enabled]


def settings(mod: Any) -> ModifierSettings | None:
    values: dict[str, Any] = {}
    kind = semantic(mod)
    if kind not in FIELDS:
        return None
    for public, native in FIELDS[kind].items():
        value = getattr(mod, native)
        if native in {"use_axis", "use_bisect_axis", "use_bisect_flip_axis"}:
            value = axes(value)
        elif native in {"mirror_object", "target", "object"}:
            value = value.name if value else None
        elif native == "wrap_method":
            value = {v: k for k, v in METHODS.items()}.get(value, str(value).lower())
        elif native in ENUMS:
            value = str(value).lower()
        values[public] = value
    match kind:
        case "corrective_smooth":
            return CorrectiveSmoothSettings.model_validate(values)
        case "triangulate":
            return TriangulateSettings.model_validate(values)
        case "mirror":
            return MirrorSettings.model_validate(values)
        case "subdivision_surface":
            return SubdivisionSettings.model_validate(values)
        case "shrinkwrap":
            projection = None
            if mod.wrap_method == "PROJECT":
                projection = ProjectionSettings.model_validate(
                    dict(
                        axes=axes([getattr(mod, "use_project_" + a) for a in "xyz"]),
                        positive=mod.use_positive_direction,
                        negative=mod.use_negative_direction,
                        cull_face=str(mod.cull_face).lower(),
                        invert_cull=mod.use_invert_cull,
                        limit=mod.project_limit,
                    )
                )
            return ShrinkwrapSettings.model_validate(
                {**values, "projection": projection}
            )
        case "boolean":
            return BooleanSettings.model_validate(
                {**values, "operand_type": str(mod.operand_type).lower()}
            )
        case "solidify":
            return SolidifySettings.model_validate(
                {
                    **values,
                    "mode": "simple" if mod.solidify_mode == "EXTRUDE" else "complex",
                }
            )
    return None


def summary(obj: Any, mod: Any) -> ModifierSummary:
    return ModifierSummary(
        name=mod.name,
        index=list(obj.modifiers).index(mod),
        type=semantic(mod),
        supported=mod.type in SEMANTIC_TYPES,
        enabled_viewport=mod.show_viewport,
        enabled_render=mod.show_render,
        show_in_editmode=mod.show_in_editmode,
        show_on_cage=mod.show_on_cage
        if mod.type in {"MIRROR", "SUBSURF", "SHRINKWRAP", "SOLIDIFY"}
        else None,
        settings=settings(mod),
    )


def inspect(obj: Any) -> ModifierInspectResult:
    if len(obj.modifiers) > MAX_MODIFIERS:
        raise OperationError(
            "invalid_context", "Modifier stack exceeds inspection capacity"
        )
    return ModifierInspectResult(
        object_name=obj.name, modifiers=[summary(obj, m) for m in obj.modifiers]
    )


def value(mod: Any, patch: dict[str, Any], name: str) -> Any:
    return (
        patch[name]
        if name in patch
        else getattr(mod, name)
        if mod is not None
        else DEFAULTS[name]
    )


def reference(owner: Any, name: str, *, require_mesh: bool) -> Any:
    target = scene_object(name)
    if target == owner or (require_mesh and target.type != "MESH"):
        raise OperationError(
            "modifier_dependency_invalid",
            "Modifier reference must be another compatible object",
        )
    return target


def dependencies(
    obj: Any, replacement: Any = None, patch: dict[str, Any] | None = None
) -> list[Any]:
    keys = getattr(obj.data, "shape_keys", None) if obj.data else None
    if (
        obj.animation_data is not None
        or (obj.data and getattr(obj.data, "animation_data", None))
        or (keys is not None and keys.animation_data is not None)
    ):
        raise OperationError(
            "modifier_dependency_invalid",
            "Animated dependency chains are not supported",
        )
    result = [obj.parent] if obj.parent else []
    for item in [*obj.modifiers, *obj.constraints]:
        if getattr(item, "type", None) == "NODES":
            raise OperationError(
                "modifier_dependency_invalid",
                "Geometry Nodes dependencies require a dedicated graph workflow",
            )
        # Object pointers include unsupported existing native constraints/modifiers.
        # Collections and nested constraint targets are bounded explicitly below.
        for prop in item.bl_rna.properties:
            if prop.type == "POINTER":
                target = getattr(item, prop.identifier)
                if item == replacement and patch and prop.identifier in patch:
                    target = patch[prop.identifier]
                if isinstance(target, bpy.types.Object):
                    result.append(target)
                elif isinstance(target, bpy.types.Collection):
                    if len(target.all_objects) > 256:
                        raise OperationError(
                            "modifier_dependency_invalid",
                            "Dependency collection exceeds capacity",
                        )
                    result.extend(target.all_objects)
        if hasattr(item, "targets"):
            result.extend(t.target for t in item.targets if t.target)
    return result


def check_dependencies(owner: Any, mod: Any, patch: dict[str, Any]) -> None:
    roots = dependencies(owner, mod, patch)
    if mod is None:
        roots.extend(v for v in patch.values() if isinstance(v, bpy.types.Object))
    visited: set[int] = set()
    active = {int(owner.as_pointer())}

    def visit(obj: Any) -> None:
        key = int(obj.as_pointer())
        if key in active:
            raise OperationError(
                "modifier_dependency_invalid",
                "Modifier reference would create or retain a dependency cycle",
            )
        if key in visited:
            return
        if len(visited) + len(active) >= 256:
            raise OperationError(
                "modifier_dependency_invalid", "Dependency chain exceeds capacity"
            )
        active.add(key)
        for child in dependencies(obj):
            visit(child)
        active.remove(key)
        visited.add(key)

    for target in roots:
        visit(target)


def data_size(data: Any) -> int:
    return len(data.vertices) + len(data.edges) + len(data.polygons) + len(data.loops)


def check_geometry(data: Any) -> None:
    mesh.check_budget(data_size(data))
    if any(
        not math.isfinite(component)
        for vertex in data.vertices
        for component in vertex.co
    ):
        raise OperationError(
            "invalid_context", "Modifier output contains non-finite coordinates"
        )


def budget(
    obj: Any,
    stack: list[tuple[Any, dict[str, Any]]],
    *,
    render: bool = False,
    source_size: int | None = None,
    strict: bool = False,
) -> None:
    from . import surface_deform

    surface_deform.validate(obj)
    size = data_size(obj.data) if source_size is None else source_size
    constructive_seen = False
    # A triangulated quad adds one edge, one face and two corners. Even without
    # manifold assumptions this is less than twice the original total size.
    quads_only = source_size is None and all(
        len(f.vertices) <= 4 for f in obj.data.polygons
    )
    mesh.check_budget(size)
    for mod, patch in stack:
        if not value(mod, patch, "show_render" if render else "show_viewport"):
            continue
        kind = patch.get("type", mod.type if mod is not None else None)
        if kind == "CORRECTIVE_SMOOTH":
            if constructive_seen or value(mod, patch, "rest_source") != "ORCO":
                raise OperationError(
                    "invalid_context",
                    "Corrective Smooth requires original coordinates "
                    "before all constructive modifiers",
                )
            if (
                value(mod, patch, "vertex_group")
                and value(mod, patch, "vertex_group") not in obj.vertex_groups
            ):
                raise OperationError(
                    "invalid_arguments", "Corrective Smooth mask group is missing"
                )
        if kind not in SAME_TOPOLOGY:
            constructive_seen = True
        if kind == "MULTIRES":
            from .multires import budget as multires_budget

            multires_budget(obj, int(mod.total_levels))
            level = int(
                mod.render_levels if render else max(mod.levels, mod.sculpt_levels)
            )
            size *= 2 * 4**level if level else 1
            quads_only = quads_only or level > 0
        elif kind == "SUBSURF":
            level = value(mod, patch, "render_levels" if render else "levels")
            if level > MAX_SUBDIVISION_LEVEL or value(
                mod, patch, "use_adaptive_subdivision"
            ):
                raise OperationError(
                    "invalid_context",
                    "Subdivision exceeds the supported fixed-level safety policy",
                )
            # Conservative total-element estimate, including the first ngon split.
            size *= 2 * 4**level if level else 1
            quads_only = quads_only or level > 0
        elif kind == "MIRROR":
            size *= 2 ** sum(value(mod, patch, "use_axis")) * 2
        elif kind == "TRIANGULATE":
            # General ngons add at most four times their corner count; account
            # for shared edges/vertices without assuming isolated polygons.
            size *= 2 if quads_only else 5
        elif kind == "SOLIDIFY":
            size *= 6
        elif kind == "BOOLEAN":
            quads_only = False
            if strict and value(mod, patch, "operand_type") != "OBJECT":
                raise OperationError(
                    "invalid_context",
                    "Collection Boolean evaluation is outside the bounded "
                    "object-operand workflow",
                )
            target = value(mod, patch, "object")
            if target is not None:
                # Intersections are data-dependent. This is a preflight estimate,
                # not a mathematical bound on arbitrary Boolean arrangements.
                size = (size + data_size(target.data)) * 8
        elif kind == "SHRINKWRAP":
            if value(mod, patch, "subsurf_levels"):
                raise OperationError(
                    "invalid_context",
                    "Shrinkwrap internal subdivision is outside the supported safety "
                    "policy",
                )
        elif kind not in SAME_TOPOLOGY:
            quads_only = False
            if strict:
                raise OperationError(
                    "invalid_context",
                    "Cannot bound an unsupported topology-generating modifier",
                    {"type": str(kind).lower()},
                )
        mesh.check_budget(size)


def stack(
    obj: Any, mod: Any = None, patch: dict[str, Any] | None = None
) -> list[tuple[Any, dict[str, Any]]]:
    result = [
        (m, patch if m == mod and patch is not None else {}) for m in obj.modifiers
    ]
    if mod is None and patch is not None:
        result.append((None, patch))
    return result


def closed(obj: Any) -> bool:
    with mesh.snapshot(obj) as bm:
        return bool(bm.faces) and all(
            e.is_manifold and e.is_contiguous for e in bm.edges
        )


def validate_state(obj: Any, mod: Any, patch: dict[str, Any]) -> None:
    kind = patch.get("type", mod.type if mod is not None else None)
    if kind == "MIRROR" and not any(value(mod, patch, "use_axis")):
        raise OperationError(
            "invalid_arguments", "At least one mirror axis is required"
        )
    if kind == "SHRINKWRAP":
        if value(mod, patch, "target") is None:
            raise OperationError(
                "modifier_dependency_invalid", "Shrinkwrap requires a target Mesh"
            )
        if value(mod, patch, "wrap_method") not in METHODS.values():
            raise OperationError(
                "modifier_type_unsupported", "This Shrinkwrap method is inspection-only"
            )
        if value(mod, patch, "wrap_method") == "PROJECT" and not (
            value(mod, patch, "use_positive_direction")
            or value(mod, patch, "use_negative_direction")
        ):
            raise OperationError(
                "invalid_arguments", "Project requires a positive or negative direction"
            )
    if kind == "BOOLEAN":
        target = value(mod, patch, "object")
        if value(mod, patch, "operand_type") != "OBJECT":
            raise OperationError(
                "modifier_type_unsupported",
                "Collection Boolean operands are inspection-only",
            )
        if target is None or target.type != "MESH":
            raise OperationError(
                "modifier_dependency_invalid", "Boolean requires an operand Mesh"
            )
        if value(mod, patch, "solver") == "MANIFOLD":
            if (
                not closed(obj)
                or not closed(target)
                or target.modifiers
                or any(m != mod for m in obj.modifiers)
            ):
                raise OperationError(
                    "invalid_context",
                    "Manifold solver requires closed, consistently oriented authored "
                    "meshes without other modifiers",
                )
    if kind == "SOLIDIFY" and value(mod, patch, "solidify_mode") != "EXTRUDE":
        raise OperationError(
            "modifier_type_unsupported", "Complex Solidify settings are inspection-only"
        )
    if (
        kind == "SOLIDIFY"
        and value(mod, patch, "use_rim_only")
        and not value(mod, patch, "use_rim")
    ):
        raise OperationError("invalid_arguments", "rim_only requires rim")
    check_dependencies(obj, mod, patch)


def prepare(
    obj: Any, arguments: ModifierCreateArguments | ModifierConfigureArguments, mod: Any
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for public, native in COMMON.items():
        if public in arguments.model_fields_set:
            patch[native] = getattr(arguments, public)
    supplied = arguments.settings.model_dump(exclude_unset=True)
    for public, native in FIELDS[arguments.type].items():
        if public not in supplied:
            continue
        new = supplied[public]
        if native in {"use_axis", "use_bisect_axis", "use_bisect_flip_axis"}:
            new = tuple(axis in new for axis in "xyz")
        elif native in {"mirror_object", "target", "object"}:
            new = reference(obj, new, require_mesh=native != "mirror_object")
        elif native == "wrap_method":
            new = METHODS[new]
        elif native in ENUMS:
            new = new.upper()
        patch[native] = new
    if supplied.get("clear_mirror_object"):
        patch["mirror_object"] = None
    if "projection" in supplied:
        if value(mod, patch, "wrap_method") != "PROJECT":
            raise OperationError(
                "invalid_arguments", "projection settings require the project method"
            )
        for key, new in supplied["projection"].items():
            if key == "axes":
                patch.update({"use_project_" + a: a in new for a in "xyz"})
            else:
                patch[PROJECTION[key]] = new.upper() if key == "cull_face" else new
    planned = {**patch, "type": TYPES[arguments.type]}
    validate_state(obj, mod, planned)
    budget(obj, stack(obj, mod, planned))
    budget(obj, stack(obj, mod, planned), render=True)
    return patch


def write(mod: Any, patch: dict[str, Any]) -> None:
    for key, new in patch.items():
        setattr(mod, key, new)


def create(obj: Any, arguments: ModifierCreateArguments) -> ModifierSummary:
    mutable(obj)
    if len(obj.modifiers) >= MAX_MODIFIERS:
        raise OperationError("invalid_context", "Modifier stack exceeds capacity")
    if arguments.name is not None and obj.modifiers.get(arguments.name) is not None:
        raise OperationError(
            "invalid_arguments", "Modifier name already exists on this object"
        )
    if any(m.use_pin_to_last for m in obj.modifiers):
        raise OperationError(
            "invalid_context", "Unpin the last modifier before appending to this stack"
        )
    if arguments.name is not None and (
        "\x00" in arguments.name or len(arguments.name.encode("utf-8")) > 63
    ):
        raise OperationError(
            "invalid_arguments", "Modifier name must fit Blender's 63-byte name limit"
        )
    patch = prepare(obj, arguments, None)
    mod = obj.modifiers.new(
        arguments.name or arguments.type.replace("_", " ").title(),
        TYPES[arguments.type],
    )
    try:
        write(mod, patch)
        return summary(obj, mod)
    except BaseException:
        obj.modifiers.remove(mod)
        raise


def configure(obj: Any, arguments: ModifierConfigureArguments) -> ModifierSummary:
    mutable(obj)
    mod = find(obj, arguments.modifier_name)
    if mod.type not in SEMANTIC_TYPES:
        raise OperationError(
            "modifier_type_unsupported", "Existing modifier type is inspection-only"
        )
    if semantic(mod) != arguments.type:
        raise OperationError(
            "invalid_arguments",
            "type must match the existing modifier; configuration cannot change its "
            "type",
        )
    patch = prepare(obj, arguments, mod)
    before = {
        key: tuple(getattr(mod, key))
        if key.startswith("use_") and key.endswith("axis")
        else getattr(mod, key)
        for key in patch
    }
    try:
        write(mod, patch)
        return summary(obj, mod)
    except BaseException:
        write(mod, before)
        raise


def move(obj: Any, name: str, index: int) -> ModifierInspectResult:
    mutable(obj)
    mod = find(obj, name)
    if index >= len(obj.modifiers):
        raise OperationError(
            "invalid_arguments", "index must be less than modifier_count"
        )
    if any(m.type == "MULTIRES" for m in obj.modifiers):
        raise OperationError(
            "invalid_context",
            "Multires stack order is protected by its dedicated workflow",
        )
    previous = list(obj.modifiers).index(mod)
    if previous == index:
        return inspect(obj)
    if any(m.use_pin_to_last for m in obj.modifiers):
        raise OperationError(
            "invalid_context", "Unpin the last modifier before reordering this stack"
        )
    planned = stack(obj)
    planned.insert(index, planned.pop(previous))
    budget(obj, planned)
    budget(obj, planned, render=True)
    obj.modifiers.move(previous, index)
    try:
        if list(obj.modifiers).index(mod) != index:
            raise OperationError(
                "invalid_context",
                "Blender could not preserve the requested stack order",
            )
        return inspect(obj)
    except BaseException:
        obj.modifiers.move(list(obj.modifiers).index(mod), previous)
        raise


def remove(obj: Any, name: str) -> ModifierRemoveResult:
    mutable(obj)
    mod = find(obj, name)
    if mod.type == "MULTIRES":
        raise OperationError(
            "invalid_context",
            "Removing Multires can destroy sculpt displacement and is not exposed",
        )
    planned: list[tuple[Any, dict[str, Any]]] = [
        (m, {}) for m in obj.modifiers if m != mod
    ]
    budget(obj, planned)
    budget(obj, planned, render=True)
    remaining = [
        summary(obj, m).model_copy(update={"index": i})
        for i, m in enumerate(m for m in obj.modifiers if m != mod)
    ]
    result = ModifierRemoveResult(
        object_name=obj.name, removed=name, modifiers=remaining
    )
    obj.modifiers.remove(mod)
    return result


def inspect_evaluated(obj: Any, uv_map: str | None = None) -> EvaluatedMeshSummary:
    from . import surface_basis, surface_deform

    surface_deform.validate(obj)

    if (
        obj.data.is_editmode
        or bpy.app.is_job_running("RENDER")
        or obj.name not in bpy.context.view_layer.objects
    ):
        raise OperationError(
            "invalid_context",
            "Evaluated inspection requires Object Mode data in the current view "
            "layer outside rendering",
        )
    state = inspect(obj)
    check_dependencies(obj, None, {})
    budget(obj, stack(obj), strict=True)
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    authored_copy = None
    try:
        mesh.check_budget(data_size(obj.data))
        mesh.check_budget(data_size(evaluated.data))
        data = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
        if data is None:
            raise OperationError(
                "invalid_context", "Dependency evaluation did not produce a Mesh"
            )
        check_geometry(data)
        bm = bmesh.new()
        try:
            bm.from_mesh(data)
            mesh.refresh(bm)
            evaluated_summary = mesh.summary(obj, bm, data)
        finally:
            bm.free()
        with mesh.snapshot(obj) as authored_bm:
            authored_hash = mesh.summary(obj, authored_bm).geometry_sha256
        authored_copy = obj.data.copy()
        authored_basis = surface_basis.inspect(authored_copy, authored_hash, uv_map)
        evaluated_basis = surface_basis.inspect(
            data, evaluated_summary.geometry_sha256, uv_map
        )
        differences = []
        for modifier in obj.modifiers:
            if modifier.show_viewport != modifier.show_render:
                differences.append(modifier.name + ": visibility")
            if (
                modifier.type in {"SUBSURF", "MULTIRES"}
                and (modifier.show_viewport or modifier.show_render)
                and modifier.levels != modifier.render_levels
            ):
                differences.append(modifier.name + ": subdivision levels")
        values = evaluated_summary.model_dump(
            include={
                "vertex_count",
                "edge_count",
                "face_count",
                "loop_count",
                "bounds_min",
                "bounds_max",
                "manifold_summary",
            }
        )
        return EvaluatedMeshSummary(
            object_name=obj.name,
            source_mesh_name=obj.data.name,
            modifier_count=len(state.modifiers),
            modifier_stack=[
                ModifierStackEntry(name=m.name, type=m.type) for m in state.modifiers
            ],
            authored_basis=authored_basis,
            evaluated_basis=evaluated_basis,
            viewport_render_settings_differences=differences,
            **values,
        )
    finally:
        if authored_copy is not None:
            bpy.data.meshes.remove(authored_copy)
        evaluated.to_mesh_clear()


def material_slots(obj: Any) -> list[tuple[str, Any]]:
    return [(slot.link, slot.material) for slot in obj.material_slots]


def restore_material_slots(obj: Any, slots: list[tuple[str, Any]]) -> None:
    if len(obj.material_slots) != len(slots):
        raise OperationError("modifier_apply_failed", "Material slot count changed")
    for slot, (link, material) in zip(obj.material_slots, slots, strict=True):
        slot.link = link
        if link == "OBJECT":
            slot.material = material
    if material_slots(obj) != slots:
        raise OperationError("modifier_apply_failed", "Material slot state changed")


def apply(obj: Any, name: str) -> ModifierApplyResult:
    mutable(obj)
    if any(m.type == "MULTIRES" for m in obj.modifiers):
        raise OperationError(
            "invalid_context",
            "Applying modifiers on a Multires mesh can destroy sculpt displacement",
        )
    mod = find(obj, name)
    if mod.type not in SEMANTIC_TYPES:
        raise OperationError(
            "modifier_type_unsupported",
            "Application supports only the five typed modifier types",
        )
    original = obj.data
    if original.shape_keys is not None:
        raise OperationError(
            "mesh_has_shape_keys",
            "Modifier application requires a dedicated shape-key-aware workflow",
        )
    if (
        not original.is_editable
        or original.library is not None
        or original.override_library is not None
        or original.animation_data is not None
        or original.has_custom_normals
        or obj.constraints
        or (obj.parent and obj.parent.type in {"ARMATURE", "LATTICE"})
        or any(
            child.parent == obj and child.parent_type in {"VERTEX", "VERTEX_3"}
            for child in bpy.data.objects
        )
    ):
        raise OperationError(
            "invalid_context",
            "Application requires local Mesh data without custom normals, animated "
            "data, constraints or topology-dependent "
            "parenting",
        )
    if not mod.show_viewport:
        raise OperationError(
            "invalid_context",
            "Enable the named modifier in the viewport before applying it",
        )
    validate_state(obj, mod, {})
    budget(obj, [(mod, {})], strict=True)
    remaining: list[tuple[Any, dict[str, Any]]] = [
        (m, {}) for m in obj.modifiers if m != mod
    ]
    # Preflight both the isolated application and the subsequent remaining stack.
    budget(obj, [(mod, {}), *remaining], strict=True)
    budget(obj, [(mod, {}), *remaining], render=True, strict=True)
    staged = None
    candidate = None
    committed = False
    original_slots = material_slots(obj)
    active_material_index = obj.active_material_index
    try:
        staged = obj.copy()
        candidate = original.copy()
        staged.data = candidate
        for other in list(staged.modifiers):
            if other.name != name:
                staged.modifiers.remove(other)
        bpy.context.scene.collection.objects.link(staged)
        with bpy.context.temp_override(
            object=staged,
            active_object=staged,
            selected_objects=[staged],
            selected_editable_objects=[staged],
        ):
            status = bpy.ops.object.modifier_apply(
                modifier=name,
                merge_customdata=False,
                single_user=False,
                use_selected_objects=False,
            )
        if status != {"FINISHED"} or staged.modifiers.get(name) is not None:
            raise OperationError(
                "modifier_apply_failed", "Blender did not apply the named modifier"
            )
        if staged.data != candidate:
            raise OperationError(
                "modifier_apply_failed", "Blender unexpectedly replaced the staged Mesh"
            )
        check_geometry(candidate)
        if (
            [layer.name for layer in candidate.uv_layers]
            != [layer.name for layer in original.uv_layers]
            or list(candidate.materials)[: len(original.materials)]
            != list(original.materials)
            or [group.name for group in staged.vertex_groups]
            != [group.name for group in obj.vertex_groups]
            or material_slots(staged)[: len(original_slots)] != original_slots
        ):
            raise OperationError(
                "modifier_apply_failed",
                "Application unexpectedly changed authored UV maps, material slots "
                "or group definitions",
            )
        budget(obj, remaining, source_size=data_size(candidate))
        budget(obj, remaining, source_size=data_size(candidate), render=True)
        authored = mesh.inspect(staged).model_copy(
            update={"object_name": obj.name, "mesh_users": 1}
        )
        result = ModifierApplyResult(
            object_name=obj.name,
            applied=name,
            mesh=authored,
            modifiers=[
                summary(obj, m).model_copy(update={"index": i})
                for i, (m, _) in enumerate(remaining)
            ],
        )
        obj.data = candidate
        try:
            restore_material_slots(obj, material_slots(staged))
            obj.active_material_index = active_material_index
            obj.modifiers.remove(mod)
        except BaseException:
            obj.data = original
            restore_material_slots(obj, original_slots)
            obj.active_material_index = active_material_index
            raise
        committed = True
        return result
    except OperationError:
        raise
    except Exception as exc:
        raise OperationError(
            "modifier_apply_failed",
            "Native modifier application failed; original geometry and stack were "
            "preserved",
        ) from exc
    finally:
        if staged is not None:
            bpy.data.objects.remove(staged, do_unlink=True)
        if candidate is not None and not committed and candidate.users == 0:
            bpy.data.meshes.remove(candidate)
        if committed and original.users == 0:
            bpy.data.meshes.remove(original)
