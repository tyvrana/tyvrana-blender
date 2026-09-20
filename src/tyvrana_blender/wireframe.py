"""Temporary world-space cage geometry for bounded native wire rendering."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

from . import modifiers, retopo_geometry
from .errors import OperationError
from .models import WireframeRegion, WireframeRenderOptions


def retain_region(data: Any, region: WireframeRegion) -> None:
    """Discard distant faces on a temporary world-space copy, without cutting edges."""
    bm = bmesh.new()
    try:
        bm.from_mesh(data)
        outside = [
            face
            for face in bm.faces
            if any(
                max(v.co[k] for v in face.verts) < region.minimum[k]
                or min(v.co[k] for v in face.verts) > region.maximum[k]
                for k in range(3)
            )
        ]
        bmesh.ops.delete(bm, geom=outside, context="FACES")
        unused = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=unused, context="VERTS")
        bm.to_mesh(data)
    finally:
        bm.free()


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
                copied = data.copy()
                data_copies.append(copied)
                copied.transform(transform)
                if options.region is not None:
                    retain_region(copied, options.region)
                edge_count += len(copied.edges)
                if edge_count > options.max_edges:
                    raise OperationError(
                        "work_limit_exceeded",
                        f"Wire display exceeds {options.max_edges} evaluated edges; "
                        "select fewer objects or a smaller inspection focus/region",
                    )
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
