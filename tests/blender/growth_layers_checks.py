"""Broad layered strips: correction, native equivalence and atomic failure."""

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.growth_checks import GrowthTests, growth, models
from tests.blender.realized_growth_reference import template_points

layers = importlib.import_module(growth.__package__ + ".growth_layers")
lm = importlib.import_module(growth.__package__ + ".growth_layers_models")
elements = importlib.import_module(growth.__package__ + ".geometry_elements")
qa = importlib.import_module(growth.__package__ + ".geometry_qa")
qm = importlib.import_module(growth.__package__ + ".geometry_qa_models")


class LayerTests(GrowthTests):
    def fixture(self):
        self.template()
        mesh = bpy.data.objects["Template"].data
        # Narrow pinned root; broad parallel strips intersect away from their roots.
        for v in mesh.vertices:
            v.co.x = (-1 if v.index % 2 == 0 else 1) * (0.005 if v.co.z == 0 else 0.24)
        self.spec = self.spec.model_copy(
            update=dict(
                families=[
                    models.GrowthFamily(
                        name="Strip",
                        length=0.8,
                        shape=[[0, 0, 0], [0, 0, 1]],
                        template=dict(object_name="Template", mode="deform"),
                    )
                ],
                regions=[
                    models.GrowthRegion(
                        name="Row",
                        family="Strip",
                        guides=0,
                        rows=[
                            dict(
                                name="A",
                                path=[[0.3, 0.5], [0.7, 0.5]],
                                count=8,
                                overlap="over_previous",
                            )
                        ],
                    )
                ],
            )
        )
        self.create()
        self.surface.shape_key_add(name="Basis")
        key = self.surface.shape_key_add(name="Fold")
        for v in key.data:
            v.co.z += 0.2 * v.co.x**2
        for frame, value in [(1, 0), (2, 1), (3, 0)]:
            key.value = value
            key.keyframe_insert(data_path="value", frame=frame)
        self.args = lm.LayerCorrectArguments(
            object_name="Field",
            frame_start=1,
            frame_end=3,
            samples=5,
            direction=[0, 1, 0],
            maximum_lift=0.4,
            steps=32,
            max_seconds=20,
        )

    def test_correction_native_equivalence_replacement_and_clear(self):
        self.fixture()
        query = qm.GeometryInspectArguments(
            instances=[dict(object_name="Field")],
            frames=[1, 1.5, 2, 2.5, 3],
            containment=False,
        )
        before = qa.inspect(query)
        self.assertGreater(before.samples[0].instances[0].contact_element_pairs, 0)
        bpy.context.scene.frame_set(7, subframe=0.25)
        result = layers.correct(self.args)
        print("LAYER_RESULT", result.model_dump_json())
        self.assertTrue(result.valid)
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, 0.25)
        after = qa.inspect(query)
        self.assertTrue(
            all(s.instances[0].contact_element_pairs == 0 for s in after.samples)
        )
        for frame in [1, 1.25, 2, 2.75, 3]:
            bpy.context.scene.frame_set(int(frame), subframe=frame % 1)
            obj, _, _, group = growth.owned("Field")
            lazy = elements.collect("Field")
            actual = template_points(obj, group, 4096)
            expected = [p for e in lazy.elements for p in e.points()]
            self.assertLess(
                max((a - b).length for a, b in zip(actual, expected, strict=True)), 2e-5
            )
            self.assertTrue(
                all(
                    e.offsets[0].length == 0 and e.offsets[1].length == 0
                    for e in lazy.elements
                )
            )
        repeat = layers.correct(self.args.model_copy(update=dict(replace=True)))
        self.assertEqual(result.cache_sha256, repeat.cache_sha256)
        obj, _, _, group = growth.owned("Field")
        old = obj[layers.KEY]
        counts = (
            len(bpy.data.objects),
            len(bpy.data.meshes),
            len(bpy.data.node_groups),
        )
        with patch.object(layers, "playback", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                layers.correct(self.args.model_copy(update=dict(replace=True)))
        self.assertEqual(obj[layers.KEY], old)
        self.assertEqual(
            counts,
            (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.node_groups)),
        )
        failed = layers.correct(
            self.args.model_copy(update=dict(replace=True, maximum_lift=0.00001))
        )
        self.assertFalse(failed.valid)
        self.assertEqual(obj[layers.KEY], old)
        layers.invalidate("blender.action.edit")
        self.assertFalse(
            layers.inspect(lm.LayerObjectArguments(object_name="Field")).valid
        )
        with self.assertRaises(growth.OperationError):
            qa.inspect(query)
        layers.clear(lm.LayerObjectArguments(object_name="Field"))
        self.assertNotIn(layers.KEY, obj)
        self.assertGreater(
            qa.inspect(query).samples[0].instances[0].contact_element_pairs, 0
        )

    def test_fixed_attachment_body_conflict_and_work_budget(self):
        self.fixture()
        data = bpy.data.meshes.new("Pinned obstacle")
        data.from_pydata(
            [(-2, 0, -0.1), (2, 0, -0.1), (2, 0, 2), (-2, 0, 2)], [], [(0, 1, 2, 3)]
        )
        obj = bpy.data.objects.new("Pinned obstacle", data)
        bpy.context.scene.collection.objects.link(obj)
        before = (
            len(bpy.data.objects),
            len(bpy.data.meshes),
            len(bpy.data.node_groups),
        )
        result = layers.correct(self.args.model_copy(update=dict(colliders=[obj.name])))
        self.assertFalse(result.valid)
        self.assertIn("object:Pinned obstacle", result.conflicts[0].obstacle)
        self.assertNotIn(layers.KEY, bpy.data.objects["Field"])
        self.assertEqual(
            before,
            (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.node_groups)),
        )
        with self.assertRaises(growth.OperationError):
            layers.correct(self.args.model_copy(update=dict(max_vertices=1)))
        self.assertNotIn(layers.KEY, bpy.data.objects["Field"])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            LayerTests(n) for n in LayerTests.__dict__ if n.startswith("test_")
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("GROWTH_LAYERS_NATIVE_PASSED")
