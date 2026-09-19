"""Independent native realization for small test comparisons only."""

import importlib
from typing import Any

import bpy  # type: ignore[import-not-found]

package = "bl_ext.user_default.tyvrana_blender"
growth_nodes = importlib.import_module(package + ".growth_nodes")
growth = importlib.import_module(package + ".growth")
sample_indices = importlib.import_module(package + ".layer_math").sample_indices


def template_points(obj: Any, group: Any, count: int) -> list[Any]:
    graph = growth_nodes.GrowthGraph("Growth template QA")
    clone = None
    evaluated = None
    mesh = None
    try:
        source = graph.info(obj, "Evaluated growth", "ORIGINAL")
        realize = graph.node("GeometryNodeRealizeInstances")
        graph.wire(source.outputs["Geometry"], realize.inputs["Geometry"])
        graph.wire(realize.outputs["Geometry"], graph.output.inputs["Geometry"])
        carrier = bpy.data.meshes.new("Growth template QA")
        clone = bpy.data.objects.new("Growth template QA", carrier)
        clone.matrix_world = obj.matrix_world.copy()
        mod = clone.modifiers.new("Template QA", "NODES")
        mod.node_group = graph.group
        bpy.context.scene.collection.objects.link(clone)
        clone.hide_set(True)
        clone.hide_render = True
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        evaluated = clone.evaluated_get(dg)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
        if mesh is None:
            return []
        if len(mesh.vertices) > 2000000:
            growth.fail("Template QA exceeds2000000 evaluated vertices")
        return [
            obj.matrix_world @ mesh.vertices[i].co
            for i in sample_indices(range(len(mesh.vertices)), count)
        ]
    finally:
        if evaluated is not None and mesh is not None:
            evaluated.to_mesh_clear()
        if clone:
            clone_data = clone.data
            bpy.data.objects.remove(clone, do_unlink=True)
            if not clone_data.users:
                bpy.data.meshes.remove(clone_data)
        bpy.data.node_groups.remove(graph.group)
