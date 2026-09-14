"""Temporary world-space cage geometry for bounded native wire rendering."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import modifiers, retopo_geometry
from .models import WireframeRenderOptions
from .operations import OperationError

MAX_WIRE_EDGES = 8192


@contextmanager
def display(options: WireframeRenderOptions | None) -> Iterator[None]:
    """Render evaluated cage edges without altering authored data or modifiers."""
    if options is None:
        yield
        return
    originals = [modifiers.object_mesh(name) for name in options.objects]
    if any(not obj.is_editable for obj in originals):
        raise OperationError(
            "invalid_context", "Wire display requires editable objects"
        )
    data_copies: list[Any] = []
    helpers: list[Any] = []
    hidden = [(obj, obj.hide_render) for obj in originals]
    material = None
    try:
        depsgraph = None
        for obj in originals:
            depsgraph = retopo_geometry.graph(obj)
        edge_count = 0
        # Validate/evaluate before changing visibility or constructing helpers.
        for obj in originals:
            with retopo_geometry.evaluated_mesh(obj, depsgraph) as (data, transform):
                edge_count += len(data.edges)
                if edge_count > MAX_WIRE_EDGES:
                    raise OperationError(
                        "work_limit_exceeded",
                        "Wire display exceeds 8192 evaluated edges",
                    )
                copied = data.copy()
                data_copies.append(copied)
                copied.transform(transform)
                copied.update()
        material = bpy.data.materials.new("Wire display")
        node = material.node_tree.nodes.get("Principled BSDF")
        node.inputs["Base Color"].default_value = (0.02, 0.75, 1, 1)
        node.inputs["Emission Color"].default_value = (0.02, 0.75, 1, 1)
        node.inputs["Emission Strength"].default_value = 1
        for data in data_copies:
            data.materials.clear()
            data.materials.append(material)
            for polygon in data.polygons:
                polygon.material_index = 0
            helper = bpy.data.objects.new("Wire display", data)
            helpers.append(helper)
            bpy.context.scene.collection.objects.link(helper)
            helper.hide_viewport = True
            if options.surface_offset:
                offset = helper.modifiers.new("Display offset", "DISPLACE")
                offset.direction = "NORMAL"
                offset.mid_level = 0
                offset.strength = options.surface_offset
            wire = helper.modifiers.new("Display edges", "WIREFRAME")
            wire.thickness = options.thickness
            wire.offset = 0
            wire.use_replace = True
        for obj, _ in hidden:
            obj.hide_render = True
        bpy.context.view_layer.update()
        yield
    finally:
        for obj, state in hidden:
            obj.hide_render = state
        for obj in helpers:
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in data_copies:
            bpy.data.meshes.remove(data)
        if material is not None:
            bpy.data.materials.remove(material)
        bpy.context.view_layer.update()
