"""Native modifier behavior, data ownership, application and resource safety."""

import importlib
import math
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
api = importlib.import_module("bl_ext.user_default.tyvrana_blender.modifiers")
models = importlib.import_module("bl_ext.user_default.tyvrana_blender.modifier_models")


def authored(data: Any) -> Any:
    return (
        [tuple(v.co) for v in data.vertices],
        [(tuple(e.vertices), e.use_seam, e.use_edge_sharp) for e in data.edges],
        [(tuple(f.vertices), f.material_index, f.use_smooth) for f in data.polygons],
        [
            (u.name, u.active_render, [tuple(v.vector) for v in u.uv])
            for u in data.uv_layers
        ],
        [m.name if m else None for m in data.materials],
        [[(g.group, g.weight) for g in v.groups] for v in data.vertices],
    )


class ModifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # These cases exercise handlers directly. Share one worker; lifecycle
        # restart/rollback behavior has dedicated integration coverage.
        adapter.register()
        adapter.pump()

    def setUp(self) -> None:
        self.backend = adapter.BlenderBackend()
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.meshes):
            if data.users == 0:
                bpy.data.meshes.remove(data)
        bpy.ops.mesh.primitive_cube_add()
        self.obj = bpy.context.object
        self.obj.name = "Surface"

    def tearDown(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

    @classmethod
    def tearDownClass(cls) -> None:
        runtime = adapter._runtime
        worker = runtime.worker if runtime else None
        adapter.unregister()
        assert not bpy.app.timers.is_registered(adapter.pump)
        if worker:
            assert worker.process.returncode == 0
            assert not worker.spool.root.exists()

    def response(self, operation: str, **arguments: Any) -> Any:
        return operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="modifier-test",
                operation="blender." + operation,
                arguments={"object_name": self.obj.name, **arguments},
            ),
        )

    def call(self, operation: str, **arguments: Any) -> Any:
        result = self.response(operation, **arguments)
        self.assertIsInstance(result, OperationSuccess, str(result))
        return result.result

    def error(self, operation: str, code: str, **arguments: Any) -> None:
        result = self.response(operation, **arguments)
        self.assertIsInstance(result, OperationFailure, str(result))
        self.assertEqual(result.error.code, code)

    def add(self, kind: str, **settings: Any) -> Any:
        return self.call("modifier.create", type=kind, name="Shape", settings=settings)

    def configure(self, kind: str, **settings: Any) -> Any:
        return self.call(
            "modifier.configure", type=kind, modifier_name="Shape", settings=settings
        )

    def evaluated(self) -> Any:
        return self.call("mesh.inspect_evaluated")

    def plane(self, *, z: float = 0) -> None:
        bpy.ops.mesh.primitive_plane_add(size=2)
        data = bpy.context.object.data
        for v in data.vertices:
            v.co.z = z
        self.obj = bpy.context.object
        self.obj.name = "Sheet"

    def target(self, *, x: float = 1) -> Any:
        bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
        obj = bpy.context.object
        obj.name = "Target"
        return obj

    def half(self) -> None:
        data = self.obj.data
        for v in data.vertices:
            if v.co.x < 0:
                v.co.x = 0
        # The central face is omitted to make a watertight mirrored result.
        import bmesh  # type: ignore[import-not-found]

        bm = bmesh.new()
        try:
            bm.from_mesh(data)
            center = [f for f in bm.faces if all(v.co.x == 0 for v in f.verts)]
            bmesh.ops.delete(bm, geom=center, context="FACES_ONLY")
            bm.to_mesh(data)
        finally:
            bm.free()

    def test_triangulate_after_subdivision_preserves_authored_basis(self) -> None:
        before = self.call("mesh.inspect_evaluated", uv_map="UVMap")["authored_basis"]
        self.call(
            "modifier.create",
            type="subdivision_surface",
            name="Smooth",
            settings={"levels": 2, "render_levels": 2},
        )
        quads = self.evaluated()
        # A quad-aware bound admits this evaluated surface without multiplying
        # unrelated worst cases for arbitrary ngons into its stack estimate.
        with patch.object(api.mesh, "MAX_WORK_ELEMENTS", 4000):
            tri = self.add(
                "triangulate",
                quad_method="fixed",
                ngon_method="clip",
                min_vertices=4,
                keep_custom_normals=True,
            )
        self.assertEqual(tri["settings"]["quad_method"], "fixed")
        result = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        self.assertEqual(result["authored_basis"], before)
        self.assertEqual(result["face_count"], quads["face_count"] * 2)
        self.assertEqual(result["vertex_count"], quads["vertex_count"])
        self.assertEqual(result["evaluated_basis"]["zero_tangent_count"], 0)
        self.assertTrue(result["evaluated_basis"]["has_custom_normals"])
        self.configure("triangulate", min_vertices=5)
        self.assertEqual(self.evaluated()["face_count"], quads["face_count"])
        self.call("modifier.remove", modifier_name="Shape")
        self.assertEqual(self.evaluated()["evaluated_basis"], quads["evaluated_basis"])

    def test_empty_and_unsupported_stack_inspection(self) -> None:
        self.assertEqual(self.call("modifier.inspect")["modifiers"], [])
        first = self.obj.modifiers.new("Existing", "BEVEL")
        first.width = 0.3
        other = self.obj.modifiers.new("Graph", "NODES")
        result = self.call("modifier.inspect")["modifiers"]
        self.assertEqual([m["name"] for m in result], [first.name, other.name])
        self.assertTrue(
            all(not m["supported"] and m["settings"] is None for m in result)
        )
        self.assertEqual(
            first.width, bpy.data.objects[self.obj.name].modifiers[first.name].width
        )

    def test_mirror_completes_half_without_authored_mutation(self) -> None:
        self.half()
        before = authored(self.obj.data)
        result = self.add("mirror")
        self.assertEqual(result["settings"]["axes"], ["x"])
        evaluated = self.evaluated()
        self.assertEqual(evaluated["bounds_min"], [-1, -1, -1])
        self.assertEqual(evaluated["bounds_max"], [1, 1, 1])
        self.assertEqual(evaluated["vertex_count"], 12)
        self.assertEqual(evaluated["face_count"], 10)
        self.assertEqual(evaluated["manifold_summary"]["non_manifold_edge_count"], 0)
        self.assertEqual(before, authored(self.obj.data))

    def test_mirror_bisect_and_unexposed_uv_settings_preserved(self) -> None:
        self.add("mirror")
        mod = self.obj.modifiers["Shape"]
        mod.use_mirror_u = True
        mod.offset_u = 0.4
        mod.use_mirror_vertex_groups = False
        self.configure(
            "mirror",
            axes=["x", "y"],
            bisect_axes=["x"],
            bisect_flip_axes=["x"],
            clipping=True,
            merge=False,
            merge_threshold=0.03,
        )
        self.assertEqual(tuple(mod.use_axis), (True, True, False))
        self.assertTrue(
            mod.use_bisect_axis[0] and mod.use_bisect_flip_axis[0] and mod.use_clip
        )
        self.assertFalse(mod.use_mirror_merge)
        self.assertTrue(mod.use_mirror_u)
        self.assertAlmostEqual(mod.offset_u, 0.4)
        self.assertFalse(mod.use_mirror_vertex_groups)
        self.assertGreater(self.evaluated()["face_count"], 6)

    def test_mirror_reference_and_clear_preserve_object_origin(self) -> None:
        pivot = bpy.data.objects.new("Pivot", None)
        bpy.context.scene.collection.objects.link(pivot)
        pivot.location.x = 3
        pose = self.obj.matrix_world.copy()
        self.add("mirror", mirror_object=pivot.name, merge=False)
        self.assertAlmostEqual(self.evaluated()["bounds_max"][0], 7)
        self.configure("mirror", clear_mirror_object=True)
        self.assertIsNone(self.obj.modifiers["Shape"].mirror_object)
        self.assertEqual(self.obj.matrix_world, pose)
        self.assertEqual(pivot.location.x, 3)

    def test_mirror_remove_restores_half(self) -> None:
        self.half()
        initial = self.evaluated()
        self.add("mirror")
        self.assertGreater(self.evaluated()["vertex_count"], initial["vertex_count"])
        self.call("modifier.remove", modifier_name="Shape")
        self.assertEqual(self.evaluated(), initial)

    def test_subdivision_levels_and_authored_evaluated_distinction(self) -> None:
        before = authored(self.obj.data)
        self.add("subdivision_surface", levels=1, render_levels=3)
        self.assertEqual(self.call("mesh.inspect")["vertex_count"], 8)
        self.assertEqual(self.evaluated()["vertex_count"], 26)
        self.configure("subdivision_surface", levels=2)
        self.assertEqual(self.evaluated()["vertex_count"], 98)
        self.assertEqual(self.obj.modifiers["Shape"].render_levels, 3)
        self.assertEqual(before, authored(self.obj.data))

    def test_subdivision_simple_and_catmull_clark_geometry(self) -> None:
        self.add("subdivision_surface", levels=2, mode="simple")
        for value in self.evaluated()["bounds_max"]:
            self.assertAlmostEqual(value, 1, places=6)
        self.configure(
            "subdivision_surface",
            mode="catmull_clark",
            uv_smooth="preserve_corners",
            boundary_smooth="preserve_corners",
            use_creases=False,
            show_only_control_edges=False,
        )
        self.assertTrue(all(x < 0.95 for x in self.evaluated()["bounds_max"]))
        self.assertEqual(self.obj.modifiers["Shape"].uv_smooth, "PRESERVE_CORNERS")
        self.assertEqual(
            self.obj.modifiers["Shape"].boundary_smooth, "PRESERVE_CORNERS"
        )

    def test_subdivision_forecast_rejects_growth_without_mutation(self) -> None:
        self.add("subdivision_surface", levels=6, render_levels=6)
        before = self.call("modifier.inspect")
        self.error(
            "modifier.create",
            "invalid_context",
            type="subdivision_surface",
            settings={"levels": 6, "render_levels": 6},
        )
        self.assertEqual(self.call("modifier.inspect"), before)

    def test_native_excessive_subdivision_is_inspectable_but_not_evaluated(
        self,
    ) -> None:
        mod = self.obj.modifiers.new("Excessive", "SUBSURF")
        mod.levels = 11
        self.assertEqual(
            self.call("modifier.inspect")["modifiers"][0]["settings"]["levels"], 11
        )
        self.error("mesh.inspect_evaluated", "invalid_context")

    def test_shrinkwrap_nearest_surface_and_offset(self) -> None:
        target = self.obj
        self.plane(z=2)
        before = authored(self.obj.data)
        target_before = authored(target.data)
        self.add("shrinkwrap", target=target.name)
        self.assertAlmostEqual(self.evaluated()["bounds_max"][2], 1)
        self.configure("shrinkwrap", offset=0.25, mode="above_surface")
        self.assertAlmostEqual(
            self.evaluated()["bounds_max"][2], 1 + 0.25 / math.sqrt(3), places=6
        )
        self.assertEqual(before, authored(self.obj.data))
        self.assertEqual(target_before, authored(target.data))

    def test_shrinkwrap_project_directions_and_culling(self) -> None:
        target = self.obj
        self.plane(z=2)
        self.add(
            "shrinkwrap",
            target=target.name,
            method="project",
            projection={
                "axes": ["z"],
                "positive": False,
                "negative": True,
                "cull_face": "off",
                "limit": 4,
            },
        )
        self.assertAlmostEqual(self.evaluated()["bounds_max"][2], 1)
        self.configure(
            "shrinkwrap", projection={"cull_face": "front", "invert_cull": True}
        )
        self.assertEqual(self.obj.modifiers["Shape"].cull_face, "FRONT")
        self.assertTrue(self.obj.modifiers["Shape"].use_invert_cull)
        self.assertTrue(all(math.isfinite(x) for x in self.evaluated()["bounds_max"]))

    def test_shrinkwrap_target_normal_project(self) -> None:
        target = self.obj
        self.plane(z=2)
        self.add(
            "shrinkwrap", target=target.name, method="target_normal_project", offset=0.1
        )
        result = self.evaluated()
        self.assertLess(result["bounds_max"][2], 2)
        self.assertEqual(result["vertex_count"], 4)

    def test_shrinkwrap_project_cross_field_rejection_is_atomic(self) -> None:
        target = self.target()
        self.add("shrinkwrap", target=target.name, method="project")
        before = self.call("modifier.inspect")
        self.error(
            "modifier.configure",
            "invalid_arguments",
            modifier_name="Shape",
            type="shrinkwrap",
            settings={
                "offset": 12,
                "projection": {"positive": False, "negative": False},
            },
        )
        self.assertEqual(self.call("modifier.inspect"), before)
        self.error(
            "modifier.configure",
            "invalid_arguments",
            modifier_name="Shape",
            type="shrinkwrap",
            settings={"method": "nearest_surface", "projection": {"axes": ["x"]}},
        )

    def test_shrinkwrap_unexposed_groups_and_auxiliary_target_preserved(self) -> None:
        target = self.target()
        self.add("shrinkwrap", target=target.name)
        group = self.obj.vertex_groups.new(name="Influence")
        mod = self.obj.modifiers["Shape"]
        mod.vertex_group = group.name
        mod.auxiliary_target = target
        self.configure("shrinkwrap", offset=-0.1)
        self.assertEqual(mod.vertex_group, group.name)
        self.assertEqual(mod.auxiliary_target, target)

    def test_boolean_all_operations_and_current_solvers(self) -> None:
        target = self.target()
        # FLOAT does not support coplanar overlapping faces reliably.
        target.scale.y = target.scale.z = 1.5
        before = authored(target.data)
        self.add("boolean", operand_object=target.name)
        for solver in ("float", "exact", "manifold"):
            for operation, minimum, maximum in (
                ("difference", -1, 0),
                ("union", -1, 2),
                ("intersect", 0, 1),
            ):
                with self.subTest(solver=solver, operation=operation):
                    self.configure("boolean", solver=solver, operation=operation)
                    result = self.evaluated()
                    self.assertAlmostEqual(result["bounds_min"][0], minimum)
                    self.assertAlmostEqual(result["bounds_max"][0], maximum)
                    self.assertEqual(
                        result["manifold_summary"]["non_manifold_edge_count"], 0
                    )
        self.assertEqual(before, authored(target.data))
        self.assertFalse(target.hide_render or target.hide_viewport)

    def test_boolean_manifold_rejects_open_input(self) -> None:
        target = self.target()
        self.plane()
        self.error(
            "modifier.create",
            "invalid_context",
            type="boolean",
            settings={"operand_object": target.name, "solver": "manifold"},
        )
        self.assertEqual(len(self.obj.modifiers), 0)

    def test_collection_boolean_inspectable_configuration_rejected(self) -> None:
        mod = self.obj.modifiers.new("Shape", "BOOLEAN")
        mod.operand_type = "COLLECTION"
        state = self.call("modifier.inspect")
        self.assertEqual(
            state["modifiers"][0]["settings"]["operand_type"], "collection"
        )
        self.error(
            "modifier.configure",
            "modifier_type_unsupported",
            type="boolean",
            modifier_name="Shape",
            settings={"operation": "union"},
        )
        self.assertEqual(state, self.call("modifier.inspect"))

    def test_solidify_signed_thickness_offset_and_rim(self) -> None:
        self.plane()
        before = authored(self.obj.data)
        self.add("solidify", thickness=0.4, offset=0)
        result = self.evaluated()
        self.assertEqual((result["vertex_count"], result["face_count"]), (8, 6))
        self.assertAlmostEqual(result["bounds_min"][2], -0.2)
        self.assertAlmostEqual(result["bounds_max"][2], 0.2)
        self.configure(
            "solidify",
            thickness=-0.8,
            offset=-1,
            even_thickness=True,
            quality_normals=True,
        )
        self.assertAlmostEqual(self.evaluated()["bounds_max"][2], 0.8)
        self.configure("solidify", rim_only=True)
        self.assertEqual(self.evaluated()["face_count"], 5)
        self.assertEqual(before, authored(self.obj.data))

    def test_solidify_preserves_unexposed_material_offsets_and_clamp(self) -> None:
        self.add("solidify")
        mod = self.obj.modifiers["Shape"]
        mod.material_offset = 1
        mod.material_offset_rim = 2
        mod.thickness_clamp = 0.5
        self.configure("solidify", thickness=0.3)
        self.assertEqual(
            (mod.material_offset, mod.material_offset_rim, mod.thickness_clamp),
            (1, 2, 0.5),
        )

    def test_complex_solidify_is_inspected_and_preserved(self) -> None:
        mod = self.obj.modifiers.new("Shape", "SOLIDIFY")
        mod.solidify_mode = "NON_MANIFOLD"
        self.assertEqual(
            self.call("modifier.inspect")["modifiers"][0]["settings"]["mode"], "complex"
        )
        self.error(
            "modifier.configure",
            "modifier_type_unsupported",
            type="solidify",
            modifier_name="Shape",
            settings={"thickness": 0.2},
        )
        self.assertEqual(mod.solidify_mode, "NON_MANIFOLD")

    def test_common_visibility_preserves_authored_state(self) -> None:
        self.add("subdivision_surface")
        before = authored(self.obj.data)
        self.call(
            "modifier.configure",
            type="subdivision_surface",
            modifier_name="Shape",
            enabled_viewport=False,
            enabled_render=True,
            show_in_editmode=False,
        )
        self.assertEqual(self.evaluated()["vertex_count"], 8)
        state = self.call("modifier.inspect")["modifiers"][0]
        self.assertFalse(state["enabled_viewport"] or state["show_in_editmode"])
        self.assertTrue(state["enabled_render"])
        self.assertFalse(state["show_on_cage"])
        self.assertEqual(before, authored(self.obj.data))

    def test_native_generated_names_and_explicit_duplicates(self) -> None:
        first = self.call("modifier.create", type="mirror")
        second = self.call("modifier.create", type="mirror")
        self.assertNotEqual(first["name"], second["name"])
        self.error(
            "modifier.create", "invalid_arguments", type="mirror", name=first["name"]
        )
        self.assertEqual(len(self.obj.modifiers), 2)
        self.error("modifier.create", "invalid_arguments", type="mirror", name="é" * 32)

    def test_missing_modifier_and_type_mismatch(self) -> None:
        for operation in ("configure", "move", "remove", "apply"):
            extra = (
                {"type": "mirror"}
                if operation == "configure"
                else {"index": 0}
                if operation == "move"
                else {}
            )
            self.error(
                "modifier." + operation,
                "modifier_not_found",
                modifier_name="Missing",
                **extra,
            )
        self.add("mirror")
        self.error(
            "modifier.configure",
            "invalid_arguments",
            modifier_name="Shape",
            type="solidify",
        )
        self.assertEqual(self.obj.modifiers["Shape"].type, "MIRROR")

    def test_unsupported_configure_apply_and_explicit_move_remove(self) -> None:
        first = self.obj.modifiers.new("Existing", "BEVEL")
        first.width = 0.3
        second = self.obj.modifiers.new("Other", "SMOOTH")
        self.error(
            "modifier.configure",
            "modifier_type_unsupported",
            modifier_name=first.name,
            type="mirror",
        )
        self.error(
            "modifier.apply", "modifier_type_unsupported", modifier_name=first.name
        )
        result = self.call("modifier.move", modifier_name=second.name, index=0)
        self.assertEqual(
            [m["name"] for m in result["modifiers"]], [second.name, first.name]
        )
        result = self.call("modifier.remove", modifier_name=second.name)
        self.assertEqual(result["removed"], "Other")
        self.assertEqual(
            first.width, bpy.data.objects[self.obj.name].modifiers[0].width
        )

    def test_order_changes_geometry_and_exact_indices(self) -> None:
        self.plane()
        self.add("solidify", thickness=1, offset=0)
        self.call(
            "modifier.create",
            type="subdivision_surface",
            name="Smooth",
            settings={"levels": 2},
        )
        before = self.evaluated()
        self.call("modifier.move", modifier_name="Smooth", index=0)
        after = self.evaluated()
        self.assertNotEqual(before["bounds_max"], after["bounds_max"])
        self.assertEqual(
            [m["name"] for m in after["modifier_stack"]], ["Smooth", "Shape"]
        )
        self.assertEqual(
            self.call("modifier.move", modifier_name="Smooth", index=0)["modifiers"][0][
                "index"
            ],
            0,
        )
        self.error(
            "modifier.move", "invalid_arguments", modifier_name="Smooth", index=2
        )

    def test_pinned_last_never_silently_reordered(self) -> None:
        self.add("mirror")
        mod = self.obj.modifiers.new("Pinned", "SOLIDIFY")
        mod.use_pin_to_last = True
        before = self.call("modifier.inspect")
        self.error("modifier.move", "invalid_context", modifier_name="Shape", index=1)
        self.error("modifier.create", "invalid_context", type="mirror")
        self.assertEqual(before, self.call("modifier.inspect"))

    def test_shared_mesh_only_isolated_at_application(self) -> None:
        sibling = bpy.data.objects.new("Sibling", self.obj.data)
        bpy.context.scene.collection.objects.link(sibling)
        original = self.obj.data
        before = authored(original)
        self.add("subdivision_surface")
        self.configure("subdivision_surface", levels=2)
        self.assertEqual(self.obj.data, sibling.data)
        self.assertEqual(len(sibling.modifiers), 0)
        evaluated = self.evaluated()
        result = self.call("modifier.apply", modifier_name="Shape")
        self.assertEqual(result["mesh"]["vertex_count"], evaluated["vertex_count"])
        self.assertEqual(self.call("mesh.inspect")["vertex_count"], 98)
        self.assertNotEqual(self.obj.data, sibling.data)
        self.assertEqual(sibling.data, original)
        self.assertEqual(before, authored(sibling.data))
        self.assertEqual(result["modifiers"], [])

    def test_apply_mirror_matches_evaluated_and_preserves_other_stack(self) -> None:
        self.half()
        self.add("mirror")
        evaluated = self.evaluated()
        other = self.obj.modifiers.new("Later", "SUBSURF")
        other.uv_smooth = "NONE"
        pointer = other.as_pointer()
        result = self.call("modifier.apply", modifier_name="Shape")
        self.assertEqual(result["mesh"]["vertex_count"], evaluated["vertex_count"])
        self.assertEqual(other.as_pointer(), pointer)
        self.assertEqual(other.uv_smooth, "NONE")
        self.assertEqual([m["name"] for m in result["modifiers"]], ["Later"])

    def test_apply_nonfirst_bakes_only_named_native_effect(self) -> None:
        self.add("subdivision_surface")
        other = self.obj.modifiers.new("Shell", "SOLIDIFY")
        other.thickness = 0.2
        self.call("modifier.apply", modifier_name="Shell")
        self.assertEqual(len(self.obj.data.vertices), 16)
        self.assertEqual([m.name for m in self.obj.modifiers], ["Shape"])
        self.assertGreater(self.evaluated()["vertex_count"], 16)

    def test_apply_restores_active_selection_mode_and_counts(self) -> None:
        self.add("subdivision_surface")
        target = self.target(x=4)
        self.obj.select_set(False)
        before = (
            bpy.context.view_layer.objects.active,
            tuple(bpy.context.selected_objects),
            bpy.context.mode,
            len(bpy.data.objects),
            len(bpy.data.meshes),
        )
        self.call("modifier.apply", modifier_name="Shape")
        after = (
            bpy.context.view_layer.objects.active,
            tuple(bpy.context.selected_objects),
            bpy.context.mode,
            len(bpy.data.objects),
            len(bpy.data.meshes),
        )
        self.assertEqual(before, after)
        self.assertEqual(bpy.context.object, target)

    def test_apply_all_other_supported_types(self) -> None:
        target = self.target()
        material = bpy.data.materials.new("Object Finish")
        self.call("material.assign", material_name=material.name)
        for kind, settings in (
            ("shrinkwrap", {"target": target.name}),
            ("boolean", {"operand_object": target.name}),
            ("solidify", {"thickness": 0.2}),
        ):
            with self.subTest(kind=kind):
                self.add(kind, **settings)
                evaluated = self.evaluated()
                self.call("modifier.apply", modifier_name="Shape")
                result = self.call("mesh.inspect")
                self.assertEqual(api.material_slots(self.obj), [("OBJECT", material)])
                for key in (
                    "vertex_count",
                    "edge_count",
                    "face_count",
                    "bounds_min",
                    "bounds_max",
                ):
                    self.assertEqual(result[key], evaluated[key])

    def test_shape_keys_allow_stack_but_protect_apply(self) -> None:
        self.obj.shape_key_add(name="Basis")
        key = self.obj.shape_key_add(name="Expression")
        key.data[0].co.z += 0.5
        before = [tuple(v.co) for v in key.data]
        original = self.obj.data
        self.add("subdivision_surface")
        self.configure("subdivision_surface", levels=2)
        self.assertEqual(self.evaluated()["vertex_count"], 98)
        self.error("modifier.apply", "mesh_has_shape_keys", modifier_name="Shape")
        self.assertEqual(self.obj.data, original)
        self.assertEqual(before, [tuple(v.co) for v in key.data])
        self.call("modifier.remove", modifier_name="Shape")

    def test_apply_preserves_uv_material_attributes_and_vertex_weights(self) -> None:
        material = bpy.data.materials.new("Finish")
        self.obj.data.materials.append(material)
        self.obj.data.uv_layers.new(name="Detail")
        self.obj.data.uv_layers.active_index = 1
        self.obj.data.uv_layers[0].active_render = True
        attribute = self.obj.data.attributes.new("density", "FLOAT", "POINT")
        for point in attribute.data:
            point.value = 0.5
        group = self.obj.vertex_groups.new(name="Influence")
        group.add(list(range(8)), 0.75, "REPLACE")
        for edge in self.obj.data.edges:
            edge.use_seam = True
        self.add("subdivision_surface", mode="simple")
        self.call("modifier.apply", modifier_name="Shape")
        data = self.obj.data
        self.assertEqual(list(data.materials), [material])
        self.assertTrue(all(face.material_index == 0 for face in data.polygons))
        self.assertEqual([u.name for u in data.uv_layers], ["UVMap", "Detail"])
        self.assertEqual(data.uv_layers.active.name, "Detail")
        self.assertTrue(data.uv_layers["UVMap"].active_render)
        self.assertTrue(
            all(
                math.isfinite(x) for u in data.uv_layers for v in u.uv for x in v.vector
            )
        )
        self.assertTrue(
            all(abs(p.value - 0.5) < 1e-6 for p in data.attributes["density"].data)
        )
        self.assertEqual([g.name for g in self.obj.vertex_groups], ["Influence"])
        self.assertTrue(
            all(
                len(v.groups) == 1 and abs(v.groups[0].weight - 0.75) < 1e-6
                for v in data.vertices
            )
        )
        self.assertEqual(sum(e.use_seam for e in data.edges), 24)

    def test_apply_failure_does_not_change_source_or_leak_stage(self) -> None:
        self.add("subdivision_surface")
        original = self.obj.data
        before = authored(original)
        counts = (len(bpy.data.objects), len(bpy.data.meshes))
        with patch.object(
            api.mesh, "inspect", side_effect=RuntimeError("stage validation failed")
        ):
            self.error("modifier.apply", "modifier_apply_failed", modifier_name="Shape")
        self.assertEqual(self.obj.data, original)
        self.assertEqual(before, authored(original))
        self.assertEqual([m.name for m in self.obj.modifiers], ["Shape"])
        self.assertEqual(counts, (len(bpy.data.objects), len(bpy.data.meshes)))

    def test_apply_preserves_object_overrides_and_shared_sibling(self) -> None:
        underlying = bpy.data.materials.new("Underlying")
        finish = bpy.data.materials.new("Local Finish")
        other = bpy.data.materials.new("Sibling Finish")
        self.obj.data.materials.append(underlying)
        self.obj.data.materials.append(None)
        sibling = bpy.data.objects.new("Sibling", self.obj.data)
        bpy.context.scene.collection.objects.link(sibling)
        self.call("material.assign", material_name=finish.name)
        self.call("material.assign", object_name=sibling.name, material_name=other.name)
        self.obj.active_material_index = 1
        original = self.obj.data
        before = authored(original)
        slots = api.material_slots(self.obj)
        sibling_slots = api.material_slots(sibling)
        self.add("subdivision_surface", mode="simple")
        counts = (len(bpy.data.objects), len(bpy.data.meshes))
        with patch.object(
            api.mesh, "inspect", side_effect=RuntimeError("invalid stage")
        ):
            self.error("modifier.apply", "modifier_apply_failed", modifier_name="Shape")
        self.assertEqual(self.obj.data, original)
        self.assertEqual(api.material_slots(self.obj), slots)
        self.assertEqual(authored(original), before)
        self.assertEqual(counts, (len(bpy.data.objects), len(bpy.data.meshes)))
        self.call("modifier.apply", modifier_name="Shape")
        self.assertNotEqual(self.obj.data, original)
        self.assertEqual(api.material_slots(self.obj), slots)
        self.assertEqual(self.obj.active_material_index, 1)
        self.assertEqual(list(self.obj.data.materials), [underlying, None])
        self.assertEqual(sibling.data, original)
        self.assertEqual(authored(original), before)
        self.assertEqual(api.material_slots(sibling), sibling_slots)

    def test_disabled_application_is_explicit_failure(self) -> None:
        self.add("subdivision_surface")
        self.obj.modifiers["Shape"].show_viewport = False
        self.error("modifier.apply", "invalid_context", modifier_name="Shape")
        self.assertEqual(len(self.obj.data.vertices), 8)
        self.assertEqual(len(self.obj.modifiers), 1)

    def test_partial_configuration_rolls_back_native_write_failure(self) -> None:
        self.add("mirror")
        before = self.call("modifier.inspect")
        original_write = api.write
        calls = 0

        def fail_once(mod: Any, fields: dict[str, Any]) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                mod.use_axis = (False, True, False)
                raise RuntimeError("write failed")
            original_write(mod, fields)

        arguments = models.CONFIGURE.validate_python(
            {
                "object_name": self.obj.name,
                "modifier_name": "Shape",
                "type": "mirror",
                "settings": {"axes": ["y"], "clipping": True},
            }
        )
        with patch.object(api, "write", fail_once), self.assertRaises(RuntimeError):
            self.backend.modifier_configure(arguments)
        self.assertEqual(before, self.call("modifier.inspect"))

    def test_missing_self_and_wrong_type_references_are_atomic(self) -> None:
        empty = bpy.data.objects.new("Empty", None)
        bpy.context.scene.collection.objects.link(empty)
        for kind, field in (
            ("mirror", "mirror_object"),
            ("shrinkwrap", "target"),
            ("boolean", "operand_object"),
        ):
            for name, code in (
                ("Missing", "object_not_found"),
                (self.obj.name, "modifier_dependency_invalid"),
            ):
                with self.subTest(kind=kind, name=name):
                    self.error(
                        "modifier.create", code, type=kind, settings={field: name}
                    )
            if kind != "mirror":
                self.error(
                    "modifier.create",
                    "modifier_dependency_invalid",
                    type=kind,
                    settings={field: empty.name},
                )
        self.assertEqual(len(self.obj.modifiers), 0)

    def test_cross_modifier_cycle_and_parent_cycle_rejected(self) -> None:
        target = self.target()
        back = target.modifiers.new("Back", "SHRINKWRAP")
        back.target = self.obj
        self.error(
            "modifier.create",
            "modifier_dependency_invalid",
            type="boolean",
            settings={"operand_object": target.name},
        )
        target.modifiers.remove(back)
        target.parent = self.obj
        self.error(
            "modifier.create",
            "modifier_dependency_invalid",
            type="mirror",
            settings={"mirror_object": target.name},
        )

    def test_constraint_cycle_is_detected(self) -> None:
        target = self.target()
        constraint = target.constraints.new("COPY_LOCATION")
        constraint.target = self.obj
        self.error(
            "modifier.create",
            "modifier_dependency_invalid",
            type="shrinkwrap",
            settings={"target": target.name},
        )

    def test_reference_replacement_validates_proposed_graph(self) -> None:
        target = self.target()
        self.add("mirror", mirror_object=target.name)
        target.parent = self.obj
        self.configure("mirror", clear_mirror_object=True)
        self.assertIsNone(self.obj.modifiers["Shape"].mirror_object)

    def test_readonly_objects_and_data_policy(self) -> None:
        self.add("mirror")
        original = self.obj.data

        # Native RNA identifiers expose editability; use a proxy to exercise both
        # local-object/linked-data and linked-object guards without fixture files.
        class Proxy:
            def __init__(self, wrapped: Any, **values: Any) -> None:
                self.wrapped = wrapped
                self.__dict__.update(values)

            def __getattr__(self, key: str) -> Any:
                return getattr(self.wrapped, key)

        with self.assertRaises(operations.OperationError):
            api.mutable(Proxy(self.obj, is_editable=False))
        with self.assertRaises(operations.OperationError):
            api.apply(Proxy(self.obj, data=Proxy(original, library=object())), "Shape")
        self.assertEqual(self.obj.data, original)

    def test_edit_mode_rejected_without_context_change(self) -> None:
        self.add("mirror")
        bpy.context.view_layer.objects.active = self.obj
        bpy.ops.object.mode_set(mode="EDIT")
        for operation, fields in (
            ("modifier.create", {"type": "mirror"}),
            ("modifier.apply", {"modifier_name": "Shape"}),
            ("mesh.inspect_evaluated", {}),
        ):
            self.error(operation, "invalid_context", **fields)
            self.assertEqual(bpy.context.mode, "EDIT_MESH")
        self.assertEqual(
            self.call("modifier.inspect")["modifiers"][0]["type"], "mirror"
        )

    def test_native_linked_mesh_allows_object_stack_but_rejects_apply(self) -> None:
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "modifier-mesh.blend"
        bpy.data.libraries.write(str(path), {self.obj.data})
        try:
            with bpy.data.libraries.load(str(path), link=True) as (source, target):
                target.meshes = [source.meshes[0]]
            self.obj.data = target.meshes[0]
            original = self.obj.data
            self.add("subdivision_surface")
            self.configure("subdivision_surface", levels=2)
            self.assertEqual(self.obj.data, original)
            self.assertEqual(self.evaluated()["vertex_count"], 98)
            self.error("modifier.apply", "invalid_context", modifier_name="Shape")
            self.assertEqual(self.obj.data, original)
            self.call("modifier.remove", modifier_name="Shape")
        finally:
            path.unlink()

    def test_native_linked_object_is_inspectable_but_not_mutable(self) -> None:
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "modifier-object.blend"
        self.obj.name = "LibrarySurface"
        bpy.data.libraries.write(str(path), {self.obj})
        self.obj.name = "Surface"
        try:
            with bpy.data.libraries.load(str(path), link=True) as (source, target):
                target.objects = [source.objects[0]]
            linked = target.objects[0]
            bpy.context.scene.collection.objects.link(linked)
            self.assertEqual(
                self.call("modifier.inspect", object_name=linked.name)["modifiers"], []
            )
            self.error(
                "modifier.create",
                "invalid_context",
                object_name=linked.name,
                type="mirror",
            )
            self.assertEqual(len(linked.modifiers), 0)
            self.obj.name = linked.name
            self.error("modifier.inspect", "invalid_context", object_name=linked.name)
        finally:
            path.unlink()

    def test_names_with_nul_cannot_target_a_prefix_object_or_modifier(self) -> None:
        self.error(
            "modifier.create",
            "invalid_arguments",
            object_name=self.obj.name + "\x00Other",
            type="mirror",
        )
        self.assertEqual(len(self.obj.modifiers), 0)
        self.add("mirror")
        before = authored(self.obj.data)
        for operation in ("remove", "apply"):
            self.error(
                "modifier." + operation,
                "invalid_arguments",
                modifier_name="Shape\x00Other",
            )
        self.error(
            "modifier.configure",
            "invalid_arguments",
            modifier_name="Shape\x00Other",
            type="mirror",
            settings={"clipping": True},
        )
        self.assertEqual([m.name for m in self.obj.modifiers], ["Shape"])
        self.assertEqual(before, authored(self.obj.data))

    def test_animated_shape_key_dependencies_are_not_assumed_safe(self) -> None:
        self.obj.shape_key_add(name="Basis")
        keys = self.obj.data.shape_keys
        keys.animation_data_create()
        self.error("modifier.create", "modifier_dependency_invalid", type="mirror")
        self.assertEqual(len(self.obj.modifiers), 0)
        self.assertEqual(self.call("modifier.inspect")["modifiers"], [])

    def test_vertex_parented_children_preserve_topology_on_apply(self) -> None:
        child = bpy.data.objects.new("Attached", None)
        bpy.context.scene.collection.objects.link(child)
        child.parent = self.obj
        child.parent_type = "VERTEX"
        child.parent_vertices = (0, 0, 0)
        self.add("subdivision_surface")
        before = authored(self.obj.data)
        self.error("modifier.apply", "invalid_context", modifier_name="Shape")
        self.assertEqual(before, authored(self.obj.data))

    def test_remaining_armature_modifier_is_not_applied_or_rebuilt(self) -> None:
        self.add("subdivision_surface", mode="simple")
        bpy.ops.object.armature_add()
        rig = bpy.context.object
        mod = self.obj.modifiers.new("Deform", "ARMATURE")
        mod.object = rig
        pointer = mod.as_pointer()
        self.call("modifier.apply", modifier_name="Shape")
        self.assertEqual(mod.as_pointer(), pointer)
        self.assertEqual(mod.object, rig)
        self.assertEqual(len(self.obj.data.vertices), 26)

    def test_render_and_opaque_growth_guards_do_not_mutate(self) -> None:
        self.add("mirror")
        before = self.call("modifier.inspect")
        with patch.object(
            api,
            "bpy",
            SimpleNamespace(
                context=bpy.context,
                app=SimpleNamespace(is_job_running=lambda job: True),
            ),
        ):
            self.error(
                "modifier.configure",
                "invalid_context",
                type="mirror",
                modifier_name="Shape",
                settings={"clipping": True},
            )
        self.assertEqual(before, self.call("modifier.inspect"))
        self.obj.modifiers.new("Generator", "ARRAY")
        self.error("mesh.inspect_evaluated", "invalid_context")
        self.error("modifier.apply", "invalid_context", modifier_name="Shape")

    def test_application_data_validation_failure_preserves_original(self) -> None:
        self.add("subdivision_surface")
        before = authored(self.obj.data)
        size = len(bpy.data.meshes)
        with patch.object(
            api,
            "check_geometry",
            side_effect=operations.OperationError("invalid_context", "Invalid output"),
        ):
            self.error("modifier.apply", "invalid_context", modifier_name="Shape")
        self.assertEqual(before, authored(self.obj.data))
        self.assertEqual(len(bpy.data.meshes), size)

    def test_shape_key_stack_move_and_common_controls(self) -> None:
        self.obj.shape_key_add(name="Basis")
        self.add("mirror")
        self.call("modifier.create", type="subdivision_surface", name="Smooth")
        self.call("modifier.move", modifier_name="Smooth", index=0)
        self.call(
            "modifier.configure",
            modifier_name="Shape",
            type="mirror",
            enabled_render=False,
        )
        self.assertEqual(len(self.obj.data.shape_keys.key_blocks), 1)
        self.assertEqual([m.name for m in self.obj.modifiers], ["Smooth", "Shape"])

    def test_custom_normals_and_vertex_parenting_protect_application(self) -> None:
        self.add("subdivision_surface")
        self.obj.data.normals_split_custom_set([(0, 0, 1)] * len(self.obj.data.loops))
        self.error("modifier.apply", "invalid_context", modifier_name="Shape")
        self.assertTrue(self.obj.data.has_custom_normals)

    def test_surface_basis_tracks_subdivision_and_preserves_authored_data(self) -> None:
        for face in self.obj.data.polygons:
            face.use_smooth = True
        before = authored(self.obj.data)
        count = len(bpy.data.meshes)
        base = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        self.add("subdivision_surface", levels=2, render_levels=2, uv_smooth="none")
        result = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        self.assertEqual(base["authored_basis"], result["authored_basis"])
        self.assertEqual(before, authored(self.obj.data))
        self.assertEqual(len(bpy.data.meshes), count)
        self.assertEqual(result["viewport_render_settings_differences"], [])
        for key in ["geometry_sha256", "corner_normals_sha256", "tangents_sha256"]:
            self.assertNotEqual(
                base["evaluated_basis"][key], result["evaluated_basis"][key]
            )
        self.assertEqual(result["evaluated_basis"]["zero_tangent_count"], 0)
        self.configure("subdivision_surface", render_levels=3)
        self.assertEqual(
            self.evaluated()["viewport_render_settings_differences"],
            ["Shape: subdivision levels"],
        )

    def test_surface_basis_reports_flags_creases_and_custom_normals(self) -> None:
        base = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        for face in self.obj.data.polygons:
            face.use_smooth = True
        self.obj.data.edges[0].use_edge_sharp = True
        self.obj.data.edges[1].use_seam = True
        self.obj.data.attributes.new("crease_edge", "FLOAT", "EDGE").data[0].value = 0.5
        self.obj.data.normals_split_custom_set([(0, 0, 1)] * len(self.obj.data.loops))
        result = self.call("mesh.inspect_evaluated", uv_map="UVMap")["authored_basis"]
        self.assertEqual(
            result["geometry_sha256"], base["authored_basis"]["geometry_sha256"]
        )
        self.assertNotEqual(
            result["shading_flags_sha256"],
            base["authored_basis"]["shading_flags_sha256"],
        )
        self.assertEqual(result["smooth_face_count"], 6)
        self.assertEqual(result["sharp_edge_count"], 1)
        self.assertEqual(result["seam_edge_count"], 1)
        self.assertEqual(result["creased_edge_count"], 1)
        self.assertTrue(result["has_custom_normals"])

    def test_surface_basis_mirror_uv_and_failure_cleanup(self) -> None:
        before = authored(self.obj.data)
        base = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        self.add("mirror", axes=["x"], uv_flip_u=True)
        result = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        self.assertEqual(result["authored_basis"], base["authored_basis"])
        self.assertNotEqual(
            result["evaluated_basis"]["uv_sha256"], base["evaluated_basis"]["uv_sha256"]
        )
        count = len(bpy.data.meshes)
        self.error("mesh.inspect_evaluated", "uv_map_not_found", uv_map="Missing")
        self.assertEqual(len(bpy.data.meshes), count)
        self.assertEqual(authored(self.obj.data), before)
        self.assertEqual(self.call("mesh.inspect_evaluated", uv_map="UVMap"), result)

    def test_surface_basis_rejects_ngon_tangents_without_mutation(self) -> None:
        bpy.data.objects.remove(self.obj, do_unlink=True)
        bpy.ops.mesh.primitive_circle_add(vertices=5, fill_type="NGON")
        self.obj = bpy.context.object
        self.obj.name = "Surface"
        self.obj.data.uv_layers.new(name="UVMap")
        before = authored(self.obj.data)
        count = len(bpy.data.meshes)
        self.error("mesh.inspect_evaluated", "invalid_context", uv_map="UVMap")
        self.assertEqual(len(bpy.data.meshes), count)
        self.assertEqual(authored(self.obj.data), before)
        self.assertEqual(self.evaluated()["face_count"], 1)

    def test_surface_basis_measures_parallel_tangent_repeatability(self) -> None:
        for face in self.obj.data.polygons:
            face.use_smooth = True
        self.add("subdivision_surface", levels=5, render_levels=5, uv_smooth="none")
        self.call(
            "modifier.create",
            type="triangulate",
            name="Triangles",
            settings={"quad_method": "fixed", "keep_custom_normals": True},
        )
        before = authored(self.obj.data)
        count = len(bpy.data.meshes)
        result = self.call("mesh.inspect_evaluated", uv_map="UVMap")
        self.assertGreater(result["face_count"], 10000)
        repeat = result["evaluated_basis"]["tangent_repeatability"]
        self.assertEqual(len(repeat["repeated_sha256"]), 64)
        self.assertLess(repeat["maximum_component_delta"], 1e-5)
        self.assertEqual(repeat["handedness_change_count"], 0)
        self.assertEqual(before, authored(self.obj.data))
        self.assertEqual(count, len(bpy.data.meshes))
        self.assertIsNone(self.evaluated()["evaluated_basis"]["tangent_repeatability"])

    def test_evaluated_repeated_inspection_clears_temporary_geometry(self) -> None:
        self.add("subdivision_surface")
        count = len(bpy.data.meshes)
        original = self.obj.data
        expected = self.evaluated()
        for _ in range(30):
            self.assertEqual(self.evaluated(), expected)
        self.assertEqual(len(bpy.data.meshes), count)
        self.assertEqual(self.obj.data, original)

    def test_evaluated_cleanup_on_success_and_summary_failure(self) -> None:
        self.add("subdivision_surface")
        cleared = []

        class Proxy:
            def __init__(self, wrapped: Any) -> None:
                self.wrapped = wrapped

            def __getattr__(self, name: str) -> Any:
                return getattr(self.wrapped, name)

            def __contains__(self, name: str) -> bool:
                return name in self.wrapped

            def evaluated_get(self, graph: Any) -> Any:
                return Proxy(self.wrapped.evaluated_get(graph))

            def to_mesh_clear(self) -> None:
                cleared.append(True)
                self.wrapped.to_mesh_clear()

        proxy = Proxy(self.obj)
        api.inspect_evaluated(proxy)
        self.assertEqual(len(cleared), 1)
        with (
            patch.object(
                api.mesh, "summary", side_effect=RuntimeError("summary failed")
            ),
            self.assertRaises(RuntimeError),
        ):
            api.inspect_evaluated(proxy)
        self.assertEqual(len(cleared), 2)
        self.assertEqual(self.evaluated()["vertex_count"], 26)

    def test_evaluated_budget_checks_before_and_after_extraction(self) -> None:
        self.add("subdivision_surface")
        with patch.object(api.mesh, "MAX_WORK_ELEMENTS", 10):
            self.error("mesh.inspect_evaluated", "invalid_context")
        with (
            patch.object(api, "budget"),
            patch.object(api.mesh, "MAX_WORK_ELEMENTS", 60),
        ):
            self.error("mesh.inspect_evaluated", "invalid_context")
        self.assertEqual(self.evaluated()["vertex_count"], 26)

    def test_mesh_summary_is_local_and_never_contains_evaluated_indices(self) -> None:
        self.obj.location = (10, 20, 30)
        self.obj.scale = (2, 3, 4)
        result = self.evaluated()
        self.assertEqual(result["bounds_min"], [-1, -1, -1])
        self.assertEqual(result["source_mesh_name"], self.obj.data.name)
        self.assertEqual(result["evaluation"], "viewport")
        self.assertNotIn("vertices", result)
        self.assertNotIn("indices", result)

    def test_network_thread_cannot_access_modifier_or_evaluated_bpy(self) -> None:
        arguments = models.ModifierInspectArguments(object_name=self.obj.name)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.backend.modifier_inspect, arguments)
            with self.assertRaisesRegex(RuntimeError, "main thread"):
                future.result()
        arguments = models.EvaluatedMeshArguments(object_name=self.obj.name)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.backend.mesh_inspect_evaluated, arguments)
            with self.assertRaisesRegex(RuntimeError, "main thread"):
                future.result()


suite = unittest.defaultTestLoader.loadTestsFromTestCase(ModifierTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print(f"BLENDER_MODIFIER_TESTS_PASSED {result.testsRun}")
