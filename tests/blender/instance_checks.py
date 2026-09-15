"""Native UV binding, deformation, preservation and allocation regression checks."""

import importlib
import math
import os
import tempfile
import unittest
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
api = importlib.import_module("bl_ext.user_default.tyvrana_blender.instances")
models = importlib.import_module("bl_ext.user_default.tyvrana_blender.instance_models")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")


class InstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.backend = adapter.BlenderBackend()
        self.backend.mesh_create(
            models.MeshCreateArguments(
                name="Surface",
                vertices=[[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]],
                faces=[[0, 1, 2, 3]],
                corner_uvs=[[0, 0], [1, 0], [1, 1], [0, 1]],
            )
        )
        self.backend.mesh_create(
            models.MeshCreateArguments(
                name="Asset",
                vertices=[[-0.02, 0, 0], [0.02, 0, 0], [0, 0.1, 0.01]],
                faces=[[0, 1, 2]],
                hidden=True,
            )
        )
        self.surface = bpy.data.objects["Surface"]
        self.spec = models.SurfaceDistribution(
            surface_object="Surface",
            prototype_object="Asset",
            uv_map="UVMap",
            guides=[[[-0.5, -0.5, 0], [-0.5, 0.5, 0]], [[0.5, -0.5, 0], [0.5, 0.5, 0]]],
            rows=3,
            columns=4,
            surface_offset=0.02,
            spacing_variation=0,
            direction_variation=0,
            stagger=0,
        )

    def create(self) -> Any:
        return self.backend.surface_instances_create(
            models.SurfaceInstancesCreateArguments(
                name="Instances",
                distribution=self.spec,
            )
        )

    def inspect(self) -> Any:
        return self.backend.surface_instances_inspect(
            models.SurfaceInstancesInspectArguments(
                object_name="Instances",
                sample_limit=4,
            )
        )

    def test_binding_frame_and_surface_preservation(self) -> None:
        before = [tuple(v.co) for v in self.surface.data.vertices]
        result = self.create()
        self.assertEqual(result.instance_count, 12)
        self.assertEqual(result.evaluated_instance_count, 12)
        self.assertLess(result.root_position_max_error, 1e-5)
        self.assertEqual(before, [tuple(v.co) for v in self.surface.data.vertices])
        self.assertAlmostEqual(result.region_area, 1, places=4)
        for instance in bpy.context.evaluated_depsgraph_get().object_instances:
            if instance.is_instance:
                y = instance.matrix_world.to_3x3() @ Vector((0, 1, 0))
                z = instance.matrix_world.to_3x3() @ Vector((0, 0, 1))
                self.assertGreater(y.dot(Vector((0, 1, 0))), 0.999)
                self.assertGreater(z.dot(Vector((0, 0, 1))), 0.999)
                self.assertAlmostEqual(
                    instance.matrix_world.translation.z, 0.02, places=5
                )

    def test_existing_evaluated_inspection_with_owned_instances(self) -> None:
        self.create()
        package = "bl_ext.user_default.tyvrana_blender."
        uv_layout = importlib.import_module(package + "uv_layout")
        uv_models = importlib.import_module(package + "uv_models")
        arguments = uv_models.UVLayoutArguments(
            objects=["Surface"], uv_map="UVMap", evaluated=True
        )
        report, _ = uv_layout.inspect(arguments, None)
        self.assertAlmostEqual(report.world_area, 4)
        obj = bpy.data.objects["Instances"]
        obj.modifiers[0].node_group.nodes["Instances"].mute = True
        with self.assertRaises(operations.OperationError):
            uv_layout.inspect(arguments, None)

    def test_inspection_bounds_instance_dependencies(self) -> None:
        self.create()
        package = "bl_ext.user_default.tyvrana_blender."
        retopo = importlib.import_module(package + "retopo_geometry")
        bpy.data.objects["Asset"].modifiers.new("Unknown", "NODES")
        with self.assertRaises(operations.OperationError):
            retopo.graph(self.surface)

    def test_live_deformation_and_transform(self) -> None:
        self.create()
        before = self.inspect()
        for vertex in self.surface.data.vertices:
            vertex.co.z = vertex.co.y * 0.3 + 0.4
        self.surface.data.update()
        self.surface.location = (2, 3, 4)
        self.surface.rotation_euler.z = math.pi / 4
        bpy.context.view_layer.update()
        after = self.inspect()
        self.assertEqual(before.binding_sha256, after.binding_sha256)
        self.assertEqual(after.evaluated_instance_count, 12)
        self.assertLess(after.root_position_max_error, 1e-5)
        self.assertGreater(after.samples[0].root_world[2], 4)
        self.assertLess(after.samples[0].normal_world[2], 0.99)

    def test_update_is_deterministic_and_failure_preserves_system(self) -> None:
        self.create()
        before = self.inspect()
        update = models.SurfaceInstancesConfigureArguments(
            object_name="Instances", distribution=self.spec
        )
        self.backend.surface_instances_configure(update)
        self.assertEqual(before.binding_sha256, self.inspect().binding_sha256)
        obj = bpy.data.objects["Instances"]
        original = (obj.data, obj.modifiers[0].node_group, obj[api.KEY])
        bad = self.spec.model_copy(
            update={"guides": [[[0, 0, 5], [0, 1, 5]], [[1, 0, 5], [1, 1, 5]]]}
        )
        with self.assertRaises(operations.OperationError):
            self.backend.surface_instances_configure(
                update.model_copy(update={"distribution": bad})
            )
        self.assertEqual(
            original, (obj.data, obj.modifiers[0].node_group, obj[api.KEY])
        )

    def test_invalidated_uv_is_visible(self) -> None:
        self.create()
        for value in self.surface.data.uv_layers[0].uv:
            value.vector.x += 2
        self.surface.data.update()
        bpy.context.view_layer.update()
        result = self.inspect()
        self.assertEqual(result.invalid_binding_count, 12)
        self.assertEqual(result.evaluated_instance_count, 0)

    def test_shared_instance_data_is_protected(self) -> None:
        self.create()
        obj = bpy.data.objects["Instances"]
        sibling = obj.copy()
        bpy.context.scene.collection.objects.link(sibling)
        with self.assertRaises(operations.OperationError):
            self.backend.surface_instances_configure(
                models.SurfaceInstancesConfigureArguments(
                    object_name="Instances",
                    distribution=self.spec,
                )
            )

    def test_degenerate_mesh_does_not_publish_object(self) -> None:
        with self.assertRaises(operations.OperationError):
            self.backend.mesh_create(
                models.MeshCreateArguments(
                    name="Degenerate",
                    vertices=[[0, 0, 0], [1, 0, 0], [2, 0, 0]],
                    faces=[[0, 1, 2]],
                )
            )
        self.assertNotIn("Degenerate", bpy.data.objects)

    def test_edited_graph_is_preserved(self) -> None:
        self.create()
        obj = bpy.data.objects["Instances"]
        obj.modifiers[0].node_group.nodes["Root offset"].inputs[
            "Scale"
        ].default_value = 0.3
        with self.assertRaises(operations.OperationError):
            self.backend.surface_instances_configure(
                models.SurfaceInstancesConfigureArguments(
                    object_name="Instances",
                    distribution=self.spec,
                )
            )
        self.assertAlmostEqual(
            obj.modifiers[0]
            .node_group.nodes["Root offset"]
            .inputs["Scale"]
            .default_value,
            0.3,
        )

    def test_overlapping_uv_surface_is_rejected_without_leaks(self) -> None:
        self.backend.mesh_create(
            models.MeshCreateArguments(
                name="Overlap",
                vertices=[
                    [-1, -1, 0],
                    [1, -1, 0],
                    [1, 1, 0],
                    [-1, 1, 0],
                    [-1, -1, 0.1],
                    [1, -1, 0.1],
                    [1, 1, 0.1],
                    [-1, 1, 0.1],
                ],
                faces=[[0, 1, 2, 3], [4, 5, 6, 7]],
                corner_uvs=[[0, 0], [1, 0], [1, 1], [0, 1]] * 2,
            )
        )
        before = (
            len(bpy.data.objects),
            len(bpy.data.meshes),
            len(bpy.data.node_groups),
        )
        self.spec = self.spec.model_copy(update={"surface_object": "Overlap"})
        with self.assertRaises(operations.OperationError):
            self.create()
        self.assertEqual(
            before,
            (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.node_groups)),
        )

    def test_save_reopen_and_prototype_edit(self) -> None:
        self.create()
        before = self.inspect()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "instances.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
            after = self.inspect()
            self.assertEqual(after.binding_sha256, before.binding_sha256)
            self.assertEqual(after.evaluated_instance_count, 12)
            bpy.data.objects["Asset"].data.vertices[2].co.y *= 2
            bpy.data.objects["Asset"].data.update()
            bpy.context.view_layer.update()
            self.assertEqual(self.inspect().binding_sha256, before.binding_sha256)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(InstanceTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Native surface-instance tests failed")
    print("BLENDER_INSTANCE_TESTS_PASSED", result.testsRun)
