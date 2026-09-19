"""Lazy template geometry agrees with actual native instancing/deformation."""

import importlib
import sys
import unittest
from pathlib import Path

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.growth_checks import GrowthTests, growth, models  # noqa:E402

native_reference = importlib.import_module("tests.blender.realized_growth_reference")

elements = importlib.import_module(growth.__package__ + ".geometry_elements")
geometry_qa = importlib.import_module(growth.__package__ + ".geometry_qa")
qa_models = importlib.import_module(growth.__package__ + ".geometry_qa_models")


class ElementTests(GrowthTests):
    def test_broad_contact_ids_budgets_and_fractional_sampling(self) -> None:
        self.template()
        region = models.GrowthRegion(
            name="Row",
            family="Fiber",
            guides=0,
            rows=[dict(name="Ordered", path=[[0.1, 0.5], [0.9, 0.5]], count=20)],
        )
        family = self.spec.families[0].model_copy(
            update=dict(
                shape=[[0, 0, 0], [0, 0, 1]],
                template=models.GrowthTemplate(object_name="Template", width=8),
            )
        )
        self.spec = self.spec.model_copy(
            update=dict(families=[family], regions=[region])
        )
        self.create()
        args = qa_models.GeometryInspectArguments(
            instances=[dict(object_name="Field")], frames=[1, 1.5], containment=False
        )
        bpy.context.scene.frame_set(7, subframe=0.25)
        report = geometry_qa.inspect(args)
        row = report.samples[0].instances[0]
        self.assertEqual(row.instance_count, 20)
        self.assertEqual(row.prototype_count, 1)
        self.assertGreater(row.contact_element_pairs, 0)
        self.assertEqual(row.minimum_distance, 0)
        self.assertTrue(row.contacts[0].left_id.startswith("root:"))
        self.assertEqual(bpy.context.scene.frame_current, 7)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, 0.25)
        with self.assertRaises(geometry_qa.OperationError):
            geometry_qa.inspect(args.model_copy(update=dict(max_instance_vertices=1)))
        self.assertEqual(bpy.context.scene.frame_current, 7)
        family = family.model_copy(
            update=dict(
                template=models.GrowthTemplate(object_name="Template", width=0.1)
            )
        )
        growth.configure(
            models.GrowthConfigureArguments(object_name="Field", families=[family])
        )
        clear = geometry_qa.inspect(args).samples[0].instances[0]
        self.assertEqual(clear.contact_element_pairs, 0)
        self.assertGreater(clear.minimum_distance, 0.14)
        self.assertLessEqual(clear.transformed_vertices, clear.equivalent_vertices)

    def test_native_equivalence_without_full_realization(self) -> None:
        self.template()
        mesh = bpy.data.objects["Template"].data
        for vertex in mesh.vertices:
            if 0 < vertex.co.z < 1:
                vertex.co.z = 0.31
            vertex.co.y = 0.012 if vertex.index % 2 else -0.013
        self.surface.shape_key_add(name="Basis")
        bend = self.surface.shape_key_add(name="Bend")
        for vertex in bend.data:
            vertex.co.z += 0.1 * vertex.co.x**2
        family = self.spec.families[0].model_copy(
            update=dict(
                roll=0.3,
                shape=[[0, 0, 0], [0.7, 0.1, 0.3], [0.9, 0.4, 0.8]],
                template=models.GrowthTemplate(
                    object_name="Template", width=1.3, thickness=0.8
                ),
            )
        )
        self.spec = self.spec.model_copy(update={"families": [family]})
        self.create()
        for mode in ["instances", "deform"]:
            family = family.model_copy(
                update={
                    "template": models.GrowthTemplate(
                        object_name="Template", mode=mode, width=1.3, thickness=0.8
                    )
                }
            )
            growth.configure(
                models.GrowthConfigureArguments(object_name="Field", families=[family])
            )
            for value in [0, 0.5, 1]:
                bend.value = value
                self.surface.rotation_euler = (0.3, -0.2, 0.4)
                self.surface.scale = (1.2, 0.9, 1.1)
                bpy.context.view_layer.update()
                obj, _, _, group = growth.owned("Field")
                lazy = elements.collect("Field")
                self.assertEqual(len(lazy.prototypes), 1)
                self.assertEqual(lazy.equivalent_vertices, 384)
                self.assertTrue(all(e._points is None for e in lazy.elements))
                actual = native_reference.template_points(obj, group, 4096)
                expected = [p for e in lazy.elements for p in e.points()]
                self.assertEqual(len(actual), len(expected))
                error = max(
                    (a - b).length for a, b in zip(actual, expected, strict=True)
                )
                self.assertLess(error, 2e-5, (mode, value, error))
                for element in lazy.elements:
                    lo, hi = element.bounds()
                    self.assertTrue(
                        all(
                            lo[k] - 1e-6 <= p[k] <= hi[k] + 1e-6
                            for p in element.points()
                            for k in range(3)
                        )
                    )

    def test_native_collection_instances_share_prototype(self) -> None:
        collection = bpy.data.collections.new("PrototypeCollection")
        prototype = self.surface
        for current in list(prototype.users_collection):
            current.objects.unlink(prototype)
        collection.objects.link(prototype)
        for name, x in [("A", 0), ("B", 5)]:
            instancer = bpy.data.objects.new(name, None)
            instancer.instance_type = "COLLECTION"
            instancer.instance_collection = collection
            instancer.location.x = x
            bpy.context.scene.collection.objects.link(instancer)
        bpy.context.view_layer.update()
        a = elements.collect("A")
        b = elements.collect("B")
        self.assertEqual(len(a.elements), 1)
        self.assertEqual(len(b.elements), 1)
        self.assertEqual(
            a.elements[0].identity, elements.collect("A").elements[0].identity
        )
        self.assertAlmostEqual(
            b.elements[0].points()[0].x - a.elements[0].points()[0].x, 5
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            ElementTests(name)
            for name in ElementTests.__dict__
            if name.startswith("test_")
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("GEOMETRY_ELEMENTS_NATIVE_PASSED")
