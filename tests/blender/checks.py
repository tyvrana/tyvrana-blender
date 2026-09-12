"""Run with Blender's embedded Python and an installed extension."""

import importlib
import os
import threading
import unittest
from pathlib import Path

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


class BlenderTests(unittest.TestCase):
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
        unittest.defaultTestLoader.loadTestsFromTestCase(BlenderTests)
    )
finally:
    adapter.unregister()
if not outcome.wasSuccessful():
    raise RuntimeError("Blender integration tests failed")
print("BLENDER_TESTS_PASSED", outcome.testsRun)
