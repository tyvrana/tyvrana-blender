"""Native whole-mesh UV behavior, ownership and context restoration checks."""

import importlib
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
uv = importlib.import_module("bl_ext.user_default.tyvrana_blender.uv")


def coordinates(mesh: Any, name: str | None = None) -> list[tuple[float, float]]:
    layer = mesh.uv_layers[name] if name else mesh.uv_layers.active
    return [tuple(point.vector) for point in layer.uv]


class UVTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend()
        bpy.ops.mesh.primitive_cube_add()
        self.obj = bpy.context.object
        self.obj.name = "Surface"

    def tearDown(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        runtime = adapter._runtime
        worker = runtime.worker if runtime else None
        adapter.unregister()
        self.assertFalse(bpy.app.timers.is_registered(adapter.pump))
        if worker:
            self.assertEqual(worker.process.returncode, 0)
            self.assertFalse(worker.spool.root.exists())

    def response(self, operation: str, **arguments: Any) -> Any:
        payload = {"object_name": self.obj.name, **arguments}
        if operation == "pack_islands":
            target = {
                key: payload.pop(key)
                for key in ("object_name", "uv_map")
                if key in payload
            }
            payload = {"objects": [target], **payload}
        return operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="uv-test",
                operation="blender.uv." + operation,
                arguments=payload,
            ),
        )

    def call(self, operation: str, **arguments: Any) -> Any:
        result = self.response(operation, **arguments)
        self.assertIsInstance(result, OperationSuccess, str(result))
        return (
            result.result["objects"][0]
            if operation == "pack_islands"
            else result.result
        )

    def error(self, operation: str, code: str, **arguments: Any) -> None:
        result = self.response(operation, **arguments)
        self.assertIsInstance(result, OperationFailure, str(result))
        self.assertEqual(result.error.code, code)

    def test_empty_maps_first_creation_and_first_map_roles(self) -> None:
        for layer in list(self.obj.data.uv_layers):
            self.obj.data.uv_layers.remove(layer)
        result = self.call("inspect")
        self.assertEqual(result["maps"], [])
        self.assertIsNone(result["active_map"])
        self.error("unwrap", "uv_map_not_found", method="smart_project")
        result = self.call("create_map", set_active=False, set_render=False)
        self.assertEqual(result["active_map"], "UVMap")
        self.assertEqual(result["active_render_map"], "UVMap")
        self.assertEqual(result["maps"][0]["loop_count"], 24)

    def test_map_creation_and_independent_edit_render_roles(self) -> None:
        original = coordinates(self.obj.data)
        result = self.call("create_map", name="A")
        self.assertEqual(result["active_map"], "A")
        self.assertEqual(result["active_render_map"], "UVMap")
        self.assertEqual(coordinates(self.obj.data), original)
        result = self.call("create_map", name="B", set_active=False, set_render=True)
        self.assertEqual(result["active_map"], "A")
        self.assertEqual(result["active_render_map"], "B")
        result = self.call("set_active", name="UVMap", set_active=False)
        self.assertEqual(result["active_map"], "A")
        self.assertEqual(result["active_render_map"], "UVMap")
        result = self.call("set_active", name="B", set_render=False)
        self.assertEqual(result["active_map"], "B")
        self.assertEqual(result["active_render_map"], "UVMap")
        result = self.call("set_active", name="A")
        self.assertEqual(result["active_map"], result["active_render_map"])
        self.assertEqual([item["name"] for item in result["maps"]], ["A", "B", "UVMap"])

    def test_duplicate_exact_names_and_map_capacity_roll_back(self) -> None:
        self.error("create_map", "invalid_arguments", name="UVMap")
        long_name = "long" * 100
        self.assertEqual(
            self.call("create_map", name=long_name)["active_map"], long_name
        )
        self.obj.data.uv_layers.remove(self.obj.data.uv_layers[long_name])
        self.assertEqual(len(self.obj.data.uv_layers), 1)
        for i in range(7):
            self.call("create_map", name=f"Map{i}")
        self.error("create_map", "invalid_context", name="Overflow")
        self.assertEqual(len(self.obj.data.uv_layers), 8)

    def test_missing_object_nonmesh_and_missing_map(self) -> None:
        self.error("inspect", "object_not_found", object_name="Missing")
        self.error("set_active", "uv_map_not_found", name="Missing")
        self.error("pack_islands", "uv_map_not_found", uv_map="Missing")
        bpy.ops.object.empty_add()
        self.error("inspect", "object_not_mesh", object_name=bpy.context.object.name)

    def test_shared_mesh_isolation_for_every_mutation(self) -> None:
        for operation, arguments in [
            ("create_map", {"name": "Other"}),
            ("set_active", {"name": "UVMap"}),
            ("unwrap", {"method": "smart_project"}),
            ("pack_islands", {}),
        ]:
            with self.subTest(operation=operation):
                original = self.obj.data
                sibling = bpy.data.objects.new("Sibling", original)
                bpy.context.collection.objects.link(sibling)
                before = uv.inspect(sibling).model_dump()
                points = coordinates(original)
                self.assertEqual(self.call("inspect")["mesh_users"], 2)
                result = self.call(operation, **arguments)
                self.assertEqual(result["mesh_users"], 1)
                self.assertIsNot(self.obj.data, original)
                self.assertEqual(coordinates(original), points)
                after = uv.inspect(sibling).model_dump()
                before["mesh_users"] = 1
                self.assertEqual(after, before)
                bpy.data.objects.remove(sibling, do_unlink=True)

    def test_all_seven_methods_distribute_collapsed_uvs(self) -> None:
        for edge in self.obj.data.edges:
            edge.use_seam = True
        for method in [
            "angle_based",
            "conformal",
            "minimum_stretch",
            "smart_project",
            "cube_project",
            "cylinder_project",
            "sphere_project",
        ]:
            with self.subTest(method=method):
                for point in self.obj.data.uv_layers.active.uv:
                    point.vector = (0.5, 0.5)
                result = self.call("unwrap", method=method, correct_aspect=False)
                summary = result["maps"][0]
                self.assertEqual(summary["loop_count"], 24)
                self.assertGreater(summary["uv_max"][0] - summary["uv_min"][0], 0.1)
                self.assertGreater(summary["uv_max"][1] - summary["uv_min"][1], 0.1)
                self.assertTrue(all(edge.use_seam for edge in self.obj.data.edges))

    def test_named_unwrap_preserves_edit_and_render_active_choices(self) -> None:
        self.call("create_map", name="Other", set_active=False)
        original = coordinates(self.obj.data, "UVMap")
        self.call(
            "unwrap",
            uv_map="Other",
            method="smart_project",
            angle_limit=1.0,
            island_margin=0.01,
            area_weight=0.5,
        )
        result = self.call("inspect")
        self.assertEqual(result["active_map"], "UVMap")
        self.assertEqual(result["active_render_map"], "UVMap")
        self.assertEqual(coordinates(self.obj.data, "UVMap"), original)

    def test_pack_unit_square_and_explicit_settings(self) -> None:
        self.call("unwrap", method="cube_project", cube_size=0.5)
        result = self.call("pack_islands", padding_pixels=82, rotate=False)
        summary = result["maps"][0]
        self.assertEqual(summary["out_of_unit_square_count"], 0)
        self.assertGreaterEqual(min(summary["uv_min"]), 0.019)
        self.assertLessEqual(max(summary["uv_max"]), 0.981)
        self.call("pack_islands", padding_pixels=1, rotate=True)

    def test_empty_faces_reject_operators(self) -> None:
        mesh = bpy.data.meshes.new("Empty")
        self.obj.data = mesh
        self.call("create_map")
        self.error("unwrap", "invalid_context", method="smart_project")
        self.error("pack_islands", "invalid_context")

    def test_object_mode_active_object_and_selection_restored(self) -> None:
        bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
        other = bpy.context.object
        self.obj.select_set(True)
        selection = [
            (obj.name, obj.select_get()) for obj in bpy.context.view_layer.objects
        ]
        for operation, arguments in [
            ("create_map", {}),
            ("unwrap", {"method": "smart_project"}),
            ("pack_islands", {}),
        ]:
            self.call(operation, **arguments)
            self.assertEqual(bpy.context.mode, "OBJECT")
            self.assertEqual(bpy.context.view_layer.objects.active, other)
            self.assertEqual(
                [
                    (obj.name, obj.select_get())
                    for obj in bpy.context.view_layer.objects
                ],
                selection,
            )

    def test_edit_mode_inspection_and_selection_history_restored(self) -> None:
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        for collection in (bm.verts, bm.edges, bm.faces):
            for element in collection:
                element.select = False
        bm.faces[0].hide = True
        bm.verts[4].select = True
        bm.select_history.clear()
        bm.select_history.add(bm.verts[4])
        for face in bm.faces:
            face.uv_select = False
            for loop in face.loops:
                loop.uv_select_vert = False
                loop.uv_select_edge = False
        bm.select_flush_mode()
        bm.uv_select_sync_valid = False
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        before = uv._Selection(bm)
        result = self.call("inspect")
        self.assertEqual(result["maps"][0]["loop_count"], 24)
        self.error("pack_islands", "invalid_context")
        for operation, arguments in [
            ("unwrap", {"method": "smart_project"}),
        ]:
            result = self.call(operation, **arguments)
            self.assertEqual(result["maps"][0]["loop_count"], 24)
            self.assertEqual(bpy.context.mode, "EDIT_MESH")
            after = uv._Selection(bmesh.from_edit_mesh(self.obj.data))
            self.assertEqual(after.__dict__, before.__dict__)

    def test_other_edit_object_and_multi_object_edit_rejected(self) -> None:
        bpy.ops.mesh.primitive_cube_add()
        other = bpy.context.object
        bpy.ops.object.mode_set(mode="EDIT")
        self.error("unwrap", "invalid_context", method="smart_project")
        self.assertEqual(bpy.context.object, other)
        bpy.ops.object.mode_set(mode="OBJECT")
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        bpy.ops.object.mode_set(mode="EDIT")
        self.assertEqual(len(bpy.context.objects_in_mode), 2)
        self.error("create_map", "invalid_context")

    def test_pins_reported_and_flags_preserved(self) -> None:
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.faces.ensure_lookup_table()
        layer = bm.loops.layers.uv.active
        bm.faces[0].loops[0][layer].pin_uv = True
        bm.faces[0].loops[1][layer].pin_uv = True
        self.assertEqual(self.call("inspect")["maps"][0]["pinned_count"], 2)
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual(self.call("pack_islands")["maps"][0]["pinned_count"], 2)

    def test_operator_failure_rolls_back_uvs_and_shared_copy(self) -> None:
        for shared in (False, True):
            with self.subTest(shared=shared):
                original = self.obj.data
                points = coordinates(original)
                sibling = None
                if shared:
                    sibling = bpy.data.objects.new("Sibling", original)
                    bpy.context.collection.objects.link(sibling)

                def fail(obj: Any, call: Any) -> None:
                    obj.data.uv_layers.active.uv[0].vector = (12, 14)
                    raise operations.OperationError(
                        "uv_unwrap_failed", "Operator failed"
                    )

                with patch.object(uv, "_operator", side_effect=fail):
                    self.error("unwrap", "uv_unwrap_failed", method="smart_project")
                self.assertEqual(self.obj.data, original)
                self.assertEqual(coordinates(original), points)
                self.assertEqual(bpy.context.mode, "OBJECT")
                if sibling:
                    bpy.data.objects.remove(sibling, do_unlink=True)

    def test_cancelled_native_operator_restores_selection(self) -> None:
        with self.assertRaises(operations.OperationError):
            uv._operator(self.obj, lambda: {"CANCELLED"})
        self.assertEqual(bpy.context.mode, "OBJECT")

    def test_linked_and_hidden_mesh_rejected(self) -> None:
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "mesh-library.blend"
        bpy.data.libraries.write(str(path), {self.obj.data})
        with bpy.data.libraries.load(str(path), link=True) as (source, target):
            target.meshes = [source.meshes[0]]
        self.obj.data = target.meshes[0]
        self.call("inspect")
        self.error("unwrap", "invalid_context", method="smart_project")
        path.unlink()

    def test_fixed_projection_ignores_pivot_and_cursor(self) -> None:
        for method in ("cube_project", "cylinder_project", "sphere_project"):
            self.call("unwrap", method=method, correct_aspect=False)
            before = coordinates(self.obj.data)
            bpy.context.scene.tool_settings.transform_pivot_point = "CURSOR"
            bpy.context.scene.cursor.location = (20, 30, 40)
            self.call("unwrap", method=method, correct_aspect=False)
            self.assertEqual(coordinates(self.obj.data), before)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(UVTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print(f"BLENDER_UV_TESTS_PASSED {result.testsRun}")
