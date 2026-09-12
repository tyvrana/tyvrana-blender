"""Light checks executed in Blender's embedded Python with real datablocks."""

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
models = importlib.import_module("bl_ext.user_default.tyvrana_blender.light_models")
objects = importlib.import_module("bl_ext.user_default.tyvrana_blender.models")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
numeric = importlib.import_module("bl_ext.user_default.tyvrana_blender.numeric")


class LightTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend(adapter._runtime.worker.spool)

    def execute(
        self, name: str, arguments: JsonValue
    ) -> OperationSuccess | OperationFailure:
        response = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="light-test",
                operation=name,
                arguments=arguments,
            ),
        )
        assert isinstance(response, OperationSuccess | OperationFailure)
        return response

    def test_empty_inspection_and_default_types(self) -> None:
        self.assertEqual(self.backend.light_inspect().model_dump(), {"lights": []})
        for kind in ("point", "sun", "spot", "area"):
            summary = self.backend.light_create(
                models.LightCreateArguments(type=kind, name=kind)
            )
            data = bpy.data.objects[kind].data
            self.assertEqual(data.type, kind.upper())
            self.assertEqual(summary.energy, data.energy)
            self.assertEqual(summary.energy, 10)
            self.assertTrue(summary.normalize and summary.use_shadow)
            self.assertEqual(summary.color, [1, 1, 1])
            self.assertNotIn(
                "radius", summary.settings.model_dump() if kind == "sun" else {}
            )
        self.assertEqual(
            [x.name for x in self.backend.light_inspect().lights],
            ["area", "point", "spot", "sun"],
        )

    def test_explicit_duplicate_names_are_rejected_without_mutation(self) -> None:
        self.backend.create(objects.CreateArguments(primitive="cube", name="Existing"))
        selected = bpy.context.view_layer.objects.active
        counts = len(bpy.data.objects), len(bpy.data.lights)
        failed = self.execute(
            "blender.light.create", {"type": "point", "name": "Existing"}
        )
        assert isinstance(failed, OperationFailure)
        self.assertEqual(failed.error.code, "invalid_arguments")
        self.assertEqual(counts, (len(bpy.data.objects), len(bpy.data.lights)))
        first = self.backend.light_create(models.LightCreateArguments(type="point"))
        second = self.backend.light_create(models.LightCreateArguments(type="point"))
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(bpy.context.view_layer.objects.active, selected)
        self.assertTrue(selected.select_get())

    def test_point_configuration_and_object_transform(self) -> None:
        first = self.backend.light_create(
            models.LightCreateArguments(type="point", name="Light")
        )
        changed = self.backend.light_configure(
            models.LightConfigureArguments(
                name=first.name,
                color=[2, 0.25, 0],
                energy=100,
                radius=0.5,
                exposure=2,
                normalize=False,
                use_shadow=False,
            )
        )
        self.assertEqual(changed.color, [2, 0.25, 0])
        self.assertEqual(changed.settings.radius, 0.5)
        self.assertEqual(changed.energy, 100)
        self.assertFalse(changed.normalize or changed.use_shadow)
        self.backend.create(
            objects.CreateArguments(
                primitive="cube", name="Parent", location=[10, 0, 0]
            )
        )
        bpy.data.objects[first.name].parent = bpy.data.objects["Parent"]
        pose = self.backend.transform(
            objects.TransformArguments(
                name=first.name,
                location=[2, 3, 4],
                rotation=[0.1, 0.2, 0.3],
                scale=[2, 1, 1],
            )
        )
        summary = self.backend.light_inspect().lights[0]
        self.assertEqual(
            (summary.location, summary.rotation, summary.scale),
            (pose.location, pose.rotation, pose.scale),
        )
        self.assertEqual(summary.parent, "Parent")
        self.assertEqual(summary.energy, 100)

    def test_sun_and_spot_configuration(self) -> None:
        sun = self.backend.light_create(
            models.LightCreateArguments(type="sun", name="Sun")
        )
        sun = self.backend.light_configure(
            models.LightConfigureArguments(name=sun.name, angle=0.25)
        )
        self.assertEqual(sun.settings.angle, 0.25)
        pose = self.backend.transform(
            objects.TransformArguments(name=sun.name, rotation=[0.5, 0, 1])
        )
        self.assertEqual(self.backend.light_inspect().lights[0].rotation, pose.rotation)
        spot = self.backend.light_create(
            models.LightCreateArguments(
                type="spot", name="Spot", radius=0.5, spot_size=1.5, spot_blend=0.25
            )
        )
        self.assertEqual(
            spot.settings.model_dump(),
            {"radius": 0.5, "spot_size": 1.5, "spot_blend": 0.25},
        )
        spot = self.backend.light_configure(
            models.LightConfigureArguments(name=spot.name, spot_blend=0.75)
        )
        self.assertEqual(spot.settings.radius, 0.5)
        self.assertEqual(spot.settings.spot_blend, 0.75)

    def test_area_shapes_and_partial_combined_shape(self) -> None:
        summary = self.backend.light_create(
            models.LightCreateArguments(type="area", name="Area")
        )
        for shape in ("rectangle", "ellipse", "square", "disk"):
            fields = {"name": summary.name, "shape": shape, "size": 2}
            if shape in ("rectangle", "ellipse"):
                fields["size_y"] = 3
            summary = self.backend.light_configure(
                models.LightConfigureArguments.model_validate(fields)
            )
            self.assertEqual(summary.settings.shape, shape)
            self.assertEqual(bpy.data.objects[summary.name].data.shape, shape.upper())
            self.assertEqual(
                "size_y" in summary.settings.model_dump(),
                shape in ("rectangle", "ellipse"),
            )
        failed = self.execute(
            "blender.light.configure",
            {"name": summary.name, "energy": 123, "size_y": 4},
        )
        assert isinstance(failed, OperationFailure)
        self.assertEqual(failed.error.code, "invalid_arguments")
        self.assertEqual(self.backend.light_inspect().lights[0], summary)
        summary = self.backend.light_configure(
            models.LightConfigureArguments(name=summary.name, shape="rectangle")
        )
        self.assertEqual(summary.settings.size_y, 3)

    def test_invalid_type_specific_fields_leave_state_unchanged(self) -> None:
        for kind, field in [
            ("point", "angle"),
            ("sun", "radius"),
            ("spot", "size"),
            ("area", "spot_size"),
        ]:
            summary = self.backend.light_create(
                models.LightCreateArguments(type=kind, name=kind)
            )
            result = self.execute(
                "blender.light.configure", {"name": kind, "energy": 123, field: 1}
            )
            assert isinstance(result, OperationFailure)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(adapter.light_summary(bpy.data.objects[kind]), summary)

    def test_native_hard_limits_and_rounding(self) -> None:
        summary = self.backend.light_create(
            models.LightCreateArguments(type="spot", name="Light")
        )
        data = bpy.data.objects[summary.name].data
        self.assertEqual(
            set(data.bl_rna.properties["type"].enum_items.keys()),
            set(models.LIGHT_TYPES),
        )
        self.assertEqual(
            data.bl_rna.properties["energy"].hard_min, -numeric.FLOAT32_MAX
        )
        self.assertEqual(data.bl_rna.properties["color"].hard_max, numeric.FLOAT32_MAX)
        self.assertEqual(data.bl_rna.properties["spot_size"].hard_min, models.SPOT_MIN)
        self.assertEqual(data.bl_rna.properties["spot_size"].hard_max, models.ANGLE_MAX)
        updates: list[dict[str, JsonValue]] = [
            {"radius": -0.000001},
            {"spot_size": models.SPOT_MIN * 0.99},
            {"energy": 1e300},
            {"color": [-1, 0, 0]},
        ]
        for update in updates:
            failed = self.execute(
                "blender.light.configure", {"name": summary.name, **update}
            )
            assert isinstance(failed, OperationFailure)
            self.assertEqual(failed.error.code, "invalid_arguments")
            self.assertEqual(
                adapter.light_summary(bpy.data.objects[summary.name]), summary
            )
        changed = self.backend.light_configure(
            models.LightConfigureArguments(
                name=summary.name, energy=-20, color=[4, 2, 0.1], radius=150
            )
        )
        self.assertEqual(changed.energy, -20)
        self.assertEqual(changed.color, [4, 2, numeric.binary32(0.1)])
        self.assertEqual(changed.settings.radius, 150)

    def test_shared_data_copy_preserves_other_light(self) -> None:
        summary = self.backend.light_create(
            models.LightCreateArguments(type="point", name="Light")
        )
        obj = bpy.data.objects[summary.name]
        other = bpy.data.objects.new("Other", obj.data)
        bpy.context.scene.collection.objects.link(other)
        self.backend.light_configure(
            models.LightConfigureArguments(
                name=summary.name, energy=70, color=[0.5, 0, 0]
            )
        )
        self.assertNotEqual(obj.data, other.data)
        self.assertEqual(other.data.energy, 10)
        self.assertEqual(list(other.data.color), [1, 1, 1])

    def test_linked_data_rejected(self) -> None:
        summary = self.backend.light_create(
            models.LightCreateArguments(type="point", name="Linked")
        )
        obj = bpy.data.objects[summary.name]
        path = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "light-library.blend")
        self.backend.create(
            objects.CreateArguments(primitive="cube", name="LinkedMesh")
        )
        mesh = bpy.data.objects["LinkedMesh"]
        bpy.data.libraries.write(path, {obj, mesh})
        bpy.data.objects.remove(mesh, do_unlink=True)
        bpy.data.objects.remove(obj, do_unlink=True)
        with bpy.data.libraries.load(path, link=True) as (_, target):
            target.objects = [summary.name, "LinkedMesh"]
        linked = target.objects[0]
        bpy.context.scene.collection.objects.link(linked)
        bpy.context.scene.collection.objects.link(target.objects[1])
        wrong = self.execute("blender.light.configure", {"name": "LinkedMesh"})
        assert isinstance(wrong, OperationFailure)
        self.assertEqual(wrong.error.code, "object_not_light")
        failed = self.execute(
            "blender.light.configure", {"name": linked.name, "energy": 30}
        )
        assert isinstance(failed, OperationFailure)
        self.assertEqual(failed.error.code, "invalid_context")
        self.assertEqual(linked.data.energy, 10)

    def test_failed_configure_rolls_back_color_and_shared_copy(self) -> None:
        for shared in (False, True):
            summary = self.backend.light_create(
                models.LightCreateArguments(type="area")
            )
            obj = bpy.data.objects[summary.name]
            original = obj.data
            if shared:
                other = bpy.data.objects.new("Other", original)
                bpy.context.scene.collection.objects.link(other)
            count = len(bpy.data.lights)
            with (
                patch.object(
                    adapter,
                    "light_summary",
                    side_effect=RuntimeError("summary unavailable"),
                ),
                self.assertRaises(RuntimeError),
            ):
                self.backend.light_configure(
                    models.LightConfigureArguments(
                        name=summary.name,
                        energy=200,
                        color=[0, 0.5, 0],
                        shape="ellipse",
                        size_y=4,
                    )
                )
            self.assertEqual(obj.data, original)
            self.assertEqual(original.energy, 10)
            self.assertEqual(list(original.color), [1, 1, 1])
            self.assertEqual(original.shape, "SQUARE")
            self.assertEqual(original.size_y, 0.25)
            self.assertEqual(len(bpy.data.lights), count)

    def test_failed_creation_removes_data_and_object(self) -> None:
        counts = len(bpy.data.objects), len(bpy.data.lights)
        with (
            patch.object(
                adapter,
                "light_summary",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.backend.light_create(models.LightCreateArguments(type="point"))
        self.assertEqual((len(bpy.data.objects), len(bpy.data.lights)), counts)

    def test_lookup_and_deletion(self) -> None:
        self.backend.create(objects.CreateArguments(primitive="cube", name="Mesh"))
        for name, code in [
            ("Mesh", "object_not_light"),
            ("Missing", "object_not_found"),
        ]:
            failed = self.execute("blender.light.configure", {"name": name})
            assert isinstance(failed, OperationFailure)
            self.assertEqual(failed.error.code, code)
        light = self.backend.light_create(models.LightCreateArguments(type="point"))
        data = bpy.data.objects[light.name].data
        self.backend.delete(objects.DeleteArguments(name=light.name))
        self.assertEqual(self.backend.light_inspect().lights, [])
        self.assertEqual(data.users, 0)

    def test_main_thread_and_cancelled_queue(self) -> None:
        errors = []

        def forbidden() -> None:
            actions: tuple[Callable[[], object], ...] = (
                lambda: self.backend.light_inspect(),
                lambda: self.backend.light_create(
                    models.LightCreateArguments(type="point")
                ),
                lambda: self.backend.light_configure(
                    models.LightConfigureArguments(name="Missing")
                ),
            )
            for action in actions:
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)

        thread = threading.Thread(target=forbidden)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 3)
        self.assertTrue(all(isinstance(e, RuntimeError) for e in errors))
        runtime = adapter._runtime
        runtime.queue.submit(
            OperationRequest(
                type="operation.request",
                request_id="cancel-light",
                operation="blender.light.create",
                arguments={"type": "point"},
            )
        )
        runtime.queue.cancel("cancel-light")
        runtime.queue.drain(lambda response: self.fail("Cancelled creation completed"))
        self.assertEqual(self.backend.light_inspect().lights, [])

    def test_edit_mode_creation_is_rejected(self) -> None:
        self.backend.create(objects.CreateArguments(primitive="cube"))
        bpy.ops.object.mode_set(mode="EDIT")
        failed = self.execute("blender.light.create", {"type": "point"})
        bpy.ops.object.mode_set(mode="OBJECT")
        assert isinstance(failed, OperationFailure)
        self.assertEqual(failed.error.code, "invalid_context")
