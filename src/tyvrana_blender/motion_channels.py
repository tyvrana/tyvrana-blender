"""Resolve only enumerated scalar RNA channels; never evaluate supplied paths."""

import json
import math
from dataclasses import dataclass
from typing import Any, Never

import bpy  # type: ignore[import-not-found]
from bpy_extras import anim_utils  # type: ignore[import-not-found]

from . import organization, rig
from .motion_models import (
    Channel,
    ConstraintChannel,
    PropertyChannel,
    ShapeChannel,
    TransformChannel,
)
from .operations import OperationError

CONTROL_PREFIX = "tyvrana_control_"
CONTROLS = "_tyvrana_scalar_controls"


def fail(message: str) -> Never:
    raise OperationError("motion_invalid", message)


def editable(owner: Any) -> None:
    if owner.library or owner.override_library or not owner.is_editable:
        fail("Motion requires local editable data without library overrides")


def object_named(name: str) -> Any:
    rig.idle()
    obj = organization.object_named(name)
    if obj.name not in bpy.context.view_layer.objects:
        fail("Motion objects must belong to the current view layer")
    return obj


def control_catalog(obj: Any) -> dict[str, list[float]]:
    raw = obj.get(CONTROLS, "{}")
    try:
        if not isinstance(raw, str) or len(raw) > 16384:
            raise ValueError
        result = json.loads(raw)
        if not isinstance(result, dict) or len(result) > 64:
            raise ValueError
        for name, bounds in result.items():
            if (
                not isinstance(name, str)
                or not isinstance(bounds, list)
                or len(bounds) != 2
                or not all(
                    isinstance(v, int | float) and math.isfinite(v) for v in bounds
                )
                or bounds[0] > bounds[1]
            ):
                raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise OperationError(
            "motion_invalid", "Scalar control metadata is invalid"
        ) from exc


@dataclass
class Resolved:
    spec: Channel
    obj: Any
    owner: Any
    container: Any
    path: str
    property: str
    index: int
    minimum: float
    maximum: float

    def value(self, *, evaluated: bool = False) -> float:
        owner = self.owner
        if evaluated:
            graph = bpy.context.evaluated_depsgraph_get()
            if isinstance(self.spec, TransformChannel):
                obj = self.obj.evaluated_get(graph)
                if self.spec.bone:
                    bone = obj.pose.bones[self.spec.bone]
                    matrix = obj.convert_space(
                        pose_bone=bone,
                        matrix=bone.matrix,
                        from_space="POSE",
                        to_space="LOCAL",
                    )
                else:
                    matrix = obj.matrix_local
                component = {
                    "location": lambda: matrix.to_translation(),
                    "rotation": lambda: matrix.to_euler("XYZ"),
                    "scale": lambda: matrix.to_scale(),
                }[self.spec.property]()
                return float(component[self.index])
            owner = owner.evaluated_get(graph)
        value = owner.path_resolve(self.path)
        result = float(
            value[self.index] if isinstance(self.spec, TransformChannel) else value
        )
        if not math.isfinite(result):
            fail("Channel evaluated to a nonfinite value")
        return result

    def driver(self) -> Any:
        animation = self.owner.animation_data
        return (
            animation.drivers.find(self.path, index=self.index) if animation else None
        )

    def keyed(self) -> bool:
        animation = self.owner.animation_data
        if not animation:
            return False
        if animation.nla_tracks:
            fail("NLA tracks are outside bounded motion authoring; preserve them")
        action = animation.action
        if not action or not animation.action_slot:
            return False
        bag = anim_utils.action_get_channelbag_for_slot(action, animation.action_slot)
        return bool(bag and bag.fcurves.find(self.path, index=self.index))

    def node(self) -> tuple[int, str]:
        # All components of a transform share native evaluation dependencies.
        if isinstance(self.spec, TransformChannel | ConstraintChannel):
            return (
                int(self.obj.as_pointer()),
                "bone:" + self.spec.bone if self.spec.bone else "transform",
            )
        return int(self.owner.as_pointer()), self.path


def resolve(spec: Channel, *, write: bool = False) -> Resolved:
    obj = object_named(spec.object_name)
    owner = obj
    container = obj
    minimum, maximum = -1e9, 1e9
    index = 0
    prop: str
    if isinstance(spec, TransformChannel | ConstraintChannel) and spec.bone:
        if obj.type != "ARMATURE" or spec.bone not in obj.pose.bones:
            fail(f'Pose bone "{spec.bone}" is missing on "{obj.name}"')
        container = obj.pose.bones[spec.bone]
    if isinstance(spec, TransformChannel):
        if spec.property == "rotation" and container.rotation_mode != "XYZ":
            fail(
                "Rotation channels require existing XYZ mode; initialize "
                "pose/transforms before motion authoring"
            )
        prop = "rotation_euler" if spec.property == "rotation" else spec.property
        index = "xyz".index(spec.axis)
        path = container.path_from_id(prop)
        if spec.bone and write:
            from . import joints
            from .joint_models import MAX_XZ, MAX_Y

            if joints.KEY in container:
                if spec.property != "rotation":
                    fail(
                        "Constrained joints accept rotation channels only; centers and "
                        "scale stay fixed"
                    )
                bound = MAX_Y if spec.axis == "y" else MAX_XZ
                minimum, maximum = -bound, bound
    elif isinstance(spec, ShapeChannel):
        if obj.type != "MESH" or not obj.data.shape_keys:
            fail("Shape channel requires an existing relative shape key")
        from . import correctives

        correctives.keys_guard(obj)
        owner = obj.data.shape_keys
        container = owner.key_blocks.get(spec.key)
        if container is None or container == owner.reference_key:
            fail("Shape target must be an existing non-Basis relative key")
        if write and (container.lock_shape or container.mute or obj.data.users != 1):
            fail("Shape target must be unlocked, unmuted and on exclusive mesh data")
        prop = "value"
        path = container.path_from_id(prop)
        minimum, maximum = container.slider_min, container.slider_max
    elif isinstance(spec, PropertyChannel):
        catalog = control_catalog(obj)
        prop = CONTROL_PREFIX + spec.property
        if spec.property not in catalog or not isinstance(obj.get(prop), float):
            fail("Scalar control is absent; create it with motion.set_properties")
        minimum, maximum = catalog[spec.property]
        path = "[" + json.dumps(prop) + "]"
    else:
        constraint = container.constraints.get(spec.constraint)
        if constraint is None:
            fail(f'Constraint "{spec.constraint}" does not exist')
        container = constraint
        prop = "influence"
        path = container.path_from_id(prop)
        minimum, maximum = 0.0, 1.0
    if write:
        editable(obj)
        editable(owner)
        if obj.data:
            editable(obj.data)
        if obj.type == "ARMATURE" and obj.data.users != 1:
            fail("Motion authoring requires exclusive armature data")
        if owner.animation_data and (
            owner.animation_data.nla_tracks or owner.animation_data.use_tweak_mode
        ):
            fail("NLA/tweak-mode data is protected; bounded actions use active slots")
        if not isinstance(spec, PropertyChannel) and container.is_property_readonly(
            prop
        ):
            fail("Target scalar is read-only")
    result = Resolved(spec, obj, owner, container, path, prop, index, minimum, maximum)
    result.value()
    return result


def check_value(channel: Resolved, value: float) -> None:
    if not math.isfinite(value) or not channel.minimum <= value <= channel.maximum:
        fail(
            f"Value for {channel.path} must lie in "
            f"[{channel.minimum}, {channel.maximum}]"
        )


def refresh() -> None:
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
    bpy.context.view_layer.update()


def clear_empty_animation(owner: Any) -> None:
    animation = owner.animation_data
    if (
        animation
        and not animation.action
        and not animation.drivers
        and not animation.nla_tracks
    ):
        owner.animation_data_clear()
