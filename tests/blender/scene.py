"""Deterministic scene built only inside isolated integration-test profiles."""

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]


def prepare_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = False
    scene.render.film_transparent = False
    scene.render.use_compositing = False
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1))
    cube = bpy.context.object
    cube.name = "RenderCube"
    material = bpy.data.materials.new("RenderRed")
    assert material.node_tree is not None
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (0.65, 0.025, 0.015, 1)
    shader.inputs["Roughness"].default_value = 0.35
    cube.data.materials.append(material)
    bpy.ops.mesh.primitive_plane_add(size=200)
    bpy.context.object.name = "RenderGround"
    ground = bpy.data.materials.new("RenderGray")
    assert ground.node_tree is not None
    ground.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.16,
        0.19,
        0.24,
        1,
    )
    bpy.context.object.data.materials.append(ground)
    bpy.ops.object.camera_add(location=(6, -6, 4.5))
    camera = bpy.context.object
    camera.rotation_euler = (
        (Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.lens = 48
    scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(2, -3, 6))
    light = bpy.context.object
    light.data.energy = 1400
    light.data.shape = "DISK"
    light.data.size = 4
    if scene.world is None:
        scene.world = bpy.data.worlds.new("RenderWorld")
    assert scene.world.node_tree is not None
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (
        0.08,
        0.1,
        0.15,
        1,
    )
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.3


def prepare_lighting_scene() -> None:
    """Neutral scene with no emitters; tests create all lights through operations."""
    prepare_scene()
    for obj in list(bpy.context.scene.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)
    for material in bpy.data.materials:
        if material.node_tree is not None:
            shader = material.node_tree.nodes.get("Principled BSDF")
            if shader is not None:
                shader.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1)
                shader.inputs["Roughness"].default_value = 1
    scene = bpy.context.scene
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.cycles.samples = 32
    scene.cycles.seed = 0
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0


def prepare_material_scene() -> None:
    """Fixed studio sphere; the client creates and assigns its tested materials."""
    prepare_scene()
    scene = bpy.context.scene
    bpy.data.objects.remove(bpy.data.objects["RenderCube"], do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, location=(0, 0, 1))
    subject = bpy.context.object
    subject.name = "RenderSubject"
    for polygon in subject.data.polygons:
        polygon.use_smooth = True
    camera = scene.camera
    camera.location = (0, -7, 3)
    camera.rotation_euler = (
        (Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 5
    light = next(obj for obj in scene.objects if obj.type == "LIGHT")
    light.location = (-3, -4, 6)
    light.rotation_euler = (
        (Vector((0, 0, 1)) - light.location).to_track_quat("-Z", "Y").to_euler()
    )
    light.data.shape = "SQUARE"
    light.data.size = 1.5
    light.data.energy = 1200
    for material in bpy.data.materials:
        shader = material.node_tree.nodes.get("Principled BSDF")
        if shader is not None:
            shader.inputs["Base Color"].default_value = (0.18, 0.18, 0.18, 1)
            shader.inputs["Roughness"].default_value = 0.8
    custom = bpy.data.materials.new("CustomGraph")
    tree = custom.node_tree
    shader = tree.nodes.get("Principled BSDF")
    value = tree.nodes.new("ShaderNodeValue")
    tree.links.new(value.outputs["Value"], shader.inputs["Roughness"])
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.cycles.samples = 64
    scene.cycles.seed = 0
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (
        0.1,
        0.1,
        0.1,
        1,
    )
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.3


def prepare_shader_scene() -> None:
    """Front-facing UV plane under fixed neutral studio illumination."""
    prepare_material_scene()
    scene = bpy.context.scene
    bpy.data.objects.remove(bpy.data.objects["RenderSubject"], do_unlink=True)
    bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, 1))
    bpy.context.object.name = "RenderSubject"
    assert bpy.context.object.data.uv_layers.active is not None
    scene.camera.location = (0, 0, 7)
    scene.camera.rotation_euler = (0, 0, 0)
    light = next(obj for obj in scene.objects if obj.type == "LIGHT")
    light.location = (-2, -3, 6)
    light.rotation_euler = (
        (Vector((0, 0, 1)) - light.location).to_track_quat("-Z", "Y").to_euler()
    )
    light.data.size = 5
    light.data.energy = 400
    scene.cycles.samples = 32


def prepare_mesh_scene() -> None:
    """Authored short box with room above it for regional modeling renders."""
    prepare_scene()
    scene = bpy.context.scene
    subject = bpy.data.objects["RenderCube"]
    subject.name = "Surface"
    subject.location = (0, 0, 0)
    for vertex in subject.data.vertices:
        vertex.co.z = (vertex.co.z + 1) * 0.5
    subject.data.update()
    material = subject.data.materials[0]
    material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.25
    camera = scene.camera
    camera.location = (5, -7, 5)
    camera.rotation_euler = (
        (Vector((0, 0, 1.1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 5
    light = next(obj for obj in scene.objects if obj.type == "LIGHT")
    light.location = (-3, -4, 6)
    light.rotation_euler = (
        (Vector((0, 0, 0.5)) - light.location).to_track_quat("-Z", "Y").to_euler()
    )
    light.data.size = 3
    scene.cycles.samples = 16
    scene.cycles.seed = 0
