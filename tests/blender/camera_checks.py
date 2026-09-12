"""Camera behavior checks executed by Blender's embedded Python."""

import importlib
import os
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import (
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
models = importlib.import_module("bl_ext.user_default.tyvrana_blender.camera_models")
objects = importlib.import_module("bl_ext.user_default.tyvrana_blender.models")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
scene_helpers = importlib.import_module("tests.blender.scene")
png_helpers = importlib.import_module("tests.png")


class CameraTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend(adapter._runtime.worker.spool)

    def execute(
        self, operation: str, arguments: JsonValue
    ) -> OperationSuccess | OperationFailure:
        response = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="camera-test",
                operation=operation,
                arguments=arguments,
            ),
        )
        assert isinstance(response, OperationSuccess | OperationFailure)
        return response

    def render(self) -> bytes:
        spool = adapter._runtime.worker.spool
        result, descriptor = self.backend.render(
            objects.RenderArguments(width=256, height=256)
        )
        data = Path(spool.root, descriptor.artifact_id + ".png").read_bytes()
        self.assertEqual(png_helpers.inspect_png(data), (256, 256))
        spool.release((descriptor,))
        self.assertEqual(list(spool.root.iterdir()), [])
        return data

    def test_empty_camera_inspection(self) -> None:
        self.assertEqual(
            self.backend.camera_inspect().model_dump(),
            {"active_camera": None, "cameras": []},
        )

    def test_create_defaults_names_selection_and_activation(self) -> None:
        self.backend.create(objects.CreateArguments(primitive="cube", name="Selected"))
        selected = bpy.context.view_layer.objects.active
        first = self.backend.camera_create(models.CameraCreateArguments(name="Camera"))
        self.assertTrue(first.active)
        self.assertEqual(first.projection, "perspective")
        self.assertEqual(first.lens_mm, 50)
        second = self.backend.camera_create(
            models.CameraCreateArguments(name="Camera", lens_mm=72)
        )
        self.assertNotEqual(second.name, first.name)
        self.assertFalse(second.active)
        self.assertEqual(bpy.data.objects[second.name].data.lens, 72)
        self.assertEqual(bpy.context.view_layer.objects.active, selected)
        third = self.backend.camera_create(
            models.CameraCreateArguments(name="A", make_active=True)
        )
        inspected = self.backend.camera_inspect()
        self.assertEqual(inspected.active_camera, third.name)
        self.assertEqual(
            [c.name for c in inspected.cameras],
            sorted([first.name, second.name, third.name]),
        )
        self.assertEqual(sum(c.active for c in inspected.cameras), 1)

    def test_orthographic_configuration_and_partial_values(self) -> None:
        created = self.backend.camera_create(
            models.CameraCreateArguments(
                name="Ortho", projection="orthographic", ortho_scale=4
            )
        )
        self.assertEqual(created.ortho_scale, 4)
        self.assertIsNone(created.lens_mm)
        changed = self.backend.camera_configure(
            models.CameraConfigureArguments(
                name=created.name,
                ortho_scale=9,
                clip_start=0.5,
                clip_end=250,
                shift_x=0.25,
                shift_y=-0.125,
            )
        )
        self.assertEqual(
            (
                changed.ortho_scale,
                changed.clip_start,
                changed.clip_end,
                changed.shift_x,
                changed.shift_y,
            ),
            (9, 0.5, 250, 0.25, -0.125),
        )
        partial = self.backend.camera_configure(
            models.CameraConfigureArguments(name=created.name, shift_x=3)
        )
        self.assertEqual(partial.ortho_scale, 9)
        self.assertEqual(partial.clip_end, 250)
        self.assertEqual(partial.shift_y, -0.125)
        self.assertEqual(partial.shift_x, 3)

    def test_combined_invalid_clipping_does_not_mutate(self) -> None:
        camera = self.backend.camera_create(
            models.CameraCreateArguments(name="Camera", clip_start=1, clip_end=10)
        )
        for update in (
            {"clip_start": 11, "lens_mm": 90},
            {"clip_end": 0.5, "shift_x": 1},
            {"ortho_scale": 3},
        ):
            result = self.execute(
                "blender.camera.configure", {"name": camera.name, **update}
            )
            assert isinstance(result, OperationFailure)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(self.backend.camera_inspect().cameras[0], camera)

    def test_float32_limits_and_precision_are_not_silently_clamped(self) -> None:
        camera = self.backend.camera_create(models.CameraCreateArguments(name="Camera"))
        data = bpy.data.objects[camera.name].data
        self.assertEqual(data.bl_rna.properties["lens"].hard_min, 1)
        self.assertEqual(data.bl_rna.properties["clip_start"].hard_min, models.CLIP_MIN)
        self.assertEqual(data.bl_rna.properties["shift_x"].hard_max, models.FLOAT32_MAX)
        self.assertEqual(
            set(data.bl_rna.properties["type"].enum_items.keys()),
            set(models.PROJECTIONS),
        )
        for update in (
            {"lens_mm": 0.999999999},
            {"clip_start": 1, "clip_end": 1.000000001},
            {"shift_y": 1e300},
        ):
            result = self.execute(
                "blender.camera.configure", {"name": camera.name, **update}
            )
            assert isinstance(result, OperationFailure)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(self.backend.camera_inspect().cameras[0], camera)
        changed = self.backend.camera_configure(
            models.CameraConfigureArguments(name=camera.name, lens_mm=8000, shift_x=3)
        )
        self.assertEqual(changed.lens_mm, 8000)
        self.assertEqual(changed.shift_x, 3)

    def test_panorama_and_custom_are_inspected_without_pretending_optics(self) -> None:
        camera = self.backend.camera_create(models.CameraCreateArguments(name="Camera"))
        data = bpy.data.objects[camera.name].data
        for native, normalized in (("PANO", "panoramic"), ("CUSTOM", "custom")):
            data.type = native
            result = self.backend.camera_inspect().cameras[0]
            self.assertEqual(result.projection, normalized)
            self.assertIsNone(result.lens_mm)
            self.assertIsNone(result.ortho_scale)
            failure = self.execute(
                "blender.camera.configure", {"name": camera.name, "shift_x": 1}
            )
            assert isinstance(failure, OperationFailure)
            self.assertEqual(failure.error.code, "unsupported_projection")
            self.assertEqual(data.shift_x, 0)
        switched = self.backend.camera_configure(
            models.CameraConfigureArguments(
                name=camera.name, projection="orthographic", ortho_scale=5
            )
        )
        self.assertEqual(switched.projection, "orthographic")
        self.assertEqual(switched.ortho_scale, 5)

    def test_object_transform_is_canonical_and_inspection_uses_local_pose(self) -> None:
        camera = self.backend.camera_create(models.CameraCreateArguments(name="Camera"))
        self.backend.create(
            objects.CreateArguments(
                primitive="cube", name="Parent", location=[10, 0, 0]
            )
        )
        bpy.data.objects[camera.name].parent = bpy.data.objects["Parent"]
        transformed = self.backend.transform(
            objects.TransformArguments(
                name=camera.name,
                location=[2, 3, 4],
                rotation=[0.1, 0.2, 0.3],
                scale=[2, 2, 2],
            )
        )
        inspected = self.backend.camera_inspect().cameras[0]
        self.assertEqual(
            (inspected.location, inspected.rotation, inspected.scale),
            (transformed.location, transformed.rotation, transformed.scale),
        )
        self.assertEqual(inspected.location, [2, 3, 4])
        configured = self.backend.camera_configure(
            models.CameraConfigureArguments(name=camera.name, lens_mm=60)
        )
        self.assertEqual(
            (configured.location, configured.rotation, configured.scale),
            (inspected.location, inspected.rotation, inspected.scale),
        )

    def test_shared_camera_data_is_made_independent(self) -> None:
        camera = self.backend.camera_create(models.CameraCreateArguments(name="Camera"))
        obj = bpy.data.objects[camera.name]
        other = bpy.data.objects.new("Other", obj.data)
        bpy.context.scene.collection.objects.link(other)
        self.backend.camera_configure(
            models.CameraConfigureArguments(name=camera.name, lens_mm=80)
        )
        self.assertNotEqual(obj.data, other.data)
        self.assertEqual(obj.data.lens, 80)
        self.assertEqual(other.data.lens, 50)

    def test_linked_camera_data_rejects_configuration_but_can_be_active(self) -> None:
        camera = self.backend.camera_create(
            models.CameraCreateArguments(name="LibraryCamera")
        )
        obj = bpy.data.objects[camera.name]
        path = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "camera-library.blend")
        bpy.data.libraries.write(path, {obj})
        bpy.data.objects.remove(obj, do_unlink=True)
        with bpy.data.libraries.load(path, link=True) as (source, target):
            target.objects = [camera.name]
        linked = target.objects[0]
        bpy.context.scene.collection.objects.link(linked)
        active = self.backend.camera_set_active(
            models.CameraSetActiveArguments(name=linked.name)
        )
        self.assertTrue(active.active)
        failure = self.execute(
            "blender.camera.configure", {"name": linked.name, "lens_mm": 80}
        )
        assert isinstance(failure, OperationFailure)
        self.assertEqual(failure.error.code, "invalid_context")
        self.assertEqual(linked.data.lens, 50)

    def test_unexpected_summary_failure_restores_camera_data(self) -> None:
        for shared in (False, True):
            camera = self.backend.camera_create(
                models.CameraCreateArguments(name="Camera")
            )
            obj = bpy.data.objects[camera.name]
            original = obj.data
            if shared:
                other = bpy.data.objects.new("Other", original)
                bpy.context.scene.collection.objects.link(other)
            count = len(bpy.data.cameras)
            # Fail only Python result construction; data writes/rollback use real bpy.
            with (
                patch.object(
                    adapter,
                    "camera_summary",
                    side_effect=RuntimeError("summary unavailable"),
                ),
                self.assertRaises(RuntimeError),
            ):
                self.backend.camera_configure(
                    models.CameraConfigureArguments(
                        name=camera.name, lens_mm=90, shift_x=0.25
                    )
                )
            self.assertEqual(obj.data, original)
            self.assertEqual(original.lens, 50)
            self.assertEqual(original.shift_x, 0)
            self.assertEqual(len(bpy.data.cameras), count)

    def test_failed_creation_removes_object_and_data(self) -> None:
        self.backend.camera_create(models.CameraCreateArguments(name="Existing"))
        previous = bpy.context.scene.camera
        counts = len(bpy.data.objects), len(bpy.data.cameras)
        with (
            patch.object(
                adapter,
                "camera_summary",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.backend.camera_create(
                models.CameraCreateArguments(name="Failed", make_active=True)
            )
        self.assertEqual((len(bpy.data.objects), len(bpy.data.cameras)), counts)
        self.assertEqual(bpy.context.scene.camera, previous)

    def test_failed_active_selection_restores_previous_camera(self) -> None:
        self.backend.camera_create(models.CameraCreateArguments(name="First"))
        second = self.backend.camera_create(models.CameraCreateArguments(name="Second"))
        previous = bpy.context.scene.camera
        with (
            patch.object(
                adapter,
                "camera_summary",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.backend.camera_set_active(
                models.CameraSetActiveArguments(name=second.name)
            )
        self.assertEqual(bpy.context.scene.camera, previous)

    def test_missing_and_non_camera_targets(self) -> None:
        self.backend.create(objects.CreateArguments(primitive="cube", name="Mesh"))
        for operation in ("blender.camera.configure", "blender.camera.set_active"):
            for name, code in (
                ("Missing", "object_not_found"),
                ("Mesh", "object_not_camera"),
            ):
                result = self.execute(operation, {"name": name})
                assert isinstance(result, OperationFailure)
                self.assertEqual(result.error.code, code)
        self.assertIsNone(bpy.context.scene.camera)

    def test_all_camera_operations_require_main_thread(self) -> None:
        errors: list[Exception] = []

        def forbidden() -> None:
            actions: list[Callable[[], object]] = [
                lambda: self.backend.camera_inspect(),
                lambda: self.backend.camera_create(models.CameraCreateArguments()),
                lambda: self.backend.camera_configure(
                    models.CameraConfigureArguments(name="Missing")
                ),
                lambda: self.backend.camera_set_active(
                    models.CameraSetActiveArguments(name="Missing")
                ),
            ]
            for action in actions:
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)

        thread = threading.Thread(target=forbidden)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 4)
        self.assertTrue(all(isinstance(exc, RuntimeError) for exc in errors))
        self.assertEqual(len(bpy.context.scene.objects), 0)

    def test_queued_camera_cancel_and_edit_mode_creation(self) -> None:
        runtime = adapter._runtime
        runtime.queue.submit(
            OperationRequest(
                type="operation.request",
                request_id="cancel-camera",
                operation="blender.camera.create",
                arguments={},
            )
        )
        runtime.queue.cancel("cancel-camera")
        runtime.queue.drain(
            lambda result: self.fail("Cancelled camera operation completed")
        )
        self.assertEqual(self.backend.camera_inspect().cameras, [])
        self.backend.create(objects.CreateArguments(primitive="cube"))
        bpy.ops.object.mode_set(mode="EDIT")
        result = self.execute("blender.camera.create", {})
        assert isinstance(result, OperationFailure)
        self.assertEqual(result.error.code, "invalid_context")
        bpy.ops.object.mode_set(mode="OBJECT")

    def test_native_active_camera_render_and_deletion(self) -> None:
        scene_helpers.prepare_scene()
        for camera in self.backend.camera_inspect().cameras:
            self.backend.delete(objects.DeleteArguments(name=camera.name))
        first = self.backend.camera_create(
            models.CameraCreateArguments(
                name="Perspective",
                location=[0, -8, 4.5],
                rotation=[1.1583858728408813, 0, 0],
                lens_mm=40,
            )
        )
        second = self.backend.camera_create(
            models.CameraCreateArguments(
                name="Orthographic",
                projection="orthographic",
                ortho_scale=6,
                location=[2, -8, 4.5],
                rotation=[1.1583858728408813, 0, 0],
            )
        )
        self.backend.camera_set_active(models.CameraSetActiveArguments(name=first.name))
        perspective = self.render()
        self.backend.camera_set_active(
            models.CameraSetActiveArguments(name=second.name)
        )
        ortho = self.render()
        self.assertNotEqual(
            png_helpers.rgb_pixels(perspective), png_helpers.rgb_pixels(ortho)
        )
        self.backend.camera_configure(
            models.CameraConfigureArguments(name=second.name, ortho_scale=9)
        )
        wider = self.render()
        self.assertNotEqual(
            png_helpers.rgb_pixels(ortho), png_helpers.rgb_pixels(wider)
        )
        self.backend.delete(objects.DeleteArguments(name=second.name))
        remaining = self.backend.camera_inspect()
        self.assertIsNone(remaining.active_camera)
        self.assertEqual([c.name for c in remaining.cameras], [first.name])
        result = self.execute("blender.render.image", {})
        assert isinstance(result, OperationFailure)
        self.assertEqual(result.error.code, "no_camera")
