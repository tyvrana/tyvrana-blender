"""Native rooted-growth mechanics, ownership, scale and failure recovery."""

import importlib
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
package = "bl_ext.user_default.tyvrana_blender"
growth = importlib.import_module(package + ".growth")
models = importlib.import_module(package + ".growth_models")
qa = importlib.import_module(package + ".growth_qa")
errors = importlib.import_module(package + ".errors")


class GrowthTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in [bpy.data.hair_curves, bpy.data.meshes, bpy.data.node_groups]:
            for data in list(collection):
                if data.users == 0:
                    collection.remove(data)
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=4)
        self.surface = bpy.context.object
        self.surface.name = "Surface"
        self.spec = models.GrowthCreateArguments(
            name="Field",
            surface="Surface",
            families=[models.GrowthFamily(name="Fiber", length=0.5, radius=0.004)],
            regions=[
                models.GrowthRegion(
                    name="Region", family="Fiber", guides=16, children=64
                )
            ],
        )

    def create(self) -> Any:
        return growth.create(self.spec)

    def inspect(self, **kwargs: Any) -> Any:
        return growth.inspect(
            models.GrowthInspectArguments(object_name="Field", **kwargs)
        )

    def test_native_curves_ids_and_interpolation(self) -> None:
        self.create()
        obj = bpy.data.objects["Field"]
        self.assertEqual(obj.type, "CURVES")
        self.assertIs(obj.data.surface, self.surface)
        result = self.inspect(guide_limit=2, include_points=True)
        self.assertEqual(result.summary.evaluated_curves, 64)
        self.assertLess(result.summary.qa.maximum_root_error, 1e-5)
        _, _, _, group = growth.owned("Field")
        with growth.evaluated_path(obj, group) as data:
            self.assertEqual(
                [v.value for v in data.attributes["growth_root_id"].data],
                list(range(20001, 20065)),
            )
        self.assertEqual(result.guides[0].root_id, 1)
        self.assertEqual(result.summary.qa.frame_flips, 0)

    def test_density_and_guide_edits_preserve_identity(self) -> None:
        self.create()
        before = self.inspect(guide_limit=2, include_points=True)
        growth.configure(
            models.GrowthConfigureArguments(
                object_name="Field",
                guides=[models.GrowthGuideEdit(root_id=1, length_scale=2)],
            )
        )
        changed = self.inspect(guide_limit=2, include_points=True)
        self.assertEqual(changed.guides[0].root_id, 1)
        self.assertEqual(changed.guides[0].uv, before.guides[0].uv)
        self.assertAlmostEqual(
            changed.guides[0].points[-1][2], 2 * before.guides[0].points[-1][2]
        )
        region = self.spec.regions[0].model_copy(update={"children": 128})
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", regions=[region])
        )
        after = self.inspect(guide_limit=2, include_points=True)
        self.assertEqual(after.summary.evaluated_curves, 128)
        self.assertEqual(after.guides, changed.guides)

    def test_shape_keys_and_object_transform(self) -> None:
        self.create()
        self.surface.shape_key_add(name="Basis")
        key = self.surface.shape_key_add(name="Bend")
        for v in key.data:
            v.co.z += 0.3 * v.co.x * v.co.x
        key.value = 0.75
        self.surface.location = (2, 1, -1)
        self.surface.rotation_euler = (0.2, -0.3, 0.5)
        self.surface.scale = (1.3, 0.9, 1.1)
        bpy.context.view_layer.update()
        result = self.inspect()
        self.assertLess(result.summary.qa.maximum_root_error, 3e-4)
        self.assertEqual(result.summary.qa.frame_flips, 0)

    def test_invalid_uv_and_topology_rebind(self) -> None:
        self.create()
        obj = bpy.data.objects["Field"]
        old = [v.value for v in obj.data.attributes["growth_root_id"].data]
        self.surface.data.uv_layers.active.uv[0].vector.x += 0.01
        result = self.inspect()
        self.assertFalse(result.summary.valid)
        self.assertEqual(result.summary.invalid_roots, 64)
        with self.assertRaises(errors.OperationError):
            growth.configure(
                models.GrowthConfigureArguments(object_name="Field", neighbors=2)
            )
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", rebind=True)
        )
        self.assertTrue(self.inspect().summary.valid)
        self.assertNotEqual(
            old, [v.value for v in obj.data.attributes["growth_root_id"].data]
        )
        self.surface.data.edges.add(1)
        self.assertFalse(self.inspect().summary.valid)

    def test_creation_and_update_rollback(self) -> None:
        def counts() -> tuple[int, int, int, int]:
            return (
                len(bpy.data.objects),
                len(bpy.data.hair_curves),
                len(bpy.data.meshes),
                len(bpy.data.node_groups),
            )

        before = counts()
        with patch.object(
            growth.growth_nodes, "build", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(before, counts())
        self.assertFalse(self.surface.add_rest_position_attribute)
        self.create()
        obj = bpy.data.objects["Field"]
        data = obj.data
        metadata = obj[growth.KEY]
        before = counts()
        with patch.object(growth, "summary", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                growth.configure(
                    models.GrowthConfigureArguments(object_name="Field", neighbors=2)
                )
        self.assertIs(obj.data, data)
        self.assertEqual(metadata, obj[growth.KEY])
        self.assertEqual(before, counts())
        self.assertTrue(self.inspect().summary.valid)

    def template(self) -> None:
        mesh = bpy.data.meshes.new("Template")
        mesh.from_pydata(
            [
                (-0.05, 0, 0),
                (0.1, 0, 0),
                (-0.025, 0, 0.5),
                (0.07, 0, 0.5),
                (-0.01, 0, 1),
                (0.02, 0, 1),
            ],
            [],
            [(0, 1, 3, 2), (2, 3, 5, 4)],
        )
        obj = bpy.data.objects.new("Template", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.hide_render = True
        obj.hide_set(True)

    def test_instances_and_flexible_template(self) -> None:
        self.template()
        family = self.spec.families[0].model_copy(
            update={
                "shape": [[0, 0, 0], [0.5, 0, 0.5], [1, 0, 0.5]],
                "template": models.GrowthTemplate(
                    object_name="Template", mode="instances"
                ),
            }
        )
        self.spec = self.spec.model_copy(update={"families": [family]})
        self.create()
        result = self.inspect()
        self.assertEqual(result.summary.instance_count, 64)
        obj, _, _, group = growth.owned("Field")
        rigid = qa.template_points(obj, group, 4096)
        self.assertEqual(len(rigid), 64 * 6)
        family = family.model_copy(
            update={
                "template": models.GrowthTemplate(object_name="Template", mode="deform")
            }
        )
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", families=[family])
        )
        obj, _, _, group = growth.owned("Field")
        bent = qa.template_points(obj, group, 4096)
        self.assertEqual(len(bent), len(rigid))
        self.assertGreater(
            max((a - b).length for a, b in zip(rigid, bent, strict=True)), 0.1
        )
        self.assertLess(
            self.inspect(template_samples=128).summary.qa.maximum_root_error, 1e-5
        )
        self.assertEqual(len([o for o in bpy.data.objects if o.type == "MESH"]), 3)

    def test_pose_clearance_detect_and_fix_restores_keys(self) -> None:
        self.create()
        self.surface.shape_key_add(name="Basis")
        key = self.surface.shape_key_add(name="Lift")
        for v in key.data:
            v.co.z += 0.5
        key.value = 0.3
        # A separate oriented ground surface makes a negative offset visible.
        bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0.4))
        bpy.context.object.name = "Obstacle"
        args = models.GrowthSampleArguments(
            object_name="Field",
            poses=[
                {
                    "name": "low",
                    "shape_values": [
                        {"object_name": "Surface", "key": "Lift", "value": 0}
                    ],
                },
                {
                    "name": "high",
                    "shape_values": [
                        {"object_name": "Surface", "key": "Lift", "value": 1}
                    ],
                },
            ],
            clearance_objects=["Obstacle"],
        )
        bad = qa.sample(args)
        self.assertGreater(bad.samples[0].qa.penetrating_samples, 0)
        self.assertEqual(bad.samples[1].qa.penetrating_samples, 0)
        self.assertAlmostEqual(key.value, 0.3, places=6)
        family = self.spec.families[0].model_copy(update={"offset": 0.5})
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", families=[family])
        )
        fixed = qa.sample(args)
        self.assertEqual(sum(x.qa.penetrating_samples for x in fixed.samples), 0)
        self.assertAlmostEqual(key.value, 0.3, places=6)

    def test_owned_graph_and_attribute_guards(self) -> None:
        self.create()
        obj, _, _, group = growth.owned("Field")
        node = group.nodes.new("ShaderNodeValue")
        with self.assertRaises(errors.OperationError):
            growth.configure(
                models.GrowthConfigureArguments(object_name="Field", neighbors=2)
            )
        group.nodes.remove(node)
        obj.data.attributes["growth_root_id"].data[0].value = 999
        with self.assertRaises(errors.OperationError):
            self.inspect()

    def test_armature_and_corrective_deformation(self) -> None:
        self.surface.shape_key_add(name="Basis")
        key = self.surface.shape_key_add(name="Correction")
        for v in key.data:
            v.co.z += 0.08 * v.co.x * v.co.x
        bpy.ops.object.armature_add()
        arm = bpy.context.object
        arm.name = "Rig"
        weight = self.surface.vertex_groups.new(name="Bone")
        weight.add(list(range(len(self.surface.data.vertices))), 1.0, "REPLACE")
        mod = self.surface.modifiers.new("Skin", "ARMATURE")
        mod.object = arm
        self.create()
        before = self.inspect(guide_limit=2)
        bone = arm.pose.bones["Bone"]
        bone.rotation_mode = "XYZ"
        for angle in [0.0, 0.5, -0.8]:
            bone.rotation_euler = (angle, 0.2, -0.1)
            key.value = abs(angle)
            bpy.context.view_layer.update()
            result = self.inspect(guide_limit=2)
            self.assertLess(result.summary.qa.maximum_root_error, 1e-4)
            self.assertEqual(result.guides, before.guides)
            self.assertEqual(result.summary.qa.frame_flips, 0)

    def test_region_flow_family_and_roll_updates(self) -> None:
        self.template()
        a = self.spec.families[0].model_copy(
            update={
                "template": models.GrowthTemplate(object_name="Template", mode="deform")
            }
        )
        b = a.model_copy(update={"name": "Other", "length": 0.2, "roll": 0.7})
        regions = [
            models.GrowthRegion(
                name="Left",
                family="Fiber",
                guides=8,
                children=32,
                selector={
                    "domain": "face",
                    "mode": "box",
                    "min": [-3, -3, -1],
                    "max": [0, 3, 1],
                },
            ),
            models.GrowthRegion(
                name="Right",
                family="Other",
                guides=8,
                children=32,
                flow=[-1, 0, 0],
                selector={
                    "domain": "face",
                    "mode": "box",
                    "min": [0, -3, -1],
                    "max": [3, 3, 1],
                },
            ),
        ]
        self.spec = self.spec.model_copy(
            update={"families": [a, b], "regions": regions}
        )
        self.create()
        result = self.inspect(guide_limit=16)
        self.assertEqual({g.region for g in result.guides}, {"Left", "Right"})
        self.assertEqual({g.family for g in result.guides}, {"Fiber", "Other"})
        obj, _, _, group = growth.owned("Field")
        before = qa.template_points(obj, group, 4096)
        a = a.model_copy(update={"roll": 1.0})
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", families=[a, b])
        )
        obj, _, _, group = growth.owned("Field")
        after = qa.template_points(obj, group, 4096)
        self.assertGreater(
            max((x - y).length for x, y in zip(before, after, strict=True)), 0.02
        )
        self.assertEqual(result.guides, self.inspect(guide_limit=16).guides)

    def test_binding_and_shared_pointer_guards(self) -> None:
        self.template()
        family = self.spec.families[0].model_copy(
            update={"template": models.GrowthTemplate(object_name="Template")}
        )
        self.spec = self.spec.model_copy(update={"families": [family]})
        self.create()
        _, _, roots, group = growth.owned("Field")
        group.nodes["Template 0"].inputs["Object"].default_value = self.surface
        with self.assertRaises(errors.OperationError):
            self.inspect()
        group.nodes["Template 0"].inputs["Object"].default_value = bpy.data.objects[
            "Template"
        ]
        roots.location.x = 1
        with self.assertRaises(errors.OperationError):
            self.inspect()
        roots.location.x = 0
        bpy.context.view_layer.update()
        self.surface.data.vertices[0].co.z += 0.1
        self.assertFalse(self.inspect().summary.valid)
        self.surface.data.uv_layers.remove(self.surface.data.uv_layers.active)
        self.assertFalse(self.inspect().summary.valid)

    def test_user_hair_and_shared_owned_data_rejected(self) -> None:
        data = bpy.data.hair_curves.new("User")
        obj = bpy.data.objects.new("User", data)
        bpy.context.scene.collection.objects.link(obj)
        with self.assertRaises(errors.OperationError):
            growth.remove(models.GrowthRemoveArguments(object_name="User"))
        self.create()
        clone = bpy.data.objects["Field"].copy()
        bpy.context.scene.collection.objects.link(clone)
        with self.assertRaises(errors.OperationError):
            self.inspect()
        bpy.data.objects.remove(clone, do_unlink=True)
        self.assertTrue(self.inspect().summary.valid)

    def test_disabled_modifier_and_failed_sweep_restore(self) -> None:
        self.create()
        obj = bpy.data.objects["Field"]
        obj.modifiers[0].show_render = False
        with self.assertRaises(errors.OperationError):
            self.inspect()
        obj.modifiers[0].show_render = True
        self.surface.shape_key_add(name="Basis")
        key = self.surface.shape_key_add(name="Lift")
        key.value = 0.4
        frame = bpy.context.scene.frame_current
        args = models.GrowthSampleArguments(
            object_name="Field",
            poses=[
                {
                    "name": "Changed",
                    "shape_values": [
                        {"object_name": "Surface", "key": "Lift", "value": 1}
                    ],
                }
            ],
        )
        with patch.object(growth, "inspect", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                qa.sample(args)
        self.assertAlmostEqual(key.value, 0.4, places=6)
        self.assertEqual(frame, bpy.context.scene.frame_current)

    def test_evaluated_frame_normals_are_perpendicular(self) -> None:
        self.create()
        self.surface.rotation_euler = (0.2, 0.65, -0.1)
        self.surface.shape_key_add(name="Basis")
        key = self.surface.shape_key_add(name="Correction")
        for v in key.data:
            v.co.z += 0.1 * v.co.x * v.co.x
        key.value = 0.6
        bpy.context.view_layer.update()
        obj, _, _, group = growth.owned("Field")
        with growth.evaluated_path(obj, group) as data:
            for curve in data.curves:
                tangent = (
                    curve.points[1].position - curve.points[0].position
                ).normalized()
                normal = (
                    data.attributes["growth_frame_normal"]
                    .data[curve.first_point_index]
                    .vector
                )
                self.assertLess(abs(tangent.dot(normal)), 1e-4)
        self.assertEqual(self.inspect().summary.qa.frame_flips, 0)

    def test_cleanup_keeps_shared_resources(self) -> None:
        self.template()
        mat = bpy.data.materials.new("Shared")
        family = self.spec.families[0].model_copy(
            update={
                "material": mat.name,
                "template": models.GrowthTemplate(object_name="Template"),
            }
        )
        self.spec = self.spec.model_copy(update={"families": [family]})
        self.create()
        result = growth.remove(models.GrowthRemoveArguments(object_name="Field"))
        self.assertTrue(result.removed)
        self.assertIn("Surface", bpy.data.objects)
        self.assertIn("Template", bpy.data.objects)
        self.assertIn("Shared", bpy.data.materials)
        self.assertFalse(any(growth.KEY in o for o in bpy.data.objects))
        self.assertFalse(any(growth.KEY in g for g in bpy.data.node_groups))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(GrowthTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("BLENDER_GROWTH_TESTS_PASSED", result.testsRun)
