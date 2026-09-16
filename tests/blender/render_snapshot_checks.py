"""Native snapshot fidelity and safety on disposable scene data."""

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

package = "bl_ext.user_default.tyvrana_blender"
snapshot = importlib.import_module(package + ".render_snapshot")
artifacts = importlib.import_module(package + ".artifacts")
models = importlib.import_module(package + ".models")
errors = importlib.import_module(package + ".errors")
scene_helpers = importlib.import_module("tests.blender.scene")


class RenderSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        scene_helpers.prepare_scene()
        self.spool = artifacts.ArtifactSpool()
        self.addCleanup(self.spool.close)
        self.identifier = "b" * 32

    def test_snapshot_preserves_scene_state_and_live_image_pixels(self) -> None:
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
        result = snapshot.prepare(
            models.RenderArguments(width=64, cycles={"samples": 2}),
            self.spool,
            self.identifier,
        )
        self.assertEqual(result.engine, "CYCLES")
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
        config = json.loads((root / "config.json").read_text())
        buffer = next(b for b in config["buffers"] if b["name"] == image.name)
        values = np.fromfile(root / buffer["filename"], dtype=np.float32)
        self.assertTrue(np.array_equal(values, expected))
        self.assertTrue((root / "scene.blend").is_file())
        bpy.data.images.remove(image)

    def test_busy_bake_rejects_render_preparation(self) -> None:
        with patch.object(snapshot.bake_jobs, "busy", return_value=True):
            with self.assertRaises(errors.OperationError) as raised:
                snapshot.prepare(models.RenderArguments(), self.spool, self.identifier)
        self.assertEqual(raised.exception.error.code, "adapter_busy")
        self.assertFalse((self.spool.root / "jobs").exists())

    def test_invalid_output_and_missing_camera_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "existing.png"
            destination.write_bytes(b"unchanged")
            with self.assertRaises(errors.OperationError) as raised:
                snapshot.prepare(
                    models.RenderArguments(output={"filepath": str(destination)}),
                    self.spool,
                    self.identifier,
                )
            self.assertEqual(raised.exception.error.code, "file_exists")
            self.assertEqual(destination.read_bytes(), b"unchanged")
            with self.assertRaises(errors.OperationError) as raised:
                snapshot.prepare(
                    models.RenderArguments(
                        output={"filepath": str(Path(root) / "missing" / "image.png")}
                    ),
                    self.spool,
                    self.identifier,
                )
            self.assertEqual(raised.exception.error.code, "file_destination_invalid")
        bpy.context.scene.camera = None
        with self.assertRaises(errors.OperationError) as raised:
            snapshot.prepare(models.RenderArguments(), self.spool, self.identifier)
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
