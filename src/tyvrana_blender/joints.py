"""Native rest edits and owned local joint limits, separate from control rigs."""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from .inspection import page
from .joint_models import (
    MAX_XZ,
    MAX_Y,
    JointConfigureArguments,
    JointEvaluation,
    JointFrame,
    JointLimits,
    StructuralPose,
    StructuralRest,
    StructuralSegment,
    StructureInspectArguments,
    StructureSummary,
    orthonormal_axes,
)
from .operations import OperationError
from .rig_models import (
    ArmatureCreateArguments,
    ArmatureRestArguments,
    ArmatureSummary,
    RestBone,
)

KEY = "tyvrana_joint_limits"
CONSTRAINT_NAME = "Tyvrana Joint Limits"


def fail(message: str) -> None:
    raise OperationError("joint_context_invalid", message)


def model_limits(pose: Any) -> JointLimits | None:
    raw = pose.get(KEY)
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > 2048:
        fail(f'Joint "{pose.name}" ownership metadata is invalid')
    try:
        return JointLimits.model_validate_json(raw)
    except ValueError:
        fail(f'Joint "{pose.name}" ownership metadata is invalid')
    return None


def constraint(pose: Any) -> Any:
    c = pose.constraints.get(CONSTRAINT_NAME)
    if model_limits(pose) is None:
        if c is not None:
            fail(f'Joint "{pose.name}" has an unowned reserved constraint name')
        return None
    if c is None or c.type != "LIMIT_ROTATION":
        fail(f'Joint "{pose.name}" owned rotation constraint is missing or replaced')
    return c


def validate_constraints(obj: Any) -> None:
    for p in obj.pose.bones:
        limits = model_limits(p)
        c = constraint(p)
        if len(p.constraints) != int(c is not None):
            fail(f'Bone "{p.name}" has unsupported external constraints')
        if c is None:
            continue
        if (
            c.owner_space != "LOCAL"
            or c.euler_order != "XYZ"
            or c.mute
            or abs(c.influence - 1) > 1e-6
            or c.use_legacy_behavior
            or c.use_transform_limit
            or p.rotation_mode != "XYZ"
            or not c.is_valid
        ):
            fail(
                f'Joint "{p.name}" native settings changed; reconfigure '
                f"its owned limits"
            )
        assert limits is not None
        for axis in ("x", "y", "z"):
            span = getattr(limits, axis)
            if bool(getattr(c, "use_limit_" + axis)) != (span is not None):
                fail(f'Joint "{p.name}" axis enable state changed; reconfigure limits')
            if span and (
                abs(getattr(c, "min_" + axis) - span.minimum) > 1e-6
                or abs(getattr(c, "max_" + axis) - span.maximum) > 1e-6
            ):
                fail(f'Joint "{p.name}" range changed; reconfigure limits')


def editable(obj: Any, *, constraints: bool = True) -> None:
    from .blender import data_mutation_context, main_thread
    from .organization import object_editable

    main_thread()
    data_mutation_context()
    object_editable(obj)
    if (
        obj.data.library
        or obj.data.override_library
        or not obj.data.is_editable
        or obj.data.users != 1
        or obj.animation_data
        or obj.data.animation_data
        or obj.constraints
    ):
        fail(
            "Structural mutation requires exclusive local data without "
            "animation, drivers or object constraints"
        )
    if constraints:
        validate_constraints(obj)


def positive_uniform(matrix: Any) -> None:
    columns = [matrix.to_3x3().col[i] for i in range(3)]
    lengths = [v.length for v in columns]
    if (
        min(lengths) < 1e-8
        or matrix.to_3x3().determinant() <= 0
        or max(lengths) - min(lengths) > 1e-5 * max(lengths)
        or any(
            abs(columns[i].dot(columns[j])) > 1e-5 * lengths[i] * lengths[j]
            for i in range(3)
            for j in range(i)
        )
    ):
        fail(
            "World joint frames require a positive uniform object scale "
            "without shear; use armature-space inspection"
        )


def resolve_rest(spec: RestBone, world: Any, space: str) -> RestBone:
    from .references import resolve

    inv = world.inverted()

    def endpoint(value: Any) -> list[float]:
        if isinstance(value, list):
            point = inv @ Vector(value) if space == "world" else Vector(value)
        else:
            point = inv @ resolve(value)
        return list(point)

    head, tail = endpoint(spec.head), endpoint(spec.tail)
    values = spec.model_dump(mode="python", exclude_unset=True)
    values.update(head=head, tail=tail)
    if spec.x_reference is not None:
        reference = (
            inv.to_3x3() @ Vector(spec.x_reference)
            if space == "world"
            else Vector(spec.x_reference)
        )
        values["x_reference"] = list(reference)
    try:
        return RestBone.model_validate(values)
    except ValueError as exc:
        fail(f'Resolved geometry/frame for "{spec.name}" is invalid: {str(exc)[:350]}')
    raise AssertionError("unreachable")


def prepare_create(args: ArmatureCreateArguments) -> ArmatureCreateArguments:
    values = args.model_dump(mode="python", exclude={"bones"})
    values["bones"] = [
        resolve_rest(b, Matrix.Identity(4), args.space) for b in args.bones
    ]
    return ArmatureCreateArguments.model_validate(values)


def orient_bone(bone: Any, spec: RestBone) -> None:
    bone.head = spec.head
    bone.tail = spec.tail
    if spec.x_reference is not None:
        _, _, z = orthonormal_axes(list(bone.tail - bone.head), spec.x_reference)
        bone.align_roll(Vector(z))
    else:
        bone.roll = spec.roll
    bone.use_deform = spec.deform
    bone.head_radius = spec.head_radius
    bone.tail_radius = spec.tail_radius
    bone.envelope_distance = spec.envelope_distance


def set_limits(p: Any, limits: JointLimits | None) -> None:
    c = constraint(p)
    if limits is None:
        if c is not None:
            p.constraints.remove(c)
        if KEY in p:
            del p[KEY]
        return
    if c is None:
        c = p.constraints.new("LIMIT_ROTATION")
        c.name = CONSTRAINT_NAME
    p.rotation_mode = "XYZ"
    c.owner_space = "LOCAL"
    c.euler_order = "XYZ"
    c.influence = 1.0
    c.mute = False
    c.use_legacy_behavior = False
    c.use_transform_limit = False
    for axis in ("x", "y", "z"):
        span = getattr(limits, axis)
        setattr(c, "use_limit_" + axis, span is not None)
        setattr(c, "min_" + axis, span.minimum if span else 0)
        setattr(c, "max_" + axis, span.maximum if span else 0)
    p[KEY] = limits.model_dump_json()


def initialize(obj: Any, bones: list[RestBone]) -> None:
    for spec in bones:
        if spec.limits is not None:
            set_limits(obj.pose.bones[spec.name], spec.limits)
    bpy.context.view_layer.update()
    validate_constraints(obj)


def principal(rotation: Any) -> None:
    if (
        abs(rotation[0]) > MAX_XZ
        or abs(rotation[2]) > MAX_XZ
        or abs(rotation[1]) > MAX_Y
    ):
        fail(
            "Constrained XYZ poses require |X,Z| <= pi-0.0001 and |Y| <= "
            "pi/2-0.0001 radians; multi-turn and gimbal singularities are "
            "outside this contract"
        )


def pose_guard(obj: Any, bones: Any, reset: bool) -> None:
    if not any(KEY in p for p in obj.pose.bones):
        return
    for p in obj.pose.bones:
        if not reset and any(abs(v - 1) > 1e-6 for v in p.scale):
            fail("Constrained structures require unit pose scale; reset the pose first")
        if KEY in p and not reset:
            principal(p.rotation_euler)
    for item in bones:
        if any(abs(v - 1) > 1e-6 for v in item.scale):
            fail("Constrained structures require unit pose scale")
        if KEY in obj.pose.bones[item.name]:
            if any(item.location):
                fail("Joint centers are fixed; constrained bones accept rotation only")
            principal(item.rotation)


def evaluation(obj: Any, evaluated: Any, name: str) -> JointEvaluation:
    p = obj.pose.bones[name]
    current = evaluated.pose.bones[name]
    local = evaluated.convert_space(
        pose_bone=current, matrix=current.matrix, from_space="POSE", to_space="LOCAL"
    )
    requested = p.matrix_basis.to_euler("XYZ")
    angles = local.to_euler("XYZ")
    return JointEvaluation(
        requested_rotation=list(requested),
        evaluated_rotation=list(angles),
        changed_by_constraint=max(abs(angles[i] - requested[i]) for i in range(3))
        > 1e-5,
    )


def configure(args: JointConfigureArguments) -> ArmatureSummary:
    from . import rig

    obj = rig.armature(args.object_name)
    editable(obj, constraints=False)
    if any(j.name not in obj.pose.bones for j in args.joints):
        fail("Joint update references a missing bone")
    chosen = {j.name for j in args.joints}
    resulting = {p.name for p in obj.pose.bones if KEY in p}
    for item in args.joints:
        if item.limits is None:
            resulting.discard(item.name)
        else:
            resulting.add(item.name)
    if resulting and any(abs(v - 1) > 1e-6 for p in obj.pose.bones for v in p.scale):
        fail("Constrained structures require unit pose scale; reset the pose first")
    for p in obj.pose.bones:
        c = constraint(p)
        if len(p.constraints) != int(c is not None):
            fail(f'Bone "{p.name}" has unsupported external constraints')
        if p.name in chosen:
            if any(p.location) or any(abs(v - 1) > 1e-6 for v in p.scale):
                fail(
                    "Configure fixed joint centers with zero translation and unit scale"
                )
            if (
                p.rotation_mode != "XYZ"
                and max(
                    abs(v - (1 if i == j else 0))
                    for i, row in enumerate(p.matrix_basis)
                    for j, v in enumerate(row)
                )
                > 1e-6
            ):
                fail("Set this bone to an XYZ pose before configuring limits")
            principal(p.matrix_basis.to_euler("XYZ"))
    # Retain full owned constraint state for repair and rollback, not just metadata.
    before = {
        p.name: (p.rotation_mode, p.get(KEY), native_limit_state(p))
        for p in obj.pose.bones
        if p.name in chosen
    }
    try:
        for item in args.joints:
            set_limits(obj.pose.bones[item.name], item.limits)
        bpy.context.view_layer.update()
        validate_constraints(obj)
        return rig.inspect(obj, [j.name for j in args.joints], args.sample_limit)
    except Exception:
        for name, (mode, raw, state) in before.items():
            restore_limit_state(obj.pose.bones[name], raw, state)
            obj.pose.bones[name].rotation_mode = mode
        bpy.context.view_layer.update()
        raise


LIMIT_FIELDS = (
    "owner_space",
    "euler_order",
    "influence",
    "mute",
    "use_legacy_behavior",
    "use_transform_limit",
    "use_limit_x",
    "use_limit_y",
    "use_limit_z",
    "min_x",
    "max_x",
    "min_y",
    "max_y",
    "min_z",
    "max_z",
)


def native_limit_state(p: Any) -> dict[str, Any] | None:
    c = constraint(p)
    return {k: getattr(c, k) for k in LIMIT_FIELDS} if c else None


def restore_limit_state(p: Any, raw: Any, state: dict[str, Any] | None) -> None:
    c = p.constraints.get(CONSTRAINT_NAME)
    if c:
        p.constraints.remove(c)
    if KEY in p:
        del p[KEY]
    if state:
        c = p.constraints.new("LIMIT_ROTATION")
        c.name = CONSTRAINT_NAME
        for k, v in state.items():
            setattr(c, k, v)
        p[KEY] = raw


@contextmanager
def editing(obj: Any) -> Iterator[None]:
    active = bpy.context.view_layer.objects.active
    selected = list(bpy.context.selected_objects)
    try:
        for other in selected:
            other.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        if bpy.context.mode != "EDIT_ARMATURE":
            fail("Blender could not enter armature Edit Mode")
        yield
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for current in list(bpy.context.selected_objects):
            current.select_set(False)
        for other in selected:
            other.select_set(True)
        bpy.context.view_layer.objects.active = active


def rest_guard(obj: Any) -> None:
    editable(obj)
    if obj.data.pose_position != "POSE":
        fail("Rest editing requires Pose position with neutral requested channels")
    for p in obj.pose.bones:
        if (
            max(
                abs(v - (1 if i == j else 0))
                for i, row in enumerate(p.matrix_basis)
                for j, v in enumerate(row)
            )
            > 1e-6
        ):
            fail("Reset the requested pose before editing rest structure")
        if p.custom_shape or any(k != KEY for k in p.keys()):
            fail("Rest edits preserve external pose metadata and control shapes")
    users = bpy.data.user_map(subset={obj, obj.data})
    allowed = {obj, bpy.context.scene, *obj.users_collection}
    if (users.get(obj, set()) | users.get(obj.data, set())) - allowed:
        fail(
            "Rest edits require an unbound structure without external "
            "references; edit before binding or use a separate structural "
            "armature"
        )
    if obj.children:
        fail("Rest edits preserve dependent object children")


def current_definition(obj: Any, bone: Any) -> RestBone:
    return RestBone(
        name=str(bone.name),
        head=list(bone.head_local),
        tail=list(bone.tail_local),
        parent=str(bone.parent.name) if bone.parent else None,
        connected=bool(bone.use_connect),
        x_reference=list(bone.matrix_local.to_3x3().col[0]),
        deform=bool(bone.use_deform),
        head_radius=bone.head_radius,
        tail_radius=bone.tail_radius,
        envelope_distance=bone.envelope_distance,
        limits=model_limits(obj.pose.bones[bone.name]),
    )


def edit_rest(args: ArmatureRestArguments) -> ArmatureSummary:
    from . import rig

    obj = rig.armature(args.object_name)
    rest_guard(obj)
    if args.space == "world":
        positive_uniform(obj.matrix_world)
    renames = {r.name: r.rename for r in args.renames}
    for old, new in renames.items():
        if old not in obj.data.bones:
            fail(f'Rename source "{old}" is missing')
        if new in obj.data.bones or len(new.encode()) > 63 or "\x00" in new:
            fail(
                "Rename targets must be unused native bone names of at "
                "most 63 UTF-8 bytes"
            )
    before = {
        p.name: (p.get(KEY), native_limit_state(p), p.rotation_mode)
        for p in obj.pose.bones
    }
    definitions = {}
    for b in obj.data.bones:
        s = current_definition(obj, b)
        values = s.model_dump(mode="python", exclude_unset=True)
        values["name"] = renames.get(s.name, s.name)
        values["parent"] = renames.get(s.parent, s.parent) if s.parent else None
        definitions[values["name"]] = RestBone.model_validate(values)
    for spec in args.bones:
        definitions[spec.name] = resolve_rest(spec, obj.matrix_world, args.space)
    try:
        plan = ArmatureCreateArguments(name=obj.name, bones=list(definitions.values()))
    except ValueError as exc:
        fail(f"Final rest structure is invalid: {str(exc)[:450]}")
    old_data = obj.data
    data = old_data.copy()
    staged = None
    published = False
    try:
        staged = bpy.data.objects.new("Rest structure staging", data)
        bpy.context.scene.collection.objects.link(staged)
        with editing(staged):
            for bone in data.edit_bones:
                bone.use_connect = False
                bone.parent = None
            for old, new in renames.items():
                data.edit_bones[old].name = new
            for spec in args.bones:
                resolved = definitions[spec.name]
                bone = data.edit_bones.get(spec.name) or data.edit_bones.new(spec.name)
                if bone.name != spec.name:
                    fail("Requested bone name cannot be represented natively")
                orient_bone(bone, resolved)
            for spec in plan.bones:
                if spec.parent:
                    bone = data.edit_bones[spec.name]
                    bone.parent = data.edit_bones[spec.parent]
                    bone.use_connect = spec.connected
        for p in obj.pose.bones:
            c = constraint(p)
            if c:
                p.constraints.remove(c)
            if KEY in p:
                del p[KEY]
        obj.data = data
        published = True
        for spec in plan.bones:
            set_limits(obj.pose.bones[spec.name], spec.limits)
        bpy.context.view_layer.update()
        validate_constraints(obj)
        result = rig.inspect(obj, limit=args.sample_limit)
    except Exception:
        if published:
            obj.data = old_data
        for name, (raw, state, mode) in before.items():
            restore_limit_state(obj.pose.bones[name], raw, state)
            obj.pose.bones[name].rotation_mode = mode
        bpy.context.view_layer.update()
        raise
    finally:
        if staged is not None:
            bpy.data.objects.remove(staged, do_unlink=True)
        if data.users == 0:
            bpy.data.armatures.remove(data)
    if old_data.users == 0:
        name = old_data.name
        bpy.data.armatures.remove(old_data)
        data.name = name
    return result


def structure_issues(obj: Any, bone: Any) -> list[str]:
    issues = []
    p = obj.pose.bones[bone.name]
    if not math.isfinite(bone.length) or bone.length < 1e-6:
        issues.append("invalid_length")
    if bone.use_connect and (
        not bone.parent or (bone.head_local - bone.parent.tail_local).length > 1e-5
    ):
        issues.append("disconnected_head")
    axes = [bone.matrix_local.to_3x3().col[i] for i in range(3)]
    if any(abs(a.length - 1) > 1e-5 for a in axes) or any(
        abs(axes[i].dot(axes[j])) > 1e-5 for i in range(3) for j in range(i)
    ):
        issues.append("invalid_frame")
    try:
        limits = model_limits(p)
        c = constraint(p)
        if len(p.constraints) != int(c is not None):
            issues.append("external_constraints")
        if limits:
            if any(abs(v) > 1e-6 for v in p.location):
                issues.append("translated_joint_center")
            if any(abs(v - 1) > 1e-6 for v in p.scale):
                issues.append("scaled_joint_pose")
            if (
                abs(p.rotation_euler.x) > MAX_XZ
                or abs(p.rotation_euler.z) > MAX_XZ
                or abs(p.rotation_euler.y) > MAX_Y
            ):
                issues.append("nonprincipal_joint_pose")
            # Validate one bone without returning arbitrary native properties.
            if (
                c.owner_space != "LOCAL"
                or c.euler_order != "XYZ"
                or c.mute
                or c.influence != 1
                or c.use_legacy_behavior
                or c.use_transform_limit
                or not c.is_valid
                or p.rotation_mode != "XYZ"
            ):
                issues.append("modified_joint_constraint")
            for axis in ("x", "y", "z"):
                span = getattr(limits, axis)
                if (
                    getattr(c, "use_limit_" + axis) != (span is not None)
                    or span
                    and (
                        abs(getattr(c, "min_" + axis) - span.minimum) > 1e-6
                        or abs(getattr(c, "max_" + axis) - span.maximum) > 1e-6
                    )
                ):
                    issues.append("modified_joint_limits")
                    break
    except OperationError:
        issues.append("invalid_joint_ownership")
    return issues[:8]


def inspect_structure(args: StructureInspectArguments) -> StructureSummary:
    from . import rig

    obj = rig.armature(args.object_name)
    bpy.context.view_layer.update()
    if args.space == "world" and "frame" in args.fields:
        positive_uniform(obj.matrix_world)
    bones = list(obj.data.bones)
    scope = bones
    if args.root is not None:
        root = obj.data.bones.get(args.root)
        if root is None:
            fail("Subtree root bone is missing")
        scope = [b for b in bones if b == root or root in b.parent_recursive]
    selected, info = page(scope, args, lambda b: str(b.name))
    issues = {b.name: structure_issues(obj, b) for b in bones}
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    world = evaluated.matrix_world
    rows = []
    for b in selected:
        p = evaluated.pose.bones[b.name]
        rest_transform = (
            world
            if args.space == "world"
            else b.parent.matrix_local.inverted()
            if args.space == "parent" and b.parent
            else Matrix.Identity(4)
        )
        pose_transform = (
            world
            if args.space == "world"
            else evaluated.pose.bones[b.parent.name].matrix.inverted()
            if args.space == "parent" and b.parent
            else Matrix.Identity(4)
        )
        rest = None
        frame = None
        pose = None
        limits = None
        c = None
        if "rest" in args.fields:
            h = rest_transform @ b.head_local
            t = rest_transform @ b.tail_local
            rest = StructuralRest(
                head=list(h), tail=list(t), length=float((t - h).length)
            )
        if "frame" in args.fields:
            matrix = rest_transform @ b.matrix_local
            frame = JointFrame(
                center=list(matrix.translation),
                x=list(matrix.to_3x3().col[0].normalized()),
                y=list(matrix.to_3x3().col[1].normalized()),
                z=list(matrix.to_3x3().col[2].normalized()),
            )
        try:
            c = constraint(obj.pose.bones[b.name])
            if "limits" in args.fields:
                limits = model_limits(obj.pose.bones[b.name])
        except OperationError:
            pass  # Recorded in bounded structural issues.
        if "pose" in args.fields:
            e = evaluation(obj, evaluated, b.name)
            pose = StructuralPose(
                **e.model_dump(),
                head=list(pose_transform @ p.head),
                tail=list(pose_transform @ p.tail),
            )
        rows.append(
            StructuralSegment(
                name=str(b.name),
                parent=str(b.parent.name) if b.parent else None,
                connected=bool(b.use_connect),
                rest=rest,
                frame=frame,
                pose=pose,
                limits=limits,
                joint_constraint=str(c.name) if c else None,
                constraint_count=len(obj.pose.bones[b.name].constraints),
                valid=not issues[b.name],
                issues=issues[b.name],
            )
        )
    return StructureSummary(
        object_name=str(obj.name),
        bone_count=len(bones),
        joint_count=sum(KEY in p for p in obj.pose.bones),
        valid=not any(issues.values()),
        invalid_bone_count=sum(bool(i) for i in issues.values()),
        space=args.space,
        segments=rows,
        page=info,
    )


def bone_point(source: Any) -> Any:
    from . import rig

    obj = rig.armature(source.object)
    if source.bone not in obj.data.bones:
        fail(f'Bone point "{source.bone}" is missing; use its current native name')
    if source.state == "rest":
        bone = obj.data.bones[source.bone]
        point = bone.head_local if source.endpoint == "head" else bone.tail_local
        return obj.matrix_world @ point
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    bone = evaluated.pose.bones[source.bone]
    return evaluated.matrix_world @ (
        bone.head if source.endpoint == "head" else bone.tail
    )
