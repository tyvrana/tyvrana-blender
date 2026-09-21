"""Bounded mechanics dependencies, distinct from unrelated scene inventory."""

from typing import Any

import bpy  # type: ignore[import-not-found]

from . import motion_channels as channels
from .motion_models import MotionSampleArguments


def object_dependencies(obj: Any) -> set[Any]:
    result = {obj.parent} if obj.parent else set()
    owners = [obj, *list(obj.pose.bones)] if obj.type == "ARMATURE" else [obj]
    settings = [*obj.modifiers, *(c for owner in owners for c in owner.constraints)]
    for item in settings:
        for prop in item.bl_rna.properties:
            if prop.type == "POINTER" and prop.identifier != "rna_type":
                value = getattr(item, prop.identifier, None)
                if isinstance(value, bpy.types.Object):
                    result.add(value)
        if item.type == "NODES":
            # Some native node settings expose RNA but do not support IDProperties.
            # Their object pointers are already inspected above and in node sockets.
            try:
                properties = list(item.values())
            except TypeError:
                properties = []
            for value in properties:
                if isinstance(value, bpy.types.Object):
                    result.add(value)
            pending = [item.node_group] if item.node_group else []
            visited = set()
            node_count = 0
            while pending:
                group = pending.pop()
                if group in visited:
                    continue
                visited.add(group)
                node_count += len(group.nodes)
                if len(visited) > 64 or node_count > 4096:
                    channels.fail(
                        "Motion node dependency traversal exceeds bounded scope"
                    )
                for node in group.nodes:
                    for socket in node.inputs:
                        value = getattr(socket, "default_value", None)
                        if isinstance(value, bpy.types.Object):
                            result.add(value)
                        elif isinstance(value, bpy.types.Collection):
                            result.update(value.all_objects)
                    if getattr(node, "node_tree", None):
                        pending.append(node.node_tree)
    data_owners = [obj, obj.data]
    if obj.type == "MESH" and obj.data.shape_keys:
        data_owners.append(obj.data.shape_keys)
    for owner in data_owners:
        animation = getattr(owner, "animation_data", None)
        if animation:
            for curve in animation.drivers:
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        if isinstance(target.id, bpy.types.Object):
                            result.add(target.id)
    return result


def resolve(args: MotionSampleArguments, catalog: Any, layer_catalog: Any) -> list[Any]:
    names = set(args.scope)
    scene = bpy.context.scene

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key
                    in {
                        "object",
                        "object_name",
                        "armature_object",
                        "source",
                        "source_name",
                        "target_name",
                        "target",
                        "surface",
                        "frame",
                        "reference",
                        "name",
                    }
                    and isinstance(item, str)
                    and scene.objects.get(item)
                ):
                    names.add(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(args.model_dump())
    names.update(args.objects)
    for name in args.couplings:
        collect(catalog[name].model_dump())
    if args.layers:
        for query in args.layers.queries:
            if query.mode == "reference" and query.name in layer_catalog:
                value = layer_catalog[query.name]
                collect(value.model_dump() if hasattr(value, "model_dump") else value)
    selected = {channels.object_named(name) for name in names}
    pending = list(selected)
    while pending:
        obj = pending.pop()
        for dependency in object_dependencies(obj):
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
        if len(selected) > 256:
            channels.fail("Mechanics dependency scope exceeds256 objects")
    return sorted(selected, key=lambda obj: obj.name)


def restoration_objects(scope: list[Any]) -> list[Any]:
    # frame_set evaluates the scene globally. Capture off-scope animated owners
    # too: their raw channels may differ from the current frame's evaluated keys.
    # Static unrelated objects do not enter the restoration or geometry budgets.
    if bpy.app.handlers.frame_change_pre or bpy.app.handlers.frame_change_post:
        channels.fail("Scoped sampling cannot restore arbitrary frame-change callbacks")
    selected = set(scope)
    for obj in bpy.context.scene.objects:
        owners = [obj, obj.data]
        if obj.type == "MESH" and obj.data.shape_keys:
            owners.append(obj.data.shape_keys)
        if any(getattr(owner, "animation_data", None) for owner in owners):
            selected.add(obj)
    if len(selected) > 1024:
        channels.fail(
            "Restoration exceeds1024 animated/dependent objects; unrelated static"
            " objects are excluded"
        )
    return sorted(selected, key=lambda obj: obj.name)
