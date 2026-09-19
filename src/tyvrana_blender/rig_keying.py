"""Shared preflight, anchor sampling and staged action edits for rig matching."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import actions
from . import motion_channels as channels
from .constraint_models import RigEndpoint
from .keying_models import MatchKeying
from .motion_models import (
    ActionChannel,
    ActionEditArguments,
    Channel,
    MotionKey,
    TransformChannel,
)


def snapshot(owner: Any) -> dict[str, tuple[float, ...]]:
    return {
        name: tuple(getattr(owner, name))
        for name in (
            "location",
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
            "scale",
        )
    }


def restore(owner: Any, values: dict[str, tuple[float, ...]]) -> None:
    for name, value in values.items():
        setattr(owner, name, value)


def transforms(ref: RigEndpoint) -> list[Channel]:
    from . import joints
    from .rig_constraints import endpoint

    _, owner = endpoint(ref)
    properties = (
        ["rotation"]
        if ref.bone and joints.KEY in owner
        else ["location", "rotation", "scale"]
    )
    return [
        TransformChannel.model_validate(
            dict(object_name=ref.object_name, bone=ref.bone, property=prop, axis=axis)
        )
        for prop in properties
        for axis in "xyz"
    ]


def guard(
    ref: RigEndpoint, keying: MatchKeying | None, *, constrained: bool = False
) -> None:
    from .rig_constraints import endpoint, fail

    obj, owner = endpoint(ref, edit=True)
    if (
        (owner.constraints and not constrained)
        or any(owner.lock_location)
        or any(owner.lock_rotation)
        or any(owner.lock_scale)
    ):
        fail("Match destinations must be unlocked and have no unsupported constraints")
    if obj.animation_data and keying is None:
        fail(
            "Animated matching requires keying; "
            "actions are preserved and extended atomically"
        )
    if keying:
        for spec in transforms(ref):
            target = channels.resolve(spec, write=True)
            if target.driver():
                fail(
                    "Match destination channels are driven; "
                    "match their source controls instead"
                )


class Keys:
    def __init__(self, keying: MatchKeying | None, specs: list[Channel]):
        self.keying, self.specs = keying, specs
        self.anchor: list[float] = []
        self.frame = bpy.context.scene.frame_current
        if keying is None:
            return
        scene = bpy.context.scene
        if scene.frame_subframe or keying.anchor_frame >= self.frame:
            channels.fail(
                "Keyed matching requires an integer current frame after anchor_frame"
            )
        action = bpy.data.actions.get(keying.action_name)
        if action:
            actions.validate(actions.get(keying.action_name), edit=True)
        for spec in specs:
            target = channels.resolve(spec, write=True)
            if target.driver():
                channels.fail(
                    "Cannot key driven destinations; match their source controls"
                )
            animation = target.owner.animation_data
            if animation and animation.action not in (None, action):
                channels.fail(
                    "Keyed matching must extend the controls' existing active action"
                )
            if (
                animation
                and animation.action
                and (
                    animation.action_blend_type != "REPLACE"
                    or animation.action_influence != 1
                )
            ):
                channels.fail("Keyed matching requires a full-influence REPLACE action")
        try:
            scene.frame_set(keying.anchor_frame)
            self.anchor = [channels.resolve(s).value() for s in specs]
        finally:
            scene.frame_set(self.frame)

    @contextmanager
    def commit(self) -> Iterator[Any]:
        if self.keying is None:
            yield None
            return
        edits = [
            ActionChannel(
                target=spec,
                keys=[
                    MotionKey(
                        frame=self.keying.anchor_frame,
                        value=before,
                        interpolation="CONSTANT",
                    ),
                    MotionKey(
                        frame=self.frame,
                        value=channels.resolve(spec).value(),
                        interpolation="CONSTANT",
                    ),
                ],
            )
            for spec, before in zip(self.specs, self.anchor, strict=True)
        ]
        with actions.staged_edit(
            ActionEditArguments(
                name=self.keying.action_name,
                create=bpy.data.actions.get(self.keying.action_name) is None,
                channels=edits,
            ),
            activate=True,
        ) as result:
            yield result
