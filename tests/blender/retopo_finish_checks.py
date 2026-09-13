"""Native topology finishing, flow transitions and operation-local safety."""

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.blender.retopo_checks import RetopoCase, indices, retopo  # noqa: E402
from tests.blender.retopo_scene import tube_data  # noqa: E402
from tests.blender.sculpt_checks import adapter, authored  # noqa: E402


class NativeFinishTests(RetopoCase):
    def edge(self, a: int, b: int) -> dict[str, Any]:
        return indices(
            "edge",
            [
                next(
                    e.index for e in self.target.data.edges if set(e.vertices) == {a, b}
                )
            ],
        )

    def plane(self, n: int = 6, *, hole: bool = False) -> None:
        self.create()
        points = [
            (x * 1.2 / n - 0.6, y * 1.2 / n - 0.6, 1)
            for y in range(n + 1)
            for x in range(n + 1)
        ]
        faces = []
        for y in range(n):
            for x in range(n):
                if hole and x in {2, 3} and y in {2, 3}:
                    continue
                a = y * (n + 1) + x
                faces.append([a, a + 1, a + n + 2, a + n + 1])
        used = sorted({v for f in faces for v in f})
        mapping = {v: i for i, v in enumerate(used)}
        data = bpy.data.meshes.new("Grid")
        data.from_pydata(
            [points[v] for v in used], [], [[mapping[v] for v in f] for f in faces]
        )
        self.target.data = data
        data.update()

    def tube(self) -> None:
        tube_data(self.obj, 64, [-1 + i / 16 for i in range(33)], list(range(32)))
        self.create()
        tube_data(self.target, 12, [-0.8, -0.4, 0, 0.4, 0.8], list(range(4)))
        self.call("project", selector={"mode": "all", "domain": "vertex"})

    def check_valid(self, result: Any, *, quads: bool = True) -> None:
        q = result["after"]
        for key in [
            "non_manifold_edge_count",
            "degenerate_face_count",
            "inconsistent_winding_edge_count",
            "loose_vertex_count",
            "loose_edge_count",
            "ngon_count",
        ]:
            self.assertEqual(q[key], 0, (key, q))
        if quads:
            self.assertEqual(q["triangle_count"], 0)
        self.assertLess(
            result["correspondence_after"]["vertices"]["max_distance"], 2e-6
        )

    def test_insert_open_ring_factor_and_preservation(self) -> None:
        self.plane(4)
        before = authored(self.target)
        source = authored(self.obj)
        self.target.data.edges[0].use_seam = True
        result = self.call(
            "insert_loop", edge=self.edge(6, 7), factor=0.25, from_vertex=6
        )
        self.assertEqual(result["after"]["quad_count"], 20)
        self.assertEqual(result["created"]["vertices"], 5)
        self.assertEqual(result["flow_edges"]["total"], 4)
        self.assertEqual(authored(self.target)[0][:25], before[0])
        for index in result["created_vertices"]["indices"]:
            self.assertAlmostEqual(
                self.target.data.vertices[index].co.x, -0.225, places=6
            )
        self.assertTrue(self.target.data.edges[0].use_seam)
        self.assertTrue(result["indices_invalidated"])
        self.assertEqual(authored(self.obj), source)
        self.check_valid(result)

    def test_insert_closed_curved_ring(self) -> None:
        self.tube()
        result = self.call("insert_loop", edge=self.edge(12, 24))
        self.assertEqual(result["after"]["quad_count"], 60)
        self.assertEqual(result["flow_edges"]["total"], 12)
        self.check_valid(result)

    def test_insert_nonquad_and_orientation_fail_atomic(self) -> None:
        self.plane(4)
        self.failed_unchanged("insert_loop", edge=self.edge(6, 7), from_vertex=24)
        result = self.call("collapse", selector=self.edge(6, 7))
        self.check_valid(result, quads=False)
        tri = next(f for f in self.target.data.polygons if len(f.vertices) == 3)
        a, b = list(tri.vertices)[:2]
        self.failed_unchanged("insert_loop", edge=self.edge(a, b))

    def test_slide_loop_explicit_side_and_unchanged_topology(self) -> None:
        self.plane(4)
        edges = [self.edge(5 * y + 2, 5 * (y + 1) + 2)["indices"][0] for y in range(4)]
        before = authored(self.target)
        result = self.call(
            "slide", selector=indices("edge", edges), toward_vertex=8, factor=0.25
        )
        self.assertFalse(result["indices_invalidated"])
        self.assertEqual(authored(self.target)[1:3], before[1:3])
        for v in self.target.data.vertices:
            if v.index % 5 == 2:
                self.assertAlmostEqual(v.co.x, 0.075, places=6)
            else:
                self.assertEqual(tuple(v.co), before[0][v.index])
        self.check_valid(result)

    def test_slide_vertex_zero_and_invalid_rail(self) -> None:
        self.plane(4)
        before = authored(self.target)
        self.call("slide", selector=indices("vertex", [12]), toward_vertex=13, factor=0)
        self.assertEqual(authored(self.target), before)
        result = self.call(
            "slide", selector=indices("vertex", [12]), toward_vertex=13, factor=0.1
        )
        self.assertAlmostEqual(self.target.data.vertices[12].co.x, 0.03, places=6)
        self.check_valid(result)
        self.failed_unchanged(
            "slide", selector=self.edge(6, 11), toward_vertex=7, factor=0.3
        )
        self.failed_unchanged(
            "slide", selector=indices("vertex", [12]), toward_vertex=24, factor=0.3
        )

    def test_subdivide_quad_region_and_invalid_pattern(self) -> None:
        self.plane(2)
        self.failed_unchanged("subdivide", selector=self.edge(0, 1))
        before = authored(self.target)
        result = self.call(
            "subdivide", selector={"mode": "all", "domain": "edge"}, cuts=2
        )
        self.assertEqual(result["after"]["quad_count"], 36)
        self.assertEqual(authored(self.target)[0][:9], before[0])
        self.check_valid(result)

    def test_collapse_closed_ring_reduces_density(self) -> None:
        self.tube()
        source = authored(self.obj)
        result = self.call("collapse", selector=self.edge(12, 24), mode="ring")
        self.assertEqual(result["after"]["quad_count"], 36)
        self.assertEqual(result["removed"]["vertices"], 12)
        self.assertEqual(
            result["before"]["boundary_edge_count"],
            result["after"]["boundary_edge_count"],
        )
        self.assertEqual(source, authored(self.obj))
        self.check_valid(result)

    def test_collapse_boundary_policy_and_local_triangles(self) -> None:
        self.plane(4)
        self.failed_unchanged("collapse", selector=self.edge(0, 1))
        result = self.call("collapse", selector=self.edge(6, 7))
        self.assertEqual(result["after"]["triangle_count"], 2)
        self.assertEqual(result["removed"]["vertices"], 1)
        self.check_valid(result, quads=False)
        self.plane(4)
        result = self.call(
            "collapse", selector=self.edge(6, 7), mode="ring", allow_boundary=True
        )
        self.assertEqual(result["after"]["quad_count"], 12)
        self.check_valid(result)

    def test_quad_rotation_creates_deliberate_three_five_poles(self) -> None:
        self.plane(6)
        source = authored(self.obj)
        before = authored(self.target)
        result = self.call("rotate_edge", edge=self.edge(23, 30), direction="clockwise")
        self.assertEqual(authored(self.target)[0], before[0])
        self.assertEqual(result["after"]["quad_count"], 36)
        poles = [p for p in result["after"]["poles"] if not p["boundary"]]
        self.assertEqual(sorted(p["valence"] for p in poles), [3, 3, 5, 5])
        self.assertEqual(result["flow_edges"]["total"], 1)
        self.check_valid(result)
        result = self.call(
            "relax", selector={"mode": "all", "domain": "vertex"}, iterations=3
        )
        self.check_valid(result)
        self.assertEqual(authored(self.obj), source)

    def test_rotate_boundary_and_invalid_quad_fold_atomic(self) -> None:
        self.plane(4)
        self.failed_unchanged("rotate_edge", edge=self.edge(0, 1))
        v = self.target.data.vertices[7]
        v.co.x = -0.29
        v.co.y = -0.59
        self.target.data.update()
        self.failed_unchanged("rotate_edge", edge=self.edge(6, 11))

    def seam(self) -> tuple[Any, Any]:
        self.create()
        data = bpy.data.meshes.new("Seam")
        points: list[tuple[float, float, float]] = []
        faces = []
        for left, right in [(-0.6, -0.002), (0.002, 0.6)]:
            base = len(points)
            points += [(x, y, 1) for y in [-0.4, 0, 0.4] for x in [left, right]]
            faces += [
                [base + i, base + i + 1, base + i + 3, base + i + 2] for i in [0, 2]
            ]
        data.from_pydata(points, [], faces)
        self.target.data = data
        data.update()
        return indices(
            "edge", [self.edge(1, 3)["indices"][0], self.edge(3, 5)["indices"][0]]
        ), indices(
            "edge", [self.edge(6, 8)["indices"][0], self.edge(8, 10)["indices"][0]]
        )

    def test_stitch_weld_endpoint_orientation_and_distance(self) -> None:
        a, b = self.seam()
        self.failed_unchanged("stitch", chain_a=a, chain_b=b, start_a=1, start_b=10)
        self.failed_unchanged(
            "stitch",
            chain_a=a,
            chain_b=b,
            start_a=1,
            start_b=6,
            max_weld_distance=0.001,
        )
        result = self.call("stitch", chain_a=a, chain_b=b, start_a=5, start_b=10)
        self.assertEqual(result["removed"]["vertices"], 3)
        self.assertEqual(result["after"]["quad_count"], 4)
        self.assertEqual(result["before"]["boundary_loop_count"], 2)
        self.assertEqual(result["after"]["boundary_loop_count"], 1)
        self.check_valid(result)

    def test_fill_curved_hole_grid_and_reject_outer_boundary(self) -> None:
        self.plane(hole=True)
        loops = self.call("inspect")["target"]["boundaries"]
        hole = min(loops, key=lambda b: b["perimeter"])
        outer = max(loops, key=lambda b: b["perimeter"])
        self.failed_unchanged(
            "fill_boundary",
            boundary=indices("edge", outer["edge_indices"]["indices"]),
            corner_vertex=outer["vertex_indices"]["indices"][0],
            span=6,
        )
        boundary = indices("edge", hole["edge_indices"]["indices"])
        corner = min(hole["vertex_indices"]["indices"])
        self.failed_unchanged(
            "fill_boundary", boundary=boundary, corner_vertex=corner, span=4
        )
        result = self.call(
            "fill_boundary", boundary=boundary, corner_vertex=corner, span=2
        )
        self.assertEqual(result["after"]["quad_count"], 36)
        self.assertEqual(result["created"]["vertices"], 1)
        self.assertEqual(result["after"]["boundary_loop_count"], 1)
        self.check_valid(result)

    def test_exceptional_valence_context_and_truncation(self) -> None:
        self.plane(4)
        q = self.call("inspect")["target"]
        self.assertEqual(q["poles_total"], 4)
        self.assertTrue(all(p["boundary"] and p["valence"] == 2 for p in q["poles"]))
        self.assertFalse(q["poles_truncated"])
        data = bpy.data.meshes.new("Many boundaries")
        points: list[tuple[float, float, float]] = []
        faces = []
        for i in range(200):
            x, y = (i % 20) * 0.04 - 0.4, (i // 20) * 0.04 - 0.2
            base = len(points)
            points += [
                (x, y, 1),
                (x + 0.02, y, 1),
                (x + 0.02, y + 0.02, 1),
                (x, y + 0.02, 1),
            ]
            faces.append([base, base + 1, base + 2, base + 3])
        data.from_pydata(points, [], faces)
        self.target.data = data
        data.update()
        q = self.call("inspect")["target"]
        self.assertEqual(q["poles_total"], 800)
        self.assertEqual(len(q["poles"]), 128)
        self.assertTrue(q["poles_truncated"])
        self.plane(6)
        self.call("rotate_edge", edge=self.edge(23, 30))
        q = self.call("inspect")["target"]
        self.assertEqual(len([p for p in q["poles"] if not p["boundary"]]), 4)

    def test_shared_material_selection_isolation(self) -> None:
        self.plane(4)
        sibling = self.target.copy()
        bpy.context.collection.objects.link(sibling)
        original = sibling.data
        mat = bpy.data.materials.new("Surface material")
        self.target.data.materials.append(mat)
        self.target.data.vertices[6].select = True
        old = authored(sibling)
        result = self.call("insert_loop", edge=self.edge(6, 7))
        self.assertTrue(result["mesh_isolated"])
        self.assertEqual(sibling.data, original)
        self.assertEqual(authored(sibling), old)
        self.assertEqual(list(self.target.data.materials), [mat])
        self.assertTrue(self.target.data.vertices[6].select)

    def test_staged_failure_and_valuable_data_guard(self) -> None:
        self.plane(4)
        self.target.data.uv_layers.new(name="UV")
        self.failed_unchanged("insert_loop", edge=self.edge(6, 7))
        self.target.data.uv_layers.remove(self.target.data.uv_layers[0])
        budget = retopo.target_budget
        original = self.target.data

        def fail_candidate(data: Any) -> None:
            if data != original:
                raise RuntimeError("staging failure")
            budget(data)

        with patch.object(retopo, "target_budget", side_effect=fail_candidate):
            self.failed_unchanged("rotate_edge", edge=self.edge(6, 11))

    def test_mirror_shrinkwrap_finishing_and_seam_protection(self) -> None:
        self.plane(4)
        for v in self.target.data.vertices:
            v.co.x = (v.co.x + 0.6) * 0.5
        self.target.data.update()
        mirror = self.target.modifiers.new("Symmetry", "MIRROR")
        mirror.use_clip = False
        wrap = self.target.modifiers.new("Conform", "SHRINKWRAP")
        wrap.target = self.obj
        source = authored(self.obj)
        result = self.call("insert_loop", edge=self.edge(6, 7))
        self.check_valid(result)
        self.assertEqual(
            [m.type for m in self.target.modifiers], ["MIRROR", "SHRINKWRAP"]
        )
        self.assertGreater(
            self.call("inspect")["evaluated_target"]["face_count"],
            result["after"]["quad_count"],
        )
        seam = next(
            e
            for e in self.target.data.edges
            if all(abs(self.target.data.vertices[i].co.x) < 1e-6 for i in e.vertices)
        )
        self.failed_unchanged(
            "collapse", selector=indices("edge", [seam.index]), allow_boundary=True
        )
        self.assertEqual(authored(self.obj), source)

    def test_all_finishing_operations_with_target_helpers(self) -> None:
        for helpers in [("MIRROR",), ("SHRINKWRAP",), ("MIRROR", "SHRINKWRAP")]:
            for operation in [
                "insert_loop",
                "slide",
                "subdivide",
                "collapse",
                "rotate_edge",
                "stitch",
                "fill_boundary",
            ]:
                with self.subTest(helpers=helpers, operation=operation):
                    self.setUp()
                    if operation == "stitch":
                        a, b = self.seam()
                        args = dict(chain_a=a, chain_b=b, start_a=1, start_b=6)
                    else:
                        self.plane(6, hole=operation == "fill_boundary")
                        if operation == "fill_boundary":
                            hole = min(
                                self.call("inspect")["target"]["boundaries"],
                                key=lambda v: v["perimeter"],
                            )
                            args = dict(
                                boundary=indices(
                                    "edge", hole["edge_indices"]["indices"]
                                ),
                                corner_vertex=min(hole["vertex_indices"]["indices"]),
                                span=2,
                            )
                        elif operation == "slide":
                            args = dict(
                                selector=indices("vertex", [24]),
                                toward_vertex=25,
                                factor=0.1,
                            )
                        elif operation == "subdivide":
                            args = dict(selector={"mode": "all", "domain": "edge"})
                        elif operation == "collapse":
                            args = dict(selector=self.edge(23, 24))
                        else:
                            args = dict(edge=self.edge(23, 30))
                    for v in self.target.data.vertices:
                        v.co.x = (v.co.x + 0.6) * 0.5
                    self.target.data.update()
                    for helper in helpers:
                        mod = self.target.modifiers.new(helper, helper)
                        if helper == "MIRROR":
                            mod.use_clip = False
                        else:
                            mod.target = self.obj
                    source = authored(self.obj)
                    result = self.call(operation, **args)
                    self.check_valid(result, quads=operation != "collapse")
                    self.assertEqual(
                        tuple(m.type for m in self.target.modifiers), helpers
                    )
                    self.assertEqual(source, authored(self.obj))
                    info = self.call("inspect")
                    self.assertLess(
                        info["evaluated_correspondence"]["vertices"]["max_distance"],
                        2e-6,
                    )

    def test_curved_gap_fill_and_explicit_rotation_inverse(self) -> None:
        self.sphere()
        self.plane(hole=True)
        self.call("project", selector={"mode": "all", "domain": "vertex"})
        hole = min(
            self.call("inspect")["target"]["boundaries"], key=lambda b: b["perimeter"]
        )
        result = self.call(
            "fill_boundary",
            boundary=indices("edge", hole["edge_indices"]["indices"]),
            corner_vertex=min(hole["vertex_indices"]["indices"]),
            span=2,
        )
        self.check_valid(result)
        self.setUp()
        self.plane(6)
        before = {frozenset(e.vertices) for e in self.target.data.edges}
        result = self.call("rotate_edge", edge=self.edge(23, 30))
        self.call(
            "rotate_edge",
            edge=indices("edge", result["flow_edges"]["indices"]),
            direction="counterclockwise",
        )
        self.assertEqual(
            {frozenset(e.vertices) for e in self.target.data.edges}, before
        )

    def test_vertex_only_pinches_are_reported_and_blocked(self) -> None:
        self.create()
        for closed in [False, True]:
            with self.subTest(closed=closed):
                if closed:
                    points = [
                        (0, 0, 1),
                        (0.3, 0, 1),
                        (0, 0.3, 1),
                        (0, 0, 0.7),
                        (-0.3, 0, 1),
                        (0, -0.3, 1),
                        (0, 0, 1.3),
                    ]
                    faces = [
                        [0, 2, 1],
                        [0, 1, 3],
                        [1, 2, 3],
                        [2, 0, 3],
                        [0, 5, 4],
                        [0, 4, 6],
                        [4, 5, 6],
                        [5, 0, 6],
                    ]
                else:
                    points = [
                        (0, 0, 1),
                        (0.3, 0, 1),
                        (0.3, 0.3, 1),
                        (0, 0.3, 1),
                        (-0.3, 0, 1),
                        (-0.3, -0.3, 1),
                        (0, -0.3, 1),
                    ]
                    faces = [[0, 1, 2, 3], [0, 4, 5, 6]]
                data = bpy.data.meshes.new("Pinched surface")
                data.from_pydata(points, [], faces)
                self.target.data = data
                data.update()
                quality = self.call("inspect")["target"]
                self.assertEqual(quality["non_manifold_edge_count"], 0)
                self.assertEqual(quality["non_manifold_vertex_count"], 1)
                self.failed_unchanged(
                    "project", selector={"mode": "all", "domain": "vertex"}
                )
                self.failed_unchanged(
                    "slide",
                    selector=indices("vertex", [0]),
                    toward_vertex=1,
                    factor=0.1,
                )

    def test_full_density_flow_finishing_scenario(self) -> None:
        self.sphere()
        self.plane(hole=True)
        self.call("project", selector={"mode": "all", "domain": "vertex"})
        source = authored(self.obj)
        # Grow an outer row, refine a transverse ring, then reduce another strip.
        top = [
            e.index
            for e in self.target.data.edges
            if all(self.target.data.vertices[i].co.y > 0.4 for i in e.vertices)
        ]
        self.call(
            "extrude_boundary", selector=indices("edge", top), offset=[0, 0.15, 0]
        )
        self.call("insert_loop", edge=self.edge(0, 1))
        data = self.target.data
        e = next(
            e
            for e in data.edges
            if all(data.vertices[i].co.x > 0.3 for i in e.vertices)
            and abs(
                data.vertices[e.vertices[0]].co.x - data.vertices[e.vertices[1]].co.x
            )
            > 0.05
        )
        self.call(
            "collapse",
            selector=indices("edge", [e.index]),
            mode="ring",
            allow_boundary=True,
        )
        # Fill the remaining central hole before redirecting an interior quad pair.
        hole = min(
            self.call("inspect")["target"]["boundaries"], key=lambda b: b["perimeter"]
        )
        self.call(
            "fill_boundary",
            boundary=indices("edge", hole["edge_indices"]["indices"]),
            corner_vertex=min(hole["vertex_indices"]["indices"]),
            span=2,
        )
        data = self.target.data
        e = next(
            e
            for e in data.edges
            if all(
                abs(data.vertices[i].co.x) < 0.25 and abs(data.vertices[i].co.y) < 0.25
                for i in e.vertices
            )
        )
        edge_index = e.index
        a, b = e.vertices
        positions = {v.index: v.co.copy() for v in data.vertices}
        guides = []
        for current, other in [(a, b), (b, a)]:
            neighbors = [
                v
                for candidate in data.edges
                if current in candidate.vertices
                for v in candidate.vertices
                if v != current and v != other
            ]
            toward = max(
                neighbors,
                key=lambda v: (positions[v] - positions[current]).dot(
                    positions[current] - positions[other]
                ),
            )
            guides.append((current, toward))
        for current, toward in guides:
            self.call(
                "slide",
                selector=indices("vertex", [current]),
                toward_vertex=toward,
                factor=0.2,
            )
        self.call("rotate_edge", edge=indices("edge", [edge_index]))
        result = self.call(
            "relax", selector={"mode": "all", "domain": "vertex"}, iterations=5
        )
        self.check_valid(result)
        self.assertTrue(
            any(
                not p["boundary"] and p["valence"] in {3, 5}
                for p in result["after"]["poles"]
            )
        )
        self.assertEqual(authored(self.obj), source)


def run() -> None:
    runtime = adapter._runtime
    worker = runtime.worker if runtime else None
    adapter.unregister()
    if worker:
        assert worker.process.returncode == 0 and not worker.spool.root.exists()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(NativeFinishTests)
    )
    assert not bpy.app.timers.is_registered(adapter.pump)
    if result.wasSuccessful():
        print("BLENDER_FINISH_TESTS_PASSED", result.testsRun, flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    elif not result.wasSuccessful():
        raise RuntimeError("Native finishing checks failed")


if __name__ == "__main__":
    if bpy.app.background:
        run()
    else:
        bpy.app.timers.register(run, first_interval=3)
