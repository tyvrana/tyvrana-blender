"""Native curve evaluation, dependency and transaction checks on synthetic fixtures."""

import importlib
import math
import unittest
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

PACKAGE = "bl_ext.user_default.tyvrana_blender."
backend = importlib.import_module(PACKAGE + "blender").BlenderBackend()
ops = importlib.import_module(PACKAGE + "operations")
curves = importlib.import_module(PACKAGE + "curves")


def execute(name: str, arguments: Any) -> Any:
    return ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="curve-test",
            operation="blender." + name,
            arguments=arguments,
        ),
    )


def call(name: str, /, **arguments: Any) -> Any:
    response = execute(name, arguments)
    assert isinstance(response, OperationSuccess), response
    return response.result


def reject(name: str, /, **arguments: Any) -> Any:
    response = execute(name, arguments)
    assert isinstance(response, OperationFailure), response
    return response.error


def spline(kind: str = "POLY", coords: Any = None, **kwargs: Any) -> Any:
    return dict(
        type=kind,
        points=[dict(co=p) for p in (coords or [[0, 0, 0], [0, 1, 0], [1, 2, 0]])],
        **kwargs,
    )


def anchor(kind: str = "object", name: str = "Target", **kwargs: Any) -> Any:
    return dict(kind=kind, object=name, **kwargs)


class CurveTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in [
            bpy.data.curves,
            bpy.data.armatures,
            bpy.data.meshes,
            bpy.data.node_groups,
            bpy.data.materials,
        ]:
            for item in list(collection):
                if item.users == 0:
                    collection.remove(item)

    def create(self, name: str = "Guide", **kwargs: Any) -> Any:
        spec = dict(name=name, splines=[spline()])
        spec.update(kwargs)
        result = call("curve.create", curves=[spec])
        self.assertTrue(result["curves"][0]["valid"], result)
        return bpy.data.objects[name]

    def inspect(self, name: str = "Guide", **kwargs: Any) -> Any:
        return call("curve.inspect", names=[name], **kwargs)["curves"][0]

    def vector(self, actual: Any, expected: Any, tolerance: float = 3e-5) -> None:
        self.assertLess((Vector(actual) - Vector(expected)).length, tolerance)

    def target(self) -> Any:
        obj = bpy.data.objects.new("Target", None)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def structure(self) -> Any:
        call(
            "armature.create",
            name="Target",
            bones=[
                dict(name="A", head=[0, 0, 0], tail=[0, 1, 0]),
                dict(
                    name="B", head=[0, 1, 0], tail=[0, 2, 0], parent="A", connected=True
                ),
            ],
        )
        return bpy.data.objects["Target"]

    def surface(self) -> Any:
        mesh = bpy.data.meshes.new("Surface")
        mesh.from_pydata([(0, 0, 0), (2, 0, 0), (0, 2, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("Surface", mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def allocations(self) -> Any:
        return [
            len(c) for c in [bpy.data.objects, bpy.data.curves, bpy.data.node_groups]
        ]

    def test_poly_summary_sampling_and_local_world(self) -> None:
        self.create(location=[3, 4, 5], scale=2)
        row = self.inspect(samples=5, point_limit=2)
        self.assertAlmostEqual(row["length"], 2 * (1 + math.sqrt(2)), places=5)
        self.vector(row["splines"][0]["start"], [3, 4, 5])
        self.vector(row["splines"][0]["end"], [5, 8, 5])
        self.assertEqual(len(row["splines"][0]["points"]), 2)
        self.assertTrue(row["splines"][0]["points_truncated"])
        for sample in row["splines"][0]["samples"]:
            t, n, b = [Vector(sample[k]) for k in ["tangent", "normal", "binormal"]]
            self.assertAlmostEqual(t.length, 1, places=5)
            self.assertAlmostEqual(n.length, 1, places=5)
            self.assertAlmostEqual(t.dot(n), 0, places=5)
            self.vector(t.cross(n), b)
        local = self.inspect(space="local")
        self.vector(local["splines"][0]["start"], [0, 0, 0])
        self.assertEqual(local["splines"][0]["points"], [])

    def test_bezier_handles_radius_tilt_and_nurbs(self) -> None:
        for kind in ["BEZIER", "NURBS"]:
            spec = spline(kind)
            spec["points"][1].update(radius=0.5, tilt=0.3, weight=2)
            self.create(kind, splines=[spec])
            row = self.inspect(kind, samples=7, point_limit=3)
            self.assertEqual(row["splines"][0]["type"], kind)
            self.assertGreater(row["length"], 2)
            self.assertEqual(len(row["splines"][0]["samples"]), 7)
            self.assertEqual(row["splines"][0]["radius_range"], [0.5, 1])
        manual = spline("BEZIER", [[0, 0, 0], [0, 2, 0]])
        for p in manual["points"]:
            p.update(
                handle_type="FREE", left=[-1, p["co"][1], 0], right=[1, p["co"][1], 0]
            )
        self.create("Manual", splines=[manual])
        self.assertGreater(self.inspect("Manual")["length"], 2.2)

    def test_cyclic_and_multispline_filter(self) -> None:
        self.create(
            splines=[spline(cyclic=True), spline("BEZIER", [[2, 0, 0], [2, 1, 0]])]
        )
        row = self.inspect(spline=0, samples=8)
        self.assertEqual(row["spline_count"], 2)
        self.assertEqual(len(row["splines"]), 1)
        self.vector(row["splines"][0]["start"], row["splines"][0]["end"])
        self.assertEqual(row["splines"][0]["samples"][-1]["factor"], 7 / 8)

    def test_circle_sweep_radius_edit_and_material(self) -> None:
        material = bpy.data.materials.new("Rubber")
        self.create(
            settings=dict(
                profile=dict(kind="circle", radius=0.1, resolution=8),
                material=material.name,
            )
        )
        before = self.inspect()
        self.assertEqual(before["evaluated_vertices"], 24)
        self.assertEqual(before["evaluated_faces"], 18)
        call(
            "curve.configure",
            curves=[
                dict(
                    name="Guide",
                    ranges=[
                        dict(
                            spline=0,
                            start=1,
                            points=[dict(co=[0, 1, 0], radius=2, tilt=0.5)],
                        )
                    ],
                )
            ],
        )
        self.assertEqual(self.inspect()["splines"][0]["radius_range"], [1, 2])
        self.assertEqual(
            bpy.data.objects["Guide"]
            .evaluated_get(bpy.context.evaluated_depsgraph_get())
            .data.materials[0]
            .original,
            material,
        )
        call("curve.remove", names=["Guide"])
        self.assertIn("Rubber", bpy.data.materials)

    def test_custom_profile_is_shared_and_protected(self) -> None:
        self.create(
            "Profile",
            splines=[
                spline(
                    coords=[
                        [-0.1, -0.2, 0],
                        [0.1, -0.2, 0],
                        [0.1, 0.2, 0],
                        [-0.1, 0.2, 0],
                    ],
                    cyclic=True,
                )
            ],
        )
        for name in ["Guide", "Other"]:
            self.create(
                name, settings=dict(profile=dict(kind="object", object="Profile"))
            )
        reject("curve.remove", names=["Profile"])
        self.assertEqual(self.inspect()["evaluated_vertices"], 12)
        call("curve.remove", names=["Guide", "Other"])
        self.assertIn("Profile", bpy.data.objects)
        call("curve.remove", names=["Profile"])
        self.assertEqual(self.allocations(), [0, 0, 0])

    def test_object_binding_parent_transform_rename_and_detach(self) -> None:
        target = self.target()
        self.create(bindings=[dict(spline=0, target=anchor(), offset=[0.2, 0, 0])])
        parent = bpy.data.objects.new("Parent", None)
        bpy.context.scene.collection.objects.link(parent)
        target.parent = parent
        target.location = (2, 1, 0)
        parent.rotation_euler.z = 0.5
        target.name = "Renamed"
        bpy.context.view_layer.update()
        row = self.inspect()
        self.assertTrue(row["valid"], row)
        self.assertEqual(row["bindings"][0]["target"]["object"], "Renamed")
        self.vector(
            row["bindings"][0]["evaluated_world"],
            target.matrix_world @ Vector((0.2, 0, 0)),
        )
        call("curve.configure", curves=[dict(name="Guide", bindings=[])])
        self.assertEqual(self.inspect()["bindings"], [])
        self.vector(self.inspect()["splines"][0]["start"], [0, 0, 0])

    def test_whole_spline_binding_rotates_authored_shape(self) -> None:
        target = self.target()
        self.create(bindings=[dict(spline=0, target=anchor(), follow="spline")])
        target.matrix_world = (
            Matrix.Translation((2, 3, 4)) @ Euler((0.3, 0.2, 0.4)).to_matrix().to_4x4()
        )
        bpy.context.view_layer.update()
        row = self.inspect(samples=3)
        self.assertTrue(row["valid"], row)
        self.vector(row["splines"][0]["end"], target.matrix_world @ Vector((1, 2, 0)))

    def test_two_posed_bone_endpoints_and_helper_cleanup(self) -> None:
        self.structure()
        bindings = [
            dict(spline=0, point=0, target=anchor("bone", bone="A")),
            dict(spline=0, point=2, target=anchor("bone", bone="B"), offset=[0, 1, 0]),
        ]
        self.create(bindings=bindings)
        call(
            "armature.pose",
            object_name="Target",
            bones=[
                dict(name="A", rotation=[0.4, 0, 0]),
                dict(name="B", rotation=[0, 0, 0.3]),
            ],
        )
        row = self.inspect()
        self.assertTrue(row["valid"], row)
        self.assertTrue(all(b["error"] < 1e-5 for b in row["bindings"]))
        self.assertEqual(
            len([o for o in bpy.data.objects if o.get("tyvrana_curve_helper")]), 2
        )
        call(
            "curve.configure", curves=[dict(name="Guide", settings=dict(resolution=4))]
        )
        self.assertTrue(self.inspect()["valid"])
        self.assertEqual(
            len([o for o in bpy.data.objects if o.get("tyvrana_curve_helper")]), 2
        )
        call("curve.remove", names=["Guide"])
        self.assertEqual(len(bpy.data.objects), 1)

    def test_surface_frame_deformation_transform_and_topology_invalidation(
        self,
    ) -> None:
        surface = self.surface()
        target = anchor("surface", "Surface", face=0, barycentric=[0.5, 0.25, 0.25])
        self.create(
            bindings=[
                dict(spline=0, target=target, follow="spline", offset=[0, 0, 0.2])
            ]
        )
        surface.data.vertices[2].co.z = 1
        surface.matrix_world = Matrix.Translation((3, 2, 1)) @ Matrix.Scale(2, 4)
        surface.data.update()
        bpy.context.view_layer.update()
        row = self.inspect()
        self.assertTrue(row["valid"], row)
        local = Vector((0.5, 0.5, 0.25)) + Vector((0, -1, 2)).normalized() * 0.2
        self.vector(row["bindings"][0]["evaluated_world"], surface.matrix_world @ local)
        surface.data.vertices.add(1)
        surface.data.update()
        row = self.inspect()
        self.assertFalse(row["valid"])
        self.assertIsNone(row["length"])
        self.assertIsNone(row["bindings"][0]["evaluated_world"])
        reject(
            "curve.configure", curves=[dict(name="Guide", settings=dict(resolution=4))]
        )
        call(
            "curve.configure",
            curves=[dict(name="Guide", bindings=[dict(spline=0, target=target)])],
        )
        self.assertTrue(self.inspect()["valid"])

    def test_surface_follows_native_armature_modifier(self) -> None:
        arm = self.structure()
        mesh = self.surface()
        group = mesh.vertex_groups.new(name="A")
        group.add([0, 1, 2], 1, "REPLACE")
        modifier = mesh.modifiers.new("Deform", "ARMATURE")
        modifier.object = arm
        self.create(
            bindings=[
                dict(
                    spline=0,
                    target=anchor(
                        "surface", "Surface", face=0, barycentric=[0, 0.5, 0.5]
                    ),
                )
            ]
        )
        call(
            "armature.pose",
            object_name="Target",
            bones=[dict(name="A", rotation=[0.5, 0, 0])],
        )
        row = self.inspect()
        self.assertTrue(row["valid"], row)
        self.vector(
            row["bindings"][0]["evaluated_world"], [1, math.cos(0.5), math.sin(0.5)]
        )
        modifier = mesh.modifiers.new("Topology", "SUBSURF")
        modifier.levels = 1
        bpy.context.view_layer.update()
        self.assertFalse(self.inspect()["valid"])

    def test_edits_rebind_and_batch_preflight(self) -> None:
        self.target()
        self.create(bindings=[dict(spline=0, target=anchor())])
        before = self.allocations()
        reject(
            "curve.configure",
            curves=[
                dict(
                    name="Guide",
                    ranges=[dict(spline=0, start=0, points=[dict(co=[2, 0, 0])])],
                )
            ],
        )
        reject(
            "curve.configure",
            curves=[
                dict(name="Guide", settings=dict(resolution=4)),
                dict(name="Missing", bindings=[]),
            ],
        )
        self.assertEqual(before, self.allocations())
        call(
            "curve.configure",
            curves=[dict(name="Guide", splines=[spline("NURBS")], bindings=[])],
        )
        self.assertEqual(self.inspect()["splines"][0]["type"], "NURBS")

    def test_transaction_rollbacks_release_staged_resources(self) -> None:
        self.create()
        before = self.allocations()
        original = self.inspect(samples=4)
        with patch.object(
            curves, "result", side_effect=RuntimeError("Injected finalization")
        ):
            reject("curve.create", curves=[dict(name="New", splines=[spline()])])
            reject(
                "curve.configure",
                curves=[dict(name="Guide", splines=[spline("BEZIER")])],
            )
        self.assertEqual(before, self.allocations())
        self.assertEqual(self.inspect(samples=4), original)

    def test_protect_external_graph_and_helpers(self) -> None:
        self.structure()
        obj = self.create(bindings=[dict(spline=0, target=anchor("bone", bone="A"))])
        helper = obj["tyvrana_curve_target_0"]
        external = bpy.data.objects.new("External", None)
        bpy.context.scene.collection.objects.link(external)
        external.parent = helper
        reject("curve.remove", names=["Guide"])
        reject("curve.configure", curves=[dict(name="Guide", bindings=[])])
        self.assertIn(helper.name, bpy.data.objects)
        external.parent = None
        group = obj.modifiers[0].node_group
        node = next(n for n in group.nodes if n.bl_idname == "FunctionNodeCompare")
        node.operation = "NOT_EQUAL"
        reject("curve.inspect", names=["Guide"])
        reject("curve.configure", curves=[dict(name="Guide", bindings=[])])

    def test_cycles_shared_data_and_generic_delete_are_rejected(self) -> None:
        target = self.target()
        obj = self.create()
        target.parent = obj
        reject(
            "curve.configure",
            curves=[dict(name="Guide", bindings=[dict(spline=0, target=anchor())])],
        )
        reject("object.delete", name="Guide")
        copy = obj.copy()
        bpy.context.scene.collection.objects.link(copy)
        reject(
            "curve.configure", curves=[dict(name="Guide", settings=dict(resolution=3))]
        )
        self.assertEqual(obj.data.users, 2)

    def test_world_authoring_context_and_compact_batch(self) -> None:
        target = self.target()
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        call(
            "curve.create",
            curves=[
                dict(
                    name=f"G{i:02}",
                    location=[i, 0, 0],
                    space="world",
                    splines=[spline(coords=[[i, 0, 0], [i, 1, 0]])],
                )
                for i in range(32)
            ],
            sample_limit=0,
        )
        self.assertEqual(bpy.context.view_layer.objects.active, target)
        self.assertEqual(bpy.context.selected_objects, [target])
        result = call("curve.inspect", prefix="G", limit=4)
        self.assertEqual(len(result["curves"]), 4)
        self.assertEqual(result["page"]["matched_count"], 32)
        self.vector(
            self.inspect("G07", space="local")["splines"][0]["start"], [0, 0, 0]
        )

    def test_bezier_and_nurbs_attachment_control_indices(self) -> None:
        target = self.target()
        for kind in ["BEZIER", "NURBS"]:
            self.create(
                kind,
                splines=[spline(kind)],
                bindings=[dict(spline=0, point=1, target=anchor(), offset=[0, 1, 0])],
            )
            target.location = (1, 2, 3)
            bpy.context.view_layer.update()
            self.assertTrue(self.inspect(kind)["valid"])
            self.vector(self.inspect(kind)["bindings"][0]["evaluated_world"], [1, 3, 3])

    def test_native_graph_evaluates_without_inspection(self) -> None:
        target = self.target()
        obj = self.create(bindings=[dict(spline=0, target=anchor())])
        target.location = (2, 3, 4)
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            self.vector(mesh.vertices[0].co, [2, 3, 4])
        finally:
            evaluated.to_mesh_clear()

    def test_batch_dependency_cycle_is_preflighted(self) -> None:
        self.create("A")
        self.create("B")
        before = self.allocations()
        reject(
            "curve.configure",
            curves=[
                dict(name="A", bindings=[dict(spline=0, target=anchor(name="B"))]),
                dict(name="B", bindings=[dict(spline=0, target=anchor(name="A"))]),
            ],
        )
        self.assertEqual(before, self.allocations())
        self.assertEqual(self.inspect("A")["bindings"], [])

    def test_missing_targets_bad_profiles_and_singular_transforms(self) -> None:
        before = self.allocations()
        cases: list[dict[str, Any]] = [
            dict(bindings=[dict(spline=0, target=anchor())]),
            dict(settings=dict(profile=dict(kind="object", object="Missing"))),
        ]
        for spec in cases:
            reject(
                "curve.create", curves=[dict(name="Bad", splines=[spline()], **spec)]
            )
            self.assertEqual(before, self.allocations())
        target = self.target()
        target.scale = (0, 0, 0)
        bpy.context.view_layer.update()
        reject(
            "curve.create",
            curves=[
                dict(
                    name="Bad",
                    splines=[spline()],
                    bindings=[dict(spline=0, target=anchor())],
                )
            ],
        )
        self.assertNotIn("Bad", bpy.data.objects)

    def test_external_topology_and_nonplanar_profile_are_visible(self) -> None:
        obj = self.create()
        obj.data.splines[0].points.add(1)
        reject("curve.inspect", names=["Guide"])
        profile = self.create(
            "Profile",
            splines=[spline(coords=[[-1, -1, 0], [1, -1, 0], [0, 1, 0]], cyclic=True)],
        )
        self.create(
            "Swept", settings=dict(profile=dict(kind="object", object="Profile"))
        )
        profile.data.splines[0].points[0].co.z = 1
        bpy.context.view_layer.update()
        self.assertFalse(self.inspect("Swept")["valid"])

    def test_topology_replacement_multiple_splines_and_world_handles(self) -> None:
        self.create()
        call(
            "curve.configure",
            curves=[dict(name="Guide", splines=[spline("BEZIER"), spline("NURBS")])],
        )
        self.assertEqual(self.inspect()["spline_count"], 2)
        call("curve.configure", curves=[dict(name="Guide", splines=[spline()])])
        self.assertEqual(self.inspect()["spline_count"], 1)
        spec = spline("BEZIER", [[3, 0, 0], [3, 2, 0]])
        for p in spec["points"]:
            p.update(
                handle_type="ALIGNED", left=[2, p["co"][1], 0], right=[4, p["co"][1], 0]
            )
        self.create("World", splines=[spec], space="world", location=[3, 0, 0])
        point = self.inspect("World", point_limit=1)["splines"][0]["points"][0]
        self.vector(point["co"], [0, 0, 0])
        self.vector(point["left"], [-1, 0, 0])

    def test_nonuniform_curve_transform_and_external_modifier_preserved(self) -> None:
        obj = self.create()
        obj.scale = (1, 2, 1)
        bpy.context.view_layer.update()
        reject("curve.inspect", names=["Guide"])
        obj.scale = (1, 1, 1)
        bpy.context.view_layer.update()
        modifier = obj.modifiers.new("External", "SOLIDIFY")
        reject("curve.configure", curves=[dict(name="Guide", bindings=[])])
        self.assertEqual(obj.modifiers[1], modifier)

    def test_bone_rename_follows_native_constraint(self) -> None:
        target = self.structure()
        self.create(bindings=[dict(spline=0, target=anchor("bone", bone="A"))])
        target.data.bones["A"].name = "Renamed"
        bpy.context.view_layer.update()
        self.assertEqual(self.inspect()["bindings"][0]["target"]["bone"], "Renamed")

    def test_partial_native_deletion_reports_progress(self) -> None:
        self.create("A")
        self.create("B")
        original = curves.organization.remove_object

        def remove(obj: Any) -> None:
            if obj.name == "B":
                raise RuntimeError("Injected deletion")
            original(obj)

        with patch.object(curves.organization, "remove_object", side_effect=remove):
            error = reject("curve.remove", names=["A", "B"])
        self.assertEqual(error.code, "curve_remove_failed")
        self.assertEqual(error.details["removed"], ["A"])
        self.assertEqual(error.details["remaining"], ["B"])
        self.assertTrue(self.inspect("B")["valid"])

    def test_binding_preflight_accepts_owned_curves(self) -> None:
        self.create(settings=dict(profile=dict(kind="circle", radius=0.1)))
        self.structure()
        self.surface()
        row = call(
            "armature.bind",
            object_name="Surface",
            armature_object="Target",
            weights=dict(
                method="explicit",
                vertices=[
                    dict(vertex=i, influences=[dict(bone="A", weight=1)])
                    for i in range(3)
                ],
            ),
        )
        self.assertEqual(row["unweighted_vertex_count"], 0)

    def test_sweep_radius_and_sampled_tilt_match_native_geometry(self) -> None:
        spec = spline(coords=[[0, 0, 0], [0, 1, 0], [0, 2, 0]])
        spec["points"][1].update(radius=2, tilt=0.5)
        obj = self.create(
            splines=[spec],
            settings=dict(profile=dict(kind="circle", radius=0.1, resolution=16)),
        )
        row = self.inspect(samples=3)
        middle = row["splines"][0]["samples"][1]
        self.assertAlmostEqual(middle["radius"], 2, places=5)
        self.assertAlmostEqual(middle["tilt"], 0.5, places=5)
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            rings = [
                [v.co for v in mesh.vertices if abs(v.co.y - y) < 1e-5]
                for y in range(3)
            ]
            for ring, radius in zip(rings, [0.1, 0.2, 0.1], strict=True):
                self.assertEqual(len(ring), 16)
                for p in ring:
                    self.assertAlmostEqual(math.hypot(p.x, p.z), radius, places=5)
        finally:
            evaluated.to_mesh_clear()
        neutral = self.create(
            "Neutral", splines=[spline(coords=[[0, 0, 0], [0, 1, 0], [0, 2, 0]])]
        )
        normal = Vector(
            self.inspect(neutral.name, samples=3)["splines"][0]["samples"][1]["normal"]
        )
        self.assertAlmostEqual(
            normal.dot(Vector(middle["normal"])), math.cos(0.5), places=5
        )

    def test_evaluated_sampling_is_repeatable_and_bounded(self) -> None:
        self.create(splines=[spline("BEZIER")])
        before = self.allocations()
        first = self.inspect(samples=64, point_limit=1, point_offset=1)
        second = self.inspect(samples=64, point_limit=1, point_offset=1)
        self.assertEqual(first, second)
        self.assertEqual(len(first["splines"][0]["samples"]), 64)
        self.assertEqual(len(first["splines"][0]["points"]), 1)
        self.assertEqual(before, self.allocations())


suite = unittest.defaultTestLoader.loadTestsFromTestCase(CurveTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Native curve checks failed")
print("CURVE_NATIVE_PASSED", result.testsRun)
