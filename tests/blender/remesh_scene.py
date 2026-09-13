"""A closed, unevenly tessellated organic surface for blockout verification."""

import math

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]


def prepare_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    segments, rings = 40, 20
    points = [(0.0, 0.0, 0.8)]
    for j in range(1, rings):
        theta = math.pi * (j / rings) ** 1.25
        for i in range(segments):
            t = i / segments
            phi = 2 * math.pi * (t + 0.12 * math.sin(2 * math.pi * t))
            x = 1.6 * math.sin(theta) * math.cos(phi)
            y = 1.1 * math.sin(theta) * math.sin(phi)
            z = 0.8 * math.cos(theta)
            points.append((x, y, z))
    points.append((0.0, 0.0, -0.8))
    faces: list[tuple[int, ...]] = []
    for i in range(segments):
        n = (i + 1) % segments
        faces.append((0, 1 + i, 1 + n))
        for j in range(rings - 2):
            a = 1 + j * segments
            faces.append((a + i, a + segments + i, a + segments + n, a + n))
        a = 1 + (rings - 2) * segments
        faces.append((a + n, a + i, len(points) - 1))
    data = bpy.data.meshes.new("Surface")
    data.from_pydata(points, [], faces)
    data.update()
    obj = bpy.data.objects.new("Surface", data)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    material = bpy.data.materials.new("Material")
    node = material.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (0.28, 0.35, 0.39, 1)
    node.inputs["Roughness"].default_value = 0.5
    data.materials.append(material)
    camera = bpy.data.cameras.new("Camera")
    camera.type = "ORTHO"
    camera.ortho_scale = 4.0
    camera_obj = bpy.data.objects.new("Camera", camera)
    bpy.context.scene.collection.objects.link(camera_obj)
    camera_obj.location = (0, -0.5, 6)
    camera_obj.rotation_euler = (
        (-camera_obj.location).to_track_quat("-Z", "Y").to_euler()
    )
    scene = bpy.context.scene
    scene.camera = camera_obj
    for name, position, energy in [
        ("Light", (-3, 3, 4), 450),
        ("Light.001", (3, -2, 3), 140),
    ]:
        light = bpy.data.lights.new(name, "AREA")
        light.energy = energy
        light.shape = "DISK"
        light.size = 3
        light_obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(light_obj)
        light_obj.location = position
        light_obj.rotation_euler = (
            (-Vector(position)).to_track_quat("-Z", "Y").to_euler()
        )
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x = scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    scene.world.color = (0.04, 0.04, 0.04)
    bpy.context.view_layer.update()
