"""Owned native drivers generated from bounded typed scalar relationships."""

import hashlib
import json
from typing import Any

import bpy  # type: ignore[import-not-found]
from pydantic import ValidationError

from . import coupling_mechanisms as mechanisms
from . import motion_channels as channels
from .errors import OperationError
from .inspection import page
from .motion_math import expression, mapped, output_bounds
from .motion_models import (
    Channel,
    CouplingConfigureArguments,
    CouplingInspectArguments,
    CouplingInspectResult,
    CouplingSpec,
    CouplingSummary,
    MechanismSolution,
    MechanismSolveArguments,
    MotionNames,
    MotionRemoveArguments,
    MotionSampleArguments,
    PropertiesArguments,
    TransformChannel,
)

KEY = "_tyvrana_couplings"
MAX_COUPLINGS = 256


def pointer(name: str, side: str) -> str:
    return KEY + "_" + hashlib.sha256(name.encode()).hexdigest()[:16] + "_" + side


def source_items(spec: CouplingSpec) -> list[tuple[str, Channel, float]]:
    return [("source", spec.source, 1.0)] + [
        (f"source_{i + 1}", s.channel, s.weight)
        for i, s in enumerate(spec.additional_sources)
    ]


def driver_expression(spec: CouplingSpec) -> str:
    source = "x" + "".join(
        f"+(x{i + 1}*({s.weight!r}))" for i, s in enumerate(spec.additional_sources)
    )
    return expression(
        spec.mapping, "(" + source + ")" if spec.additional_sources else source
    )


def source_value(spec: CouplingSpec) -> float:
    return sum(
        channels.resolve(s).value(evaluated=True) * w for _, s, w in source_items(spec)
    )


def pointer_items(spec: CouplingSpec) -> list[tuple[str, Channel]]:
    return [(side, s) for side, s, _ in source_items(spec)] + [("target", spec.target)]


def records() -> dict[str, CouplingSpec]:
    raw = bpy.context.scene.get(KEY, "{}")
    try:
        if not isinstance(raw, str) or len(raw) > 262144:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or len(data) > MAX_COUPLINGS:
            raise ValueError
        result = {}
        for name, value in data.items():
            spec = CouplingSpec.model_validate_json(json.dumps(value))
            if name != spec.name:
                raise ValueError
            for side, original in pointer_items(spec):
                obj = bpy.context.scene.get(pointer(name, side))
                if obj is not None and not isinstance(obj, bpy.types.Object):
                    raise ValueError
                if obj is not None:
                    channel = original.model_copy(update={"object_name": obj.name})
                    if side.startswith("source_"):
                        idx = int(side.split("_")[1]) - 1
                        extras = list(spec.additional_sources)
                        extras[idx] = extras[idx].model_copy(
                            update={"channel": channel}
                        )
                        spec = spec.model_copy(update={"additional_sources": extras})
                    else:
                        spec = spec.model_copy(update={side: channel})
            result[name] = spec
        return result
    except (ValueError, TypeError, ValidationError) as exc:
        raise OperationError(
            "motion_invalid", "Coupling metadata is invalid or exceeds256 relationships"
        ) from exc


def persist(data: dict[str, CouplingSpec]) -> None:
    raw = json.dumps(
        {n: s.model_dump(mode="json") for n, s in data.items()}, separators=(",", ":")
    )
    if len(raw) > 262144 or len(data) > MAX_COUPLINGS:
        channels.fail("Coupling catalog exceeds256 entries/256KiB")
    bpy.context.scene[KEY] = raw
    expected = {
        pointer(name, side)
        for name, spec in data.items()
        for side, _ in pointer_items(spec)
    }
    for key in list(bpy.context.scene.keys()):
        if key.startswith(KEY + "_") and key not in expected:
            del bpy.context.scene[key]
    for name, spec in data.items():
        for side, channel in pointer_items(spec):
            bpy.context.scene[pointer(name, side)] = channels.object_named(
                channel.object_name
            )


def driver_matches(
    spec: CouplingSpec, source: channels.Resolved, target: channels.Resolved
) -> bool:
    curve = target.driver()
    if (
        curve is None
        or curve.mute
        or len(curve.modifiers)
        or len(curve.keyframe_points)
    ):
        return False
    driver = curve.driver
    if (
        driver.type != "SCRIPTED"
        or driver.use_self
        or driver.expression != driver_expression(spec)
        or len(driver.variables) != len(source_items(spec))
    ):
        return False
    for i, (_, channel, _) in enumerate(source_items(spec)):
        resolved = source if i == 0 else channels.resolve(channel)
        variable = driver.variables[i]
        native = variable.targets[0]
        if variable.name != ("x" if i == 0 else f"x{i}") or native.id != resolved.owner:
            return False
        if isinstance(channel, TransformChannel):
            if not (
                variable.type == "TRANSFORMS"
                and native.bone_target == (channel.bone or "")
                and native.transform_type == transform_type(channel)
                and native.transform_space == "LOCAL_SPACE"
                and native.rotation_mode == "XYZ"
            ):
                return False
        elif variable.type != "SINGLE_PROP" or native.data_path != resolved.path:
            return False
    return True


def transform_type(spec: TransformChannel) -> str:
    return (
        {"location": "LOC", "rotation": "ROT", "scale": "SCALE"}[spec.property]
        + "_"
        + spec.axis.upper()
    )


def create_driver(spec: CouplingSpec) -> None:
    source = channels.resolve(spec.source)
    target = channels.resolve(spec.target, write=True)
    curve = (
        target.owner.driver_add(target.path, target.index)
        if isinstance(spec.target, TransformChannel)
        else target.owner.driver_add(target.path)
    )
    for modifier in list(curve.modifiers):
        curve.modifiers.remove(modifier)
    curve.keyframe_points.clear()
    driver = curve.driver
    driver.type = "SCRIPTED"
    driver.use_self = False
    for i, (_, channel, _) in enumerate(source_items(spec)):
        source = channels.resolve(channel)
        variable = driver.variables.new()
        variable.name = "x" if i == 0 else f"x{i}"
        native = variable.targets[0]
        if isinstance(channel, TransformChannel):
            variable.type = "TRANSFORMS"
            native.id = source.owner
            native.bone_target = channel.bone or ""
            native.transform_type = transform_type(channel)
            native.transform_space = "LOCAL_SPACE"
            native.rotation_mode = "XYZ"
        else:
            variable.type = "SINGLE_PROP"
            native.id_type = source.owner.id_type
            native.id = source.owner
            native.data_path = source.path
    driver.expression = driver_expression(spec)
    if driver.expression != driver_expression(spec):
        channels.fail(
            "Mapping exceeds native expression capacity; reduce "
            "knots/sources or simplify coefficients"
        )
    if not driver.is_simple_expression:
        channels.fail("Generated driver is not a native simple expression")
    target.owner.update_tag()


def delete_driver(spec: CouplingSpec) -> None:
    target = channels.resolve(spec.target, write=True)
    animation = target.owner.animation_data
    curve = target.driver()
    if animation and curve:
        animation.drivers.remove(curve)
        target.owner.update_tag()


def cycle_guard(data: dict[str, CouplingSpec]) -> None:
    dependencies: dict[tuple[int, str], set[tuple[int, str]]] = {}
    resolved = [
        (channels.resolve(source), channels.resolve(s.target))
        for s in data.values()
        for _, source, _ in source_items(s)
    ]
    if len(data) > MAX_COUPLINGS:
        channels.fail("Coupling catalog exceeds256 relationships")
    for source, target in resolved:
        dependencies.setdefault(target.node(), set()).add(source.node())
        for item in [source, target]:
            obj = item.obj
            if obj.parent:
                dependencies.setdefault(
                    (int(obj.as_pointer()), "transform"), set()
                ).add((int(obj.parent.as_pointer()), "transform"))
            if isinstance(item.spec, TransformChannel) and item.spec.bone:
                bone = obj.data.bones[item.spec.bone]
                while bone:
                    node = (int(obj.as_pointer()), "bone:" + bone.name)
                    dependencies.setdefault(node, set()).add(
                        (
                            int(obj.as_pointer()),
                            "bone:" + bone.parent.name if bone.parent else "transform",
                        )
                    )
                    bone = bone.parent
    active: set[tuple[int, str]] = set()
    seen: set[tuple[int, str]] = set()

    def visit(node: tuple[int, str]) -> None:
        if node in active:
            channels.fail(
                "Dependency cycle detected; drive independent downstream channels"
                " instead"
            )
        if node in seen:
            return
        if len(seen) + len(active) > 4096:
            channels.fail("Dependency graph exceeds4096 channels")
        active.add(node)
        for source in dependencies.get(node, set()):
            visit(source)
        active.remove(node)
        seen.add(node)

    for node in list(dependencies):
        visit(node)
    # Unsupported external drivers on an involved transform can hide RNA cycles.
    known = {
        (target.owner.as_pointer(), target.path, target.index) for _, target in resolved
    }
    parents = set()
    for source, target in resolved:
        for item in [source, target]:
            parent = item.obj.parent
            while parent and parent not in parents:
                parents.add(parent)
                animation = parent.animation_data
                if animation and any(
                    (parent.as_pointer(), c.data_path, c.array_index) not in known
                    for c in animation.drivers
                ):
                    channels.fail(
                        "Ancestor has unverified external driver dependencies"
                    )
                parent = parent.parent
    for source, target in resolved:
        for item in [source, target]:
            animation = item.owner.animation_data
            if animation:
                for curve in animation.drivers:
                    identity = (
                        item.owner.as_pointer(),
                        curve.data_path,
                        curve.array_index,
                    )
                    if identity not in known and (
                        isinstance(item.spec, TransformChannel)
                        or curve.data_path == item.path
                    ):
                        channels.fail(
                            "Involved channel owner has an unrelated driver "
                            "with unverified "
                            "dependencies; preserve it and use independent controls"
                        )
    from . import rig_constraints

    rig_constraints.graph_guard([], coupling_catalog=data)


def configure(args: CouplingConfigureArguments) -> MotionNames:
    channels.editable(bpy.context.scene)
    old = records()
    proposed = dict(old)
    for spec in args.couplings:
        if spec.name in old and not args.replace:
            channels.fail(f'Coupling "{spec.name}" exists; use replace=true')
        if spec.name in old:
            prior = old[spec.name]
            if not driver_matches(
                prior, channels.resolve(prior.source), channels.resolve(prior.target)
            ):
                channels.fail(
                    "Owned driver was edited externally; preserve it and repair "
                    "ownership before replacing"
                )
        target = channels.resolve(spec.target, write=True)
        owned_target = next(
            (
                s.name
                for s in old.values()
                if channels.resolve(s.target).owner == target.owner
                and channels.resolve(s.target).path == target.path
                and channels.resolve(s.target).index == target.index
            ),
            None,
        )
        if target.driver() and owned_target != spec.name:
            channels.fail(
                "Target already driven; configure its existing owned relationship"
            )
        if target.keyed():
            channels.fail(
                "Target has an active keyframe channel; remove/detach that "
                "channel before coupling"
            )
        clamp = output_bounds(spec.mapping)
        if (target.minimum, target.maximum) != (-1e9, 1e9):
            if clamp is None or clamp[0] < target.minimum or clamp[1] > target.maximum:
                channels.fail(
                    "Bounded target requires an explicit output clamp within its "
                    "writable range"
                )
        channels.check_value(target, mapped(spec.mapping, source_value(spec))[0])
        proposed[spec.name] = spec
    identities = [
        (r.owner.as_pointer(), r.path, r.index)
        for r in (channels.resolve(s.target) for s in proposed.values())
    ]
    if len(identities) != len(set(identities)):
        channels.fail("A native scalar target can have only one coupling")
    cycle_guard(proposed)
    mechanism_data = mechanisms.proposed(
        args.mechanisms,
        args.replace,
        set(proposed),
        {s.target.model_dump_json() for s in proposed.values()},
    )
    previous_metadata = {
        k: bpy.context.scene[k]
        for k in bpy.context.scene.keys()
        if k.startswith((KEY, mechanisms.KEY))
    }
    created: list[CouplingSpec] = []
    removed: list[CouplingSpec] = []
    previous_animation = {
        channels.resolve(s.target).owner: channels.resolve(
            s.target
        ).owner.animation_data
        is not None
        for s in args.couplings
    }
    original_values = [
        (channels.resolve(s.target), channels.resolve(s.target).value())
        for s in args.couplings
    ]
    try:
        for spec in args.couplings:
            if spec.name in old:
                delete_driver(old[spec.name])
                removed.append(old[spec.name])
            created.append(spec)
            create_driver(spec)
        channels.refresh()
        if any(
            not channels.resolve(s.target).driver().driver.is_valid
            for s in args.couplings
        ):
            channels.fail(
                "Native dependency evaluation rejected a relationship; batch "
                "rolled back"
            )
        persist(proposed)
        mechanisms.persist(mechanism_data)
    except BaseException:
        for key in list(bpy.context.scene.keys()):
            if key.startswith((KEY, mechanisms.KEY)):
                del bpy.context.scene[key]
        for key, value in previous_metadata.items():
            bpy.context.scene[key] = value
        for spec in reversed(created):
            delete_driver(spec)
        for spec in removed:
            create_driver(spec)
        for target, value in original_values:
            set_value(target, value)
        for owner, existed in previous_animation.items():
            if not existed:
                channels.clear_empty_animation(owner)
        channels.refresh()
        raise
    return MotionNames(
        names=[s.name for s in args.couplings] + [m.name for m in args.mechanisms]
    )


def set_value(target: channels.Resolved, value: float) -> None:
    if isinstance(target.spec, TransformChannel):
        getattr(target.container, target.property)[target.index] = value
    elif target.spec.kind == "property":
        target.container[target.property] = value
    else:
        setattr(target.container, target.property, value)
    target.owner.update_tag()


def summary(spec: CouplingSpec, data: dict[str, CouplingSpec]) -> CouplingSummary:
    result: dict[str, Any] = dict(
        name=spec.name,
        source=spec.source,
        target=spec.target,
        mapping=spec.mapping,
        additional_sources=spec.additional_sources,
        valid=False,
    )
    try:
        for side, _ in pointer_items(spec):
            if bpy.context.scene.get(pointer(spec.name, side)) is None:
                channels.fail(
                    "Referenced source/target was removed; relationship is invalid"
                )
        source, target = channels.resolve(spec.source), channels.resolve(spec.target)
        if not driver_matches(spec, source, target):
            channels.fail(
                "Native driver differs from the owned mapping or channel; repair "
                "explicitly"
            )
        curve = target.driver()
        if (
            not curve.is_valid
            or not curve.driver.is_valid
            or not curve.driver.is_simple_expression
        ):
            channels.fail("Native driver reports invalid dependency evaluation")
        value = source_value(spec)
        expected, saturated = mapped(spec.mapping, value)
        actual = target.value()
        evaluated = target.value(evaluated=True)
        dependencies = [
            name
            for name, other in data.items()
            if channels.resolve(other.target).node()
            in {channels.resolve(s).node() for _, s, _ in source_items(spec)}
        ][:64]
        return CouplingSummary(
            **(
                result
                | dict(
                    valid=True,
                    source_value=value,
                    mapped_value=expected,
                    target_value=actual,
                    evaluated_target_value=evaluated,
                    mapping_error=abs(actual - expected),
                    constrained_error=abs(evaluated - expected),
                    saturated=saturated,
                    dependencies=dependencies,
                )
            )
        )
    except (OperationError, ValueError, KeyError, ReferenceError) as exc:
        return CouplingSummary(**result, issues=[str(exc)[:256]])


def inspect(args: CouplingInspectArguments) -> CouplingInspectResult:
    data = records()
    bpy.context.view_layer.update()
    mechanism_names = set(mechanisms.catalog())
    selected, info = page(list(data) + sorted(mechanism_names), args, lambda n: n)
    return CouplingInspectResult(
        couplings=[summary(data[n], data) for n in selected if n in data],
        mechanisms=[mechanisms.summary(n) for n in selected if n in mechanism_names],
        page=info,
    )


def removable(spec: CouplingSpec) -> bool:
    scene = bpy.context.scene
    if scene.get(pointer(spec.name, "target")) is None:
        return False  # Deleted owner: clear only the stale relationship metadata.
    target = channels.resolve(spec.target, write=True)
    if all(
        scene.get(pointer(spec.name, side)) is not None
        for side, _, _ in source_items(spec)
    ):
        if not driver_matches(spec, channels.resolve(spec.source), target):
            channels.fail("Native driver is no longer owned; preserve unrelated data")
        return True
    curve = target.driver()
    if curve is None:
        return False
    driver = curve.driver
    if (
        driver.type != "SCRIPTED"
        or driver.expression != driver_expression(spec)
        or driver.use_self
        or len(driver.variables) != len(source_items(spec))
        or curve.modifiers
        or curve.keyframe_points
        or any(
            v.name != ("x" if i == 0 else f"x{i}")
            for i, v in enumerate(driver.variables)
        )
        or any(
            v.targets[0].id is not None
            and v.targets[0].id != bpy.context.scene.get(pointer(spec.name, side))
            for v, (side, _, _) in zip(
                driver.variables, source_items(spec), strict=True
            )
        )
    ):
        channels.fail("Missing-source driver was edited; refusing unrelated data")
    return True


def remove(args: MotionRemoveArguments) -> MotionNames:
    channels.editable(bpy.context.scene)
    data = records()
    mechanism_data = mechanisms.catalog()
    remove_native = set()
    for name in args.names:
        if name in mechanism_data:
            continue
        if name not in data:
            channels.fail(f'Coupling "{name}" is missing')
        if removable(data[name]):
            remove_native.add(name)
    previous_metadata = {
        k: bpy.context.scene[k]
        for k in bpy.context.scene.keys()
        if k.startswith((KEY, mechanisms.KEY))
    }
    removed = []
    try:
        for name in args.names:
            if name in remove_native:
                delete_driver(data[name])
                removed.append(data[name])
        persist({n: s for n, s in data.items() if n not in args.names})
        mechanisms.persist(
            {n: s for n, s in mechanism_data.items() if n not in args.names}
        )
    except BaseException:
        for key in list(bpy.context.scene.keys()):
            if key.startswith((KEY, mechanisms.KEY)):
                del bpy.context.scene[key]
        for key, value in previous_metadata.items():
            bpy.context.scene[key] = value
        for spec in removed:
            create_driver(spec)
        channels.refresh()
        raise
    for spec in removed:
        channels.clear_empty_animation(channels.resolve(spec.target).owner)
    for name in args.names:
        if name not in data:
            continue
        for side, _ in pointer_items(data[name]):
            key = pointer(name, side)
            if key in bpy.context.scene:
                del bpy.context.scene[key]
    channels.refresh()
    return MotionNames(names=args.names)


def set_properties(args: PropertiesArguments) -> MotionNames:
    staged = []
    proposed_controls: dict[int, dict[str, list[float]]] = {}
    for spec in args.properties:
        obj = channels.object_named(spec.object_name)
        channels.editable(obj)
        catalog = channels.control_catalog(obj)
        name = channels.CONTROL_PREFIX + spec.name
        if name in obj and spec.name not in catalog:
            channels.fail("Scalar property name is occupied by unrelated data")
        if spec.name in catalog:
            from .motion_models import PropertyChannel

            target = channels.resolve(
                PropertyChannel(object_name=obj.name, property=spec.name), write=True
            )
            if target.driver() or target.keyed():
                channels.fail(
                    "Scalar property is animated/driven; edit the action or coupling"
                )
        proposed = proposed_controls.setdefault(int(obj.as_pointer()), dict(catalog))
        proposed[spec.name] = [spec.minimum, spec.maximum]
        if len(proposed) > 64:
            channels.fail("Object scalar control catalog exceeds64 properties")
        staged.append((obj, spec, obj.get(name), obj.get(channels.CONTROLS)))
    try:
        for obj, spec, _, _ in staged:
            obj[channels.CONTROL_PREFIX + spec.name] = float(spec.value)
            catalog = channels.control_catalog(obj)
            catalog[spec.name] = [spec.minimum, spec.maximum]
            obj[channels.CONTROLS] = json.dumps(catalog)
            obj.update_tag()
        channels.refresh()
    except BaseException:
        for obj, spec, value, metadata in staged:
            key = channels.CONTROL_PREFIX + spec.name
            if value is None:
                if key in obj:
                    del obj[key]
            else:
                obj[key] = value
            if metadata is None:
                if channels.CONTROLS in obj:
                    del obj[channels.CONTROLS]
            else:
                obj[channels.CONTROLS] = metadata
        channels.refresh()
        raise
    return MotionNames(names=[s.object_name + ":" + s.name for s in args.properties])


def internal_driver_dependency(obj: Any, curve: Any) -> bool:
    """Recognize same-rig native channels already checked at property granularity."""
    data = records()
    for spec in data.values():
        if (
            all(s.object_name == obj.name for _, s, _ in source_items(spec))
            and spec.target.object_name == obj.name
        ):
            try:
                source, target = (
                    channels.resolve(spec.source),
                    channels.resolve(spec.target),
                )
                if target.driver() == curve and driver_matches(spec, source, target):
                    cycle_guard(data)
                    return True
            except OperationError:
                return False
    return False


def solve(args: MechanismSolveArguments) -> MechanismSolution:
    from . import coupling_solver, motion, motion_scope

    spec = mechanisms.definition(args.name)
    query = MotionSampleArguments(
        frames=[bpy.context.scene.frame_current], mechanism=args.name
    )
    scope = motion_scope.restoration_objects(motion_scope.resolve(query, {}, {}))
    result: MechanismSolution | None = None
    with motion.restored_state(
        scope,
        commit=lambda: bool(
            args.apply and result is not None and result.status == "SOLVED"
        ),
    ):
        result, _ = coupling_solver.solve(
            spec, coupling_solver.EvaluationBudget(args.max_evaluations)
        )
        result = result.model_copy(
            update={
                "applied": args.apply and result.status == "SOLVED",
                "restored": not (args.apply and result.status == "SOLVED"),
            }
        )
        if result.applied:
            mechanisms.save_solution(spec, result)
    return result
