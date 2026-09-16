"""Transactional slotted actions with explicit owner assignment and bounded keys."""

import json
from typing import Any

import bpy  # type: ignore[import-not-found]
from bpy_extras import anim_utils  # type: ignore[import-not-found]
from pydantic import TypeAdapter, ValidationError

from . import motion_channels as channels
from .errors import OperationError
from .motion_models import (
    ActionAssignArguments,
    ActionChannelSummary,
    ActionEditArguments,
    ActionInspectArguments,
    ActionRemoveArguments,
    ActionResult,
    Channel,
    MotionKey,
    MotionNames,
)

KEY = "_tyvrana_action_channels"
ADAPTER: TypeAdapter[Channel] = TypeAdapter(Channel)


def get(name: str) -> Any:
    action = bpy.data.actions.get(name)
    if action is None or KEY not in action:
        channels.fail(f'Owned action "{name}" does not exist')
    return action


def declarations(action: Any) -> list[tuple[Channel, int]]:
    raw = action.get(KEY)
    try:
        if not isinstance(raw, str) or len(raw) > 131072:
            raise ValueError
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 128:
            raise ValueError
        result = []
        for row in rows:
            channel = ADAPTER.validate_json(json.dumps(row["target"]))
            handle = row["slot"]
            obj = action.get(f"_tyvrana_object_{handle}")
            if not obj or not isinstance(handle, int):
                channels.fail("Action owner was removed; repair the declared channel")
            channel = channel.model_copy(update={"object_name": obj.name})
            target = channels.resolve(channel)
            if target.owner != action.get(f"_tyvrana_id_{handle}"):
                channels.fail(
                    "Action owner data was replaced; explicit reconstruction required"
                )
            result.append((channel, handle))
        return result
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise OperationError(
            "motion_invalid", "Owned action metadata is invalid"
        ) from exc


def slot(action: Any, handle: int) -> Any:
    value = next((s for s in action.slots if s.handle == handle), None)
    if value is None:
        channels.fail("Action slot is missing")
    return value


def bag(action: Any, handle: int) -> Any:
    return anim_utils.action_get_channelbag_for_slot(action, slot(action, handle))


def validate(action: Any, *, edit: bool = False) -> list[tuple[Channel, int]]:
    rows = declarations(action)
    if edit:
        channels.editable(action)
    if (
        len(action.layers) != 1
        or len(action.layers[0].strips) != 1
        or action.layers[0].strips[0].type != "KEYFRAME"
    ):
        channels.fail("Owned actions require one native layer and keyframe strip")
    expected = set()
    allowed = set()
    for spec, handle in rows:
        target = channels.resolve(spec, write=edit)
        expected.add((handle, target.path, target.index))
        allowed.add((target.owner, handle))
    count = 0
    for s in action.slots:
        for user in s.users():
            if (user, s.handle) not in allowed:
                channels.fail("Action has an unrelated/shared slot user; preserve it")
        b = bag(action, s.handle)
        if not b:
            continue
        for f in b.fcurves:
            if (
                (s.handle, f.data_path, f.array_index) not in expected
                or f.modifiers
                or f.sampled_points
                or f.mute
            ):
                channels.fail(
                    "Action contains unrelated or unsupported native channels"
                )
            if len(f.keyframe_points) > 512 or any(
                k.interpolation not in {"CONSTANT", "LINEAR", "BEZIER"}
                or k.co.x != round(k.co.x)
                for k in f.keyframe_points
            ):
                channels.fail(
                    "Action keys exceed512/channel or use unsupported "
                    "interpolation/fractional frames"
                )
            if any(
                k.interpolation == "BEZIER"
                and (
                    k.handle_left_type != "AUTO_CLAMPED"
                    or k.handle_right_type != "AUTO_CLAMPED"
                )
                for k in f.keyframe_points
            ):
                channels.fail("Externally authored Bezier handles are protected")
            if edit:
                target = next(
                    channels.resolve(spec)
                    for spec, handle in rows
                    if handle == s.handle
                    and channels.resolve(spec).path == f.data_path
                    and channels.resolve(spec).index == f.array_index
                )
                for key in f.keyframe_points:
                    channels.check_value(target, float(key.co.y))
            count += len(f.keyframe_points)
    if count > 8192:
        channels.fail("Action exceeds8192 keys")
    owners = {channels.resolve(s).owner for s, _ in rows}
    direct = sum(
        bool(o.animation_data and o.animation_data.action == action) for o in owners
    )
    if action.users > direct + int(action.use_fake_user):
        channels.fail(
            "Action has external users such as NLA/constraints; preserve "
            "shared action data"
        )
    return rows


def identity(spec: Channel) -> tuple[int, str, int]:
    target = channels.resolve(spec)
    return int(target.owner.as_pointer()), target.path, target.index


def edit(args: ActionEditArguments) -> ActionResult:
    from .deformation_geometry import native_name

    native_name(args.name)
    channels.object_named(args.channels[0].target.object_name)
    if args.create:
        if bpy.data.actions.get(args.name):
            channels.fail("Action name exists; choose an unused name or create=false")
        original = None
        rows: list[tuple[Channel, int]] = []
    else:
        original = get(args.name)
        rows = validate(original, edit=True)
    for item in args.channels:
        target = channels.resolve(item.target, write=True)
        if target.driver():
            channels.fail(
                "Action channel conflicts with a driver; remove the coupling first"
            )
        for key in item.keys:
            channels.check_value(target, key.value)
    candidate = original.copy() if original else bpy.data.actions.new(args.name)
    assignments: list[tuple[Any, Any]] = []
    try:
        if not original:
            candidate.use_fake_user = True
            candidate.layers.new("Motion").strips.new(type="KEYFRAME")
            candidate[KEY] = "[]"
        entries = {identity(spec): (spec, handle) for spec, handle in rows}
        owners = {channels.resolve(spec).owner: handle for spec, handle in rows}
        for item in args.channels:
            target = channels.resolve(item.target, write=True)
            ident = identity(item.target)
            existing = entries.get(ident)
            if item.remove_channel:
                if not existing:
                    channels.fail("Cannot remove an absent action channel")
                b = bag(candidate, existing[1])
                b.fcurves.remove(b.fcurves.find(target.path, index=target.index))
                del entries[ident]
                continue
            if target.owner not in owners:
                s = candidate.slots.new(
                    id_type=target.owner.id_type, name=target.owner.name
                )
                owners[target.owner] = s.handle
                candidate[f"_tyvrana_object_{s.handle}"] = target.obj
                candidate[f"_tyvrana_id_{s.handle}"] = target.owner
            handle = owners[target.owner]
            b = anim_utils.action_ensure_channelbag_for_slot(
                candidate, slot(candidate, handle)
            )
            curve = b.fcurves.find(target.path, index=target.index)
            if curve is None:
                curve = b.fcurves.new(target.path, index=target.index)
            keys = (
                {}
                if item.replace_keys
                else {
                    int(k.co.x): MotionKey(
                        frame=int(k.co.x),
                        value=float(k.co.y),
                        interpolation=k.interpolation,
                    )
                    for k in curve.keyframe_points
                }
            )
            for frame in item.remove_frames:
                if frame not in keys:
                    channels.fail(f"Cannot remove absent key at frame {frame}")
                del keys[frame]
            keys.update({k.frame: k for k in item.keys})
            if not keys:
                channels.fail("Channel would have no keys; use remove_channel=true")
            if len(keys) > 512:
                channels.fail("Action channel exceeds512 keys")
            curve.keyframe_points.clear()
            curve.keyframe_points.add(len(keys))
            for native, spec in zip(
                curve.keyframe_points,
                sorted(keys.values(), key=lambda k: k.frame),
                strict=True,
            ):
                native.co = spec.frame, spec.value
                native.interpolation = spec.interpolation
                native.handle_left_type = "AUTO_CLAMPED"
                native.handle_right_type = "AUTO_CLAMPED"
            if item.extrapolation is not None:
                curve.extrapolation = item.extrapolation
            if curve.extrapolation == "LINEAR" and (target.minimum, target.maximum) != (
                -1e9,
                1e9,
            ):
                channels.fail("Bounded scalar targets require CONSTANT extrapolation")
            curve.update()
            entries[ident] = item.target, handle
        used = {h for _, h in entries.values()}
        for s in list(candidate.slots):
            if s.handle not in used:
                for prefix in ["_tyvrana_object_", "_tyvrana_id_"]:
                    if prefix + str(s.handle) in candidate:
                        del candidate[prefix + str(s.handle)]
                candidate.slots.remove(s)
        if not entries:
            channels.fail(
                "Action requires at least one channel; remove the action explicitly"
            )
        candidate[KEY] = json.dumps(
            [
                dict(target=s.model_dump(mode="json"), slot=h)
                for s, h in entries.values()
            ]
        )
        validate(candidate)
        if original:
            for owner in {channels.resolve(s).owner for s, _ in rows}:
                animation = owner.animation_data
                if animation and animation.action == original:
                    old_slot = animation.action_slot
                    if owner not in {
                        channels.resolve(s).owner for s, _ in entries.values()
                    }:
                        channels.fail(
                            "Detach action before removing its last channel "
                            "for an assigned "
                            "owner"
                        )
                    assignments.append((owner, old_slot))
                    animation.action = candidate
                    animation.action_slot = slot(candidate, old_slot.handle)
            old_name = original.name
            original.name = old_name + ".replaced"
            candidate.name = old_name
        channels.refresh()
        result = inspect(
            ActionInspectArguments(name=candidate.name), include_channels=False
        )
    except BaseException:
        for owner, old_slot in assignments:
            owner.animation_data.action = original
            owner.animation_data.action_slot = old_slot
        bpy.data.actions.remove(candidate)
        if original:
            original.name = args.name
        channels.refresh()
        raise
    if original:
        bpy.data.actions.remove(original)
    return result


def assign(args: ActionAssignArguments) -> MotionNames:
    action = get(args.name)
    rows = validate(action, edit=True)
    owners = {channels.resolve(s).owner: handle for s, handle in rows}
    saved = []
    for spec, _ in rows:
        target = channels.resolve(spec, write=True)
        if not args.detach and target.driver():
            channels.fail(
                "Action channel conflicts with a driver; remove that coupling"
            )
    for owner in owners:
        animation = owner.animation_data
        previous = animation.action if animation else None
        if not args.detach and previous not in (None, action) and not args.replace:
            channels.fail(
                "Owner has an active action; set replace=true to retain and "
                "replace its assignment"
            )
        saved.append(
            (
                owner,
                previous,
                animation.action_slot if animation else None,
                animation.action_blend_type if animation else "REPLACE",
                animation.action_influence if animation else 1.0,
                animation.action_extrapolation if animation else "HOLD",
            )
        )
    try:
        for owner, handle in owners.items():
            animation = owner.animation_data_create()
            if args.detach:
                if animation.action == action:
                    animation.action = None
            else:
                animation.action = action
                animation.action_slot = slot(action, handle)
                animation.action_blend_type = "REPLACE"
                animation.action_influence = 1.0
                animation.action_extrapolation = "HOLD"
        channels.refresh()
    except BaseException:
        for owner, old, old_slot, blend, influence, extrapolation in saved:
            animation = owner.animation_data_create()
            animation.action = old
            animation.action_slot = old_slot
            animation.action_blend_type = blend
            animation.action_influence = influence
            animation.action_extrapolation = extrapolation
        channels.refresh()
        raise
    if args.detach:
        for owner in owners:
            channels.clear_empty_animation(owner)
    return MotionNames(names=[owner.name for owner in owners])


def remove(args: ActionRemoveArguments) -> MotionNames:
    action = get(args.name)
    validate(action, edit=True)
    if action.users > int(action.use_fake_user):
        channels.fail("Action still has users; explicitly detach it before removal")
    bpy.data.actions.remove(action)
    return MotionNames(names=[args.name])


def inspect(
    args: ActionInspectArguments, *, include_channels: bool = True
) -> ActionResult:
    action = get(args.name)
    rows = validate(action)
    total = 0
    all_frames: list[float] = []
    summaries = []
    for index, (spec, handle) in enumerate(rows):
        target = channels.resolve(spec)
        curve = bag(action, handle).fcurves.find(target.path, index=target.index)
        if curve is None:
            channels.fail("Declared action channel is missing")
        frames = [float(k.co.x) for k in curve.keyframe_points]
        total += len(frames)
        all_frames.extend(frames)
        if not include_channels or not args.offset <= index < args.offset + args.limit:
            continue
        animation = target.owner.animation_data
        keys = [
            MotionKey(
                frame=int(k.co.x), value=float(k.co.y), interpolation=k.interpolation
            )
            for k in list(curve.keyframe_points)[: args.key_limit]
        ]
        summaries.append(
            ActionChannelSummary(
                target=spec,
                key_count=len(frames),
                frame_min=min(frames) if frames else None,
                frame_max=max(frames) if frames else None,
                interpolations=sorted({k.interpolation for k in curve.keyframe_points}),
                extrapolation=curve.extrapolation,
                assigned=bool(
                    animation
                    and animation.action == action
                    and animation.action_slot == slot(action, handle)
                ),
                driven=target.driver() is not None,
                current_value=target.value(),
                evaluated_value=target.value(evaluated=True),
                keys=keys,
                keys_truncated=len(keys) < len(frames),
            )
        )
    return ActionResult(
        name=action.name,
        channel_count=len(rows),
        key_count=total,
        slot_count=len(action.slots),
        frame_min=min(all_frames) if all_frames else None,
        frame_max=max(all_frames) if all_frames else None,
        channels=summaries,
        next_offset=args.offset + len(summaries)
        if include_channels and args.offset + len(summaries) < len(rows)
        else None,
        valid=True,
    )
