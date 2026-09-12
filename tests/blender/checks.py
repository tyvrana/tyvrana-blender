"""Run with Blender's embedded Python and an installed extension."""

import hashlib
import importlib
import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import (
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

extension = importlib.import_module("bl_ext.user_default.tyvrana_blender")
adapter = importlib.import_module(extension.__name__ + ".blender")
operations = importlib.import_module(extension.__name__ + ".operations")
models = importlib.import_module(extension.__name__ + ".models")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
scene_helpers = importlib.import_module("tests.blender.scene")
png_helpers = importlib.import_module("tests.png")
camera_checks = importlib.import_module("tests.blender.camera_checks")
light_checks = importlib.import_module("tests.blender.light_checks")


class BlenderTests(unittest.TestCase):
    def test_render_png_and_settings_restored(self) -> None:
        scene_helpers.prepare_scene()
        render = bpy.context.scene.render
        render.resolution_x, render.resolution_y, render.resolution_percentage = (
            640,
            480,
            25,
        )
        render.use_border = render.use_crop_to_border = True
        render.image_settings.file_format = "JPEG"
        render.image_settings.color_mode = "RGB"
        saved = (
            render.resolution_x,
            render.resolution_y,
            render.resolution_percentage,
            render.use_border,
            render.use_crop_to_border,
            render.image_settings.file_format,
            render.image_settings.color_mode,
            render.filepath,
        )
        spool = adapter._runtime.worker.spool
        response = operations.execute(
            adapter.BlenderBackend(spool),
            OperationRequest(
                type="operation.request",
                request_id="render",
                operation="blender.render.image",
                arguments={},
            ),
        )
        self.assertIsInstance(response, OperationSuccess)
        descriptor = response.artifacts[0]
        data = (spool.root / (descriptor.artifact_id + ".png")).read_bytes()
        self.assertEqual(png_helpers.inspect_png(data), (512, 512))
        png_helpers.assert_image_variation(data)
        self.assertEqual(descriptor.byte_size, len(data))
        self.assertEqual(descriptor.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(descriptor.media_type, "image/png")
        self.assertEqual(
            response.result, {"width": 512, "height": 512, "format": "png"}
        )
        self.assertNotIn(str(spool.root), response.model_dump_json())
        self.assertEqual(
            saved,
            (
                render.resolution_x,
                render.resolution_y,
                render.resolution_percentage,
                render.use_border,
                render.use_crop_to_border,
                render.image_settings.file_format,
                render.image_settings.color_mode,
                render.filepath,
            ),
        )
        spool.release(response.artifacts)
        self.assertEqual(list(spool.root.iterdir()), [])

    def test_render_failure_restores_settings_and_removes_file(self) -> None:
        scene_helpers.prepare_scene()
        render = bpy.context.scene.render
        saved = render.resolution_x, render.resolution_y, render.resolution_percentage
        spool = adapter._runtime.worker.spool
        render_module = importlib.import_module(extension.__name__ + ".render")
        tree = bpy.data.node_groups.new("OutputTest", "CompositorNodeTree")
        output = tree.nodes.new("CompositorNodeOutputFile")
        old_tree = bpy.context.scene.compositing_node_group
        bpy.context.scene.compositing_node_group = tree

        def cancelled_render(*args: object, **kwargs: object) -> set[str]:
            self.assertTrue(output.mute)
            self.assertEqual(render.resolution_x, 128)
            self.assertEqual(render.resolution_percentage, 100)
            return {"CANCELLED"}

        # Replace only operator invocation; context, settings, and storage are real.
        boundary = SimpleNamespace(
            context=bpy.context,
            app=bpy.app,
            data=bpy.data,
            ops=SimpleNamespace(render=SimpleNamespace(render=cancelled_render)),
        )
        with patch.object(render_module, "bpy", boundary):
            response = operations.execute(
                adapter.BlenderBackend(spool),
                OperationRequest(
                    type="operation.request",
                    request_id="failed",
                    operation="blender.render.image",
                    arguments={"width": 128},
                ),
            )
        self.assertIsInstance(response, OperationFailure)
        self.assertEqual(response.error.code, "render_failed")
        self.assertFalse(output.mute)
        bpy.context.scene.compositing_node_group = old_tree
        bpy.data.node_groups.remove(tree)
        self.assertEqual(
            saved,
            (render.resolution_x, render.resolution_y, render.resolution_percentage),
        )
        self.assertEqual(list(spool.root.iterdir()), [])

    def test_render_without_camera_is_structured_failure(self) -> None:
        response = operations.execute(
            adapter.BlenderBackend(adapter._runtime.worker.spool),
            OperationRequest(
                type="operation.request",
                request_id="no-camera",
                operation="blender.render.image",
                arguments={},
            ),
        )
        self.assertIsInstance(response, OperationFailure)
        self.assertEqual(response.error.code, "no_camera")

    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        adapter.register()
        adapter.pump()

    def test_empty_scene(self) -> None:
        scene = adapter.BlenderBackend().inspect()
        self.assertEqual(scene.object_count, 0)
        self.assertEqual(scene.objects, [])
        self.assertIsNone(scene.active_object)
        self.assertEqual(scene.selected_objects, [])

    def test_all_primitives(self) -> None:
        backend = adapter.BlenderBackend()
        for primitive in (
            "cube",
            "plane",
            "uv_sphere",
            "ico_sphere",
            "cylinder",
            "cone",
            "torus",
        ):
            with self.subTest(primitive=primitive):
                obj = backend.create(
                    models.CreateArguments(primitive=primitive, name=primitive)
                )
                self.assertEqual(obj.name, primitive)
                self.assertEqual(obj.type, "MESH")
                self.assertTrue(len(bpy.data.objects[obj.name].data.vertices) > 0)
        self.assertEqual(backend.inspect().object_count, 7)

    def test_create_transform_inspect_delete(self) -> None:
        backend = adapter.BlenderBackend()
        cube = backend.create(
            models.CreateArguments(primitive="cube", name="RoundTrip")
        )
        self.assertEqual(cube.dimensions, [2, 2, 2])
        moved = backend.transform(
            models.TransformArguments(
                name=cube.name,
                location=[2, 3, 4],
                rotation=[0.1, 0.2, 0.3],
                scale=[2, 3, 4],
            )
        )
        self.assertEqual(moved.location, [2, 3, 4])
        self.assertEqual(moved.scale, [2, 3, 4])
        for actual, wanted in zip(moved.rotation, [0.1, 0.2, 0.3], strict=True):
            self.assertAlmostEqual(actual, wanted, places=5)
        partial = backend.transform(
            models.TransformArguments(name=cube.name, location=[5, 6, 7])
        )
        self.assertEqual(partial.scale, [2, 3, 4])
        self.assertEqual(backend.inspect().objects[0].location, [5, 6, 7])
        self.assertEqual(
            backend.delete(models.DeleteArguments(name=cube.name)).deleted, cube.name
        )
        self.assertEqual(backend.inspect().object_count, 0)

    def test_invalid_primitive_is_protocol_failure(self) -> None:
        result = operations.execute(
            adapter.BlenderBackend(),
            OperationRequest(
                type="operation.request",
                request_id="invalid",
                operation="blender.object.create_primitive",
                arguments={"primitive": "duck"},
            ),
        )
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "invalid_arguments")
        self.assertEqual(len(bpy.context.scene.objects), 0)

    def test_missing_objects_are_protocol_failures(self) -> None:
        for operation in ("blender.object.delete", "blender.object.set_transform"):
            result = operations.execute(
                adapter.BlenderBackend(),
                OperationRequest(
                    type="operation.request",
                    request_id="missing",
                    operation=operation,
                    arguments={"name": "absent"},
                ),
            )
            self.assertIsInstance(result, OperationFailure)
            self.assertEqual(result.error.code, "object_not_found")

    def test_metadata_selection_parent_visibility(self) -> None:
        backend = adapter.BlenderBackend()
        backend.create(models.CreateArguments(primitive="cube", name="Z-parent"))
        backend.create(models.CreateArguments(primitive="plane", name="A-child"))
        child = bpy.data.objects["A-child"]
        child.parent = bpy.data.objects["Z-parent"]
        child.hide_render = True
        scene = backend.inspect()
        self.assertEqual([obj.name for obj in scene.objects], ["A-child", "Z-parent"])
        self.assertEqual(scene.active_object, "A-child")
        self.assertEqual(scene.selected_objects, ["A-child"])
        self.assertEqual(scene.objects[0].parent, "Z-parent")
        self.assertTrue(scene.objects[0].hide_render)

    def test_wrong_thread_cannot_access_blender(self) -> None:
        errors: list[Exception] = []

        def forbidden() -> None:
            try:
                adapter.BlenderBackend().inspect()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=forbidden)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_queued_cancellation_prevents_scene_mutation(self) -> None:
        runtime = adapter._runtime
        runtime.queue.submit(
            OperationRequest(
                type="operation.request",
                request_id="cancel",
                operation="blender.object.create_primitive",
                arguments={"primitive": "cube"},
            )
        )
        runtime.queue.cancel("cancel")
        runtime.queue.drain(lambda result: self.fail("Cancelled operation completed"))
        self.assertEqual(runtime.queue.pending_count, 0)
        self.assertEqual(len(bpy.context.scene.objects), 0)

    def test_disable_reenable_cleanup_and_stable_instance(self) -> None:
        identity = adapter.INSTANCE_ID
        for _ in range(3):
            runtime = adapter._runtime
            adapter.unregister()
            self.assertEqual(runtime.worker.process.returncode, 0)
            self.assertEqual(runtime.queue.pending_count, 0)
            self.assertFalse(bpy.app.timers.is_registered(adapter.pump))
            self.assertNotIn(adapter.before_exit, bpy.app.handlers.exit_pre)
            self.assertIsNone(adapter._runtime)
            adapter.register()
            self.assertIsNone(adapter._runtime)
            adapter.pump()
            self.assertEqual(adapter.INSTANCE_ID, identity)
        self.assertEqual(threading.enumerate(), [threading.main_thread()])

    def test_edit_mode_rejects_mutations(self) -> None:
        adapter.BlenderBackend().create(
            models.CreateArguments(primitive="cube", name="Edit")
        )
        bpy.ops.object.mode_set(mode="EDIT")
        cases: list[tuple[str, JsonValue]] = [
            ("blender.object.create_primitive", {"primitive": "cube"}),
            ("blender.object.delete", {"name": "Edit"}),
        ]
        for operation, arguments in cases:
            result = operations.execute(
                adapter.BlenderBackend(),
                OperationRequest(
                    type="operation.request",
                    request_id="edit",
                    operation=operation,
                    arguments=arguments,
                ),
            )
            self.assertIsInstance(result, OperationFailure)
            self.assertEqual(result.error.code, "invalid_context")
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual(len(bpy.context.scene.objects), 1)

    def test_saved_project_and_load_lifecycle(self) -> None:
        filepath = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "test.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        self.assertEqual(adapter.BlenderBackend().inspect().filepath, filepath)
        self.assertEqual(adapter._runtime.filepath, filepath)
        old_worker = adapter._runtime.worker
        bpy.ops.wm.open_mainfile(filepath=filepath)
        self.assertEqual(old_worker.process.returncode, 0)
        self.assertEqual(adapter._runtime.filepath, filepath)
        self.assertTrue(bpy.app.timers.is_registered(adapter.pump))

    def test_protocol_success_has_json_summary(self) -> None:
        result = operations.execute(
            adapter.BlenderBackend(),
            OperationRequest(
                type="operation.request",
                request_id="inspect",
                operation="blender.scene.inspect",
                arguments={},
            ),
        )
        self.assertIsInstance(result, OperationSuccess)
        self.assertEqual(result.request_id, "inspect")
        self.assertEqual(result.result["object_count"], 0)


try:
    outcome = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [
                unittest.defaultTestLoader.loadTestsFromTestCase(BlenderTests),
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    light_checks.LightTests
                ),
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    camera_checks.CameraTests
                ),
            ]
        )
    )
finally:
    adapter.unregister()
if not outcome.wasSuccessful():
    raise RuntimeError("Blender integration tests failed")
print("BLENDER_TESTS_PASSED", outcome.testsRun)
