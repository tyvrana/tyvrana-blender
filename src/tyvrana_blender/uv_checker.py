"""Temporary UV-grid diagnostics on actual object/modifier stacks."""

from collections.abc import Iterator
from contextlib import contextmanager

import bpy  # type: ignore[import-not-found]

from . import uv
from .models import UVCheckerRenderOptions
from .operations import OperationError


@contextmanager
def display(options: UVCheckerRenderOptions | None) -> Iterator[None]:
    if options is None:
        yield
        return
    objects = [uv.mesh_object(name) for name in options.objects]
    excluded = []
    for name in options.exclude_objects:
        matches = [obj for obj in bpy.context.scene.objects if obj.name == name]
        if len(matches) != 1:
            raise OperationError(
                "object_not_found",
                "Excluded render object must exist uniquely in this scene",
            )
        excluded.append(matches[0])
    for obj in objects:
        if (
            obj.mode != "OBJECT"
            or not obj.is_editable
            or obj not in list(bpy.context.scene.objects)
        ):
            raise OperationError(
                "invalid_context",
                "Checker rendering requires editable scene objects in Object Mode",
            )
        uv.layer_for(obj.data, options.uv_map)
    saved = [
        (
            obj,
            obj.data,
            obj.hide_render,
            [(slot.link, slot.material) for slot in obj.material_slots],
        )
        for obj in objects
    ]
    hidden = [(obj, obj.hide_render) for obj in excluded]
    copies = []
    material = image = None
    overrides = [
        (layer, layer.material_override) for layer in bpy.context.scene.view_layers
    ]
    try:
        image = bpy.data.images.new("UV diagnostic grid", width=1024, height=1024)
        image.generated_type = "COLOR_GRID"
        material = bpy.data.materials.new("UV diagnostic")
        tree = material.node_tree
        bsdf = tree.nodes.get("Principled BSDF")
        bsdf.inputs["Roughness"].default_value = 0.8
        coordinates = tree.nodes.new("ShaderNodeUVMap")
        coordinates.uv_map = options.uv_map
        scale = tree.nodes.new("ShaderNodeVectorMath")
        scale.operation = "SCALE"
        scale.inputs["Scale"].default_value = options.grid_scale
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        tree.links.new(coordinates.outputs["UV"], scale.inputs[0])
        tree.links.new(scale.outputs["Vector"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
        tree.links.new(texture.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 0.12
        for obj, original, _, _ in saved:
            copied = original.copy()
            copies.append(copied)
            copied.materials.clear()
            copied.materials.append(material)
            for face in copied.polygons:
                face.material_index = 0
            obj.data = copied
            for slot in obj.material_slots:
                slot.link = "DATA"
            obj.hide_render = False
        for obj, _ in hidden:
            obj.hide_render = True
        for layer, _ in overrides:
            layer.material_override = None
        bpy.context.view_layer.update()
        yield
    finally:
        for obj, original, hide_render, slots in saved:
            obj.data = original
            obj.hide_render = hide_render
            for slot, (link, assigned) in zip(obj.material_slots, slots, strict=True):
                slot.link = link
                if link == "OBJECT":
                    slot.material = assigned
        for obj, state in hidden:
            obj.hide_render = state
        for layer, override in overrides:
            layer.material_override = override
        for copied in copies:
            bpy.data.meshes.remove(copied)
        if material is not None:
            bpy.data.materials.remove(material)
        if image is not None:
            bpy.data.images.remove(image)
        bpy.context.view_layer.update()
