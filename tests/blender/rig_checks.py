"""Native FK, skinning, persistence, ownership and rollback regressions."""

import importlib
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest

PACKAGE = "bl_ext.user_default.tyvrana_blender."
adapter = importlib.import_module(PACKAGE + "blender")
api = importlib.import_module(PACKAGE + "rig")
models = importlib.import_module(PACKAGE + "rig_models")
mesh_models = importlib.import_module(PACKAGE + "instance_models")
operations = importlib.import_module(PACKAGE + "operations")
file_models = importlib.import_module(PACKAGE + "file_models")


class RigTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.backend = adapter.BlenderBackend()
        vertices = [[x, i / 5, 0.0] for i in range(11) for x in [-0.1, 0.0, 0.1]]
        faces = [
            [i * 3 + j, i * 3 + j + 1, (i + 1) * 3 + j + 1, (i + 1) * 3 + j]
            for i in range(10)
            for j in range(2)
        ]
        self.backend.mesh_create(
            mesh_models.MeshCreateArguments(
                name="Surface",
                vertices=vertices,
                faces=faces,
                corner_uvs=[
                    [vertices[v][0] + 0.5, vertices[v][1] / 2] for f in faces for v in f
                ],
            )
        )
        self.obj = bpy.data.objects["Surface"]
        self.args = models.ArmatureCreateArguments(
            name="Rig",
            bones=[
                models.RestBone(
                    name="Root",
                    head=[0.0, 0.0, 0.0],
                    tail=[0.0, 1.0, 0.0],
                    head_radius=0.2,
                    tail_radius=0.2,
                    envelope_distance=0.4,
                ),
                models.RestBone(
                    name="Tip",
                    head=[0.0, 1.0, 0.0],
                    tail=[0.0, 2.0, 0.0],
                    parent="Root",
                    connected=True,
                    head_radius=0.2,
                    tail_radius=0.2,
                    envelope_distance=0.4,
                ),
            ],
        )

    def make(self) -> Any:
        return self.backend.armature_create(self.args)

    def bind(self) -> Any:
        return self.backend.armature_bind(
            models.ArmatureBindArguments(
                object_name="Surface",
                armature_object="Rig",
                weights=models.EnvelopeWeights(bones=["Root", "Tip"]),
            )
        )

    def pose(self, angle: float = math.pi / 2) -> Any:
        return self.backend.armature_pose(
            models.ArmaturePoseArguments(
                object_name="Rig",
                bones=[models.PoseBone(name="Tip", rotation=[0.0, 0.0, angle])],
            )
        )

    def inspect(self) -> Any:
        return self.backend.armature_inspect(
            models.ArmatureInspectArguments(object_name="Rig")
        )

    def deform(self) -> Any:
        return self.backend.deformation_inspect(
            models.DeformationInspectArguments(
                armature_object="Rig", objects=["Surface"], sample_limit=4
            )
        )

    def authored(self) -> Any:
        return (
            [tuple(v.co) for v in self.obj.data.vertices],
            [tuple(p.vertices) for p in self.obj.data.polygons],
            [[tuple(i.vector) for i in u.uv] for u in self.obj.data.uv_layers],
        )

    def allocations(self) -> Any:
        return (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.armatures))

    def test_hierarchy_selection_and_mode_preserved(self) -> None:
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        result = self.make()
        self.assertEqual((result.bone_count, result.root_count), (2, 1))
        tip = next(b for b in result.bones if b.name == "Tip")
        self.assertEqual(tip.parent, "Root")
        self.assertTrue(tip.connected)
        self.assertEqual(tip.rest_head_world, [0, 1, 0])
        self.assertEqual(bpy.context.mode, "OBJECT")
        self.assertEqual(list(bpy.context.selected_objects), [self.obj])
        self.assertEqual(bpy.context.view_layer.objects.active, self.obj)

    def test_binding_pose_and_evaluated_comparison(self) -> None:
        authored = self.authored()
        self.make()
        bound = self.bind()
        self.assertEqual(bound.unweighted_vertex_count, 0)
        self.assertLessEqual(bound.maximum_influences, 2)
        self.assertAlmostEqual(bound.weight_sum_min, 1, places=6)
        self.assertEqual(self.authored(), authored)
        folded = self.deform().meshes[0]
        self.assertLess(folded.displacement_max, 1e-6)
        self.pose()
        result = self.deform()
        self.assertGreater(result.meshes[0].displacement_max, 1)
        self.assertGreater(result.meshes[0].changed_vertex_count, 10)
        self.assertEqual(result.meshes[0].topology_sha256, folded.topology_sha256)
        self.assertEqual(self.authored(), authored)
        self.assertEqual(result.restored_pose_position, "pose")
        self.backend.armature_pose(
            models.ArmaturePoseArguments(object_name="Rig", reset=True)
        )
        self.assertLess(self.deform().meshes[0].displacement_max, 1e-6)

    def test_invalid_hierarchy_dispatch_does_not_allocate(self) -> None:
        before = self.allocations()
        args = self.args.model_dump(mode="json")
        args["bones"][0]["parent"] = "Tip"
        response = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="cycle",
                operation="blender.armature.create",
                arguments=args,
            ),
        )
        self.assertIsInstance(response, OperationFailure)
        self.assertEqual(response.error.code, "invalid_arguments")
        self.assertEqual(before, self.allocations())

    def test_creation_failure_restores_resources_and_context(self) -> None:
        before = self.allocations()
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        with patch.object(
            api, "inspect", side_effect=RuntimeError("injected finalization failure")
        ):
            with self.assertRaises(RuntimeError):
                self.make()
        self.assertEqual(before, self.allocations())
        self.assertEqual(bpy.context.mode, "OBJECT")
        self.assertEqual(bpy.context.view_layer.objects.active, self.obj)

    def test_wrong_type_missing_bone_and_failed_pose_are_atomic(self) -> None:
        with self.assertRaises(operations.OperationError):
            api.armature("Surface")
        self.make()
        before = self.inspect().pose_sha256
        with self.assertRaises(operations.OperationError):
            self.backend.armature_pose(
                models.ArmaturePoseArguments(
                    object_name="Rig",
                    reset=True,
                    bones=[models.PoseBone(name="Missing")],
                )
            )
        self.assertEqual(before, self.inspect().pose_sha256)
        with patch.object(
            api, "inspect", side_effect=RuntimeError("injected pose failure")
        ):
            with self.assertRaises(RuntimeError):
                self.pose()
        self.assertEqual(before, self.inspect().pose_sha256)

    def test_invalid_weights_do_not_publish_groups_or_modifiers(self) -> None:
        self.make()
        for weighting in [
            models.EnvelopeWeights(bones=["Absent"]),
            models.ExplicitWeights(
                method="explicit",
                vertices=[
                    models.VertexWeights(
                        vertex=999,
                        influences=[models.Influence(bone="Root", weight=1.0)],
                    )
                ],
            ),
            models.ExplicitWeights(
                method="explicit",
                vertices=[
                    models.VertexWeights(
                        vertex=0, influences=[models.Influence(bone="Root", weight=1.0)]
                    )
                ],
            ),
        ]:
            before = self.allocations()
            with self.assertRaises(operations.OperationError):
                self.backend.armature_bind(
                    models.ArmatureBindArguments(
                        object_name="Surface", armature_object="Rig", weights=weighting
                    )
                )
            self.assertEqual(before, self.allocations())
            self.assertEqual(len(self.obj.vertex_groups), 0)
            self.assertEqual(len(self.obj.modifiers), 0)

    def test_binding_publication_failure_rolls_back(self) -> None:
        self.make()
        before = self.allocations()
        original = self.obj.data
        with patch.object(
            api, "binding_summary", side_effect=RuntimeError("injected binding failure")
        ):
            with self.assertRaises(RuntimeError):
                self.bind()
        self.assertEqual(self.obj.data, original)
        self.assertEqual(before, self.allocations())
        self.assertEqual(len(self.obj.vertex_groups), 0)
        self.assertEqual(len(self.obj.modifiers), 0)
        self.bind()
        digest = self.inspect().bindings[0].weights_sha256
        with patch.object(
            api,
            "binding_summary",
            side_effect=RuntimeError("injected rebinding failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.bind()
        self.assertEqual(digest, self.inspect().bindings[0].weights_sha256)

    def test_inspection_failure_restores_actual_pose(self) -> None:
        self.make()
        self.bind()
        self.pose()
        before = self.inspect().pose_sha256
        with patch.object(
            api, "comparison", side_effect=RuntimeError("injected compare failure")
        ):
            with self.assertRaises(RuntimeError):
                self.deform()
        self.assertEqual(bpy.data.objects["Rig"].data.pose_position, "POSE")
        self.assertEqual(before, self.inspect().pose_sha256)

    def test_external_groups_shared_mesh_and_rig_dependencies_are_preserved(
        self,
    ) -> None:
        self.make()
        self.obj.vertex_groups.new(name="Root")
        with self.assertRaises(operations.OperationError):
            self.bind()
        self.obj.vertex_groups.clear()
        linked = bpy.data.objects.new("Shared", self.obj.data)
        with self.assertRaises(operations.OperationError):
            self.bind()
        bpy.data.objects.remove(linked)
        bpy.data.objects["Rig"].parent = self.obj
        with self.assertRaises(operations.OperationError):
            self.bind()

    def test_explicit_weights_and_unrelated_groups_survive(self) -> None:
        self.make()
        group = self.obj.vertex_groups.new(name="Keep")
        group.add([0], 0.37, "REPLACE")
        self.backend.armature_bind(
            models.ArmatureBindArguments(
                object_name="Surface",
                armature_object="Rig",
                weights=models.ExplicitWeights(
                    method="explicit",
                    vertices=[
                        models.VertexWeights(
                            vertex=i,
                            influences=[models.Influence(bone="Root", weight=1.0)],
                        )
                        for i in range(len(self.obj.data.vertices))
                    ],
                ),
            )
        )
        self.assertAlmostEqual(self.obj.vertex_groups["Keep"].weight(0), 0.37, places=6)
        self.assertEqual(self.inspect().bindings[0].maximum_influences, 1)

    def test_transformed_armature_uses_world_space_envelopes(self) -> None:
        self.make()
        rig = bpy.data.objects["Rig"]
        rig.location = self.obj.location = (2, 3, 4)
        rig.rotation_euler.z = self.obj.rotation_euler.z = 0.6
        bpy.context.view_layer.update()
        self.assertEqual(self.bind().unweighted_vertex_count, 0)
        self.assertLess(self.deform().meshes[0].displacement_max, 1e-5)
        self.pose()
        self.assertGreater(self.deform().meshes[0].displacement_max, 1)

    def test_mirror_group_pair_allows_one_sided_articulation(self) -> None:
        for v in self.obj.data.vertices:
            v.co.x += 0.3
        args = models.ArmatureCreateArguments(
            name="Rig",
            bones=[
                models.RestBone(
                    name="Segment.R",
                    head=[0.3, 0.0, 0.0],
                    tail=[0.3, 2.0, 0.0],
                    head_radius=0.2,
                    tail_radius=0.2,
                ),
                models.RestBone(
                    name="Segment.L",
                    head=[-0.3, 0.0, 0.0],
                    tail=[-0.3, 2.0, 0.0],
                    head_radius=0.2,
                    tail_radius=0.2,
                ),
            ],
        )
        self.backend.armature_create(args)
        mirror = self.obj.modifiers.new("Mirror", "MIRROR")
        self.assertTrue(mirror.use_mirror_vertex_groups)
        self.backend.armature_bind(
            models.ArmatureBindArguments(
                object_name="Surface",
                armature_object="Rig",
                modifier_index=1,
                weights=models.EnvelopeWeights(bones=["Segment.R"]),
            )
        )
        dg = bpy.context.evaluated_depsgraph_get()
        rest = api.snapshot(self.obj, dg)[0]
        self.backend.armature_pose(
            models.ArmaturePoseArguments(
                object_name="Rig",
                bones=[models.PoseBone(name="Segment.R", rotation=[0.0, 0.0, 0.5])],
            )
        )
        posed = api.snapshot(self.obj, bpy.context.evaluated_depsgraph_get())[0]
        self.assertGreater(
            max((a - b).length for a, b in zip(rest, posed, strict=True) if a.x > 0),
            0.5,
        )
        self.assertLess(
            max((a - b).length for a, b in zip(rest, posed, strict=True) if a.x < 0),
            1e-6,
        )

    def test_save_reopen_preserves_binding_pose_and_cleanup(self) -> None:
        self.make()
        self.bind()
        self.pose(0.6)
        before = self.inspect()
        geometry = self.deform().meshes[0].posed_geometry_sha256
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "rig.blend")
            self.backend.file_save(file_models.FileSaveArguments(filepath=path))
            self.backend.file_open(
                file_models.FileOpenArguments(filepath=path, discard_current=True)
            )
            after = self.inspect()
            self.assertEqual(before.rest_sha256, after.rest_sha256)
            self.assertEqual(before.pose_sha256, after.pose_sha256)
            self.assertEqual(
                before.bindings[0].weights_sha256, after.bindings[0].weights_sha256
            )
            self.assertEqual(geometry, self.deform().meshes[0].posed_geometry_sha256)
        bpy.data.objects.remove(bpy.data.objects["Rig"], do_unlink=True)
        with self.assertRaises(operations.OperationError):
            self.deform()

    def test_surface_instances_follow_internal_armature_deformation(self) -> None:
        self.make()
        self.backend.mesh_create(
            mesh_models.MeshCreateArguments(
                name="Detail",
                vertices=[[-0.01, 0.0, 0.0], [0.01, 0.0, 0.0], [0.0, 0.08, 0.0]],
                faces=[[0, 1, 2]],
                hidden=True,
            )
        )
        spec = mesh_models.SurfaceDistribution(
            surface_object="Surface",
            prototype_object="Detail",
            uv_map="UVMap",
            guides=[
                [[-0.06, 1.2, 0.0], [-0.06, 1.8, 0.0]],
                [[0.06, 1.2, 0.0], [0.06, 1.8, 0.0]],
            ],
            rows=3,
            columns=2,
            surface_offset=0.01,
        )
        self.backend.surface_instances_create(
            mesh_models.SurfaceInstancesCreateArguments(
                name="Details", distribution=spec
            )
        )
        self.bind()
        args = mesh_models.SurfaceInstancesInspectArguments(
            object_name="Details", sample_limit=6
        )
        before = self.backend.surface_instances_inspect(args)
        self.backend.armature_pose(
            models.ArmaturePoseArguments(
                object_name="Rig",
                bones=[models.PoseBone(name="Tip", rotation=[0.7, 0.0, 0.5])],
            )
        )
        after = self.backend.surface_instances_inspect(args)
        self.assertEqual(before.binding_sha256, after.binding_sha256)
        self.assertEqual(after.evaluated_instance_count, 6)
        self.assertEqual(after.invalid_binding_count, 0)
        self.assertLess(after.root_position_max_error, 1e-5)
        actual = [
            i.matrix_world.copy()
            for i in bpy.context.evaluated_depsgraph_get().object_instances
            if i.is_instance and i.parent and i.parent.original.name == "Details"
        ]
        for sample in after.samples:
            matrix = min(
                actual, key=lambda m: (m.translation - Vector(sample.root_world)).length
            )
            self.assertGreater(
                matrix.to_3x3().col[2].normalized().dot(Vector(sample.normal_world)),
                0.999,
            )
            self.assertGreater(
                matrix.to_3x3().col[1].normalized().dot(Vector(sample.direction_world)),
                0.999,
            )
        self.assertGreater(
            max(
                abs(a.root_world[2] - b.root_world[2])
                for a, b in zip(before.samples, after.samples, strict=True)
            ),
            0.4,
        )
        self.assertGreater(
            max(
                abs(a.normal_world[1] - b.normal_world[1])
                for a, b in zip(before.samples, after.samples, strict=True)
            ),
            0.3,
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RigTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Native rigging tests failed")
    print("BLENDER_RIG_TESTS_PASSED", result.testsRun)
