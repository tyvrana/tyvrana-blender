"""Native display cleanup, shared-data preservation and preflight guards."""

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.sculpt_checks import NativeCase, adapter, authored  # noqa: E402

assert adapter.__package__ is not None
wireframe = importlib.import_module(adapter.__package__ + ".wireframe")
models = importlib.import_module(adapter.__package__ + ".models")
operations = importlib.import_module(adapter.__package__ + ".operations")


class WireframeTests(NativeCase):
    def test_shared_data_and_failure_cleanup(self) -> None:
        other = bpy.data.objects.new("Shared", self.obj.data)
        bpy.context.scene.collection.objects.link(other)
        self.obj.scale = (2, 1, 0.5)
        material = bpy.data.materials.new("Original")
        self.obj.data.materials.append(material)
        mod = self.obj.modifiers.new("Mirror", "MIRROR")
        mod.use_axis = (True, False, False)
        self.obj.hide_render = True
        bpy.context.view_layer.update()
        before = authored(self.obj)
        data = self.obj.data
        resources = tuple(
            len(x) for x in (bpy.data.objects, bpy.data.meshes, bpy.data.materials)
        )
        options = models.WireframeRenderOptions(
            objects=[self.obj.name], surface_offset=0.02
        )
        with self.assertRaisesRegex(RuntimeError, "injected render failure"):
            with wireframe.display(options):
                self.assertTrue(self.obj.hide_render)
                self.assertIs(self.obj.data, data)
                self.assertEqual(len(self.obj.modifiers), 1)
                raise RuntimeError("injected render failure")
        self.assertTrue(self.obj.hide_render)
        self.assertIs(self.obj.data, data)
        self.assertIs(other.data, data)
        self.assertEqual(authored(self.obj), before)
        self.assertEqual(len(self.obj.modifiers), 1)
        self.assertEqual(
            tuple(
                len(x) for x in (bpy.data.objects, bpy.data.meshes, bpy.data.materials)
            ),
            resources,
        )

    def test_budget_rejection_leaves_no_copies(self) -> None:
        before = tuple(
            len(x) for x in (bpy.data.objects, bpy.data.meshes, bpy.data.materials)
        )
        options = models.WireframeRenderOptions(objects=[self.obj.name])
        with patch.object(wireframe, "MAX_WIRE_EDGES", 1):
            with self.assertRaises(operations.OperationError):
                with wireframe.display(options):
                    self.fail("Limit was not enforced")
        self.assertFalse(self.obj.hide_render)
        self.assertEqual(
            tuple(
                len(x) for x in (bpy.data.objects, bpy.data.meshes, bpy.data.materials)
            ),
            before,
        )

    def test_unbounded_modifier_rejected_before_evaluation(self) -> None:
        self.obj.modifiers.new("Unbounded", "NODES")
        options = models.WireframeRenderOptions(objects=[self.obj.name])
        with patch.object(wireframe.retopo_geometry, "evaluated_mesh") as evaluate:
            with self.assertRaises(operations.OperationError):
                with wireframe.display(options):
                    self.fail("Unsupported evaluation was accepted")
            evaluate.assert_not_called()


if __name__ == "__main__":
    runtime = adapter._runtime
    worker = runtime.worker if runtime else None
    adapter.unregister()
    if worker:
        assert worker.process.returncode == 0
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(WireframeTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Native wireframe tests failed")
    print("BLENDER_WIREFRAME_TESTS_PASSED 3", flush=True)
