"""Authored mask statistics and persistent native face-domain sculpt regions."""

import math
from array import array
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import mesh, modifiers, multires
from .errors import OperationError
from .mesh_selectors import SelectionError, select
from .sculpt_models import (
    MAX_FACE_SETS,
    FaceSetsAssignArguments,
    FaceSetsAssignResult,
    FaceSetsInitializeArguments,
    FaceSetsSummary,
    FaceSetSummary,
    MaskStatistics,
    SculptMaskSummary,
)


def attribute(data: Any, name: str, domain: str, kind: str) -> Any:
    result = data.attributes.get(name)
    if result is not None and (result.domain != domain or result.data_type != kind):
        raise OperationError("invalid_context", f"Malformed native attribute: {name}")
    return result


def mask_inspect(obj: Any) -> SculptMaskSummary:
    if obj.data.is_editmode:
        raise OperationError(
            "invalid_context", "Leave Edit Mode to inspect sculpt masks"
        )
    mesh.check_budget(modifiers.data_size(obj.data))
    attr = attribute(obj.data, ".sculpt_mask", "POINT", "FLOAT")
    count = len(obj.data.vertices)
    values = array("f", [0.0]) * count
    if attr is not None:
        attr.data.foreach_get("value", values)
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise OperationError(
            "invalid_context", "Native mask values must be finite in [0, 1]"
        )
    mod = multires.find(obj, required=False)
    level = int(mod.sculpt_levels) if mod and not mod.use_sculpt_base_mesh else 0
    return SculptMaskSummary(
        object_name=obj.name,
        multires_level=level,
        effective_values_available=not bool(mod and mod.total_levels),
        base_mesh=MaskStatistics(
            present=attr is not None,
            sample_count=count,
            min=min(values, default=0),
            max=max(values, default=0),
            mean=math.fsum(values) / count if count else 0,
            masked_fraction=sum(v > 0.001 for v in values) / count if count else 0,
            fully_masked_fraction=sum(v >= 0.999 for v in values) / count
            if count
            else 0,
            unmasked_fraction=sum(v <= 0.001 for v in values) / count if count else 0,
        ),
    )


def face_values(obj: Any) -> tuple[bool, list[int]]:
    if obj.data.is_editmode:
        raise OperationError("invalid_context", "Leave Edit Mode to inspect Face Sets")
    mesh.check_budget(modifiers.data_size(obj.data))
    attr = attribute(obj.data, ".sculpt_face_set", "FACE", "INT")
    values = (
        [int(v.value) for v in attr.data]
        if attr is not None
        else [1] * len(obj.data.polygons)
    )
    if any(v < 0 for v in values):
        raise OperationError(
            "invalid_context", "Negative Face Set IDs require explicit repair"
        )
    if len(set(values) - {0}) > MAX_FACE_SETS:
        raise OperationError(
            "invalid_context", "Face Set summary supports at most 256 sets"
        )
    return attr is not None, values


def face_sets_inspect(obj: Any) -> FaceSetsSummary:
    authored, values = face_values(obj)
    groups: dict[int, list[Any]] = {}
    for face, identity in zip(obj.data.polygons, values, strict=True):
        if identity:
            groups.setdefault(identity, []).append(face)
    summaries = []
    for identity, faces in sorted(groups.items()):
        points = [obj.data.vertices[i].co for face in faces for i in face.vertices]
        summaries.append(
            FaceSetSummary(
                id=identity,
                face_count=len(faces),
                hidden_face_count=sum(face.hide for face in faces),
                area=math.fsum(face.area for face in faces),
                bounds_min=[min(p[i] for p in points) for i in range(3)],
                bounds_max=[max(p[i] for p in points) for i in range(3)],
            )
        )
    return FaceSetsSummary(
        object_name=obj.name,
        authored=authored,
        face_sets=summaries,
        unassigned_face_count=values.count(0),
    )


def writable(obj: Any) -> None:
    mod = multires.find(obj, required=False)
    multires.editable(obj, mod)
    multires.budget(obj, int(mod.total_levels) if mod else 0)
    if obj.use_dynamic_topology_sculpting:
        raise OperationError("invalid_context", "Dyntopo requires a separate workflow")


def write_face_sets(obj: Any, values: list[int]) -> bool:
    if len(set(values) - {0}) > MAX_FACE_SETS:
        raise OperationError("invalid_context", "At most 256 Face Sets are supported")
    # Copy the complete Mesh, including native Multires custom data. Never
    # reconstruct it through BMesh, which would discard displacement/mask grids.
    original = obj.data
    staged = original.copy()
    shared = sum(o.data == original for o in bpy.data.objects if o.type == "MESH") > 1
    try:
        attr = attribute(staged, ".sculpt_face_set", "FACE", "INT")
        if attr is None:
            attr = staged.attributes.new(".sculpt_face_set", "INT", "FACE")
        attr.data.foreach_set("value", values)
        staged.update()
        obj.data = staged
        bpy.context.view_layer.update()
    except BaseException:
        obj.data = original
        bpy.data.meshes.remove(staged)
        raise
    if original.users == 0:
        name = original.name
        bpy.data.meshes.remove(original)
        staged.name = name
    return bool(shared)


def face_sets_assign(
    obj: Any, arguments: FaceSetsAssignArguments
) -> FaceSetsAssignResult:
    writable(obj)
    _, values = face_values(obj)
    with mesh.snapshot(obj) as bm:
        try:
            indices = [f.index for f in select(bm, arguments.selector, obj)]
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
    if not indices:
        raise OperationError(
            "invalid_arguments", "Face Set assignment selected no faces"
        )
    identity = arguments.face_set_id or max(1, max(values, default=1)) + 1
    if identity > 2_147_483_647:
        raise OperationError("invalid_context", "No positive Face Set ID remains")
    for index in indices:
        values[index] = identity
    isolated = write_face_sets(obj, values)
    return FaceSetsAssignResult(
        assigned_id=identity,
        assigned_face_count=len(indices),
        mesh_isolated=isolated,
        summary=face_sets_inspect(obj),
    )


def face_sets_initialize(
    obj: Any, arguments: FaceSetsInitializeArguments
) -> FaceSetsSummary:
    writable(obj)
    _, values = face_values(obj)
    # Deterministic authored-topology equivalents of Blender's initialization
    # modes. Hidden faces retain their IDs and form boundaries for flood fill.
    with mesh.snapshot(obj) as bm:
        visible = {f.index for f in bm.faces if not f.hide}
        if arguments.mode == "materials":
            for index in visible:
                values[index] = int(bm.faces[index].material_index) + 1
        else:
            identity = max((values[f.index] for f in bm.faces if f.hide), default=0)
            while visible:
                identity += 1
                if identity > 2_147_483_647:
                    raise OperationError(
                        "invalid_context", "No positive Face Set ID remains"
                    )
                stack = [min(visible)]
                visible.remove(stack[0])
                while stack:
                    index = stack.pop()
                    values[index] = identity
                    for edge in bm.faces[index].edges:
                        if arguments.mode == "uv_seams" and edge.seam:
                            continue
                        if arguments.mode == "sharp_edges" and not edge.smooth:
                            continue
                        for neighbor in edge.link_faces:
                            if neighbor.index in visible:
                                visible.remove(neighbor.index)
                                stack.append(neighbor.index)
    write_face_sets(obj, values)
    return face_sets_inspect(obj)
