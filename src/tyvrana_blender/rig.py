"""Declarative native FK armatures and transactional, bounded skin binding."""

import hashlib
import json
import math
import time
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]

from . import modifiers, retopo_geometry
from .operations import OperationError
from .rig_models import (
    MAX_BONES,
    MAX_VERTICES,
    MAX_WEIGHT_WORK,
    ArmatureBindArguments,
    ArmatureCreateArguments,
    ArmaturePoseArguments,
    ArmatureSummary,
    BindingSummary,
    BoneSummary,
    DeformationInspectArguments,
    DeformationSample,
    DeformationSummary,
    EnvelopeWeights,
    MeshDeformation,
)

KEY = "tyvrana_armature_binding"


def fail(message: str) -> None:
    raise OperationError("rig_context_invalid", message)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def idle() -> None:
    if bpy.context.mode != "OBJECT" or bpy.app.is_job_running("RENDER"):
        fail("Rig operations require Object Mode outside rendering")


def armature(name: str, *, edit: bool = False) -> Any:
    idle()
    obj = bpy.context.scene.objects.get(name)
    if obj is None or obj.type != "ARMATURE":
        fail(f'Armature object "{name}" does not exist in the current scene')
    if not 0 < len(obj.data.bones) <= MAX_BONES:
        fail("Armature must contain 1..128 bones")
    retopo_geometry.matrix(obj)
    if edit and (
        obj.library
        or obj.override_library
        or obj.data.library
        or obj.data.users != 1
        or obj.animation_data
        or obj.data.animation_data
        or obj.constraints
        or any(b.constraints for b in obj.pose.bones)
    ):
        fail("Preserve shared/linked rigs, animation, drivers and constraints")
    return obj


def rest_signature(obj: Any) -> str:
    return digest(
        [
            (
                b.name,
                b.parent.name if b.parent else None,
                b.use_connect,
                b.use_deform,
                [list(row) for row in b.matrix_local],
                b.length,
                b.head_radius,
                b.tail_radius,
                b.envelope_distance,
            )
            for b in obj.data.bones
        ]
    )


def inspect(
    obj: Any, names: list[str] | None = None, limit: int = 16
) -> ArmatureSummary:
    bones = list(obj.data.bones)
    if names is not None:
        if len(set(names)) != len(names) or any(n not in obj.data.bones for n in names):
            fail("Requested bones must be unique and present")
        bones = [obj.data.bones[n] for n in names]
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    world = evaluated.matrix_world
    samples = []
    for bone in bones[:limit]:
        pose = obj.pose.bones[bone.name]
        current = evaluated.pose.bones[bone.name]
        loc, rotation, scale = pose.matrix_basis.decompose()
        samples.append(
            BoneSummary(
                name=bone.name,
                parent=bone.parent.name if bone.parent else None,
                connected=bone.use_connect,
                deform=bone.use_deform,
                rest_head_world=list(world @ bone.head_local),
                rest_tail_world=list(world @ bone.tail_local),
                posed_head_world=list(world @ current.head),
                posed_tail_world=list(world @ current.tail),
                location=list(loc),
                rotation_mode=pose.rotation_mode,
                rotation_quaternion=list(rotation),
                scale=list(scale),
            )
        )
    bound = [
        o
        for o in bpy.context.scene.objects
        if o.type == "MESH"
        and KEY in o
        and any(m.type == "ARMATURE" and m.object == obj for m in o.modifiers)
    ]
    if len(bound) > 16:
        fail("Armature summary is limited to 16 owned bound meshes")
    return ArmatureSummary(
        object_name=obj.name,
        bone_count=len(obj.data.bones),
        root_count=sum(b.parent is None for b in obj.data.bones),
        pose_position=obj.data.pose_position.lower(),
        rest_sha256=rest_signature(obj),
        pose_sha256=digest(
            [(p.name, [list(row) for row in p.matrix_basis]) for p in obj.pose.bones]
        ),
        bones=samples,
        bones_truncated=len(bones) > limit,
        bindings=[binding_summary(o) for o in bound],
    )


def create(args: ArmatureCreateArguments) -> ArmatureSummary:
    idle()
    if bpy.data.objects.get(args.name) is not None:
        fail("Choose an unused armature object name")
    active = bpy.context.view_layer.objects.active
    selected = list(bpy.context.selected_objects)
    data = bpy.data.armatures.new(args.name)
    obj = None
    try:
        obj = bpy.data.objects.new(args.name, data)
        bpy.context.scene.collection.objects.link(obj)
        if obj.name != args.name:
            fail("Armature name exceeds the native name capacity")
        for old in selected:
            old.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        for item in args.bones:
            bone = data.edit_bones.new(item.name)
            if bone.name != item.name:
                fail("Bone name exceeds native capacity")
            bone.head = item.head
            bone.tail = item.tail
            bone.roll = item.roll
            bone.use_deform = item.deform
            bone.head_radius = item.head_radius
            bone.tail_radius = item.tail_radius
            bone.envelope_distance = item.envelope_distance
        for item in args.bones:
            if item.parent is not None:
                bone = data.edit_bones[item.name]
                bone.parent = data.edit_bones[item.parent]
                bone.use_connect = item.connected
        bpy.ops.object.mode_set(mode="OBJECT")
        obj.show_in_front = True
        data.display_type = "OCTAHEDRAL"
        result = inspect(obj)
    except BaseException:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            bpy.data.armatures.remove(data)
        raise
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for current in list(bpy.context.selected_objects):
            current.select_set(False)
        for old in selected:
            old.select_set(True)
        bpy.context.view_layer.objects.active = active
    return result


def binding_modifier(obj: Any) -> Any:
    if KEY not in obj:
        fail("Mesh has no owned armature binding")
    try:
        metadata = json.loads(obj[KEY])
        mod = obj.modifiers.get(metadata["modifier"])
    except (ValueError, TypeError, KeyError):
        fail("Armature binding metadata is invalid")
    if mod is None or mod.type != "ARMATURE" or mod.object is None:
        fail("Armature binding modifier or target was removed")
    if (
        not mod.use_vertex_groups
        or mod.use_bone_envelopes
        or mod.use_multi_modifier
        or mod.vertex_group
    ):
        fail("Owned Armature modifier settings were changed externally")
    return mod


def binding_summary(obj: Any) -> BindingSummary:
    mod = binding_modifier(obj)
    names = {b.name for b in mod.object.data.bones if b.use_deform}
    indices = {g.index: g.name for g in obj.vertex_groups if g.name in names}
    if len(obj.data.vertices) > MAX_VERTICES or len(names) > MAX_BONES:
        fail("Binding exceeds inspection limits")
    counts = dict.fromkeys(sorted(names), 0)
    rows = []
    sums = []
    maximum = 0
    for vertex in obj.data.vertices:
        row = sorted(
            (indices[g.group], g.weight)
            for g in vertex.groups
            if g.group in indices and g.weight > 0
        )
        if any(not math.isfinite(w) or w > 1 for _, w in row):
            fail("Binding contains invalid native weights")
        rows.append(row)
        sums.append(sum(w for _, w in row))
        maximum = max(maximum, len(row))
        for name, _ in row:
            counts[name] += 1
    weighted = sum(s > 0 for s in sums)
    return BindingSummary(
        object_name=obj.name,
        armature_object=mod.object.name,
        modifier_name=mod.name,
        modifier_index=list(obj.modifiers).index(mod),
        preserve_volume=mod.use_deform_preserve_volume,
        vertex_count=len(rows),
        weighted_vertex_count=weighted,
        unweighted_vertex_count=len(rows) - weighted,
        maximum_influences=maximum,
        weight_sum_min=min(sums, default=0),
        weight_sum_max=max(sums, default=0),
        weights_sha256=digest(rows),
        bone_vertex_counts=counts,
    )


def weights(
    obj: Any, rig: Any, args: ArmatureBindArguments
) -> list[list[tuple[str, float]]]:
    names = {b.name for b in rig.data.bones if b.use_deform}
    rows: list[list[tuple[str, float]]] = [[] for _ in obj.data.vertices]
    if isinstance(args.weights, EnvelopeWeights):
        chosen = args.weights.bones
        if any(name not in names for name in chosen):
            fail("Envelope weights require existing deform bones")
        if len(chosen) * len(rows) > MAX_WEIGHT_WORK:
            fail("Envelope evaluation exceeds 1000000 vertex/bone pairs")
        transform = rig.matrix_world.inverted() @ obj.matrix_world
        bones = [rig.data.bones[name] for name in chosen]
        for vertex in obj.data.vertices:
            point = transform @ vertex.co
            scores = [(b.name, float(b.evaluate_envelope(point))) for b in bones]
            scores = sorted(
                [(n, w) for n, w in scores if w > 1e-8], key=lambda v: (-v[1], v[0])
            )[: args.weights.max_influences]
            total = sum(w for _, w in scores)
            rows[vertex.index] = [(n, w / total) for n, w in scores] if total else []
    else:
        for item in args.weights.vertices:
            if item.vertex >= len(rows) or any(
                i.bone not in names for i in item.influences
            ):
                fail("Explicit weights reference a missing vertex or deform bone")
            total = sum(i.weight for i in item.influences)
            rows[item.vertex] = [(i.bone, i.weight / total) for i in item.influences]
    if not args.allow_unweighted and any(not row for row in rows):
        fail(
            "Binding leaves unweighted vertices; correct weights/envelopes "
            "or explicitly allow it"
        )
    return rows


def bind(args: ArmatureBindArguments) -> BindingSummary:
    start = time.perf_counter()
    rig = armature(args.armature_object, edit=True)
    obj = modifiers.object_mesh(args.object_name)
    if (
        obj.mode != "OBJECT"
        or obj.library
        or obj.override_library
        or obj.data.library
        or obj.data.users != 1
        or obj.data.shape_keys
        or not 0 < len(obj.data.vertices) <= MAX_VERTICES
    ):
        fail("Bind an exclusive local Mesh with 1..100000 vertices and no shape keys")
    # Prevent a rig->mesh dependency becoming a cycle when the mesh is bound.
    retopo_geometry.graph(rig, obj)
    old_mod = binding_modifier(obj) if KEY in obj else None
    if any(m.type == "ARMATURE" and m != old_mod for m in obj.modifiers):
        fail("Preserve existing unowned Armature modifiers")
    if old_mod is not None and old_mod.object != rig:
        fail("Rebinding must preserve the current armature target")
    index_limit = len(obj.modifiers) - (old_mod is not None)
    if args.modifier_index > index_limit or (
        old_mod is None and len(obj.modifiers) >= 128
    ):
        fail("Modifier insertion index exceeds the current stack")
    names = [b.name for b in rig.data.bones if b.use_deform]
    if not names:
        fail("Armature has no deform bones")
    if old_mod is None and any(n in obj.vertex_groups for n in names):
        fail(
            "Preserve existing bone-named vertex groups; binding requires unused names"
        )
    rows = weights(obj, rig, args)
    old_data = obj.data
    old_meta = obj.get(KEY)
    old_index = list(obj.modifiers).index(old_mod) if old_mod else None
    old_volume = old_mod.use_deform_preserve_volume if old_mod else None
    data = old_data.copy()
    staged = None
    added = []
    mod = old_mod
    published = False
    try:
        staged = bpy.data.objects.new("Skin binding staging", data)
        for group in obj.vertex_groups:
            staged.vertex_groups.new(name=group.name)
        for name in names:
            group = staged.vertex_groups.get(name) or staged.vertex_groups.new(
                name=name
            )
            group.remove(list(range(len(rows))))
        for vertex, row in enumerate(rows):
            for name, weight in row:
                staged.vertex_groups[name].add([vertex], weight, "REPLACE")
        for name in names:
            if name not in obj.vertex_groups:
                added.append(obj.vertex_groups.new(name=name))
        if mod is None:
            mod = obj.modifiers.new("Armature Deform", "ARMATURE")
        mod.object = rig
        mod.use_vertex_groups = True
        mod.use_bone_envelopes = False
        mod.use_deform_preserve_volume = args.preserve_volume
        mod.use_multi_modifier = False
        mod.vertex_group = ""
        obj.modifiers.move(list(obj.modifiers).index(mod), args.modifier_index)
        obj.data = data
        published = True
        obj[KEY] = json.dumps({"modifier": mod.name})
        bpy.context.view_layer.update()
        result = binding_summary(obj)
    except BaseException:
        if published:
            obj.data = old_data
        for group in reversed(added):
            obj.vertex_groups.remove(group)
        if old_mod is None and mod is not None:
            obj.modifiers.remove(mod)
        elif old_mod is not None:
            old_mod.use_deform_preserve_volume = old_volume
            obj.modifiers.move(list(obj.modifiers).index(old_mod), old_index)
        if old_meta is None:
            if KEY in obj:
                del obj[KEY]
        else:
            obj[KEY] = old_meta
        raise
    finally:
        if staged is not None:
            bpy.data.objects.remove(staged, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
    if old_data.users == 0:
        name = old_data.name
        bpy.data.meshes.remove(old_data)
        data.name = name
    return result.model_copy(update={"binding_seconds": time.perf_counter() - start})


def pose(args: ArmaturePoseArguments) -> ArmatureSummary:
    obj = armature(args.object_name, edit=True)
    if obj.data.pose_position != "POSE":
        fail("Armature must use Pose position for pose updates")
    names = [b.name for b in args.bones]
    if any(n not in obj.pose.bones for n in names):
        fail("Pose update references a missing bone")
    affected = (
        list(obj.pose.bones) if args.reset else [obj.pose.bones[n] for n in names]
    )
    for item in args.bones:
        p = obj.pose.bones[item.name]
        if p.bone.use_connect and any(item.location):
            fail("Connected bone heads cannot receive translation channels")
    if any(
        any(p.lock_location) or any(p.lock_rotation) or any(p.lock_scale)
        for p in affected
    ):
        fail("Preserve locked pose channels")
    saved = [
        (
            p,
            p.rotation_mode,
            p.location.copy(),
            p.rotation_euler.copy(),
            p.rotation_quaternion.copy(),
            tuple(p.rotation_axis_angle),
            p.scale.copy(),
        )
        for p in affected
    ]
    try:
        if args.reset:
            for p in affected:
                p.matrix_basis = Matrix.Identity(4)
        for item in args.bones:
            p = obj.pose.bones[item.name]
            p.rotation_mode = "XYZ"
            p.location = item.location
            p.rotation_euler = item.rotation
            p.scale = item.scale
        bpy.context.view_layer.update()
        return inspect(obj, names if not args.reset else None)
    except BaseException:
        for p, mode, loc, euler, quat, axis, scale in saved:
            p.rotation_mode = mode
            p.location = loc
            p.rotation_euler = euler
            p.rotation_quaternion = quat
            p.rotation_axis_angle = axis
            p.scale = scale
        bpy.context.view_layer.update()
        raise


def snapshot(obj: Any, graph: Any) -> tuple[list[Any], list[Any], list[Any]]:
    with retopo_geometry.evaluated_mesh(obj, graph) as (data, transform):
        data.calc_loop_triangles()
        if len(data.vertices) > 250_000 or len(data.loop_triangles) > 500_000:
            fail("Deformation snapshot exceeds 250000 vertices/500000 triangles")
        return (
            [transform @ v.co for v in data.vertices],
            [tuple(e.vertices) for e in data.edges],
            [tuple(t.vertices) for t in data.loop_triangles],
        )


def comparison(name: str, rest: Any, posed: Any, limit: int) -> MeshDeformation:
    a, edges, triangles = rest
    b, pe, pt = posed
    if len(a) != len(b) or edges != pe or triangles != pt or not a or not triangles:
        fail("Rest and posed evaluated topology differs or has no surface")
    distances = [(q - p).length for p, q in zip(a, b, strict=True)]
    edge_ratios = [
        (b[j] - b[i]).length / (a[j] - a[i]).length
        for i, j in edges
        if (a[j] - a[i]).length > 1e-10
    ]
    ratios = []
    for i, j, k in triangles:
        area = (a[j] - a[i]).cross(a[k] - a[i]).length
        if area > 1e-16:
            ratios.append((b[j] - b[i]).cross(b[k] - b[i]).length / area)
    if not ratios or not edge_ratios:
        fail("Rest mesh is degenerate")
    indices = sorted(range(len(a)), key=lambda i: (-distances[i], i))[:limit]
    return MeshDeformation(
        object_name=name,
        vertex_count=len(a),
        triangle_count=len(triangles),
        topology_sha256=digest([edges, triangles]),
        rest_geometry_sha256=digest([list(v) for v in a]),
        posed_geometry_sha256=digest([list(v) for v in b]),
        changed_vertex_count=sum(d > 1e-6 for d in distances),
        displacement_max=max(distances),
        displacement_mean=sum(distances) / len(distances),
        rest_bounds_min=[min(v[i] for v in a) for i in range(3)],
        rest_bounds_max=[max(v[i] for v in a) for i in range(3)],
        posed_bounds_min=[min(v[i] for v in b) for i in range(3)],
        posed_bounds_max=[max(v[i] for v in b) for i in range(3)],
        edge_length_ratio_min=min(edge_ratios),
        edge_length_ratio_max=max(edge_ratios),
        triangle_area_ratio_min=min(ratios),
        triangle_area_ratio_max=max(ratios),
        collapsed_triangle_count=sum(r < 0.01 for r in ratios),
        samples=[
            DeformationSample(
                vertex=i,
                rest_world=list(a[i]),
                posed_world=list(b[i]),
                distance=distances[i],
            )
            for i in indices
        ],
    )


def deformation(args: DeformationInspectArguments) -> DeformationSummary:
    start = time.perf_counter()
    rig = armature(args.armature_object, edit=True)
    objects = [modifiers.object_mesh(n) for n in args.objects]
    for obj in objects:
        if binding_modifier(obj).object != rig:
            fail("Every inspected mesh must have an owned binding to this armature")
    graph = retopo_geometry.graph(rig)
    current = rig.data.pose_position
    posed = [snapshot(obj, graph) for obj in objects]
    try:
        rig.data.pose_position = "REST"
        bpy.context.view_layer.update()
        rest = [snapshot(obj, bpy.context.evaluated_depsgraph_get()) for obj in objects]
        results = [
            comparison(obj.name, a, b, args.sample_limit)
            for obj, a, b in zip(objects, rest, posed, strict=True)
        ]
    finally:
        rig.data.pose_position = current
        bpy.context.view_layer.update()
    return DeformationSummary(
        armature_object=rig.name,
        meshes=results,
        restored_pose_position=current.lower(),
        inspection_seconds=time.perf_counter() - start,
    )
