"""Native typed constraint ownership, dependency validation and matching."""

import json
import time
from typing import Any, NoReturn

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]

from . import organization
from .constraint_models import (
    IK,
    ChildOf,
    ConstraintsConfigureArguments,
    ConstraintsInspectArguments,
    ConstraintSpec,
    ConstraintsRemoveArguments,
    ConstraintsResult,
    ConstraintSummary,
    CopyLocation,
    CopyRotation,
    CopyTransform,
    Floor,
    LimitLocation,
    PoseMatchArguments,
    PoseMatchResult,
    RigEndpoint,
    SpaceSwitchArguments,
    Track,
)
from .errors import OperationError

KEY = "_tyvrana_constraints"
TYPES = {
    "copy_transforms": "COPY_TRANSFORMS",
    "copy_rotation": "COPY_ROTATION",
    "copy_location": "COPY_LOCATION",
    "limit_location": "LIMIT_LOCATION",
    "damped_track": "DAMPED_TRACK",
    "child_of": "CHILD_OF",
    "floor": "FLOOR",
    "ik": "IK",
}
PROPERTIES = {
    "COPY_TRANSFORMS": ("mix_mode", "remove_target_shear"),
    "COPY_ROTATION": (
        "mix_mode",
        "euler_order",
        "use_x",
        "use_y",
        "use_z",
        "invert_x",
        "invert_y",
        "invert_z",
    ),
    "COPY_LOCATION": (
        "use_offset",
        "use_x",
        "use_y",
        "use_z",
        "invert_x",
        "invert_y",
        "invert_z",
    ),
    "LIMIT_LOCATION": tuple(
        n + a for a in "xyz" for n in ("use_min_", "use_max_", "min_", "max_")
    )
    + ("use_transform_limit",),
    "DAMPED_TRACK": ("track_axis",),
    "CHILD_OF": tuple(
        "use_" + p + "_" + a for p in ("location", "rotation", "scale") for a in "xyz"
    )
    + ("inverse_matrix", "set_inverse_pending"),
    "FLOOR": ("floor_location", "use_rotation", "offset"),
    "IK": (
        "pole_angle",
        "chain_count",
        "iterations",
        "use_tail",
        "use_stretch",
        "use_rotation",
        "use_location",
        "ik_type",
    ),
}


def fail(message: str) -> NoReturn:
    raise OperationError("constraint_invalid", message)


def endpoint(ref: RigEndpoint, *, edit: bool = False) -> tuple[Any, Any]:
    obj = organization.object_named(ref.object_name)
    if edit:
        organization.object_editable(obj)
    if ref.bone:
        if obj.type != "ARMATURE" or ref.bone not in obj.pose.bones:
            fail("Endpoint bone is missing; inspect the current armature")
        return obj, obj.pose.bones[ref.bone]
    return obj, obj


def world(ref: RigEndpoint) -> Any:
    obj, owner = endpoint(ref)
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return (
        evaluated.matrix_world @ evaluated.pose.bones[ref.bone].matrix
        if ref.bone
        else evaluated.matrix_world.copy()
    )


def set_world(ref: RigEndpoint, matrix: Any) -> None:
    obj, owner = endpoint(ref, edit=True)
    if ref.bone:
        owner.matrix = obj.matrix_world.inverted() @ matrix
    else:
        owner.matrix_world = matrix
    obj.update_tag()
    bpy.context.view_layer.update()


def raw(owner: Any) -> dict[str, Any]:
    try:
        text = owner.get(KEY, "{}")
        if not isinstance(text, str) or len(text) > 131072:
            raise ValueError
        data = json.loads(text)
        if not isinstance(data, dict) or len(data) > 64:
            raise ValueError
        return data
    except (ValueError, TypeError) as exc:
        raise OperationError(
            "constraint_invalid", "Constraint ownership metadata is invalid"
        ) from exc


def pointer(name: str, side: str) -> str:
    import hashlib

    return KEY + "_" + hashlib.sha256(name.encode()).hexdigest()[:16] + "_" + side


def snapshot(c: Any) -> dict[str, Any]:
    values = {}
    for prop in ("owner_space", "target_space", "mute", *PROPERTIES[c.type]):
        value = getattr(c, prop)
        values[prop] = [list(r) for r in value] if isinstance(value, Matrix) else value
    return values


def definition(
    owner: Any, ref: RigEndpoint, name: str, *, check: bool = True
) -> ConstraintSpec:
    data = raw(owner)
    if name not in data:
        fail("Constraint is not adapter-owned; preserve external constraints")
    entry = data[name]
    spec = ConstraintSpec.model_validate_json(json.dumps(entry["spec"]))
    settings = spec.settings
    for side in ("target", "pole"):
        target = getattr(settings, side, None)
        if target is not None:
            obj = owner.get(pointer(name, side))
            if obj is None:
                fail("Constraint target was removed; restore its reference first")
            settings = settings.model_copy(
                update={side: target.model_copy(update={"object_name": obj.name})}
            )
    spec = spec.model_copy(update={"owner": ref, "settings": settings})
    c = owner.constraints.get(name)
    if check:
        if (
            c is None
            or c.type != TYPES[settings.kind]
            or snapshot(c) != entry["native"]
        ):
            fail(
                "Owned constraint settings changed externally; preserve external edits"
            )
        for side in ("target", "pole"):
            target = getattr(settings, side, None)
            if target is not None:
                obj, _ = endpoint(target)
                native_side = "pole_target" if side == "pole" else "target"
                sub = "pole_subtarget" if side == "pole" else "subtarget"
                if getattr(c, native_side) != obj or getattr(c, sub) != (
                    target.bone or ""
                ):
                    fail("Owned constraint target changed externally")
    return spec.model_copy(
        update={"influence": c.influence if c is not None else spec.influence}
    )


def node(ref: RigEndpoint) -> tuple[int, str]:
    obj, _ = endpoint(ref)
    return int(obj.as_pointer()), "bone:" + ref.bone if ref.bone else "transform"


def dependencies(
    ref: RigEndpoint,
    overrides: dict[tuple[int, str, str], ConstraintSpec] | None = None,
) -> list[RigEndpoint]:
    obj, owner = endpoint(ref)
    result = []
    if ref.bone:
        if owner.parent:
            result.append(RigEndpoint(object_name=obj.name, bone=owner.parent.name))
        else:
            result.append(RigEndpoint(object_name=obj.name))
    elif obj.parent:
        result.append(
            RigEndpoint(
                object_name=obj.parent.name,
                bone=obj.parent_bone if obj.parent_type == "BONE" else None,
            )
        )
    specs = {n: definition(owner, ref, n) for n in raw(owner)}
    if overrides:
        for (ident, bone, name), spec in overrides.items():
            if (ident, bone) == node(ref):
                specs[name] = spec
    for c in owner.constraints:
        if c.name not in specs and not (
            c.type == "LIMIT_ROTATION"
            and c.name == "Tyvrana Joint Limits"
            and "tyvrana_joint_limits" in owner
        ):
            fail("Involved owner has unverified external constraint dependencies")
    for spec in specs.values():
        for side in ("target", "pole"):
            target = getattr(spec.settings, side, None)
            if target:
                result.append(target)
    return result


def graph_guard(
    specs: list[ConstraintSpec],
    *,
    coupling_catalog: dict[str, Any] | None = None,
    extra_refs: list[RigEndpoint] | None = None,
) -> None:
    from . import couplings, motion_channels
    from .motion_models import TransformChannel

    overrides = {(node(s.owner)[0], node(s.owner)[1], s.name): s for s in specs}
    graph: dict[tuple[int, str], set[tuple[int, str]]] = {}
    pending = [s.owner for s in specs] + (extra_refs or [])
    catalog = couplings.records() if coupling_catalog is None else coupling_catalog
    known = set()
    for spec in catalog.values():
        target = motion_channels.resolve(spec.target)
        known.add((target.owner.as_pointer(), target.path, target.index))
        for _, source, _ in couplings.source_items(spec):
            resolved = motion_channels.resolve(source)
            graph.setdefault(target.node(), set()).add(resolved.node())
            for item in (target, resolved):
                if isinstance(item.spec, TransformChannel):
                    pending.append(
                        RigEndpoint(object_name=item.obj.name, bone=item.spec.bone)
                    )
    seen = set()
    while pending:
        ref = pending.pop()
        key = node(ref)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 4096:
            fail("Constraint dependency graph exceeds 4096 nodes")
        obj, owner = endpoint(ref)
        deps = dependencies(ref, overrides)
        graph.setdefault(key, set()).update(node(d) for d in deps)
        pending.extend(deps)
        current = {n: definition(owner, ref, n) for n in raw(owner)}
        current.update(
            {n: s for (ident, bone, n), s in overrides.items() if (ident, bone) == key}
        )
        for relation in current.values():
            settings = relation.settings
            if not isinstance(settings, IK):
                continue
            if ref.bone is None:
                fail("IK requires a pose-bone owner")
            ancestor = owner
            for _ in range(settings.chain_length):
                if ancestor is None:
                    fail("IK chain_length exceeds available ancestors")
                ancestor_ref = RigEndpoint(object_name=obj.name, bone=ancestor.name)
                pending.append(ancestor_ref)
                graph.setdefault(node(ancestor_ref), set()).add(node(settings.target))
                if settings.pole:
                    graph[node(ancestor_ref)].add(node(settings.pole))
                ancestor = ancestor.parent
        if obj.animation_data and any(
            (obj.as_pointer(), c.data_path, c.array_index) not in known
            for c in obj.animation_data.drivers
        ):
            fail("Involved object has unverified external drivers")
    organization.ordered(graph)


def apply(owner: Any, spec: ConstraintSpec) -> Any:
    settings = spec.settings
    c = owner.constraints.new(TYPES[settings.kind])
    c.name = spec.name
    if c.name != spec.name:
        fail("Native constraint name collides or is truncated")
    c.influence = spec.influence
    if hasattr(settings, "target"):
        c.target = endpoint(settings.target)[0]
        c.subtarget = settings.target.bone or ""
    if isinstance(settings, CopyTransform | CopyRotation | CopyLocation):
        c.owner_space = settings.owner_space
        c.target_space = settings.target_space
        if (
            spec.owner.bone is None
            and settings.owner_space in ("POSE", "LOCAL_WITH_PARENT")
        ) or (
            settings.target.bone is None
            and settings.target_space in ("POSE", "LOCAL_WITH_PARENT")
        ):
            fail("POSE/LOCAL_WITH_PARENT spaces require bone endpoints")
    if isinstance(settings, CopyTransform):
        c.mix_mode = settings.mix
    if isinstance(settings, CopyRotation):
        c.mix_mode = settings.mix
        c.euler_order = "XYZ"
    if isinstance(settings, CopyRotation | CopyLocation):
        for axis in "xyz":
            setattr(c, "use_" + axis, axis in settings.axes)
    if isinstance(settings, CopyLocation):
        c.use_offset = settings.offset
    if isinstance(settings, LimitLocation):
        c.owner_space = settings.owner_space
        for axis in "xyz":
            limits = getattr(settings, axis)
            setattr(c, "use_min_" + axis, limits is not None)
            setattr(c, "use_max_" + axis, limits is not None)
            if limits:
                setattr(c, "min_" + axis, limits.minimum)
                setattr(c, "max_" + axis, limits.maximum)
    if isinstance(settings, Track):
        c.track_axis = "TRACK_" + settings.axis
    if isinstance(settings, Floor):
        c.floor_location = "FLOOR_" + settings.axis
        c.offset = settings.offset
        c.use_rotation = settings.use_rotation
    if isinstance(settings, IK):
        c.chain_count = settings.chain_length
        c.iterations = settings.iterations
        c.use_tail = True
        c.use_rotation = settings.use_rotation
        c.use_stretch = settings.use_stretch
        c.pole_angle = settings.pole_angle
        if settings.pole:
            c.pole_target = endpoint(settings.pole)[0]
            c.pole_subtarget = settings.pole.bone or ""
    if isinstance(settings, ChildOf) and settings.maintain_transform:
        c.inverse_matrix = world(settings.target).inverted()
        c.set_inverse_pending = False
    return c


def persist(owner: Any, spec: ConstraintSpec, c: Any) -> None:
    data = raw(owner)
    data[spec.name] = dict(spec=spec.model_dump(mode="json"), native=snapshot(c))
    owner[KEY] = json.dumps(data)
    for side in ("target", "pole"):
        target = getattr(spec.settings, side, None)
        key = pointer(spec.name, side)
        if target:
            owner[key] = endpoint(target)[0]
        elif key in owner:
            del owner[key]


def summary(ref: RigEndpoint, name: str, detail: bool = False) -> ConstraintSummary:
    _, owner = endpoint(ref)
    c = owner.constraints.get(name)
    issues = []
    spec = None
    try:
        spec = definition(owner, ref, name)
        if not c.is_valid:
            issues.append("Native constraint reports invalid evaluation")
    except (OperationError, ValueError, KeyError) as exc:
        issues.append(str(exc)[:400])
    matrix = world(ref)
    return ConstraintSummary(
        name=name,
        owner=ref,
        type=c.type if c else "MISSING",
        influence=c.influence if c else 0,
        valid=not issues,
        issues=issues,
        definition=spec if detail else None,
        evaluated_location=list(matrix.translation),
        evaluated_rotation=list(matrix.to_quaternion()),
    )


def configure(args: ConstraintsConfigureArguments) -> ConstraintsResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    prepared = []
    additions: dict[int, int] = {}
    for spec in args.constraints:
        obj, owner = endpoint(spec.owner, edit=True)
        if len(spec.name.encode()) > 63:
            fail("Constraint names must fit 63 UTF-8 bytes")
        data = raw(owner)
        old = owner.constraints.get(spec.name)
        if old:
            if not args.replace:
                fail(
                    "Constraint exists; use replace=true to update owned configuration"
                )
            definition(owner, spec.owner, spec.name)
        elif spec.name in data:
            fail("Owned constraint is missing; preserve metadata for repair")
        added = additions.get(owner.as_pointer(), 0)
        if len(owner.constraints) + added >= 64 and not old:
            fail("Owner exceeds 64 constraints")
        if not old:
            additions[owner.as_pointer()] = added + 1
        animation = obj.animation_data
        path = old.path_from_id() if old else ""
        if animation and (
            animation.action
            or animation.nla_tracks
            or any(c.data_path.startswith(path) for c in animation.drivers)
        ):
            fail("Configure before owner animation; preserve active actions/drivers")
        prepared.append(
            (
                spec,
                owner,
                old.type if old else None,
                snapshot(old) if old else None,
                definition(owner, spec.owner, spec.name) if old else None,
                list(owner.constraints).index(old)
                if old
                else len(owner.constraints) + added,
                {k: owner[k] for k in owner.keys() if k.startswith(KEY)},
                world(spec.owner),
            )
        )
    graph_guard(args.constraints)
    changed = []
    try:
        for spec, owner, _, _, _, index, _, before in prepared:
            old = owner.constraints.get(spec.name)
            if old:
                owner.constraints.remove(old)
            changed.append((spec, owner))
            c = apply(owner, spec)
            owner.constraints.move(len(owner.constraints) - 1, index)
            bpy.context.view_layer.update()
            if (
                isinstance(spec.settings, ChildOf)
                and spec.settings.maintain_transform
                and matrix_error(world(spec.owner), before) > 0.0001
            ):
                fail("Maintain-transform is not representable in this constraint stack")
            persist(owner, spec, c)
        bpy.context.view_layer.update()
        result = [summary(s.owner, s.name) for s in args.constraints]
        if any(not r.valid for r in result):
            fail("Native constraint evaluation failed; batch restored")
        return ConstraintsResult(
            constraints=result, processing_seconds=time.perf_counter() - start
        )
    except BaseException:
        for spec, owner in reversed(changed):
            c = owner.constraints.get(spec.name)
            if c:
                owner.constraints.remove(c)
        for spec, owner, _, state, oldspec, index, meta, _ in prepared:
            if oldspec and not owner.constraints.get(spec.name):
                c = apply(owner, oldspec)
                for prop, value in (state or {}).items():
                    setattr(
                        c, prop, Matrix(value) if prop == "inverse_matrix" else value
                    )
                owner.constraints.move(len(owner.constraints) - 1, index)
            for key in list(owner.keys()):
                if key.startswith(KEY):
                    del owner[key]
            for key, value in meta.items():
                owner[key] = value
        bpy.context.view_layer.update()
        raise


def inspect(args: ConstraintsInspectArguments) -> ConstraintsResult:
    start = time.perf_counter()
    organization.idle()
    pairs = [(ref, n) for ref in args.owners for n in raw(endpoint(ref)[1])]
    if len(pairs) > 64:
        fail("Inspection exceeds 64 constraints; select fewer owners")
    return ConstraintsResult(
        constraints=[summary(ref, n, args.include_definitions) for ref, n in pairs],
        processing_seconds=time.perf_counter() - start,
    )


def remove(args: ConstraintsRemoveArguments) -> ConstraintsResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    prepared = []
    for ref in args.constraints:
        obj, owner = endpoint(ref.owner, edit=True)
        spec = definition(owner, ref.owner, ref.name)
        if obj.animation_data:
            fail("Remove constraints before adding animation or detach it explicitly")
        c = owner.constraints[ref.name]
        prepared.append(
            (
                ref,
                owner,
                spec,
                snapshot(c),
                list(owner.constraints).index(c),
                {k: owner[k] for k in owner.keys() if k.startswith(KEY)},
            )
        )
    try:
        for ref, owner, *_ in prepared:
            owner.constraints.remove(owner.constraints[ref.name])
            data = raw(owner)
            del data[ref.name]
            owner[KEY] = json.dumps(data)
            for side in ("target", "pole"):
                key = pointer(ref.name, side)
                if key in owner:
                    del owner[key]
        bpy.context.view_layer.update()
    except BaseException:
        for ref, owner, spec, state, index, meta in prepared:
            if owner.constraints.get(ref.name) is None:
                c = apply(owner, spec)
                for prop, value in state.items():
                    setattr(
                        c, prop, Matrix(value) if prop == "inverse_matrix" else value
                    )
                owner.constraints.move(len(owner.constraints) - 1, index)
            for key, value in meta.items():
                owner[key] = value
        bpy.context.view_layer.update()
        raise
    return ConstraintsResult(
        constraints=[], processing_seconds=time.perf_counter() - start
    )


def matrix_error(a: Any, b: Any) -> float:
    return float(max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)))


def match(args: PoseMatchArguments) -> PoseMatchResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    prepared = []
    for item in args.matches:
        obj, owner = endpoint(item.target, edit=True)
        if (
            owner.constraints
            or obj.animation_data
            or any(owner.lock_location)
            or any(owner.lock_rotation)
            or any(owner.lock_scale)
        ):
            fail("Match targets must be unconstrained, unlocked and unanimated")
        prepared.append(
            (item.target, world(item.source), owner, owner.matrix_basis.copy())
        )

    def depth(item: Any) -> int:
        ref, _, owner, _ = item
        obj, _ = endpoint(ref)
        count = 0
        current = owner.parent if ref.bone else obj.parent
        while current:
            count += 1
            current = current.parent
        return count

    prepared.sort(key=depth)
    try:
        for ref, matrix, _, _ in prepared:
            set_world(ref, matrix)
        error = max(matrix_error(world(ref), matrix) for ref, matrix, _, _ in prepared)
        if error > args.tolerance:
            fail("Match is not representable; inspect connected bones or scale/shear")
        return PoseMatchResult(
            matched=len(prepared),
            maximum_matrix_error=error,
            processing_seconds=time.perf_counter() - start,
        )
    except BaseException:
        for _, _, owner, basis in prepared:
            owner.matrix_basis = basis
        bpy.context.view_layer.update()
        raise


def switch_space(args: SpaceSwitchArguments) -> ConstraintsResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    _, owner = endpoint(args.owner, edit=True)
    spec = definition(owner, args.owner, args.constraint)
    if not isinstance(spec.settings, ChildOf):
        fail("Space switching requires an owned child_of constraint")
    before = world(args.owner)
    # Preserve current influence-space transform through an explicit new inverse.
    old = owner.constraints[args.constraint]
    prior = old.inverse_matrix.copy()
    target = old.target
    sub = old.subtarget
    new = spec.model_copy(
        update={
            "settings": spec.settings.model_copy(
                update={"target": args.target, "maintain_transform": False}
            )
        }
    )
    graph_guard([new])
    obj, _ = endpoint(args.target)
    old.target = obj
    old.subtarget = args.target.bone or ""
    try:
        if args.maintain_transform:
            old.inverse_matrix = (
                world(args.target).inverted() @ world(spec.settings.target) @ prior
            )
        else:
            old.inverse_matrix = Matrix.Identity(4)
        bpy.context.view_layer.update()
        if (
            args.maintain_transform
            and matrix_error(world(args.owner), before) > args.tolerance
        ):
            fail("Space switch did not preserve evaluated transform; restored")
        persist(owner, new, old)
        return ConstraintsResult(
            constraints=[summary(args.owner, args.constraint)],
            processing_seconds=time.perf_counter() - start,
        )
    except BaseException:
        old.target = target
        old.subtarget = sub
        old.inverse_matrix = prior
        bpy.context.view_layer.update()
        raise
