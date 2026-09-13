"""Curved reference surfaces and readable low-poly cages for render verification."""

import importlib
import math
from typing import Any

import bpy  # type: ignore[import-not-found]

from .remesh_scene import prepare_scene as base_scene

_display_modifiers: list[tuple[Any, Any]] = []


def display_begin(*_: Any) -> None:
    """Render actual authored edges using a temporary native display modifier."""
    assert not _display_modifiers
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and any(
            slot.material and slot.material.name == "Cage display"
            for slot in obj.material_slots
        ):
            modifier = obj.modifiers.new("Render cage display", "WIREFRAME")
            modifier.thickness = 0.009
            modifier.offset = 0
            modifier.use_replace = True
            _display_modifiers.append((obj, modifier))
    bpy.context.view_layer.update()


def display_end(*_: Any) -> None:
    for obj, modifier in _display_modifiers:
        obj.modifiers.remove(modifier)
    _display_modifiers.clear()


def clear_display() -> None:
    display_end()
    for handlers, callback in (
        (bpy.app.handlers.render_init, display_begin),
        (bpy.app.handlers.render_complete, display_end),
        (bpy.app.handlers.render_cancel, display_end),
    ):
        if callback in handlers:
            handlers.remove(callback)


def cage_material() -> Any:
    clear_display()
    material = bpy.data.materials.new("Cage display")
    node = material.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (0.02, 0.75, 1, 1)
    node.inputs["Emission Color"].default_value = (0.02, 0.75, 1, 1)
    node.inputs["Emission Strength"].default_value = 1
    bpy.app.handlers.render_init.append(display_begin)
    bpy.app.handlers.render_complete.append(display_end)
    bpy.app.handlers.render_cancel.append(display_end)
    return material


def prepare_scene(*, patch: bool = False) -> None:
    base_scene()
    source = bpy.data.objects["Surface"]
    mod = source.modifiers.new("Detail", "SUBSURF")
    mod.levels = 2
    bpy.ops.object.modifier_apply(modifier=mod.name)
    for face in source.data.polygons:
        face.use_smooth = True
    source.data.uv_layers.new(name="Source UV")
    material = cage_material()
    if patch:
        retopo = importlib.import_module("bl_ext.user_default.tyvrana_blender.retopo")
        models = importlib.import_module(
            "bl_ext.user_default.tyvrana_blender.retopo_models"
        )
        result = retopo.create_target(
            models.RetopoCreateArguments(source_object="Surface", name="Cage")
        )
        target = bpy.data.objects[result.target_object]
        retopo.execute(
            models.RetopoSeedArguments(
                source_object="Surface",
                target_object=target.name,
                center=[-0.55, 0, 0.7],
                tangent_direction=[1, 0, 0],
                width=0.8,
                height=0.7,
                u_segments=4,
                v_segments=3,
                surface_offset=0.015,
            )
        )
        for v in target.data.vertices:
            if v.index in {6, 7, 8, 11, 12, 13}:
                v.co.x += 0.065 * math.sin(v.index * 3)
                v.co.y += 0.055 * math.cos(v.index * 2)
        target.data.update()
        retopo.execute(
            models.RetopoProjectArguments(
                source_object="Surface",
                target_object=target.name,
                selector={"mode": "all", "domain": "vertex"},
                surface_offset=0.015,
            )
        )
        target.data.materials.append(material)
    bpy.context.view_layer.update()


def tube_data(obj: Any, count: int, levels: list[float], bands: list[int]) -> None:
    points: list[tuple[float, float, float]] = []
    for z in levels:
        radius = 1 + 0.1 * math.cos(math.pi * z)
        points.extend(
            (
                radius * math.cos(2 * math.pi * i / count),
                radius * math.sin(2 * math.pi * i / count),
                z,
            )
            for i in range(count)
        )
    faces = [
        (
            k * count + i,
            k * count + (i + 1) % count,
            (k + 1) * count + (i + 1) % count,
            (k + 1) * count + i,
        )
        for k in bands
        for i in range(count)
    ]
    data = bpy.data.meshes.new("Tube")
    data.from_pydata(points, [], faces)
    data.update()
    old = obj.data
    obj.data = data
    if old.users == 0:
        bpy.data.meshes.remove(old)


def prepare_tube_scene() -> None:
    base_scene()
    source = bpy.data.objects["Surface"]
    source_material = source.data.materials[0]
    tube_data(source, 64, [-1 + i / 16 for i in range(33)], list(range(32)))
    source.data.materials.append(source_material)
    for face in source.data.polygons:
        face.use_smooth = True
    retopo = importlib.import_module("bl_ext.user_default.tyvrana_blender.retopo")
    models = importlib.import_module(
        "bl_ext.user_default.tyvrana_blender.retopo_models"
    )
    result = retopo.create_target(
        models.RetopoCreateArguments(source_object="Surface", name="Cage")
    )
    target = bpy.data.objects[result.target_object]
    tube_data(target, 8, [-0.8, -0.4, 0.4, 0.8], [0, 2])
    retopo.execute(
        models.RetopoProjectArguments(
            source_object="Surface",
            target_object="Cage",
            selector={"mode": "all", "domain": "vertex"},
            surface_offset=0.02,
        )
    )
    target.data.materials.append(cage_material())
    camera = bpy.context.scene.camera
    camera.location = (3, -5, 2)
    camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
