"""Native retopology construction, correspondence, preservation and failure safety."""

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
from tyvrana_protocol import OperationFailure, OperationRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.retopo_scene import tube_data  # noqa: E402
from tests.blender.sculpt_checks import (  # noqa: E402
    NativeCase,
    adapter,
    authored,
    operations,
)

assert adapter.__package__ is not None
retopo = importlib.import_module(adapter.__package__ + ".retopo")
geometry = importlib.import_module(adapter.__package__ + ".retopo_geometry")
VERTICES = {"mode": "all", "domain": "vertex"}


def indices(domain: str, values: list[int]) -> dict[str, Any]:
    return {"mode": "indices", "domain": domain, "indices": values}


class RetopoCase(NativeCase):
    target: Any

    def response(self, op: str, **args: Any) -> Any:
        args = {"source_object": self.obj.name, **args}
        if op != "create_target":
            args = {"target_object": self.target.name, **args}
        return operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="retopo-test",
                operation="blender.retopo." + op,
                arguments=args,
            ),
        )

    def create(self) -> Any:
        result = self.call("create_target", name="Cage")
        self.target = bpy.data.objects[result["target_object"]]
        return result

    def seed(self, **args: Any) -> Any:
        return self.call(
            "seed_patch",
            **{
                "center": [0, 0, 1],
                "tangent_direction": [1, 0, 0],
                "width": 0.6,
                "height": 0.6,
                **args,
            },
        )

    def failed_unchanged(self, op: str, **args: Any) -> Any:
        before, original = authored(self.target), self.target.data
        source = authored(self.obj)
        counts = len(bpy.data.meshes), len(bpy.data.objects)
        result = self.response(op, **args)
        self.assertIsInstance(result, OperationFailure, str(result))
        self.assertEqual(self.target.data, original)
        self.assertEqual(authored(self.target), before)
        self.assertEqual(authored(self.obj), source)
        self.assertEqual(counts, (len(bpy.data.meshes), len(bpy.data.objects)))
        return result

    def grid(self, n: int = 4) -> None:
        self.create()
        self.seed(u_segments=n, v_segments=n)

    def ring_fixture(self) -> tuple[Any, Any]:
        tube_data(self.obj, 64, [-1 + i / 16 for i in range(33)], list(range(32)))
        self.create()
        tube_data(self.target, 8, [-0.8, -0.4, 0.4, 0.8], [0, 2])
        self.call("project", selector=VERTICES)
        loops = self.call("inspect")["target"]["boundaries"]
        a = next(b for b in loops if abs(b["bounds_min"][2] + 0.4) < 0.01)
        b = next(b for b in loops if abs(b["bounds_min"][2] - 0.4) < 0.01)
        return indices("edge", a["edge_indices"]["indices"]), indices(
            "edge", b["edge_indices"]["indices"]
        )


class NativeRetopoTests(RetopoCase):
    def asymmetric_half_patch(self, transformed: bool = False) -> None:
        self.sphere()
        for vertex in self.obj.data.vertices:
            vertex.co.x += 0.25
        self.obj.data.update()
        if transformed:
            self.obj.location = (2, -3, 4)
            self.obj.rotation_euler = (0.3, -0.2, 0.5)
            self.obj.scale = (2, 0.8, 1.5)
        bpy.context.view_layer.update()
        self.create()
        self.target.data.from_pydata(
            [(x * 0.2, (y - 1) * 0.2, 1.15) for y in range(3) for x in range(3)],
            [],
            [(a, a + 1, a + 4, a + 3) for a in (0, 1, 3, 4)],
        )
        self.target.data.update()
        self.target.modifiers.new("Symmetry", "MIRROR")

    def test_mirror_plane_project_fits_asymmetric_surface_in_world_space(self) -> None:
        for transformed in (False, True):
            with self.subTest(transformed=transformed):
                self.setUp()
                self.asymmetric_half_patch(transformed)
                before = authored(self.obj)
                result = self.call("project", selector=VERTICES)
                self.assertLess(
                    result["correspondence_after"]["vertices"]["max_distance"], 2e-6
                )
                for i in (0, 3, 6):
                    self.assertEqual(self.target.data.vertices[i].co.x, 0)
                self.assertEqual(authored(self.obj), before)
                self.assertEqual(len(self.target.modifiers), 1)
                self.assertEqual(
                    self.call("inspect")["evaluated_target"]["vertex_count"], 15
                )
                self.call("project", selector=VERTICES, surface_offset=0.01)
                for i in (0, 3, 6):
                    self.assertEqual(self.target.data.vertices[i].co.x, 0)

    def test_mirror_plane_extrusion_and_boundary_relax_keep_joined_seam(self) -> None:
        self.asymmetric_half_patch()
        self.call("project", selector=VERTICES)
        edges = [
            e.index
            for e in self.target.data.edges
            if all(i in (6, 7, 8) for i in e.vertices)
        ]
        result = self.call(
            "extrude_boundary", selector=indices("edge", edges), offset=[0, 0.2, -0.04]
        )
        self.assertEqual(result["created"]["faces"], 2)
        seams = [v.index for v in self.target.data.vertices if v.co.x == 0]
        self.assertEqual(len(seams), 4)
        result = self.call(
            "relax", selector=VERTICES, preserve_boundary=False, iterations=3
        )
        self.assertLess(
            result["correspondence_after"]["vertices"]["max_distance"], 1e-6
        )
        for i in seams:
            self.assertEqual(self.target.data.vertices[i].co.x, 0)
        self.assertEqual(self.call("inspect")["evaluated_target"]["vertex_count"], 20)

    def test_mirror_plane_projection_failures_preserve_geometry(self) -> None:
        self.asymmetric_half_patch()
        self.failed_unchanged(
            "project", selector=VERTICES, max_projection_distance=0.01
        )
        with patch.object(geometry, "MAX_PLANE_PROJECTION_WORK", 0):
            result = self.failed_unchanged("project", selector=VERTICES)
            self.assertEqual(result.error.code, "retopo_geometry_limit")
        for vertex in self.obj.data.vertices:
            vertex.co.x += 2
        self.obj.data.update()
        result = self.failed_unchanged("project", selector=VERTICES)
        self.assertEqual(result.error.code, "retopo_projection_failed")

    def test_create_empty_independent_aligned_named_and_delete(self) -> None:
        self.sphere()
        self.obj.location = (2, 3, 4)
        self.obj.rotation_euler = (0.2, 0.3, 0.4)
        self.obj.scale = (2, 1, 0.7)
        bpy.context.view_layer.update()
        before = authored(self.obj)
        active = bpy.context.object
        result = self.create()
        self.assertEqual(result["mesh"]["vertex_count"], 0)
        self.assertLess(
            max(
                abs(result["matrix_world"][i][j] - self.obj.matrix_world[i][j])
                for i in range(4)
                for j in range(4)
            ),
            1e-6,
        )
        self.assertNotEqual(self.target.data, self.obj.data)
        self.assertFalse(self.target.data.uv_layers)
        self.assertFalse(self.target.data.materials)
        self.assertEqual(bpy.context.object, active)
        second = self.call("create_target", name="Cage")
        self.assertEqual(second["target_object"], "Cage.001")
        bpy.data.objects.remove(self.target, do_unlink=True)
        self.assertEqual(before, authored(self.obj))

    def test_seed_quad_grid_orientation_and_curved_source(self) -> None:
        self.sphere()
        before = authored(self.obj)
        self.create()
        result = self.seed(u_segments=3, v_segments=2, tangent_direction=[0, 1, 0])
        self.assertEqual(result["created"], {"vertices": 12, "edges": 17, "faces": 6})
        self.assertTrue(result["indices_invalidated"])
        self.assertGreater(result["patch"]["tangent_u_world"][1], 0.98)
        self.assertLess(
            result["correspondence_after"]["vertices"]["max_distance"], 1e-6
        )
        self.assertGreater(
            result["correspondence_after"]["face_normal_dot"]["min"], 0.97
        )
        self.assertEqual(result["after"]["quad_count"], 6)
        self.assertEqual(before, authored(self.obj))

    def test_seed_orientation_projection_and_fold_failure_atomic(self) -> None:
        self.create()
        for extra in (
            {"tangent_direction": [0, 0, 1]},
            {"center": [0, 0, 9], "max_projection_distance": 0.1},
            {"width": 100, "height": 100},
        ):
            with self.subTest(extra=extra):
                self.failed_unchanged(
                    "seed_patch",
                    **{
                        "center": [0, 0, 1],
                        "tangent_direction": [1, 0, 0],
                        "width": 0.5,
                        "height": 0.5,
                        **extra,
                    },
                )

    def test_project_selection_offsets_world_units_and_stable_indices(self) -> None:
        self.obj.scale = (2, 3, 0.5)
        bpy.context.view_layer.update()
        self.grid(2)
        for v in self.target.data.vertices:
            v.co.z += 0.4
        self.target.data.update()
        before = authored(self.target)
        result = self.call(
            "project", selector=indices("vertex", [0, 1, 2]), surface_offset=0.05
        )
        self.assertEqual(result["selected_count"], 3)
        self.assertEqual(result["moved_count"], 3)
        self.assertFalse(result["indices_invalidated"])
        self.assertEqual(authored(self.target)[1:3], before[1:3])
        self.assertEqual(authored(self.target)[0][3:], before[0][3:])
        for v in self.target.data.vertices[:3]:
            self.assertAlmostEqual((self.target.matrix_world @ v.co).z, 0.55, places=6)
        self.call("project", selector=VERTICES, surface_offset=-0.02)
        self.assertAlmostEqual(
            self.call("inspect")["authored_correspondence"]["vertices"]["rms_distance"],
            0.02,
            places=6,
        )

    def test_project_distance_failure_is_atomic(self) -> None:
        self.grid(1)
        self.target.data.vertices[3].co.z += 2
        self.target.data.update()
        self.failed_unchanged("project", selector=VERTICES, max_projection_distance=0.1)

    def test_relax_reduces_spacing_variation_preserves_boundary(self) -> None:
        self.grid(4)
        for v in self.target.data.vertices:
            if abs(v.co.x) < 0.29 and abs(v.co.y) < 0.29:
                v.co.x += 0.065 * math.sin(v.index * 3)
                v.co.y += 0.04 * math.cos(v.index * 2)
        self.target.data.update()
        before = authored(self.target)
        boundary = self.call("inspect")["target"]["boundaries"][0]["vertex_indices"][
            "indices"
        ]
        result = self.call("relax", selector=VERTICES, iterations=12, factor=0.5)
        self.assertLess(
            result["after"]["edge_length"]["coefficient_of_variation"],
            result["before"]["edge_length"]["coefficient_of_variation"] * 0.2,
        )
        self.assertEqual(authored(self.target)[1:3], before[1:3])
        for i in boundary:
            self.assertEqual(tuple(self.target.data.vertices[i].co), before[0][i])
        self.assertLess(
            result["correspondence_after"]["vertices"]["max_distance"], 1e-6
        )
        self.assertEqual(result["after"]["degenerate_face_count"], 0)

    def test_relax_curved_source_reprojects_and_optional_boundary_moves(self) -> None:
        self.sphere()
        self.grid(3)
        result = self.call(
            "relax", selector=VERTICES, preserve_boundary=False, iterations=3
        )
        self.assertGreater(result["moved_count"], 4)
        self.assertLess(
            result["correspondence_after"]["vertices"]["max_distance"], 1e-6
        )
        self.assertEqual(result["after"]["quad_count"], 9)

    def test_extrude_edge_and_chain_preserves_original_patch(self) -> None:
        for n in (1, 3):
            with self.subTest(n=n):
                self.setUp()
                self.grid(n)
                selected = [
                    e.index
                    for e in self.target.data.edges
                    if all(self.target.data.vertices[i].co.y > 0.29 for i in e.vertices)
                ]
                before = authored(self.target)
                result = self.call(
                    "extrude_boundary",
                    selector=indices("edge", selected),
                    offset=[0, 0.25, 0],
                )
                self.assertEqual(result["created"]["faces"], n)
                self.assertEqual(result["created"]["vertices"], n + 1)
                self.assertEqual(result["after"]["quad_count"], n * n + n)
                self.assertEqual(authored(self.target)[0][: len(before[0])], before[0])
                self.assertLess(
                    result["correspondence_after"]["vertices"]["max_distance"], 1e-6
                )

    def test_extrude_invalid_internal_disconnected_and_folded(self) -> None:
        self.grid(2)
        internal = next(
            e.index
            for e in self.target.data.edges
            if all(abs(self.target.data.vertices[i].co.x) < 0.01 for i in e.vertices)
        )
        self.failed_unchanged(
            "extrude_boundary", selector=indices("edge", [internal]), offset=[0, 0.2, 0]
        )
        top = [
            e.index
            for e in self.target.data.edges
            if all(self.target.data.vertices[i].co.y > 0.29 for i in e.vertices)
        ]
        bottom = [
            e.index
            for e in self.target.data.edges
            if all(self.target.data.vertices[i].co.y < -0.29 for i in e.vertices)
        ]
        self.failed_unchanged(
            "extrude_boundary",
            selector=indices("edge", [top[0], bottom[0]]),
            offset=[0, 0.2, 0],
        )
        self.failed_unchanged(
            "extrude_boundary", selector=indices("edge", top), offset=[0, -0.2, 0]
        )

    def test_bridge_native_equal_loops_with_subdivided_bands(self) -> None:
        for segments in (1, 3):
            with self.subTest(segments=segments):
                self.setUp()
                a, b = self.ring_fixture()
                self.target.data.vertices[0].select = True
                source, before = authored(self.obj), authored(self.target)
                result = self.call(
                    "bridge_loops", loop_a=a, loop_b=b, segments=segments
                )
                self.assertEqual(result["created"]["faces"], 8 * segments)
                self.assertEqual(result["created"]["vertices"], 8 * (segments - 1))
                self.assertEqual(result["after"]["boundary_loop_count"], 2)
                self.assertEqual(result["after"]["non_manifold_edge_count"], 0)
                self.assertEqual(result["after"]["inconsistent_winding_edge_count"], 0)
                self.assertLess(
                    result["correspondence_after"]["vertices"]["max_distance"], 1e-6
                )
                self.assertEqual(authored(self.target)[0][:32], before[0])
                self.assertTrue(self.target.data.vertices[0].select)
                self.assertEqual(authored(self.obj), source)

    def test_bridge_bad_pairs_twist_and_ambiguity_are_atomic(self) -> None:
        a, b = self.ring_fixture()
        self.failed_unchanged("bridge_loops", loop_a=a, loop_b=a)
        self.failed_unchanged("bridge_loops", loop_a=a, loop_b=b, twist=4)
        self.failed_unchanged(
            "bridge_loops", loop_a=a, loop_b=indices("edge", b["indices"][:-1])
        )
        for v in self.target.data.vertices:
            if v.co.z > 0:
                x, y = v.co.x, v.co.y
                angle = math.pi / 8
                v.co.x, v.co.y = (
                    x * math.cos(angle) - y * math.sin(angle),
                    x * math.sin(angle) + y * math.cos(angle),
                )
        self.target.data.update()
        self.failed_unchanged("bridge_loops", loop_a=a, loop_b=b)

    def test_spatially_intersecting_loops_are_rejected(self) -> None:
        a, b = self.ring_fixture()
        for vertex in self.target.data.vertices:
            if vertex.index >= 16:
                vertex.co.x += 0.6
                vertex.co.z = -abs(vertex.co.z)
        self.target.data.update()
        result = self.failed_unchanged("bridge_loops", loop_a=a, loop_b=b)
        self.assertEqual(result.error.code, "retopo_boundary_invalid")

    def test_source_driver_dependency_on_target_rejected(self) -> None:
        self.grid(1)
        driver = self.obj.driver_add("location", 0).driver
        variable = driver.variables.new()
        variable.name = "position"
        variable.targets[0].id = self.target
        variable.targets[0].data_path = "location.x"
        driver.expression = "position"
        result = self.failed_unchanged("project", selector=VERTICES)
        self.assertEqual(result.error.code, "retopo_dependency_invalid")

    def test_shared_target_isolation_and_material_selection_preservation(self) -> None:
        self.grid(2)
        mat = bpy.data.materials.new("Cage material")
        self.target.data.materials.append(mat)
        self.target.data.edges[0].use_seam = True
        self.target.data.vertices[0].select = True
        sibling = self.target.copy()
        bpy.context.scene.collection.objects.link(sibling)
        original, before = self.target.data, authored(sibling)
        result = self.call("project", selector=VERTICES, surface_offset=0.01)
        self.assertTrue(result["mesh_isolated"])
        self.assertEqual(sibling.data, original)
        self.assertNotEqual(self.target.data, original)
        self.assertEqual(authored(sibling), before)
        self.assertEqual(self.target.data.materials[0], mat)
        self.assertTrue(self.target.data.edges[0].use_seam)
        self.assertTrue(self.target.data.vertices[0].select)

    def test_valuable_target_data_guards_and_inspection(self) -> None:
        for kind in (
            "uv",
            "group",
            "attribute",
            "shape",
            "animation",
            "constraint",
            "hidden",
            "normal",
        ):
            with self.subTest(kind=kind):
                self.setUp()
                self.grid(1)
                if kind == "uv":
                    self.target.data.uv_layers.new()
                elif kind == "group":
                    self.target.vertex_groups.new(name="Weights")
                elif kind == "attribute":
                    self.target.data.attributes.new("Production", "FLOAT", "POINT")
                elif kind == "shape":
                    self.target.shape_key_add(name="Basis")
                elif kind == "animation":
                    self.target.keyframe_insert("location", frame=1)
                elif kind == "constraint":
                    self.target.constraints.new("COPY_LOCATION")
                elif kind == "hidden":
                    self.target.data.vertices[0].hide = True
                else:
                    self.target.data.normals_split_custom_set(
                        [(0, 0, 1)] * len(self.target.data.loops)
                    )
                self.assertTrue(self.call("inspect")["blockers"])
                self.failed_unchanged("project", selector=VERTICES)

    def test_source_production_data_and_current_multires_are_readonly(self) -> None:
        self.sphere()
        self.obj.vertex_groups.new(name="Weights").add([0, 1], 0.75, "REPLACE")
        self.obj.shape_key_add(name="Basis")
        key = self.obj.shape_key_add(name="Detail")
        key.data[0].co *= 1.01
        key.value = 0.5
        attr = self.obj.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        attr.data[0].value = 0.8
        face_set = self.obj.data.attributes.new(".sculpt_face_set", "INT", "FACE")
        face_set.data[0].value = 4
        mod = self.obj.modifiers.new("Surface detail", "MULTIRES")
        bpy.ops.object.multires_subdivide(modifier=mod.name, mode="CATMULL_CLARK")
        self.obj.keyframe_insert("location", frame=1)
        before = authored(self.obj)
        mask = [v.value for v in self.obj.data.attributes[".sculpt_mask"].data]
        evaluated = self.obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        expected_faces = len(evaluated.data.polygons)
        self.create()
        self.seed()
        info = self.call("inspect")
        self.assertEqual(info["source"]["face_count"], expected_faces)
        self.assertGreater(expected_faces, len(self.obj.data.polygons))
        self.assertEqual(before, authored(self.obj))
        self.assertEqual(
            mask, [v.value for v in self.obj.data.attributes[".sculpt_mask"].data]
        )
        self.assertEqual(mod.total_levels, 1)
        self.assertEqual(key.value, 0.5)

    def test_source_subdivision_evaluated_level_changes_correspondence(self) -> None:
        mod = self.obj.modifiers.new("Detail", "SUBSURF")
        mod.levels = 2
        self.create()
        self.seed(width=0.3, height=0.3)
        a = self.call("inspect")
        mod.levels = 0
        bpy.context.view_layer.update()
        b = self.call("inspect")
        self.assertGreater(a["source"]["face_count"], b["source"]["face_count"])
        self.assertLess(a["authored_correspondence"]["vertices"]["rms_distance"], 1e-6)
        self.assertGreater(
            b["authored_correspondence"]["vertices"]["rms_distance"], 0.1
        )

    def test_mirror_and_shrinkwrap_authored_evaluated_separation(self) -> None:
        self.sphere()
        self.create()
        mirror = self.target.modifiers.new("Symmetry", "MIRROR")
        shrink = self.target.modifiers.new("Surface", "SHRINKWRAP")
        shrink.target = self.obj
        shrink.wrap_method = "NEAREST_SURFACEPOINT"
        source = authored(self.obj)
        self.seed(
            center=[0.5, 0, 0.866],
            tangent_direction=[0, 1, 0],
            width=0.3,
            height=0.3,
            surface_offset=0.1,
            u_segments=2,
            v_segments=2,
        )
        info = self.call("inspect")
        self.assertFalse(info["blockers"])
        self.assertEqual(info["evaluated_target"]["face_count"], 8)
        self.assertGreater(
            info["authored_correspondence"]["vertices"]["rms_distance"], 0.09
        )
        self.assertLess(
            info["evaluated_correspondence"]["vertices"]["max_distance"], 1e-5
        )
        self.call("project", selector=VERTICES)
        self.call("relax", selector=VERTICES)
        self.assertEqual(list(self.target.modifiers), [mirror, shrink])
        self.assertEqual(authored(self.obj), source)
        verts = self.target.evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        ).data.vertices
        self.assertAlmostEqual(
            min(v.co.x for v in verts), -max(v.co.x for v in verts), places=6
        )

    def test_modifier_policy_rejects_unvetted_helpers(self) -> None:
        for kind in ("clip", "axes", "wrong_source", "wrong_order", "subdivision"):
            with self.subTest(kind=kind):
                self.setUp()
                self.grid(1)
                if kind in {"clip", "axes"}:
                    mod = self.target.modifiers.new("Mirror", "MIRROR")
                    if kind == "clip":
                        mod.use_clip = True
                    else:
                        mod.use_axis[1] = True
                elif kind == "wrong_source":
                    self.target.modifiers.new("Surface", "SHRINKWRAP")
                elif kind == "wrong_order":
                    self.target.modifiers.new("Surface", "SHRINKWRAP").target = self.obj
                    self.target.modifiers.new("Mirror", "MIRROR")
                else:
                    self.target.modifiers.new("Smooth", "SUBSURF")
                self.assertTrue(self.call("inspect")["blockers"])
                self.failed_unchanged("project", selector=VERTICES)

    def test_mirror_cannot_cross_origin_plane(self) -> None:
        self.grid(1)
        self.target.modifiers.new("Mirror", "MIRROR")
        self.failed_unchanged("project", selector=VERTICES)

    def test_distance_outlier_percentile_and_wrong_region_are_spatial(self) -> None:
        self.grid(2)
        exact = self.call("inspect")["authored_correspondence"]["vertices"]
        self.assertLess(exact["rms_distance"], 1e-6)
        self.target.data.vertices[0].co.z += 0.3
        self.target.data.update()
        outlier = self.call("inspect")["authored_correspondence"]["vertices"]
        self.assertAlmostEqual(outlier["rms_distance"], 0.1, places=6)
        self.assertAlmostEqual(outlier["p95_distance"], 0.3, places=6)
        for v in self.target.data.vertices:
            v.co.z = -1
        self.target.data.update()
        opposite = self.call("inspect")["authored_correspondence"]
        self.assertLess(opposite["vertices"]["max_distance"], 1e-6)
        self.assertLess(opposite["face_normal_dot"]["max"], -0.99)

    def test_quality_mixed_faces_poles_loose_and_degenerate(self) -> None:
        self.create()
        points = [(0, 0, 1)] + [
            (0.2 * math.cos(i * math.pi / 3), 0.2 * math.sin(i * math.pi / 3), 1)
            for i in range(6)
        ]
        faces: list[tuple[int, ...]] = [(0, i + 1, (i + 1) % 6 + 1) for i in range(6)]
        points += [(0.4, 0, 1), (0.8, 0, 1), (0.8, 0.01, 1), (0.4, 0.01, 1)]
        faces += [(7, 8, 9, 10)]
        points += [
            (
                -0.7 + 0.1 * math.cos(i * 2 * math.pi / 5),
                0.1 * math.sin(i * 2 * math.pi / 5),
                1,
            )
            for i in range(5)
        ]
        faces += [tuple(range(11, 16))]
        points += [(0, 0, 2), (0, 0.1, 2), (0, 0.2, 2), (0, 0, 3), (0, 0, 4)]
        faces += [(16, 17, 18)]
        self.target.data.from_pydata(points, [(19, 20)], faces)
        self.target.data.update()
        q = self.call("inspect")["target"]
        self.assertEqual(
            (q["quad_count"], q["triangle_count"], q["ngon_count"]), (1, 7, 1)
        )
        self.assertEqual(q["valence"]["valence_6_plus"], 1)
        self.assertEqual(q["valence"]["max_valence"], 6)
        self.assertEqual(q["loose_edge_count"], 1)
        self.assertEqual(q["degenerate_face_count"], 1)
        self.assertEqual(q["extreme_aspect_ratio_count"], 1)
        self.assertEqual(q["boundary_loop_count"], 4)

    def test_nonmanifold_and_branched_boundary_diagnostics(self) -> None:
        self.create()
        self.target.data.from_pydata(
            [(0, 0, 1), (0.5, 0, 1), (0, 0.5, 1), (0, -0.5, 1), (0.5, 0.5, 1)],
            [],
            [(0, 1, 2), (1, 0, 3), (0, 1, 4)],
        )
        self.target.data.update()
        q = self.call("inspect")["target"]
        self.assertEqual(q["non_manifold_edge_count"], 1)
        self.assertEqual(q["branched_boundary_count"], 1)
        self.failed_unchanged(
            "extrude_boundary",
            selector={"mode": "boundary", "domain": "edge"},
            offset=[0, 0.1, 0],
        )

    def test_source_dependency_on_target_and_invalid_surface_rejected(self) -> None:
        self.grid(1)
        mod = self.obj.modifiers.new("Dependent", "SHRINKWRAP")
        mod.target = self.target
        self.failed_unchanged("project", selector=VERTICES)
        self.obj.modifiers.remove(mod)
        self.obj.data.clear_geometry()
        self.failed_unchanged("project", selector=VERTICES)

    def test_staging_failure_cleans_candidate_and_preserves_context(self) -> None:
        self.grid(1)
        active, selection = bpy.context.object, list(bpy.context.selected_objects)
        with patch.object(
            retopo,
            "target_budget",
            side_effect=[None, RuntimeError("candidate rejected")],
        ):
            self.failed_unchanged("project", selector=VERTICES, surface_offset=0.1)
        self.assertEqual(bpy.context.object, active)
        self.assertEqual(list(bpy.context.selected_objects), selection)
        self.assertEqual(bpy.context.mode, "OBJECT")

    def test_evaluated_mesh_and_bvh_cleanup_on_success_and_failure(self) -> None:
        evaluated = self.obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        cleared = []

        class Proxy:
            def __getattr__(self, name: str) -> Any:
                return getattr(evaluated, name)

            def to_mesh_clear(self) -> None:
                cleared.append(True)
                evaluated.to_mesh_clear()

        class Source:
            def evaluated_get(self, graph: Any) -> Any:
                return Proxy()

        for fail in (False, True):
            try:
                with geometry.evaluated_mesh(
                    Source(), bpy.context.evaluated_depsgraph_get()
                ):
                    if fail:
                        raise RuntimeError("body failure")
            except RuntimeError:
                pass
        self.assertEqual(len(cleared), 2)
        with patch.object(
            geometry.modifiers,
            "check_geometry",
            side_effect=RuntimeError("validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                with geometry.evaluated_mesh(
                    Source(), bpy.context.evaluated_depsgraph_get()
                ):
                    pass
        self.assertEqual(len(cleared), 3)
        with geometry.surface(self.obj) as (reference, _):
            self.assertIsNotNone(reference.bvh)
        self.assertIsNone(reference.bvh)
        with self.assertRaises(RuntimeError):
            with geometry.surface(self.obj) as (reference, _):
                raise RuntimeError("consumer failure")
        self.assertIsNone(reference.bvh)

    def test_geometry_work_and_evaluation_limits_preflight(self) -> None:
        self.grid(2)
        with patch.object(retopo, "MAX_RELAX_WORK", 10):
            self.failed_unchanged("relax", selector=VERTICES, iterations=2)
        with patch.object(retopo, "MAX_TARGET_ELEMENTS", 10):
            self.failed_unchanged("project", selector=VERTICES)
        self.obj.modifiers.new("Excessive", "SUBSURF").levels = 10
        with patch.object(geometry, "Surface") as build:
            self.failed_unchanged("project", selector=VERTICES)
            build.assert_not_called()

    def test_linked_target_mesh_readonly(self) -> None:
        self.grid(1)
        with tempfile.TemporaryDirectory(prefix="tyvrana-retopo-library-") as root:
            path = str(Path(root) / "mesh.blend")
            name = self.target.data.name
            bpy.data.libraries.write(path, {self.target.data})
            with bpy.data.libraries.load(path, link=True) as (_, loaded):
                loaded.meshes = [name]
            self.target.data = loaded.meshes[0]
            self.assertIn("target_read_only", self.call("inspect")["blockers"])
            self.failed_unchanged("project", selector=VERTICES)

    def test_vertex_deformation_relationship_protected(self) -> None:
        self.grid(1)
        child = bpy.data.objects.new("Dependent", None)
        bpy.context.scene.collection.objects.link(child)
        child.parent = self.target
        child.parent_type = "VERTEX"
        self.assertIn(
            "target_deformation_relationship", self.call("inspect")["blockers"]
        )
        self.failed_unchanged("project", selector=VERTICES)

    def test_main_thread_guard(self) -> None:
        self.create()
        with self.assertLogs(operations.logger, level="ERROR"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.response, "inspect").result()
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "operation_failed")


def run() -> None:
    runtime = adapter._runtime
    worker = runtime.worker if runtime else None
    adapter.unregister()
    if worker:
        assert worker.process.returncode == 0
        assert not worker.spool.root.exists()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(NativeRetopoTests)
    )
    assert not bpy.app.timers.is_registered(adapter.pump)
    if result.wasSuccessful():
        print("BLENDER_RETOPO_TESTS_PASSED", result.testsRun, flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    elif not result.wasSuccessful():
        raise RuntimeError("Native retopology checks failed")


if __name__ == "__main__":
    if bpy.app.background:
        run()
    else:
        bpy.app.timers.register(run, first_interval=3)
