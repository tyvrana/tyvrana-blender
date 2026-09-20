"""Targeted native fairing regression on isolated, deterministic surface fixtures."""

import math
import unittest
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender import operations, surface_fairing
from tyvrana_blender.blender import BlenderBackend
from tyvrana_blender.errors import OperationError


def coordinates(obj: Any) -> Any:
    points = np.empty((len(obj.data.vertices), 3))
    obj.data.vertices.foreach_get("co", points.reshape(-1))
    return points


class SurfaceFairingTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.ops.mesh.primitive_torus_add(
            major_segments=48, minor_segments=16, major_radius=1, minor_radius=0.2
        )
        self.obj = bpy.context.object
        self.obj.name = "Socket"
        self.clean = coordinates(self.obj)
        group = self.obj.vertex_groups.new(name="Rim")
        protected = self.obj.vertex_groups.new(name="Crest")
        self.selected: list[int] = []
        for vertex in self.obj.data.vertices:
            u = math.atan2(vertex.co.y, vertex.co.x)
            if abs(u) < 1.5:
                group.add([vertex.index], 1, "REPLACE")
                self.selected.append(vertex.index)
                vertex.co.z += 0.025 * math.sin(12 * u)
            if abs(u) < 0.14:
                protected.add([vertex.index], 1, "REPLACE")
        self.obj.data.update()
        self.before = coordinates(self.obj)
        self.arguments = {
            "object_name": self.obj.name,
            "type": "fair",
            "strength": 1.0,
            "iterations": 30,
            "fairing": {
                "vertex_group": "Rim",
                "protect_vertex_group": "Crest",
                "max_distance": 0.04,
                "max_work": 16000000,
            },
        }

    def call(self, **settings: Any) -> Any:
        args = {**self.arguments, "fairing": {**self.arguments["fairing"], **settings}}
        return operations.execute(
            BlenderBackend(),
            OperationRequest(
                type="operation.request",
                request_id="fairing-regression",
                operation="blender.sculpt.filter",
                arguments=args,
            ),
        )

    def test_local_regularization_preserves_opening_features_and_is_deterministic(
        self,
    ) -> None:
        original = self.obj.data.copy()
        response = self.call()
        self.assertIsInstance(response, OperationSuccess, response)
        after = coordinates(self.obj)
        outside = sorted(set(range(len(after))) - set(self.selected))
        np.testing.assert_array_equal(after[outside], self.before[outside])
        protected = [
            v.index
            for v in self.obj.data.vertices
            if any(g.group == 1 and g.weight > 0 for g in v.groups)
        ]
        np.testing.assert_array_equal(after[protected], self.before[protected])
        changed = np.linalg.norm(after - self.before, axis=1) > 0
        self.assertTrue(changed.any())
        self.assertLess(
            np.mean((after[changed, 2] - self.clean[changed, 2]) ** 2),
            np.mean((self.before[changed, 2] - self.clean[changed, 2]) ** 2),
        )
        self.assertGreater(np.linalg.norm(after[:, :2], axis=1).min(), 0.78)
        self.assertLess(np.linalg.norm(after - self.before, axis=1).max(), 0.040001)
        self.assertEqual(len(self.obj.data.polygons), 48 * 16)
        self.obj.data = original
        self.assertIsInstance(self.call(), OperationSuccess)
        np.testing.assert_array_equal(coordinates(self.obj), after)

    def test_mask_and_shared_mesh_isolation(self) -> None:
        mask = self.obj.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        mask.data[100].value = 1
        shared = bpy.data.objects.new("Shared", self.obj.data)
        bpy.context.scene.collection.objects.link(shared)
        response = self.call()
        self.assertIsInstance(response, OperationSuccess, response)
        self.assertIsNot(shared.data, self.obj.data)
        np.testing.assert_array_equal(coordinates(shared), self.before)
        np.testing.assert_array_equal(coordinates(self.obj)[100], self.before[100])

    def test_budgets_and_unsafe_geometry_leave_original_unchanged(self) -> None:
        original = self.obj.data
        for settings in ({"max_points": 5}, {"max_work": 1}, {"max_triangle_tests": 1}):
            response = self.call(**settings)
            self.assertIsInstance(response, OperationFailure, response)
            self.assertIs(self.obj.data, original)
            np.testing.assert_array_equal(coordinates(self.obj), self.before)
        with patch.object(
            surface_fairing,
            "validate_geometry",
            side_effect=OperationError("unsafe", "test rejection"),
        ):
            self.assertIsInstance(self.call(), OperationFailure)
        self.assertIs(self.obj.data, original)

    def test_publish_failure_rolls_back(self) -> None:
        original = self.obj.data

        def broken(obj: Any, staged: Any) -> None:
            obj.data = staged
            raise RuntimeError("injected publication failure")

        with patch.object(surface_fairing, "publish", side_effect=broken):
            self.assertIsInstance(self.call(), OperationFailure)
        self.assertIs(self.obj.data, original)
        np.testing.assert_array_equal(coordinates(self.obj), self.before)

    def test_thinning_and_inversion_are_rejected(self) -> None:
        from tyvrana_blender.sculpt_models import SculptFilterArguments

        args = SculptFilterArguments.model_validate(self.arguments)
        self.obj.data.calc_loop_triangles()
        triangles = np.array([t.vertices[:] for t in self.obj.data.loop_triangles])
        for factor in (0.5, -1.0):
            after = self.before.copy()
            after[:, 2] *= factor
            with self.assertRaises(OperationError):
                surface_fairing.validate_geometry(
                    self.before, after, triangles, np.ones(len(after), dtype=bool), args
                )

    def test_stable_correspondence_preserves_rigid_motion(self) -> None:
        from tyvrana_blender.sculpt_models import SculptFilterArguments

        self.obj.data.calc_loop_triangles()
        triangles = np.array([t.vertices[:] for t in self.obj.data.loop_triangles])
        after = self.before + np.array([0.01, -0.02, 0.01])
        count, ratio, _ = surface_fairing.validate_geometry(
            self.before,
            after,
            triangles,
            np.ones(len(after), dtype=bool),
            SculptFilterArguments.model_validate(self.arguments),
        )
        self.assertGreater(count, 0)
        assert ratio is not None
        self.assertAlmostEqual(ratio, 1.0, places=5)

    def test_certified_bound_is_conservative_against_independent_side_queries(
        self,
    ) -> None:
        from tyvrana_blender.sculpt_models import SculptFilterArguments

        self.obj.data.calc_loop_triangles()
        triangles = np.array([t.vertices[:] for t in self.obj.data.loop_triangles])
        after = self.before.copy()
        after[:, 2] += 0.002 * np.sin(self.before[:, 0] * 4)
        changed = np.zeros(len(after), dtype=bool)
        changed[np.linspace(0, len(after) - 1, 12, dtype=int)] = True
        original = surface_fairing.ThicknessCorrespondence.certify
        bounds = []

        def checked(instance: Any, index: int, normal: Any, *args: Any) -> Any:
            bound = original(instance, index, normal, *args)
            if bound is not None:
                old, new = instance.independent(index, normal)
                self.assertLessEqual(bound, new / old + 1e-5)
                bounds.append(bound)
            return bound

        with patch.object(surface_fairing.ThicknessCorrespondence, "certify", checked):
            surface_fairing.validate_geometry(
                self.before,
                after,
                triangles,
                changed,
                SculptFilterArguments.model_validate(self.arguments),
            )
        self.assertGreater(len(bounds), 0)

    def test_twenty_percent_thinning_rejects_and_rolls_back(self) -> None:
        bpy.ops.mesh.primitive_cube_add(size=2)
        self.obj = bpy.context.object
        for vertex in self.obj.data.vertices:
            vertex.co.z *= 0.1
        self.obj.data.update()
        self.before = coordinates(self.obj)
        group = self.obj.vertex_groups.new(name="All")
        group.add(list(range(8)), 1, "REPLACE")
        self.arguments["object_name"] = self.obj.name
        self.arguments["fairing"] = {"vertex_group": "All", "max_distance": 0.04}
        original = self.obj.data
        mesh_count = len(bpy.data.meshes)
        after = self.before.copy()
        after[:, 2] *= 0.8
        old_thickness = float(np.ptp(self.before[:, 2]))
        new_thickness = float(np.ptp(after[:, 2]))
        self.assertAlmostEqual(old_thickness, 0.2)
        self.assertAlmostEqual(new_thickness / old_thickness, 0.8)
        with patch.object(surface_fairing, "fair_points", return_value=after):
            result = self.call()
        self.assertIsInstance(result, OperationFailure, result)
        self.assertIn("max_thinning", result.error.message)
        self.assertIs(self.obj.data, original)
        self.assertEqual(len(bpy.data.meshes), mesh_count)
        np.testing.assert_array_equal(coordinates(self.obj), self.before)
        print("TRUE_THINNING", old_thickness, new_thickness, 0.8, "REJECTED")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceFairingTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("SURFACE_FAIRING_NATIVE_PASSED", result.testsRun, flush=True)
