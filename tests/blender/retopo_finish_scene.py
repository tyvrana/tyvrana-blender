"""Curved cages with local density and boundary-finishing tasks."""

import importlib
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import retopo_scene


def prepare_scene(mode: str) -> None:
    retopo = importlib.import_module("bl_ext.user_default.tyvrana_blender.retopo")
    models = importlib.import_module(
        "bl_ext.user_default.tyvrana_blender.retopo_models"
    )
    if mode == "loop":
        retopo_scene.prepare_tube_scene()
        target = bpy.data.objects["Cage"]
        material = target.data.materials[0]
        levels = [-0.85, -0.65, -0.45, 0.45, 0.65, 0.85]
        retopo_scene.tube_data(target, 32, levels, list(range(len(levels) - 1)))
        target.data.materials.append(material)
    else:
        retopo_scene.prepare_scene()
        created = retopo.create_target(
            models.RetopoCreateArguments(source_object="Surface", name="Cage")
        )
        target = bpy.data.objects[created.target_object]
        points: list[tuple[float, float, float]] = []
        faces: list[list[int]] = []
        if mode == "seam":
            for xs in [[-0.8, -0.4, -0.004], [0.004, 0.4, 0.8]]:
                base = len(points)
                points += [(x, y, 0.8) for y in [-0.4, -0.2, 0, 0.2, 0.4] for x in xs]
                for y in range(4):
                    for x in range(2):
                        a = base + y * 3 + x
                        faces.append([a, a + 1, a + 4, a + 3])
        else:
            xs = (
                [-0.9, -0.6, -0.55, -0.5, -0.45, 0, 0.45, 0.9]
                if mode == "flow"
                else [-0.9 + i * 0.225 for i in range(9)]
            )
            ys = (
                [-0.5 + i * 0.2 for i in range(6)]
                if mode == "flow"
                else [-0.6 + i * 0.2 for i in range(7)]
            )
            points = [(x, y, 0.8) for y in ys for x in xs]
            for y in range(len(ys) - 1):
                for x in range(len(xs) - 1):
                    if mode == "gap" and x in {3, 4} and y in {2, 3}:
                        continue
                    a = y * len(xs) + x
                    faces.append([a, a + 1, a + len(xs) + 1, a + len(xs)])
        used = sorted({v for face in faces for v in face})
        mapping = {v: i for i, v in enumerate(used)}
        data = bpy.data.meshes.new("Cage surface")
        data.from_pydata(
            [points[v] for v in used], [], [[mapping[v] for v in f] for f in faces]
        )
        old = target.data
        target.data = data
        if old.users == 0:
            bpy.data.meshes.remove(old)
        target.data.materials.append(bpy.data.materials["Cage display"])
        if mode == "flow":
            # An existing explicit 3/5-valence transition beside the dense strip.
            edge = next(e for e in data.edges if set(e.vertices) == {21, 29})
            retopo.execute(
                models.RetopoRotateArguments(
                    source_object="Surface",
                    target_object="Cage",
                    edge={"mode": "indices", "domain": "edge", "indices": [edge.index]},
                )
            )
    arguments: Any
    if mode == "flow":
        arguments = models.RetopoRelaxArguments(
            source_object="Surface",
            target_object="Cage",
            selector={"mode": "all", "domain": "vertex"},
            iterations=3,
            factor=0.15,
            preserve_boundary=False,
            surface_offset=0.015,
        )
    else:
        arguments = models.RetopoProjectArguments(
            source_object="Surface",
            target_object="Cage",
            selector={"mode": "all", "domain": "vertex"},
            surface_offset=0.015,
        )
    retopo.execute(arguments)
    bpy.context.view_layer.update()
