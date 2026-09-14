"""Native voxel-remesh preservation, staged failure, allocation and sculpt checks."""

import importlib
import math
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.sculpt_checks import (  # noqa: E402
    NativeCase,
    adapter,
    authored,
    coordinates,
)

assert adapter.__package__ is not None
remesh = importlib.import_module(adapter.__package__ + ".remesh")
models = importlib.import_module(adapter.__package__ + ".remesh_models")


class RemeshCase(NativeCase):
    def setUp(self) -> None:
        super().setUp()
        for layer in list(self.obj.data.uv_layers):
            self.obj.data.uv_layers.remove(layer)

    def analyze(self, **args: Any) -> Any:
        return self.call("sculpt.voxel_remesh.inspect", **args)

    def rebuild(self, **args: Any) -> Any:
        return self.call("sculpt.voxel_remesh", **{"voxel_size": 0.2, **args})

    def blocked(self, code: str, **args: Any) -> None:
        before = authored(self.obj)
        analysis = self.analyze(**args)
        self.assertIn(code, [b["code"] for b in analysis["blockers"]])
        with patch.object(remesh, "native_remesh") as native:
            self.error(
                "sculpt.voxel_remesh",
                "mesh_has_shape_keys"
                if code == "has_shape_keys"
                else "voxel_remesh_blocked",
                **{"voxel_size": 0.2, **args},
            )
            native.assert_not_called()
        self.assertEqual(authored(self.obj), before)

    def attribute(self, name: str, kind: str, domain: str, value: Any) -> Any:
        attr = self.obj.data.attributes.new(name, kind, domain)
        for item in attr.data:
            if "COLOR" in kind:
                item.color = value
            elif kind in {"FLOAT_VECTOR", "FLOAT2"}:
                item.vector = value
            else:
                item.value = value
        self.obj.data.update()
        return attr


class NativeRemeshTests(RemeshCase):
    def test_inspection_is_readonly_and_describes_destruction(self) -> None:
        data = self.obj.data
        data.remesh_voxel_size = 0.75
        before = authored(self.obj)
        result = self.analyze(voxel_size=0.1)
        self.assertFalse(result["blockers"])
        self.assertEqual(result["mesh"]["vertex_count"], 8)
        self.assertEqual(result["mesh"]["edge_length"]["coefficient_of_variation"], 0)
        self.assertTrue(result["effective_fix_poles"])
        self.assertIn("not production retopology", result["guidance"])
        self.assertIn(
            "topology_replaced", [d["code"] for d in result["destructive_effects"]]
        )
        self.assertEqual(data.remesh_voxel_size, 0.75)
        self.assertEqual(before, authored(self.obj))

    def test_resolution_replaces_topology_and_preserves_bounds(self) -> None:
        counts = []
        for size in (0.3, 0.15):
            self.setUp()
            result = self.rebuild(voxel_size=size)
            counts.append(result["after"]["vertex_count"])
            self.assertTrue(result["indices_invalidated"])
            self.assertNotEqual(result["before"]["vertex_count"], counts[-1])
            self.assertLess(
                max(abs(abs(v) - 1) for v in result["after"]["bounds_max"]), 0.08
            )
            self.assertEqual(
                result["after"]["manifold_summary"]["non_manifold_edge_count"], 0
            )
            self.assertTrue(
                all(math.isfinite(c) for f in self.obj.data.polygons for c in f.normal)
            )
        self.assertGreater(counts[1], counts[0] * 2)

    def test_sphere_adaptivity_and_volume_options(self) -> None:
        counts = []
        errors = []
        for adaptivity, volume, poles in (
            (0, True, True),
            (0.4, True, True),
            (0, False, False),
        ):
            self.setUp()
            self.sphere()
            result = self.rebuild(
                voxel_size=0.1,
                adaptivity=adaptivity,
                preserve_volume=volume,
                fix_poles=poles,
            )
            counts.append(result["after"]["face_count"])
            errors.append(
                sum(abs(v.co.length - 1) for v in self.obj.data.vertices)
                / len(self.obj.data.vertices)
            )
            self.assertEqual(result["effective_fix_poles"], poles and adaptivity == 0)
            self.assertLess(errors[-1], 0.04)
            if adaptivity:
                self.assertTrue(
                    any(len(f.vertices) == 3 for f in self.obj.data.polygons)
                )
        self.assertLess(counts[1], counts[0])
        self.assertLess(errors[0], errors[2])

    def test_shared_mesh_isolated_with_original_settings_and_data(self) -> None:
        self.attribute(".sculpt_mask", "FLOAT", "POINT", 0.5)
        sibling = self.obj.copy()
        bpy.context.scene.collection.objects.link(sibling)
        original = self.obj.data
        before = authored(sibling)
        old_size = original.remesh_voxel_size
        result = self.rebuild()
        self.assertTrue(result["isolated_shared_mesh"])
        self.assertEqual(sibling.data, original)
        self.assertNotEqual(self.obj.data, original)
        self.assertEqual(before, authored(sibling))
        self.assertEqual(original.remesh_voxel_size, old_size)
        self.assertEqual(
            [v.value for v in original.attributes[".sculpt_mask"].data], [0.5] * 8
        )

    def test_material_slots_and_face_material_reprojection_or_default(self) -> None:
        for preserve in (True, False):
            self.setUp()
            materials = [bpy.data.materials.new("Material") for _ in range(2)]
            for material in materials:
                self.obj.data.materials.append(material)
            for face in self.obj.data.polygons:
                face.material_index = face.index % 2
                face.use_smooth = face.index % 2 == 0
            self.rebuild(preserve_attributes=preserve)
            self.assertEqual(list(self.obj.data.materials), materials)
            self.assertEqual(
                {p.material_index for p in self.obj.data.polygons},
                {0, 1} if preserve else {0},
            )
            self.assertEqual(
                {p.use_smooth for p in self.obj.data.polygons},
                {False, True} if preserve else {True},
            )

    def test_masks_and_face_sets_reproject_or_disappear(self) -> None:
        for preserve in (True, False):
            self.setUp()
            self.attribute(".sculpt_mask", "FLOAT", "POINT", 1.0)
            attr = self.attribute(".sculpt_face_set", "INT", "FACE", 3)
            attr.data[0].value = 9
            result = self.rebuild(preserve_attributes=preserve)
            mask = self.call("sculpt.mask.inspect")["base_mesh"]
            sets = self.call("sculpt.face_sets.inspect")
            self.assertEqual(mask["present"], preserve)
            self.assertEqual(sets["authored"], preserve)
            if preserve:
                self.assertGreater(mask["mean"], 0.999)
                self.assertLessEqual(mask["max"], 1)
                self.assertEqual({s["id"] for s in sets["face_sets"]}, {3, 9})
            else:
                self.assertEqual(mask["max"], 0)
                self.assertEqual(sets["face_sets"][0]["id"], 1)
            self.assertTrue(result["lost_or_rebuilt_data"])

    def test_generic_attributes_and_colors_by_domain(self) -> None:
        specs = [
            ("small", "INT8", "POINT", 6),
            ("pair", "FLOAT2", "EDGE", (0.2, 0.6)),
            ("integers", "INT32_2D", "FACE", (3, 7)),
            ("point", "FLOAT", "POINT", 0.25),
            ("edge", "INT", "EDGE", 7),
            ("face", "BOOLEAN", "FACE", True),
            ("corner", "FLOAT", "CORNER", 0.5),
            ("vector", "FLOAT_VECTOR", "POINT", (0.2, 0.4, 0.6)),
            ("color", "FLOAT_COLOR", "POINT", (0.2, 0.4, 0.6, 1)),
            ("color_corner", "BYTE_COLOR", "CORNER", (0.2, 0.4, 0.6, 1)),
        ]
        for preserve in (True, False):
            self.setUp()
            for name, kind, domain, value in specs:
                self.attribute(name, kind, domain, value)
            self.rebuild(preserve_attributes=preserve)
            for name, kind, domain, value in specs:
                attr = self.obj.data.attributes.get(name)
                if not preserve:
                    self.assertIsNone(attr)
                    continue
                self.assertEqual((attr.data_type, attr.domain), (kind, domain))
                for item in attr.data:
                    actual = (
                        item.color
                        if "COLOR" in kind
                        else item.vector
                        if kind in {"FLOAT_VECTOR", "FLOAT2"}
                        else item.value
                    )
                    if isinstance(value, tuple):
                        self.assertLess(
                            max(abs(a - b) for a, b in zip(actual, value, strict=True)),
                            0.01,
                        )
                    else:
                        self.assertAlmostEqual(actual, value, places=6)

    def test_seam_and_sharp_flags_are_sampled_or_discarded(self) -> None:
        for preserve in (True, False):
            self.setUp()
            for edge in self.obj.data.edges:
                edge.use_seam = True
                edge.use_edge_sharp = True
            self.rebuild(preserve_attributes=preserve)
            self.assertEqual({e.use_seam for e in self.obj.data.edges}, {preserve})
            self.assertEqual(
                {e.use_edge_sharp for e in self.obj.data.edges}, {preserve}
            )

    def test_uv_maps_block_even_though_native_transfer_exists(self) -> None:
        self.obj.data.uv_layers.new(name="Authored")
        self.blocked("has_uv_maps", preserve_attributes=True)
        self.blocked("has_uv_maps", preserve_attributes=False)
        # Native transfer collapses corner discontinuities to one value per vertex.
        uv = self.obj.data.uv_layers[0]
        for i, item in enumerate(uv.uv):
            item.vector = (i % 4, i // 4)
        settings = models.VoxelRemeshSettings(voxel_size=0.2)
        remesh.native_remesh(self.obj, settings)
        values: dict[int, set[tuple[float, ...]]] = {}
        for loop, value in zip(
            self.obj.data.loops, self.obj.data.uv_layers[0].uv, strict=True
        ):
            values.setdefault(loop.vertex_index, set()).add(tuple(value.vector))
        self.assertTrue(all(len(v) == 1 for v in values.values()))

    def test_vertex_groups_are_protected_for_both_options(self) -> None:
        group = self.obj.vertex_groups.new(name="Weight")
        group.add([0, 1, 2], 0.7, "REPLACE")
        for preserve in (True, False):
            self.blocked("has_vertex_groups", preserve_attributes=preserve)
        remesh.native_remesh(self.obj, models.VoxelRemeshSettings(voxel_size=0.2))
        self.assertEqual(self.obj.vertex_groups[0].name, "Weight")
        self.assertTrue(any(v.groups for v in self.obj.data.vertices))

    def test_explicit_uv_discard_keeps_shared_source_and_other_attributes(self) -> None:
        for preserve in (True, False):
            self.setUp()
            for name in ("Generated", "Other UV"):
                layer = self.obj.data.uv_layers.new(name=name)
                for item in layer.uv:
                    item.vector = (0.25, 0.75)
            self.attribute(".sculpt_mask", "FLOAT", "POINT", 0.4)
            sibling = self.obj.copy()
            bpy.context.scene.collection.objects.link(sibling)
            original = self.obj.data
            before = authored(sibling)
            analysis = self.analyze(discard_uv_maps=True)
            self.assertFalse(analysis["blockers"])
            self.assertEqual(authored(sibling), before)
            effect = next(
                d for d in analysis["destructive_effects"] if d["code"] == "uv_maps"
            )
            self.assertEqual(effect["names"], ["Generated", "Other UV"])
            self.assertEqual(effect["behavior"], "discarded")
            for effect in analysis["destructive_effects"]:
                if effect["behavior"] == "reprojected":
                    self.assertNotIn("Generated", effect["names"])
                    self.assertNotIn("Other UV", effect["names"])
            result = self.rebuild(discard_uv_maps=True, preserve_attributes=preserve)
            self.assertTrue(result["settings"]["discard_uv_maps"])
            self.assertEqual(len(self.obj.data.uv_layers), 0)
            self.assertEqual(sibling.data, original)
            self.assertEqual(authored(sibling), before)
            mask = self.obj.data.attributes.get(".sculpt_mask")
            self.assertEqual(mask is not None, preserve)
            if preserve:
                self.assertTrue(all(abs(v.value - 0.4) < 1e-5 for v in mask.data))

    def test_object_material_overrides_and_shared_sibling_are_preserved(self) -> None:
        for preserve in (True, False):
            self.setUp()
            materials = [bpy.data.materials.new("Material") for _ in range(4)]
            for material in materials[:3]:
                self.obj.data.materials.append(material)
            self.obj.material_slots[1].link = "OBJECT"
            self.obj.material_slots[1].material = materials[3]
            self.obj.material_slots[2].link = "OBJECT"
            self.obj.material_slots[2].material = None
            sibling = self.obj.copy()
            bpy.context.scene.collection.objects.link(sibling)
            sibling.material_slots[1].material = materials[0]
            original = self.obj.data
            slots = remesh.modifiers.material_slots(self.obj)
            sibling_slots = remesh.modifiers.material_slots(sibling)
            self.assertFalse(self.analyze()["blockers"])
            self.rebuild(preserve_attributes=preserve)
            self.assertEqual(remesh.modifiers.material_slots(self.obj), slots)
            self.assertEqual(list(self.obj.data.materials), materials[:3])
            self.assertEqual(sibling.data, original)
            self.assertEqual(remesh.modifiers.material_slots(sibling), sibling_slots)

    def test_uv_discard_failure_preserves_maps_and_object_materials(self) -> None:
        self.obj.data.uv_layers.new(name="Generated")
        self.obj.data.materials.append(bpy.data.materials.new("Underlying"))
        self.obj.material_slots[0].link = "OBJECT"
        self.obj.material_slots[0].material = bpy.data.materials.new("Override")
        original = self.obj.data
        before = authored(self.obj)
        slots = remesh.modifiers.material_slots(self.obj)
        objects = {o.as_pointer() for o in bpy.data.objects}
        meshes = {m.as_pointer() for m in bpy.data.meshes}
        with patch.object(
            remesh, "validate_transfer", side_effect=RuntimeError("After remesh")
        ):
            self.error(
                "sculpt.voxel_remesh",
                "voxel_remesh_failed",
                voxel_size=0.2,
                discard_uv_maps=True,
            )
        self.assertEqual(self.obj.data, original)
        self.assertEqual(authored(self.obj), before)
        self.assertEqual(remesh.modifiers.material_slots(self.obj), slots)
        self.assertEqual(objects, {o.as_pointer() for o in bpy.data.objects})
        self.assertEqual(meshes, {m.as_pointer() for m in bpy.data.meshes})

    def test_uv_discard_does_not_bypass_other_production_guards(self) -> None:
        self.obj.data.uv_layers.new(name="Generated")
        self.obj.vertex_groups.new(name="Weights")
        self.blocked("has_vertex_groups", discard_uv_maps=True)
        self.setUp()
        self.obj.data.uv_layers.new(name="Generated")
        self.obj.shape_key_add(name="Basis")
        self.blocked("has_shape_keys", discard_uv_maps=True)

    def test_shape_keys_and_multires_never_reach_native(self) -> None:
        self.obj.shape_key_add(name="Basis")
        self.blocked("has_shape_keys")
        self.setUp()
        self.add_multires(2)
        self.blocked("has_multires")
        self.assertEqual(self.obj.modifiers[0].total_levels, 2)

    def test_modifier_animation_and_relationship_guards(self) -> None:
        self.obj.modifiers.new("Existing", "SUBSURF")
        self.blocked("has_modifiers")
        self.setUp()
        self.obj.data.animation_data_create()
        self.blocked("mesh_animation")
        self.setUp()
        self.obj.animation_data_create()
        self.blocked("mesh_animation")
        self.setUp()
        child = bpy.data.objects.new("Child", None)
        bpy.context.scene.collection.objects.link(child)
        child.parent = self.obj
        child.parent_type = "VERTEX"
        self.blocked("deformation_relationship")
        self.setUp()
        self.obj.constraints.new("COPY_LOCATION")
        self.blocked("deformation_relationship")
        for kind, field in (("MESH_DEFORM", "object"), ("SURFACE_DEFORM", "target")):
            self.setUp()
            dependent = self.obj.copy()
            bpy.context.scene.collection.objects.link(dependent)
            modifier = dependent.modifiers.new("Deformation", kind)
            setattr(modifier, field, self.obj)
            self.blocked("deformation_relationship")

    def test_unit_local_and_inherited_scale_guard(self) -> None:
        self.obj.scale.x = 2
        bpy.context.view_layer.update()
        self.blocked("nonunit_scale")
        self.setUp()
        parent = bpy.data.objects.new("Parent", None)
        bpy.context.scene.collection.objects.link(parent)
        self.obj.parent = parent
        parent.scale.y = 2
        bpy.context.view_layer.update()
        self.blocked("nonunit_scale")

    def test_linked_data_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tyvrana-remesh-library-") as directory:
            path = str(Path(directory) / "source.blend")
            name = self.obj.data.name
            bpy.data.libraries.write(path, {self.obj.data})
            with bpy.data.libraries.load(path, link=True) as (_, loaded):
                loaded.meshes = [name]
            self.obj.data = loaded.meshes[0]
            self.blocked("linked_data")

    def test_hidden_and_custom_normal_guards(self) -> None:
        self.obj.data.vertices[0].hide = True
        self.blocked("hidden_geometry")
        self.setUp()
        self.obj.data.normals_split_custom_set([(0, 0, 1)] * len(self.obj.data.loops))
        self.blocked("has_custom_normals")

    def test_capacity_and_coordinate_guards_precede_native(self) -> None:
        self.blocked("voxel_grid_limit", voxel_size=0.0001)
        for vertex in self.obj.data.vertices:
            vertex.co *= 10000
        self.obj.data.update()
        self.blocked("voxel_grid_limit", voxel_size=1)
        self.setUp()
        for vertex in self.obj.data.vertices:
            vertex.co.x += 200000
        self.obj.data.update()
        self.blocked("voxel_coordinate_limit", voxel_size=0.1)

        self.setUp()
        for vertex in self.obj.data.vertices:
            vertex.co *= 1e30
        self.obj.data.update()
        with patch.object(remesh, "native_remesh") as native:
            result = self.error(
                "sculpt.voxel_remesh", "voxel_remesh_blocked", voxel_size=0.1
            )
            self.assertEqual(
                result.error.details["blockers"][0]["code"], "voxel_coordinate_limit"
            )
            native.assert_not_called()

    def test_exact_grid_budget_succeeds(self) -> None:
        for vertex in self.obj.data.vertices:
            vertex.co.x *= 46
            vertex.co.y *= 46
            vertex.co.z *= 96
        self.obj.data.update()
        result = self.rebuild(voxel_size=1)
        self.assertEqual(result["grid"]["cells"], 2_000_000)
        self.assertGreater(result["after"]["face_count"], 1000)

    def test_attribute_limit_and_malformed_sculpt_data(self) -> None:
        for i in range(33):
            self.attribute("value" + str(i), "FLOAT", "POINT", 0.0)
        self.blocked("attribute_limit")
        with self.assertRaisesRegex(RuntimeError, "Output exceeds attribute capacity"):
            remesh.validate_transfer(self.obj.data, self.obj.data, set())
        self.setUp()
        self.obj.data.attributes.new("matrix", "FLOAT4X4", "POINT")
        self.blocked("unsupported_attribute")
        self.setUp()
        self.attribute(".sculpt_mask", "FLOAT", "POINT", 2)
        self.blocked("invalid_sculpt_regions")
        self.setUp()
        self.attribute(".sculpt_face_set", "INT", "FACE", -1)
        self.blocked("invalid_sculpt_regions")

    def test_failure_preserves_original_and_cleans_staging(self) -> None:
        original = self.obj.data
        before = authored(self.obj)
        objects = {o.as_pointer() for o in bpy.data.objects}
        meshes = {m.as_pointer() for m in bpy.data.meshes}
        with patch.object(
            remesh,
            "validate_transfer",
            side_effect=RuntimeError("Failure after remesh"),
        ):
            self.error("sculpt.voxel_remesh", "voxel_remesh_failed", voxel_size=0.2)
        self.assertEqual(self.obj.data, original)
        self.assertEqual(authored(self.obj), before)
        self.assertEqual(objects, {o.as_pointer() for o in bpy.data.objects})
        self.assertEqual(meshes, {m.as_pointer() for m in bpy.data.meshes})

    def test_context_selection_and_main_thread(self) -> None:
        selected = list(bpy.context.selected_objects)
        active = bpy.context.view_layer.objects.active
        scene = bpy.context.scene
        self.rebuild()
        self.assertEqual(list(bpy.context.selected_objects), selected)
        self.assertEqual(bpy.context.view_layer.objects.active, active)
        self.assertEqual(bpy.context.scene, scene)
        self.assertEqual(bpy.context.mode, "OBJECT")
        with ThreadPoolExecutor(max_workers=1) as executor:
            for method, args in [
                (
                    self.backend.sculpt_voxel_remesh_inspect,
                    models.VoxelRemeshInspectArguments(object_name=self.obj.name),
                ),
                (
                    self.backend.sculpt_voxel_remesh,
                    models.VoxelRemeshArguments(
                        object_name=self.obj.name, voxel_size=0.2
                    ),
                ),
            ]:
                with self.assertRaises(RuntimeError):
                    executor.submit(method, args).result()

    def test_open_geometry_rejected(self) -> None:
        bpy.data.objects.remove(self.obj, do_unlink=True)
        bpy.ops.mesh.primitive_plane_add()
        self.obj = bpy.context.object
        for layer in list(self.obj.data.uv_layers):
            self.obj.data.uv_layers.remove(layer)
        self.blocked("non_manifold")


class InteractiveRemeshTests(RemeshCase):
    def test_remesh_then_reinspect_raycast_and_sculpt(self) -> None:
        self.sphere()
        self.rebuild(voxel_size=0.08)
        self.call("mesh.inspect")
        query = self.call(
            "mesh.query", selector={"mode": "all", "domain": "vertex"}, limit=3
        )
        self.assertEqual(len(query["elements"]), 3)
        self.camera()
        hit = self.call("scene.raycast", mode="camera", u=0.5, v=0.5)
        self.assertTrue(hit["hit"])
        before = coordinates(self.obj)
        for brush in ("draw", "smooth", "clay"):
            hit = self.call("scene.raycast", mode="camera", u=0.5, v=0.5)
            self.call(
                "sculpt.stroke",
                brush=brush,
                samples=[{"location": hit["location_object"]}] * 4,
                radius=0.4,
                strength=0.7,
            )
        self.assertGreater(
            max(
                (a - b).length
                for a, b in zip(before, coordinates(self.obj), strict=True)
            ),
            0.005,
        )
        self.assertEqual(bpy.context.mode, "OBJECT")

    def test_sculpt_mode_blocked_without_context_change(self) -> None:
        bpy.ops.object.mode_set(mode="SCULPT")
        active = bpy.context.object
        self.blocked("object_mode_required")
        self.assertEqual(bpy.context.mode, "SCULPT")
        self.assertEqual(bpy.context.object, active)
        bpy.ops.object.mode_set(mode="OBJECT")


def run() -> None:
    runtime = adapter._runtime
    worker = runtime.worker if runtime else None
    adapter.unregister()
    if worker:
        assert worker.process.returncode == 0
        assert not worker.spool.root.exists()
    suite = unittest.TestLoader().loadTestsFromTestCase(NativeRemeshTests)
    if not bpy.app.background:
        suite.addTests(
            unittest.TestLoader().loadTestsFromTestCase(InteractiveRemeshTests)
        )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert not bpy.app.timers.is_registered(adapter.pump)
    if result.wasSuccessful():
        print("BLENDER_REMESH_TESTS_PASSED", result.testsRun, flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    elif not result.wasSuccessful():
        raise RuntimeError("Native remesh checks failed")


if bpy.app.background:
    run()
else:
    bpy.app.timers.register(run, first_interval=3)
