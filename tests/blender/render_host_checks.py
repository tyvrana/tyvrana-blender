"""Connected-host rendering fidelity and safety on disposable scene data."""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
package = "bl_ext.user_default.tyvrana_blender"
host = importlib.import_module(package + ".render_host")
artifacts = importlib.import_module(package + ".artifacts")
models = importlib.import_module(package + ".models")
errors = importlib.import_module(package + ".errors")
scene_helpers = importlib.import_module("tests.blender.scene")


class RenderHostTests(unittest.TestCase):
    def setUp(self) -> None:
        scene_helpers.prepare_scene()
        self.spool = artifacts.ArtifactSpool()
        self.addCleanup(self.spool.close)
        self.addCleanup(host.shutdown)
        self.identifier = "b" * 32

    def test_transient_inspection_restores_strong_content_on_all_outcomes(self) -> None:
        attestation = importlib.import_module(package + ".attestation")
        renderer = importlib.import_module(package + ".render")
        scene = bpy.context.scene
        scene.frame_set(4, subframe=0.25)
        before = attestation.inspect().root
        self.assertEqual(before["status"], "complete", before)
        options = models.RenderArguments(
            width=64,
            height=64,
            inspection={
                "objects": ["RenderCube"],
                "views": [{"name": "Side", "orientation": "right"}],
            },
        )
        for outcome in ("success", "failure", "cancel"):
            with self.subTest(outcome=outcome):

                def checkpoint(current: str = outcome) -> None:
                    if current == "cancel":
                        raise errors.OperationError("render_cancelled", "Test cancel")

                def fail(*args: object, **kwargs: object) -> None:
                    raise errors.OperationError("render_failed", "Test native failure")

                generator = renderer.render_steps(
                    options, self.spool, checkpoint=checkpoint
                )
                if outcome == "failure":
                    # Fail after inspection has created its camera and applied settings.
                    with patch.object(
                        renderer,
                        "bpy",
                        SimpleNamespace(
                            context=bpy.context,
                            app=bpy.app,
                            data=bpy.data,
                            ops=SimpleNamespace(render=SimpleNamespace(render=fail)),
                        ),
                    ):
                        with self.assertRaises(errors.OperationError):
                            next(generator)
                elif outcome == "cancel":
                    with self.assertRaises(errors.OperationError):
                        next(generator)
                else:
                    with self.assertRaises(StopIteration):
                        next(generator)
                generator.close()
                after = attestation.inspect().root
                for field in (
                    "status",
                    "digest",
                    "format",
                    "project_id",
                    "document_session_id",
                    "host_session_id",
                ):
                    self.assertEqual(before[field], after[field], field)
                self.assertEqual((scene.frame_current, scene.frame_subframe), (4, 0.25))
                self.assertNotIn("InspectionCamera", bpy.data.objects)
                self.assertNotIn("InspectionSheet", bpy.data.images)

    def test_render_preserves_scene_state_and_live_image_pixels(self) -> None:
        scene = bpy.context.scene
        image = bpy.data.images.new("Live pixels", width=2, height=2)
        image.pixels[:] = [0.125, 0.5, 0.75, 1] * 4
        expected = np.asarray(image.pixels[:], dtype=np.float32)
        material = bpy.data.materials["RenderRed"]
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        state = (
            bpy.data.filepath,
            bpy.data.is_dirty,
            scene.render.engine,
            scene.cycles.samples,
            scene.camera,
            bpy.context.view_layer.objects.active,
            tuple(bpy.context.selected_objects),
            image.packed_file,
        )
        result = host.prepare(
            models.RenderArguments(width=64, cycles={"samples": 2}),
            self.spool,
            self.identifier,
        )
        self.assertEqual(result.engine, "CYCLES")
        while host.busy():
            host.tick()
        self.assertEqual(
            state,
            (
                bpy.data.filepath,
                bpy.data.is_dirty,
                scene.render.engine,
                scene.cycles.samples,
                scene.camera,
                bpy.context.view_layer.objects.active,
                tuple(bpy.context.selected_objects),
                image.packed_file,
            ),
        )
        root = self.spool.root / "jobs" / self.identifier
        report = json.loads((root / "completed.json").read_text())
        self.assertNotIn("error", report, report)
        self.assertTrue(
            np.array_equal(np.asarray(image.pixels[:], dtype=np.float32), expected)
        )
        self.assertTrue((root / (self.identifier + ".png")).is_file())
        self.assertFalse(list(root.glob("*.blend")))
        bpy.data.images.remove(image)

    def test_busy_bake_rejects_render_preparation(self) -> None:
        with patch.object(host.bake_jobs, "busy", return_value=True):
            with self.assertRaises(errors.OperationError) as raised:
                host.prepare(models.RenderArguments(), self.spool, self.identifier)
        self.assertEqual(raised.exception.error.code, "adapter_busy")
        self.assertFalse((self.spool.root / "jobs").exists())

    def test_deadline_drains_frame_restores_host_and_allows_recovery(self) -> None:
        scene = bpy.context.scene
        scene.frame_set(3, subframe=0.25)
        scene.cycles.time_limit = 0
        state = (
            scene.render.engine,
            scene.cycles.samples,
            scene.frame_current,
            scene.frame_subframe,
            scene.cycles.time_limit,
        )
        host.prepare(
            models.RenderArguments(
                width=128,
                height=128,
                frames=[7, 9],
                cycles={"samples": 4096},
                budget={"max_seconds": 1},
            ),
            self.spool,
            self.identifier,
        )
        while host.busy():
            host.tick()
        root = self.spool.root / "jobs" / self.identifier
        report = json.loads((root / "completed.json").read_text())
        self.assertEqual(report["error"]["code"], "render_budget_exceeded")
        self.assertFalse(bpy.app.is_job_running("RENDER"))
        self.assertEqual(
            state,
            (
                scene.render.engine,
                scene.cycles.samples,
                scene.frame_current,
                scene.frame_subframe,
                scene.cycles.time_limit,
            ),
        )
        self.assertFalse(list(root.glob("*.zip")))
        host.prepare(
            models.RenderArguments(width=64, height=64, cycles={"samples": 1}),
            self.spool,
            "c" * 32,
        )
        while host.busy():
            host.tick()
        recovered = self.spool.root / "jobs" / ("c" * 32) / "completed.json"
        self.assertNotIn("error", json.loads(recovered.read_text()))

    def test_invalid_output_and_missing_camera_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "existing.png"
            destination.write_bytes(b"unchanged")
            with self.assertRaises(errors.OperationError) as raised:
                host.prepare(
                    models.RenderArguments(output={"filepath": str(destination)}),
                    self.spool,
                    self.identifier,
                )
            self.assertEqual(raised.exception.error.code, "file_exists")
            self.assertEqual(destination.read_bytes(), b"unchanged")
            with self.assertRaises(errors.OperationError) as raised:
                host.prepare(
                    models.RenderArguments(
                        output={"filepath": str(Path(root) / "missing" / "image.png")}
                    ),
                    self.spool,
                    self.identifier,
                )
            self.assertEqual(raised.exception.error.code, "file_destination_invalid")
        bpy.context.scene.camera = None
        with self.assertRaises(errors.OperationError) as raised:
            host.prepare(models.RenderArguments(), self.spool, self.identifier)
        self.assertEqual(raised.exception.error.code, "no_camera")

    def test_image_save_failure_restores_native_settings_and_spool(self) -> None:
        renderer = importlib.import_module(package + ".render")
        scene = bpy.context.scene
        original = (
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.image_settings.file_format,
        )

        def fail_save(**kwargs: object) -> None:
            raise OSError("Injected encoder failure")

        boundary = SimpleNamespace(
            context=bpy.context,
            app=bpy.app,
            data=SimpleNamespace(
                images=SimpleNamespace(
                    get=lambda name: SimpleNamespace(save_render=fail_save)
                )
            ),
            ops=SimpleNamespace(
                render=SimpleNamespace(render=lambda *args, **kwargs: {"FINISHED"})
            ),
        )
        with (
            patch.object(renderer, "bpy", boundary),
            self.assertLogs(renderer.logger, "ERROR"),
        ):
            with self.assertRaises(errors.OperationError) as raised:
                renderer.render_image(
                    models.RenderArguments(width=64, height=64), self.spool
                )
        self.assertEqual(raised.exception.error.code, "render_failed")
        self.assertEqual(
            original,
            (
                scene.render.resolution_x,
                scene.render.resolution_y,
                scene.render.image_settings.file_format,
            ),
        )
        self.assertFalse(list(self.spool.root.iterdir()))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RenderHostTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("RENDER_HOST_NATIVE_PASSED")
