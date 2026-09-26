"""Headless reference and measurement checks on disposable native fixtures."""

import importlib
import math
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
ops = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
refs = importlib.import_module("bl_ext.user_default.tyvrana_blender.references")
backend = adapter.BlenderBackend(adapter._runtime.worker.spool)


def call(operation: str, **arguments: Any) -> Any:
    response = ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="reference-test",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(response, OperationSuccess), response
    return response.result


def reject(operation: str, **arguments: Any) -> Any:
    response = ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="reference-error",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(response, OperationFailure), response
    return response.error


def world(x: float, y: float, z: float = 0) -> dict[str, Any]:
    return {"kind": "world", "point": [x, y, z]}


class ReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image, do_unlink=True)
        for collection in list(bpy.data.collections):
            bpy.data.collections.remove(collection)
        bpy.context.scene.unit_settings.system = "NONE"
        bpy.context.scene.unit_settings.scale_length = 1
        call("image.create_generated", name="Chart", width=200, height=100)

    def reference(self, name: str = "Front", **kwargs: Any) -> Any:
        return call(
            "reference.create", references=[{"name": name, "image": "Chart", **kwargs}]
        )["references"][0]

    def test_reference_dimensions_and_native_display(self) -> None:
        r = self.reference(size=4.0, source_label="Synthetic front view")
        self.assertEqual(r["local_dimensions"], [4, 2, 0])
        self.assertEqual(
            r["world_corners"], [[-2, -1, 0], [2, -1, 0], [2, 1, 0], [-2, 1, 0]]
        )
        self.assertFalse(r["renderable"])
        obj = bpy.data.objects["Front"]
        self.assertTrue(obj.use_empty_image_alpha)
        self.assertEqual(obj.empty_image_depth, "FRONT")
        self.assertFalse(obj.show_empty_image_perspective)
        self.assertEqual(obj.data, bpy.data.images["Chart"])

    def test_batch_create_and_filtered_page(self) -> None:
        for batch in range(3):
            call(
                "reference.create",
                references=[
                    {
                        "name": f"Ref {batch * 16 + i:02}",
                        "image": "Chart",
                        "category": "plan",
                    }
                    for i in range(16)
                ],
            )
        page = call("reference.inspect")
        self.assertEqual(len(page["references"]), 32)
        self.assertEqual(page["page"]["next_offset"], 32)
        focused = call(
            "reference.inspect",
            names=["Ref 37"],
            image="Chart",
            collection="References",
            category="plan",
        )
        self.assertEqual(len(focused["references"]), 1)
        self.assertEqual(focused["page"]["total_count"], 48)
        self.assertEqual(
            call("reference.inspect", category="missing")["references"], []
        )

    def test_patch_preserves_omitted_fields_and_selection(self) -> None:
        self.reference(size=3, category="side")
        obj = bpy.data.objects["Front"]
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        r = call(
            "reference.configure",
            references=[{"name": "Front", "opacity": 0.8}],
        )["references"][0]
        self.assertEqual(r["size"], 3)
        self.assertEqual(r["category"], "side")
        self.assertTrue(obj.select_get())
        self.assertEqual(bpy.context.view_layer.objects.active, obj)
        hidden = call(
            "reference.configure", references=[{"name": "Front", "hidden": True}]
        )["references"][0]
        self.assertTrue(hidden["hidden"])

    def test_preflight_and_native_failure_cleanup(self) -> None:
        before = (
            len(bpy.data.objects),
            len(bpy.data.collections),
            bpy.data.images["Chart"].users,
        )
        reject(
            "reference.create",
            references=[
                {"name": "Good", "image": "Chart"},
                {"name": "Bad", "image": "missing"},
            ],
        )
        self.assertEqual(
            (
                len(bpy.data.objects),
                len(bpy.data.collections),
                bpy.data.images["Chart"].users,
            ),
            before,
        )
        original = refs.apply_properties
        count = 0

        def injected(obj: Any, values: Any) -> None:
            nonlocal count
            count += 1
            original(obj, values)
            if count == 2:
                raise ops.OperationError(
                    "fixture_failure", "Injected native mutation failure"
                )

        with patch.object(refs, "apply_properties", injected):
            reject(
                "reference.create",
                references=[
                    {"name": "Good", "image": "Chart"},
                    {"name": "Other", "image": "Chart"},
                ],
            )
        self.assertEqual(
            (
                len(bpy.data.objects),
                len(bpy.data.collections),
                bpy.data.images["Chart"].users,
            ),
            before,
        )

    def test_patch_rollback_on_native_failure(self) -> None:
        self.reference("A")
        self.reference("B")
        original = refs.reference_summary
        count = 0

        def injected(obj: Any) -> Any:
            nonlocal count
            count += 1
            if count == 4:
                raise ops.OperationError("fixture_failure", "Injected summary failure")
            return original(obj)

        with patch.object(refs, "reference_summary", injected):
            reject(
                "reference.configure",
                references=[{"name": "A", "opacity": 0.1}, {"name": "B", "size": 8}],
            )
        self.assertEqual(bpy.data.objects["A"].color[3], 0.5)
        self.assertEqual(bpy.data.objects["B"].empty_display_size, 1)

    def test_remove_preserves_shared_image_and_cleans_owned_collection(self) -> None:
        self.reference("A")
        self.reference("B")
        reject("image.remove", name="Chart")
        call("reference.remove", names=["A", "B"])
        self.assertNotIn("References", bpy.data.collections)
        self.assertIn("Chart", bpy.data.images)
        call("image.remove", name="Chart")
        self.assertNotIn("Chart", bpy.data.images)

    def test_invalid_image_remains_inspectable(self) -> None:
        self.reference()
        bpy.data.images.remove(bpy.data.images["Chart"], do_unlink=True)
        r = call("reference.inspect")["references"][0]
        self.assertFalse(r["valid"])
        self.assertEqual(r["world_corners"], [])
        call("reference.remove", names=["Front"])

    def test_calibration_anchor_and_rotated_nonuniform_reference(self) -> None:
        self.reference(size=2, rotation=[0, 0, math.pi / 2], location=[3, 4, 0])
        call("object.set_transform", name="Front", scale=[2, 1, 1])
        r = call(
            "reference.calibrate", name="Front", a=[0, 0], b=[200, 0], target_distance=8
        )
        self.assertAlmostEqual(r["before_distance"], 4, places=5)
        self.assertAlmostEqual(r["after_distance"], 8, places=5)
        self.assertAlmostEqual(r["anchor_world"][0], 3.5, places=5)
        self.assertAlmostEqual(r["anchor_world"][1], 2, places=5)
        self.assertAlmostEqual(r["factor"], 2, places=5)

    def test_calibration_failure_leaves_state(self) -> None:
        self.reference()
        before = call("reference.inspect")
        reject(
            "reference.calibrate",
            name="Front",
            a=[-1, 0],
            b=[200, 0],
            target_distance=2,
        )
        reject(
            "reference.calibrate",
            name="Front",
            a=[0, 0],
            b=[200, 0],
            target_distance=1e9,
        )
        self.assertEqual(before, call("reference.inspect"))

    def test_distances_angles_and_comparisons(self) -> None:
        q = [
            {
                "kind": "distance",
                "name": "Length",
                "a": world(0, 0),
                "b": world(3, 4),
                "comparison": {"target": 5, "tolerance": 0},
            },
            {
                "kind": "angle",
                "name": "Angle",
                "a": world(1, 0),
                "vertex": world(0, 0),
                "b": world(0, 1),
                "comparison": {"target": 100, "tolerance": 2},
            },
        ]
        values = call("measurement.inspect", queries=q)["measurements"]
        self.assertEqual(values[0]["value"], 5)
        self.assertTrue(values[0]["within_tolerance"])
        self.assertEqual(values[1]["value"], 90)
        self.assertEqual(values[1]["deviation"], -10)
        self.assertFalse(values[1]["within_tolerance"])
        q[1]["a"] = world(0, 0)
        self.assertEqual(
            reject("measurement.inspect", queries=q).code, "measurement_degenerate"
        )

    def test_exact_authored_bounds_and_coordinate_frames(self) -> None:
        call(
            "object.create_primitive",
            primitive="cube",
            name="Part",
            location=[10, 0, 0],
            scale=[2, 3, 4],
            rotation=[0, 0, math.pi / 2],
        )
        query = [{"kind": "bounds", "name": "Box", "object": "Part"}]
        world_bounds = call("measurement.inspect", queries=query)["measurements"][0]
        for actual, expected in zip(world_bounds["dimensions"], [6, 4, 8], strict=True):
            self.assertAlmostEqual(actual, expected, places=5)
        local = call("measurement.inspect", queries=query, frame="Part")[
            "measurements"
        ][0]
        for value in local["dimensions"]:
            self.assertAlmostEqual(value, 2, places=5)
        self.assertEqual(
            reject(
                "measurement.inspect",
                queries=[{"kind": "bounds", "name": "Box", "object": "missing"}],
            ).code,
            "object_not_found",
        )

    def test_local_point_distance_and_meters(self) -> None:
        call("object.create_primitive", primitive="cube", name="Part", scale=[2, 3, 4])
        q = [
            {
                "kind": "distance",
                "name": "Length",
                "a": {"kind": "object", "object": "Part", "point": [0, 0, 0]},
                "b": {"kind": "object", "object": "Part", "point": [1, 0, 0]},
            }
        ]
        self.assertEqual(
            call("measurement.inspect", queries=q)["measurements"][0]["value"], 2
        )
        self.assertEqual(
            call("measurement.inspect", queries=q, frame="Part")["measurements"][0][
                "value"
            ],
            1,
        )
        call("scene.configure_units", system="metric", meters_per_unit=0.01)
        self.assertAlmostEqual(
            call("measurement.inspect", queries=q, unit="meters")["measurements"][0][
                "value"
            ],
            0.02,
        )
        self.assertEqual(list(bpy.data.objects["Part"].scale), [2, 3, 4])

    def test_landmark_attachment_update_and_measurement(self) -> None:
        call(
            "object.create_primitive",
            primitive="cube",
            name="Part",
            location=[10, 0, 0],
            scale=[2, 1, 1],
        )
        result = call(
            "landmark.set",
            landmarks=[
                {"name": "Datum", "point": [0, 0, 0]},
                {
                    "name": "Tip",
                    "point": [1, 0, 0],
                    "object": "Part",
                    "label": "hinge",
                    "category": "assembly",
                },
            ],
        )
        self.assertEqual(result["landmarks"][1]["world_point"], [12, 0, 0])
        call("object.set_transform", name="Part", location=[20, 0, 0])
        tip = call("landmark.inspect", object="Part", category="assembly")["landmarks"][
            0
        ]
        self.assertEqual(tip["world_point"], [22, 0, 0])
        bpy.data.objects["Part"].name = "Renamed"
        self.assertEqual(
            call("landmark.inspect", names=["Tip"])["landmarks"][0]["object"], "Renamed"
        )
        q = [
            {
                "kind": "distance",
                "name": "Length",
                "a": {"kind": "landmark", "name": "Datum"},
                "b": {"kind": "landmark", "name": "Tip"},
            }
        ]
        self.assertEqual(
            call("measurement.inspect", queries=q)["measurements"][0]["value"], 22
        )
        call("landmark.set", landmarks=[{"name": "Tip", "point": [3, 0, 0]}])
        self.assertEqual(
            call("landmark.inspect", names=["Tip"])["landmarks"][0]["world_point"],
            [3, 0, 0],
        )
        call("landmark.remove", names=["Datum", "Tip"])
        self.assertIn("Renamed", bpy.data.objects)

    def test_missing_attachment_is_invalid_not_world_fallback(self) -> None:
        self.reference()
        call(
            "landmark.set",
            landmarks=[{"name": "Point", "point": [1, 0, 0], "object": "Front"}],
        )
        reject("reference.remove", names=["Front"])
        # Simulate external deletion; typed domain removal correctly refuses it.
        bpy.data.objects.remove(bpy.data.objects["Front"], do_unlink=True)
        point = call("landmark.inspect")["landmarks"][0]
        self.assertFalse(point["valid"])
        self.assertEqual(point["attachment"], "object")
        reject(
            "measurement.inspect",
            queries=[
                {
                    "kind": "distance",
                    "name": "Length",
                    "a": world(0, 0),
                    "b": {"kind": "landmark", "name": "Point"},
                }
            ],
        )

    def test_landmark_batch_preflight_and_rollback(self) -> None:
        call("landmark.set", landmarks=[{"name": "A", "point": [1, 2, 3]}])
        reject(
            "landmark.set",
            landmarks=[
                {"name": "A", "point": [4, 5, 6]},
                {"name": "B", "point": [0, 0, 0], "object": "missing"},
            ],
        )
        self.assertEqual(call("landmark.inspect")["landmarks"][0]["point"], [1, 2, 3])
        with patch.object(
            refs,
            "landmark_summary",
            side_effect=ops.OperationError("fixture_failure", "Injected failure"),
        ):
            reject(
                "landmark.set",
                landmarks=[
                    {"name": "A", "point": [4, 5, 6]},
                    {"name": "B", "point": [0, 0, 0]},
                ],
            )
        self.assertNotIn("B", bpy.data.objects)
        self.assertEqual(call("landmark.inspect")["landmarks"][0]["point"], [1, 2, 3])
        reject(
            "landmark.set", landmarks=[{"name": "C", "point": [0, 0, 0], "object": "A"}]
        )

    def test_landmark_pagination_and_work_budget(self) -> None:
        for batch in range(2):
            call(
                "landmark.set",
                landmarks=[
                    {"name": f"Point {batch * 32 + i:02}", "point": [i, 0, 0]}
                    for i in range(32)
                ],
            )
        page = call("landmark.inspect", attachment="world")
        self.assertEqual(page["page"]["matched_count"], 64)
        self.assertEqual(len(page["landmarks"]), 32)
        mesh = bpy.data.meshes.new("Large")
        mesh.vertices.add(128001)
        obj = bpy.data.objects.new("Large", mesh)
        bpy.context.scene.collection.objects.link(obj)
        self.assertEqual(
            reject(
                "measurement.inspect",
                queries=[{"kind": "bounds", "name": "TooLarge", "object": "Large"}],
            ).code,
            "measurement_limit_exceeded",
        )

    def test_preserve_linked_constraints_and_invalid_native_transforms(self) -> None:
        self.reference()
        obj = bpy.data.objects["Front"]
        obj.constraints.new("COPY_LOCATION")
        reject("reference.configure", references=[{"name": "Front", "size": 2}])
        reject("reference.remove", names=["Front"])
        obj.constraints.clear()
        target = bpy.data.objects.new("External dependent", None)
        bpy.context.scene.collection.objects.link(target)
        target.constraints.new("COPY_LOCATION").target = obj
        self.reference("Other")
        reject("reference.remove", names=["Other", "Front"])
        self.assertIn("Other", bpy.data.objects)
        self.assertIn("Front", bpy.data.objects)
        bpy.data.objects.remove(target, do_unlink=True)
        obj.scale = (0, 1, 1)
        reject(
            "reference.calibrate", name="Front", a=[0, 0], b=[200, 0], target_distance=2
        )
        self.assertEqual(obj.empty_display_size, 1)

    def test_landmark_display_survives_redefinition_and_failed_edit(self) -> None:
        call("landmark.set", landmarks=[{"name": "A", "point": [0, 0, 0]}])
        obj = bpy.data.objects["A"]
        obj.empty_display_type = "SPHERE"
        obj.empty_display_size = 0.7
        call("landmark.set", landmarks=[{"name": "A", "point": [1, 0, 0]}])
        self.assertEqual(obj.empty_display_type, "SPHERE")
        self.assertAlmostEqual(obj.empty_display_size, 0.7)
        obj.delta_location = (1, 0, 0)
        self.assertFalse(call("landmark.inspect")["landmarks"][0]["valid"])
        reject("landmark.set", landmarks=[{"name": "A", "point": [2, 0, 0]}])

    def test_save_reopen_persistence(self) -> None:
        self.reference(source_label="Synthetic sheet", category="front")
        call(
            "landmark.set",
            landmarks=[{"name": "Origin", "point": [0, 0, 0], "object": "Front"}],
        )
        call("scene.configure_units", system="metric", meters_per_unit=0.01)
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "references.blend"
        before = call("reference.inspect"), call("landmark.inspect")
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        self.assertEqual(before, (call("reference.inspect"), call("landmark.inspect")))
        self.assertAlmostEqual(bpy.context.scene.unit_settings.scale_length, 0.01)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
adapter.unregister()
assert result.wasSuccessful(), "Reference native checks failed"
print(f"REFERENCE_NATIVE_PASSED {result.testsRun}")
