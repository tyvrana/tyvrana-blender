"""Native surface QA against independently known distances and failures."""

import importlib
import math
import random
import sys
import unittest
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

qa = importlib.import_module(PACKAGE + "geometry_qa")


class GeometryTests(TopologyTests):
    def test_local_inversion_hidden_by_positive_global_volume(self) -> None:
        points = [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
            [10, 0, 0],
            [14, 0, 0],
            [10, 4, 0],
            [10, 0, 4],
        ]
        faces = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
        self.call(
            "mesh.create",
            name="Local",
            vertices=points,
            faces=faces + [[i + 4 for i in f] for f in faces],
        )
        self.call("volume.snapshot", objects=[dict(source="Local", name="LocalRest")])
        obj = bpy.data.objects["Local"]
        obj.data.vertices[3].co.z = -1
        obj.data.update()
        result = self.call(
            "geometry.inspect",
            objects=[dict(object_name="Local", reference="LocalRest")],
        )
        row = result["samples"][0]["objects"][0]
        self.assertFalse(row["signed_volume_reversed"])
        self.assertGreater(row["signed_volume"], 0)
        self.assertEqual(row["negative_jacobians"], 4)
        self.assertEqual(row["jacobian_samples"], 8)
        self.assertAlmostEqual(row["minimum_jacobian"], -1, places=5)
        self.assertEqual(set(row["worst_jacobian_vertices"]), {0, 1, 2, 3})

    def test_planar_neighborhood_does_not_claim_volume_orientation(self) -> None:
        self.call("object.create_primitive", primitive="plane", name="Flat")
        self.call("volume.snapshot", objects=[dict(source="Flat", name="FlatRest")])
        report = self.call(
            "geometry.inspect", objects=[dict(object_name="Flat", reference="FlatRest")]
        )
        row = report["samples"][0]["objects"][0]
        self.assertEqual(row["jacobian_samples"], 0)
        self.assertEqual(row["jacobian_unavailable"], 4)
        self.assertIsNone(row["minimum_jacobian"])

    def cube(self, name: str, x: float = 0, scale: float = 1) -> Any:
        self.call(
            "object.create_primitive",
            primitive="cube",
            name=name,
            location=[x, 0, 0],
            scale=[scale] * 3,
        )
        return bpy.data.objects[name]

    def pair(self, **args: Any) -> Any:
        return self.call("geometry.inspect", pairs=[dict(left="A", right="B")], **args)[
            "samples"
        ][0]["pairs"][0]

    def test_separation_surface_contact_and_containment(self) -> None:
        self.cube("A")
        b = self.cube("B", 3)
        result = self.pair()
        self.assertAlmostEqual(result["minimum_distance"], 1)
        self.assertEqual(result["left_representatives_inside_right"], 0)
        self.assertEqual(result["contact_triangle_pairs"], 0)
        b.location.x = 1.5
        bpy.context.view_layer.update()
        result = self.pair()
        self.assertEqual(result["minimum_distance"], 0)
        self.assertGreater(result["contact_triangle_pairs"], 0)
        b.location.x = 0
        b.scale = (0.25,) * 3
        bpy.context.view_layer.update()
        result = self.pair()
        self.assertAlmostEqual(result["minimum_distance"], 0.75)
        self.assertEqual(result["right_representatives_inside_left"], 1)
        self.assertEqual(result["left_representatives_inside_right"], 0)

    def test_triangle_crossing_edge_minimum_and_small_scale(self) -> None:
        for scale in (1, 0.0001, 1000):
            a = [Vector(p) * scale for p in [(0, 0, 0), (2, 0, 0), (0, 2, 0)]]
            b = [
                Vector(p) * scale for p in [(0.5, 0.5, -1), (0.5, 0.5, 1), (1, 0.5, 0)]
            ]
            self.assertEqual(qa.triangle_distance(a, b)[0], 0)
        # Closest points lie inside both edges, not at either mesh's vertices.
        a = [Vector(p) for p in [(-2, 0, 0), (2, 0, 0), (0, -1, 0)]]
        b = [Vector(p) for p in [(0, -2, 1), (0, 2, 1), (1, 0, 2)]]
        distance, p, q = qa.triangle_distance(a, b)
        self.assertAlmostEqual(distance, 1)
        self.assertAlmostEqual((q - p).length, 1)

    def test_contact_exemption_and_self_intersection(self) -> None:
        vertices = [
            [-1, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0.3, -1],
            [0, 0.3, 1],
            [0.4, 0.3, 0],
        ]
        self.call(
            "mesh.create", name="Cross", vertices=vertices, faces=[[0, 1, 2], [3, 4, 5]]
        )
        result = self.call(
            "geometry.inspect",
            objects=[dict(object_name="Cross", self_intersection=True)],
        )
        row = result["samples"][0]["objects"][0]
        self.assertEqual(row["self_contact_triangle_pairs"], 1)
        for name in ["A", "B"]:
            self.call("object.create_primitive", primitive="plane", name=name)
        self.assertGreater(self.pair()["contact_triangle_pairs"], 0)
        exempt = self.call(
            "geometry.inspect",
            pairs=[
                dict(
                    left="A",
                    right="B",
                    exemptions=[dict(left_faces=[0], right_faces=[0])],
                )
            ],
        )
        row = exempt["samples"][0]["pairs"][0]
        self.assertIsNone(row["minimum_distance"])
        self.assertEqual(row["contact_triangle_pairs"], 0)
        self.assertGreater(row["exempt_triangle_pairs"], 0)

    def test_reference_diagnostics_do_not_confuse_object_rotation(self) -> None:
        a = self.cube("A")
        self.call("volume.snapshot", objects=[dict(source="A", name="Rest")])
        a.rotation_euler = (math.pi, 0, 0)
        bpy.context.view_layer.update()
        query = [dict(object_name="A", reference="Rest", self_intersection=True)]
        row = self.call("geometry.inspect", objects=query)["samples"][0]["objects"][0]
        self.assertEqual(row["normal_reversed_triangles"], 0)
        self.assertEqual(row["negative_jacobians"], 0)
        self.assertAlmostEqual(row["minimum_jacobian"], 1, places=5)
        self.assertEqual(row["self_contact_triangle_pairs"], 0)
        for vertex in a.data.vertices:
            vertex.co.x *= -1
        a.data.update()
        row = self.call("geometry.inspect", objects=query)["samples"][0]["objects"][0]
        self.assertTrue(row["signed_volume_reversed"])
        self.assertGreater(row["normal_reversed_triangles"], 0)
        for vertex in a.data.vertices:
            vertex.co.z = 0
        a.data.update()
        row = self.call("geometry.inspect", objects=query)["samples"][0]["objects"][0]
        self.assertGreater(row["collapsed_triangles"], 0)
        self.assertGreater(row["degenerate_triangles"], 0)

    def test_sampling_and_failure_restore_frame(self) -> None:
        self.cube("A")
        b = self.cube("B", 4)
        for frame, x in [(1, 4), (2, 2), (3, 4)]:
            b.location.x = x
            b.keyframe_insert("location", frame=frame)
        scene = bpy.context.scene
        scene.frame_set(7, subframe=0.25)
        args = dict(pairs=[dict(left="A", right="B")], frames=[1, 2, 3])
        result = self.call("geometry.inspect", **args)
        self.assertEqual(result["worst_frame"], 2)
        self.assertEqual(result["minimum_distance"], 0)
        self.assertEqual(scene.frame_current, 7)
        self.assertAlmostEqual(scene.frame_subframe, 0.25)
        error = self.error("geometry.inspect", **args, max_triangle_tests=1)
        self.assertIn("budget", error.message)
        self.assertEqual(scene.frame_current, 7)
        self.assertAlmostEqual(scene.frame_subframe, 0.25)

    def test_tree_matches_exhaustive_triangle_candidates(self) -> None:
        random.seed(71)
        for name, shift in [("A", 0), ("B", 2)]:
            vertices = [
                [random.random() + shift, random.random(), random.random()]
                for _ in range(60)
            ]
            self.call(
                "mesh.create",
                name=name,
                vertices=vertices,
                faces=[[i, i + 1, i + 2] for i in range(0, 60, 3)],
            )
        with qa.geometry.SurfaceCache() as cache:
            a, b = [qa.Surface(cache.get(n)) for n in ("A", "B")]
            expected = min(
                qa.triangle_distance(x, y)[0] for x in a.triangles for y in b.triangles
            )
        self.assertAlmostEqual(self.pair()["minimum_distance"], expected, places=6)


if __name__ == "__main__":
    suite = unittest.TestSuite(
        GeometryTests(n) for n in GeometryTests.__dict__ if n.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("GEOMETRY_QA_NATIVE_PASSED", result.testsRun)
