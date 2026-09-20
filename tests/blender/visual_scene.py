"""Unrelated scene load for regional diagnostic integration tests."""

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]


def prepare_scene() -> None:
    for i in range(20):
        mesh = bpy.data.meshes.new(f"Diagnostic{i:02}")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=0.5)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(mesh.name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (4 + i % 5, i // 5, 0)
    for i in range(360):
        bpy.context.scene.collection.objects.link(
            bpy.data.objects.new(f"Unrelated{i:03}", None)
        )
