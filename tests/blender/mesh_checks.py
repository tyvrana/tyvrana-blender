"""Native authored topology, transactional safety, and seam/UV integration."""

import importlib
import math
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
mesh_api = importlib.import_module("bl_ext.user_default.tyvrana_blender.mesh")

TOP = {"domain": "face", "mode": "normal", "direction": [0, 0, 2], "min_dot": 0.99}
ALL_EDGES = {"domain": "edge", "mode": "all"}
ALL_FACES = {"domain": "face", "mode": "all"}
UPPER = {"domain": "vertex", "mode": "box", "min": [-1, -1, 1], "max": [1, 1, 1]}
MUTATIONS: list[tuple[str, dict[str, Any]]] = [
    ("transform", {"selector": UPPER, "translation": [0, 0, 1]}),
    (
        "transform",
        {
            "selector": ALL_FACES,
            "translation": [0.2, 0, 0],
            "falloff": {"center": [1, 1, 1], "radii": [4, 2, 2]},
        },
    ),
    ("extrude_faces", {"selector": TOP, "offset": [0, 0, 1]}),
    ("inset_faces", {"selector": TOP, "thickness": 0.2}),
    ("bevel_edges", {"selector": ALL_EDGES, "width": 0.2, "segments": 3}),
    ("subdivide_edges", {"selector": ALL_EDGES}),
    ("delete_elements", {"selector": TOP}),
    (
        "merge_vertices",
        {"selector": {"domain": "vertex", "mode": "indices", "indices": [0, 1]}},
    ),
    ("mark_seam", {"selector": ALL_EDGES, "seam": True}),
    ("recalculate_normals", {}),
    ("set_shading", {"selector": ALL_FACES, "smooth": True}),
]


def state(mesh: Any) -> Any:
    return (
        [tuple(v.co) for v in mesh.vertices],
        [
            (tuple(e.vertices), e.use_seam, e.use_edge_sharp, e.select, e.hide)
            for e in mesh.edges
        ],
        [
            (tuple(f.vertices), f.material_index, f.use_smooth, f.select, f.hide)
            for f in mesh.polygons
        ],
        [
            (uv.name, uv.active, uv.active_render, [tuple(p.vector) for p in uv.uv])
            for uv in mesh.uv_layers
        ],
        [m.name if m else None for m in mesh.materials],
        [(a.name, a.domain, a.data_type) for a in mesh.attributes],
    )


class MeshTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend()
        self.reset()

    def reset(self) -> None:
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
        runtime = adapter._runtime
        worker = runtime.worker if runtime else None
        adapter.unregister()
        self.assertFalse(bpy.app.timers.is_registered(adapter.pump))
        if worker:
            self.assertEqual(worker.process.returncode, 0)
            self.assertFalse(worker.spool.root.exists())

    def response(self, operation: str, **arguments: Any) -> Any:
        return operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="mesh-test",
                operation="blender."
                + (operation if "." in operation else "mesh." + operation),
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

    def counts(self, expected: tuple[int, int, int]) -> None:
        result = self.call("inspect")
        self.assertEqual(
            tuple(result[key + "_count"] for key in ("vertex", "edge", "face")),
            expected,
        )

    def closed(self) -> None:
        self.assertEqual(
            self.call("inspect")["manifold_summary"]["non_manifold_edge_count"], 0
        )
        self.assertTrue(all(f.area > 1e-8 for f in self.obj.data.polygons))

    def test_inspect_authored_local_cube_and_exact_summary(self) -> None:
        self.obj.location = (12, 15, 20)
        self.obj.scale = (2, 3, 4)
        self.counts((8, 12, 6))
        result = self.call("inspect")
        self.assertEqual(result["bounds_min"], [-1, -1, -1])
        self.assertEqual(result["bounds_max"], [1, 1, 1])
        self.assertEqual(result["loop_count"], 24)
        self.assertEqual(result["uv_map_count"], 1)
        self.assertEqual(result["material_slot_count"], 0)
        self.assertFalse(result["has_shape_keys"])
        self.assertEqual(
            result["manifold_summary"],
            dict(
                boundary_edge_count=0,
                manifold_edge_count=12,
                non_manifold_edge_count=0,
                loose_vertex_count=0,
                loose_edge_count=0,
            ),
        )

    def test_vertex_edge_and_face_queries_are_deterministic(self) -> None:
        before = state(self.obj.data)
        for domain, count in [("vertex", 8), ("edge", 12), ("face", 6)]:
            result = self.call("query", selector={"domain": domain, "mode": "all"})
            self.assertEqual(result["matched_count"], count)
            self.assertFalse(result["truncated"])
            self.assertEqual(
                [e["index"] for e in result["elements"]], list(range(count))
            )
        top = self.call("query", selector=TOP)
        self.assertEqual(top["matched_count"], 1)
        face = top["elements"][0]
        self.assertEqual(face["center"], [0, 0, 1])
        self.assertEqual(face["normal"], [0, 0, 1])
        self.assertEqual(face["area"], 4)
        self.assertEqual(face["vertex_count"], 4)
        self.assertEqual(state(self.obj.data), before)

    def test_box_inclusive_vertex_and_face_median_predicates(self) -> None:
        self.assertEqual(self.call("query", selector=UPPER)["matched_count"], 4)
        face_box = {"domain": "face", "mode": "box", "min": [0, 0, 1], "max": [0, 0, 1]}
        self.assertEqual(self.call("query", selector=face_box)["matched_count"], 1)
        self.assertEqual(
            self.call(
                "query", selector={**UPPER, "min": [9, 9, 9], "max": [10, 10, 10]}
            )["matched_count"],
            0,
        )

    def test_indices_sorted_and_out_of_range_atomic(self) -> None:
        selector = {"domain": "vertex", "mode": "indices", "indices": [7, 0, 3]}
        self.assertEqual(
            [e["index"] for e in self.call("query", selector=selector)["elements"]],
            [0, 3, 7],
        )
        original = self.obj.data
        before = state(original)
        selector["indices"] = [0, 8]
        self.error("query", "invalid_arguments", selector=selector)
        self.error(
            "transform", "invalid_arguments", selector=selector, translation=[0, 0, 1]
        )
        self.assertEqual(self.obj.data, original)
        self.assertEqual(state(original), before)

    def test_empty_mesh_and_empty_mutation(self) -> None:
        self.obj.data = bpy.data.meshes.new("Empty")
        result = self.call("inspect")
        self.assertIsNone(result["bounds_min"])
        self.assertIsNone(result["bounds_max"])
        self.assertEqual(self.call("query", selector=ALL_FACES)["elements"], [])
        self.error("delete_elements", "mesh_selection_empty", selector=ALL_FACES)
        self.error("recalculate_normals", "mesh_selection_empty")

    def test_boundary_wire_and_loose_topology_counts(self) -> None:
        mesh = bpy.data.meshes.new("Open")
        mesh.from_pydata(
            [
                (0, 0, 0),
                (1, 0, 0),
                (1, 1, 0),
                (0, 1, 0),
                (3, 0, 0),
                (4, 0, 0),
                (8, 0, 0),
            ],
            [(4, 5)],
            [(0, 1, 2, 3)],
        )
        self.obj.data = mesh
        self.assertEqual(
            self.call("query", selector={"domain": "edge", "mode": "boundary"})[
                "matched_count"
            ],
            4,
        )
        self.assertEqual(
            self.call("inspect")["manifold_summary"],
            dict(
                boundary_edge_count=4,
                manifold_edge_count=0,
                non_manifold_edge_count=5,
                loose_vertex_count=1,
                loose_edge_count=1,
            ),
        )

    def test_large_query_and_ngon_are_bounded(self) -> None:
        count = 600
        mesh = bpy.data.meshes.new("Polygon")
        mesh.from_pydata(
            [
                (math.cos(i * math.tau / count), math.sin(i * math.tau / count), 0)
                for i in range(count)
            ],
            [],
            [tuple(range(count))],
        )
        self.obj.data = mesh
        vertices = self.call(
            "query", selector={"domain": "vertex", "mode": "all"}, limit=256
        )
        self.assertEqual(vertices["matched_count"], count)
        self.assertEqual(len(vertices["elements"]), 256)
        self.assertTrue(vertices["truncated"])
        face = self.call("query", selector=ALL_FACES)["elements"][0]
        self.assertEqual(face["vertex_count"], count)
        self.assertEqual(len(face["vertices"]), 128)
        self.assertTrue(face["vertices_truncated"])

    def test_edit_mode_snapshot_reads_live_geometry_without_context_change(
        self,
    ) -> None:
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts[0].co.z = 3
        bm.verts[0].select = True
        bm.select_history.add(bm.verts[0])
        before = mesh_api._Selection(bm).__dict__
        self.assertEqual(self.call("inspect")["bounds_max"][2], 3)
        self.call("query", selector=ALL_FACES)
        self.error(
            "transform", "invalid_context", selector=UPPER, translation=[0, 0, 1]
        )
        self.assertEqual(bpy.context.mode, "EDIT_MESH")
        self.assertEqual(mesh_api._Selection(bm).__dict__, before)

    def test_regional_translation_preserves_object_transform(self) -> None:
        self.obj.location = (4, 5, 6)
        self.obj.rotation_euler = (0.2, 0.3, 0.4)
        self.obj.scale = (2, 3, 4)
        before = (
            tuple(self.obj.location),
            tuple(self.obj.rotation_euler),
            tuple(self.obj.scale),
        )
        result = self.call("transform", selector=UPPER, translation=[0.5, 0, 1])
        self.assertEqual(result["transformed_vertices"], 4)
        self.assertEqual(result["mesh"]["bounds_max"], [1.5, 1, 2])
        self.assertEqual(sum(v.co.z == -1 for v in self.obj.data.vertices), 4)
        self.assertEqual(
            (
                tuple(self.obj.location),
                tuple(self.obj.rotation_euler),
                tuple(self.obj.scale),
            ),
            before,
        )
        self.counts((8, 12, 6))

    def test_edge_and_face_transform_unique_vertices_once(self) -> None:
        for selector in (ALL_EDGES, ALL_FACES):
            self.reset()
            result = self.call("transform", selector=selector, translation=[0, 0, 1])
            self.assertEqual(result["transformed_vertices"], 8)
            self.assertEqual(result["mesh"]["bounds_min"], [-1, -1, 0])
            self.assertEqual(result["mesh"]["bounds_max"], [1, 1, 2])

    def test_falloff_center_half_radius_boundary_and_local_space(self) -> None:
        for selector in (ALL_EDGES, ALL_FACES):
            self.reset()
            self.obj.location = (4, 5, 6)
            self.obj.scale = (2, 3, 4)
            self.obj.rotation_euler = (0.2, 0.3, 0.4)
            transform = self.obj.matrix_local.copy()
            before = [tuple(v.co) for v in self.obj.data.vertices]
            result = self.call(
                "transform",
                selector=selector,
                translation=[0.2, 0, 0],
                falloff={"center": [1, 1, 1], "radii": [4, 2, 2]},
            )
            self.assertEqual(result["transformed_vertices"], 2)
            for old, vertex in zip(before, self.obj.data.vertices, strict=True):
                if old == (1, 1, 1):
                    self.assertAlmostEqual(vertex.co.x, 1.2, places=6)
                elif old == (-1, 1, 1):
                    self.assertAlmostEqual(vertex.co.x, -0.9, places=6)
                else:
                    self.assertEqual(tuple(vertex.co), old)
                self.assertEqual(tuple(vertex.co)[1:], old[1:])
            self.assertEqual(self.obj.matrix_local, transform)
            self.counts((8, 12, 6))
            self.closed()

    def test_falloff_does_not_expand_explicit_selection(self) -> None:
        before = [tuple(v.co) for v in self.obj.data.vertices]
        index = before.index((-1, 1, 1))
        result = self.call(
            "transform",
            selector={"domain": "vertex", "mode": "indices", "indices": [index]},
            translation=[0.2, 0, 0],
            falloff={"center": [1, 1, 1], "radii": [4, 2, 2]},
        )
        self.assertEqual(result["transformed_vertices"], 1)
        for i, vertex in enumerate(self.obj.data.vertices):
            if i == index:
                self.assertAlmostEqual(vertex.co.x, -0.9, places=6)
            else:
                self.assertEqual(tuple(vertex.co), before[i])

    def test_falloff_miss_does_not_replace_mesh(self) -> None:
        original = self.obj.data
        before = state(original)
        count = len(bpy.data.meshes)
        self.error(
            "transform",
            "mesh_selection_empty",
            selector=ALL_FACES,
            translation=[0, 0, 1],
            falloff={"center": [20, 0, 0], "radii": [1, 1, 1]},
        )
        self.assertEqual(self.obj.data, original)
        self.assertEqual(state(original), before)
        self.assertEqual(len(bpy.data.meshes), count)

    def test_scale_rotation_translation_order_and_explicit_pivot(self) -> None:
        selector = {"domain": "vertex", "mode": "indices", "indices": [0]}
        start = self.call("query", selector=selector)["elements"][0]["co"]
        self.call(
            "transform",
            selector=selector,
            scale=[2, 1, 1],
            rotation=[0, 0, math.pi / 2],
            translation=[0, 0, 1],
            pivot=[1, 0, 0],
        )
        point = self.call("query", selector=selector)["elements"][0]["co"]
        expected = [1 - start[1], (start[0] - 1) * 2, start[2] + 1]
        for actual, target in zip(point, expected, strict=True):
            self.assertAlmostEqual(actual, target, places=5)

    def test_median_pivot_scales_upper_surface_only(self) -> None:
        self.call("transform", selector=TOP, scale=[0.5, 0.5, 1])
        self.assertTrue(
            all(
                abs(v.co.x) == 0.5 and abs(v.co.y) == 0.5
                for v in self.obj.data.vertices
                if v.co.z == 1
            )
        )
        self.assertTrue(
            all(
                abs(v.co.x) == 1 and abs(v.co.y) == 1
                for v in self.obj.data.vertices
                if v.co.z == -1
            )
        )

    def test_extrude_region_removes_internal_face_and_returns_outer_cap(self) -> None:
        result = self.call(
            "extrude_faces", selector=TOP, offset=[0, 0, 1], scale=[0.5, 0.5, 1]
        )
        self.counts((12, 20, 10))
        self.closed()
        self.assertEqual(result["selected"], {"domain": "face", "count": 1})
        self.assertEqual(result["created"], {"vertices": 4, "edges": 8, "faces": 5})
        self.assertEqual(result["removed"], {"vertices": 0, "edges": 0, "faces": 1})
        face = self.call(
            "query",
            selector={
                "domain": "face",
                "mode": "indices",
                "indices": result["region_faces"]["indices"],
            },
        )["elements"][0]
        self.assertEqual(face["center"], [0, 0, 2])
        self.assertEqual(face["area"], 1)

    def test_adjacent_face_region_has_no_internal_extrusion_walls(self) -> None:
        self.call("subdivide_edges", selector=ALL_EDGES)
        self.assertEqual(self.call("query", selector=TOP)["matched_count"], 4)
        result = self.call("extrude_faces", selector=TOP, offset=[0, 0, 1])
        self.assertEqual(result["region_faces"]["total"], 4)
        self.closed()

    def test_inset_returns_inner_faces_for_following_extrusion(self) -> None:
        result = self.call("inset_faces", selector=TOP, thickness=0.2, depth=0)
        self.counts((12, 20, 10))
        inner = self.call(
            "query",
            selector={
                "domain": "face",
                "mode": "indices",
                "indices": result["region_faces"]["indices"],
            },
        )["elements"][0]
        self.assertAlmostEqual(inner["area"], 2.56, places=5)
        result = self.call(
            "extrude_faces",
            selector={"domain": "face", "mode": "indices", "indices": [inner["index"]]},
            offset=[0, 0, 0.5],
        )
        self.counts((16, 28, 14))
        self.closed()
        self.assertEqual(result["mesh"]["bounds_max"], [1, 1, 1.5])

    def test_inset_depth_and_region_rejection_are_atomic(self) -> None:
        self.call(
            "inset_faces", selector=TOP, thickness=0.2, depth=-0.2, even_offset=False
        )
        self.closed()
        cases: list[tuple[str, dict[str, Any]]] = [
            ("extrude_faces", {"offset": [0, 0, 1]}),
            ("inset_faces", {"thickness": 0.1}),
        ]
        for operation, arguments in cases:
            original = self.obj.data
            before = state(original)
            self.error(operation, "invalid_arguments", selector=ALL_FACES, **arguments)
            self.assertEqual(self.obj.data, original)
            self.assertEqual(state(original), before)

    def test_bevel_segments_preserve_closed_nonzero_geometry_and_bounds(self) -> None:
        result = self.call(
            "bevel_edges", selector=ALL_EDGES, width=0.2, segments=3, profile=0.5
        )
        self.counts((96, 192, 98))
        self.closed()
        self.assertEqual(result["mesh"]["bounds_min"], [-1, -1, -1])
        self.assertEqual(result["mesh"]["bounds_max"], [1, 1, 1])

    def test_bevel_boundary_rejected_without_changes(self) -> None:
        self.call("delete_elements", selector=TOP)
        before = state(self.obj.data)
        self.error(
            "bevel_edges",
            "invalid_arguments",
            selector={"domain": "edge", "mode": "boundary"},
            width=0.1,
        )
        self.assertEqual(state(self.obj.data), before)

    def test_subdivide_grid_counts_and_smooth_control(self) -> None:
        self.call("subdivide_edges", selector=ALL_EDGES, cuts=1, smooth=0)
        self.counts((26, 48, 24))
        self.closed()
        flat = [tuple(v.co) for v in self.obj.data.vertices]
        self.reset()
        self.call("subdivide_edges", selector=ALL_EDGES, cuts=1, smooth=1)
        self.assertNotEqual([tuple(v.co) for v in self.obj.data.vertices], flat)
        self.closed()

    def test_delete_face_retains_vertices_edges(self) -> None:
        result = self.call("delete_elements", selector=TOP)
        self.counts((8, 12, 5))
        self.assertEqual(result["removed"], {"vertices": 0, "edges": 0, "faces": 1})
        self.assertEqual(
            self.call("query", selector={"domain": "edge", "mode": "boundary"})[
                "matched_count"
            ],
            4,
        )

    def test_delete_face_cleanup_semantics(self) -> None:
        for mode, expected in [
            ("faces_only", (4, 4, 0)),
            ("faces_and_unused", (0, 0, 0)),
        ]:
            mesh = bpy.data.meshes.new("Plane")
            mesh.from_pydata(
                [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)]
            )
            self.obj.data = mesh
            self.call("delete_elements", selector=ALL_FACES, face_mode=mode)
            self.counts(expected)

    def test_delete_vertex_removes_incident_topology(self) -> None:
        self.call(
            "delete_elements",
            selector={"domain": "vertex", "mode": "indices", "indices": [0]},
        )
        self.counts((7, 9, 3))

    def test_delete_edges_preserves_even_unused_vertices(self) -> None:
        self.call(
            "delete_elements",
            selector={"domain": "edge", "mode": "indices", "indices": [0]},
        )
        self.counts((8, 11, 4))
        mesh = bpy.data.meshes.new("Wire")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
        self.obj.data = mesh
        self.call("delete_elements", selector=ALL_EDGES)
        self.counts((2, 0, 0))

    def test_merge_center_averages_vertex_weights_without_welding_uvs(self) -> None:
        group = self.obj.vertex_groups.new(name="Weight")
        group.add([0], 0.2, "REPLACE")
        group.add([1], 0.8, "REPLACE")
        points = [tuple(self.obj.data.vertices[i].co) for i in [0, 1]]
        expected = tuple((a + b) / 2 for a, b in zip(*points, strict=True))
        result = self.call(
            "merge_vertices",
            selector={"domain": "vertex", "mode": "indices", "indices": [0, 1]},
            mode="center",
        )
        self.counts((7, 11, 6))
        merged = next(v for v in self.obj.data.vertices if tuple(v.co) == expected)
        self.assertAlmostEqual(
            self.obj.vertex_groups["Weight"].weight(merged.index), 0.5, places=6
        )
        self.assertEqual(result["removed"]["vertices"], 1)
        self.assertEqual(len(self.obj.data.uv_layers), 1)
        self.assertEqual(
            len(self.obj.data.uv_layers.active.uv), len(self.obj.data.loops)
        )

    def test_merge_requires_two_matches_and_empty_selection_fails(self) -> None:
        self.error(
            "merge_vertices",
            "invalid_arguments",
            selector={"domain": "vertex", "mode": "indices", "indices": [0]},
        )
        self.error(
            "mark_seam",
            "mesh_selection_empty",
            selector={"domain": "edge", "mode": "seam", "value": True},
            seam=False,
        )

    def test_seam_counts_idempotence_clear_and_queries(self) -> None:
        result = self.call("mark_seam", selector=ALL_EDGES, seam=True)
        self.assertEqual(result["changed_edges"], 12)
        self.assertEqual(
            self.call("mark_seam", selector=ALL_EDGES, seam=True)["changed_edges"], 0
        )
        self.assertEqual(
            self.call(
                "query", selector={"domain": "edge", "mode": "seam", "value": True}
            )["matched_count"],
            12,
        )
        self.assertEqual(
            self.call("mark_seam", selector=ALL_EDGES, seam=False)["changed_edges"], 12
        )
        self.assertEqual(
            self.call(
                "query", selector={"domain": "edge", "mode": "seam", "value": False}
            )["matched_count"],
            12,
        )

    def test_marked_seams_split_actual_angle_and_conformal_uv_islands(self) -> None:
        def discontinuities() -> int:
            with mesh_api.snapshot(self.obj) as bm:
                layer = bm.loops.layers.uv.active
                return sum(
                    any(
                        (
                            next(
                                loop
                                for loop in edge.link_faces[0].loops
                                if loop.vert == vertex
                            )[layer].uv
                            - next(
                                loop
                                for loop in edge.link_faces[1].loops
                                if loop.vert == vertex
                            )[layer].uv
                        ).length
                        > 1e-5
                        for vertex in edge.verts
                    )
                    for edge in bm.edges
                )

        for method in ["angle_based", "conformal"]:
            self.reset()
            # A spanning tree of face connections makes a valid single UV net;
            # unwrapping a closed cube without any cuts has no planar solution.
            with mesh_api.snapshot(self.obj) as bm:
                parents = list(range(len(bm.faces)))

                def component(index: int, parents: list[int] = parents) -> int:
                    while parents[index] != index:
                        index = parents[index]
                    return index

                connected_edges = []
                for edge in bm.edges:
                    first, second = (component(face.index) for face in edge.link_faces)
                    if first != second:
                        parents[first] = second
                        connected_edges.append(edge.index)
            self.call("mark_seam", selector=ALL_EDGES, seam=True)
            self.call(
                "mark_seam",
                selector={
                    "domain": "edge",
                    "mode": "indices",
                    "indices": connected_edges,
                },
                seam=False,
            )
            self.call("uv.unwrap", method=method, correct_aspect=False)
            connected = discontinuities()
            self.call("mark_seam", selector=ALL_EDGES, seam=True)
            self.call("uv.unwrap", method=method, correct_aspect=False)
            self.assertEqual(discontinuities(), 12)
            self.assertEqual(connected, 7)
            self.assertTrue(all(e.use_seam for e in self.obj.data.edges))

    def test_face_shading_preserves_geometry_data_and_shared_sibling(self) -> None:
        original = self.obj.data
        original.edges[0].use_edge_sharp = True
        original.edges[1].use_seam = True
        original.polygons[0].select = False
        original.polygons[0].hide = True
        attr = original.attributes.new("Region", "INT", "FACE")
        for i, item in enumerate(attr.data):
            item.value = i + 4
        material = bpy.data.materials.new("Override")
        original.materials.append(material)
        self.obj.material_slots[0].link = "OBJECT"
        self.obj.material_slots[0].material = material
        sibling = bpy.data.objects.new("Sibling", original)
        bpy.context.collection.objects.link(sibling)
        before = state(original)
        top = next(f.index for f in original.polygons if f.normal.z > 0.99)
        result = self.call("set_shading", selector=TOP, smooth=True)
        self.assertEqual(result["changed_faces"], 1)
        self.assertEqual(result["created"], dict(vertices=0, edges=0, faces=0))
        self.assertEqual(result["removed"], dict(vertices=0, edges=0, faces=0))
        self.assertEqual(state(sibling.data), before)
        after = state(self.obj.data)
        self.assertEqual(after[:2], before[:2])
        self.assertEqual(after[3:5], before[3:5])
        self.assertEqual(
            [item.value for item in self.obj.data.attributes["Region"].data],
            list(range(4, 10)),
        )
        self.assertEqual(self.obj.material_slots[0].link, "OBJECT")
        self.assertEqual(self.obj.material_slots[0].material, material)
        for i, (old, new) in enumerate(zip(before[2], after[2], strict=True)):
            self.assertEqual(new, (*old[:2], i == top, *old[3:]))
        queried = self.call("query", selector=ALL_FACES)["elements"]
        self.assertEqual([f["smooth"] for f in queried], [i == top for i in range(6)])
        self.assertEqual(
            self.call("set_shading", selector=TOP, smooth=True)["changed_faces"], 0
        )
        self.assertEqual(
            self.call("set_shading", selector=ALL_FACES, smooth=False)["changed_faces"],
            1,
        )
        self.assertEqual(state(self.obj.data)[:5], before[:5])

    def test_shading_result_failure_is_atomic(self) -> None:
        original = self.obj.data
        before = state(original)
        mesh_count = len(bpy.data.meshes)
        with patch.object(mesh_api, "MeshEditResult", side_effect=ValueError("result")):
            self.error(
                "set_shading", "operation_failed", selector=ALL_FACES, smooth=True
            )
        self.assertEqual(self.obj.data, original)
        self.assertEqual(state(original), before)
        self.assertEqual(len(bpy.data.meshes), mesh_count)

    def test_normals_repair_consistency_and_inside_orientation(self) -> None:
        with mesh_api.snapshot(self.obj) as bm:
            bm.faces[0].normal_flip()
            bm.to_mesh(self.obj.data)
        self.call("recalculate_normals")
        with mesh_api.snapshot(self.obj) as bm:
            self.assertAlmostEqual(bm.calc_volume(signed=True), 8)
            self.assertTrue(all(e.is_contiguous for e in bm.edges))
        self.call("recalculate_normals", inside=True)
        with mesh_api.snapshot(self.obj) as bm:
            self.assertAlmostEqual(bm.calc_volume(signed=True), -8)
            self.assertTrue(all(e.is_contiguous for e in bm.edges))

    def test_every_mutation_isolates_shared_mesh_and_preserves_sibling(self) -> None:
        for operation, arguments in MUTATIONS:
            with self.subTest(operation=operation):
                self.reset()
                original = self.obj.data
                sibling = bpy.data.objects.new("Sibling", original)
                bpy.context.collection.objects.link(sibling)
                before = state(original)
                self.assertEqual(self.call("inspect")["mesh_users"], 2)
                result = self.call(operation, **arguments)
                self.assertEqual(result["mesh"]["mesh_users"], 1)
                self.assertNotEqual(self.obj.data, original)
                self.assertEqual(sibling.data, original)
                self.assertEqual(state(original), before)

    def test_all_mutations_protect_shape_keys_and_basis(self) -> None:
        self.obj.shape_key_add(name="Basis")
        self.obj.shape_key_add(name="Raised").data[0].co.z += 0.5
        original = self.obj.data
        keys = original.shape_keys
        before = [[tuple(v.co) for v in key.data] for key in keys.key_blocks]
        self.assertTrue(self.call("inspect")["has_shape_keys"])
        self.call("query", selector=TOP)
        for operation, arguments in MUTATIONS:
            with self.subTest(operation=operation):
                self.error(operation, "mesh_has_shape_keys", **arguments)
                self.assertEqual(self.obj.data, original)
                self.assertEqual(original.shape_keys, keys)
                self.assertEqual(
                    [[tuple(v.co) for v in key.data] for key in keys.key_blocks], before
                )

    def test_linked_data_inspectable_but_all_mutations_rejected(self) -> None:
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "mesh-library.blend"
        bpy.data.libraries.write(str(path), {self.obj.data})
        with bpy.data.libraries.load(str(path), link=True) as (source, target):
            target.meshes = [source.meshes[0]]
        self.obj.data = target.meshes[0]
        original = self.obj.data
        before = state(original)
        self.call("inspect")
        self.call("query", selector=TOP)
        for operation, arguments in MUTATIONS:
            self.error(operation, "invalid_context", **arguments)
        self.assertEqual(self.obj.data, original)
        self.assertEqual(state(original), before)
        path.unlink()

    def test_custom_normals_modifiers_animation_and_vertex_parent_protected(
        self,
    ) -> None:
        self.obj.data.normals_split_custom_set([(0, 0, 1)] * len(self.obj.data.loops))
        self.error("mark_seam", "invalid_context", selector=ALL_EDGES, seam=True)
        self.reset()
        modifier = self.obj.modifiers.new("Subdivision", "SUBSURF")
        self.error("extrude_faces", "invalid_context", selector=TOP, offset=[0, 0, 1])
        self.obj.modifiers.remove(modifier)
        self.obj.data.animation_data_create()
        self.error(
            "transform", "invalid_context", selector=UPPER, translation=[0, 0, 1]
        )
        self.obj.data.animation_data_clear()
        child = bpy.data.objects.new("Child", None)
        bpy.context.collection.objects.link(child)
        child.parent = self.obj
        child.parent_type = "VERTEX"
        self.error("delete_elements", "invalid_context", selector=TOP)

    def test_material_uv_vertex_groups_custom_attributes_survive_topology(self) -> None:
        for operation, arguments in MUTATIONS[:5]:
            with self.subTest(operation=operation):
                self.reset()
                data = self.obj.data
                materials = [
                    bpy.data.materials.new("First"),
                    bpy.data.materials.new("Second"),
                ]
                for material in materials:
                    data.materials.append(material)
                for face in data.polygons:
                    face.material_index = 1
                    face.use_smooth = True
                for edge in data.edges:
                    edge.use_seam = True
                original_uv = {
                    tuple(data.vertices[loop.vertex_index].co): tuple(
                        data.uv_layers.active.uv[loop.index].vector
                    )
                    for loop in data.loops
                    if loop.index
                    in next(f for f in data.polygons if f.normal.z < -0.99).loop_indices
                }
                data.uv_layers.new(name="RenderUV")
                data.uv_layers.active = data.uv_layers["UVMap"]
                data.uv_layers["RenderUV"].active_render = True
                group = self.obj.vertex_groups.new(name="Deform")
                group.add(list(range(8)), 0.75, "REPLACE")
                attribute = data.attributes.new("Weight", "FLOAT", "POINT")
                for item in attribute.data:
                    item.value = 0.625
                self.call(operation, **arguments)
                data = self.obj.data
                self.assertEqual(list(data.materials), materials)
                self.assertTrue(all(f.material_index == 1 for f in data.polygons))
                self.assertEqual(data.uv_layers.active.name, "UVMap")
                self.assertTrue(data.uv_layers["RenderUV"].active_render)
                self.assertEqual(len(data.uv_layers), 2)
                self.assertTrue(
                    all(len(layer.uv) == len(data.loops) for layer in data.uv_layers)
                )
                self.assertTrue(
                    all(
                        math.isfinite(c)
                        for layer in data.uv_layers
                        for point in layer.uv
                        for c in point.vector
                    )
                )
                self.assertTrue(
                    all(
                        abs(self.obj.vertex_groups["Deform"].weight(v.index) - 0.75)
                        < 1e-6
                        for v in data.vertices
                    )
                )
                self.assertTrue(
                    all(
                        abs(item.value - 0.625) < 1e-6
                        for item in data.attributes["Weight"].data
                    )
                )
                self.assertTrue(any(e.use_seam for e in data.edges))
                if operation in {"transform", "extrude_faces", "inset_faces"}:
                    bottom = next(f for f in data.polygons if f.normal.z < -0.99)
                    self.assertTrue(bottom.use_smooth)
                    self.assertEqual(
                        {
                            tuple(data.vertices[data.loops[i].vertex_index].co): tuple(
                                data.uv_layers["UVMap"].uv[i].vector
                            )
                            for i in bottom.loop_indices
                        },
                        original_uv,
                    )

    def test_ui_object_and_surviving_mesh_selection_is_preserved(self) -> None:
        for face in self.obj.data.polygons:
            face.select = False
        for edge in self.obj.data.edges:
            edge.select = False
        for vertex in self.obj.data.vertices:
            vertex.select = vertex.co.z == -1
        bottom = next(f for f in self.obj.data.polygons if f.normal.z < -0.99)
        bottom.hide = True
        bottom_vertices = tuple(bottom.vertices)
        bpy.ops.object.empty_add()
        active = bpy.context.object
        objects = [
            (obj.name, obj.select_get()) for obj in bpy.context.view_layer.objects
        ]
        self.call("extrude_faces", selector=TOP, offset=[0, 0, 1])
        self.assertEqual(bpy.context.view_layer.objects.active, active)
        self.assertEqual(
            [(obj.name, obj.select_get()) for obj in bpy.context.view_layer.objects],
            objects,
        )
        self.assertEqual(bpy.context.mode, "OBJECT")
        self.assertTrue(all(v.select == (v.co.z == -1) for v in self.obj.data.vertices))
        self.assertTrue(
            next(
                f
                for f in self.obj.data.polygons
                if tuple(f.vertices) == bottom_vertices
            ).hide
        )

    def test_staged_operator_and_result_failures_leave_no_partial_geometry(
        self,
    ) -> None:
        for shared in [False, True]:
            self.reset()
            original = self.obj.data
            if shared:
                sibling = bpy.data.objects.new("Sibling", original)
                bpy.context.collection.objects.link(sibling)
            before = state(original)
            count = len(bpy.data.meshes)

            def fail(bm: Any, vertices: Any, arguments: Any) -> None:
                bmesh.ops.delete(bm, geom=list(bm.verts), context="VERTS")
                raise RuntimeError("Injected failure after staged deletion")

            with patch.object(mesh_api, "apply_transform", side_effect=fail):
                self.error(
                    "transform",
                    "operation_failed",
                    selector=UPPER,
                    translation=[0, 0, 1],
                )
            with patch.object(
                mesh_api, "summary", side_effect=ValueError("Result validation failed")
            ):
                self.error(
                    "extrude_faces", "operation_failed", selector=TOP, offset=[0, 0, 1]
                )
            self.assertEqual(self.obj.data, original)
            self.assertEqual(state(original), before)
            self.assertEqual(len(bpy.data.meshes), count)

    def test_capacity_and_numeric_overflow_fail_without_writeback(self) -> None:
        original = self.obj.data
        before = state(original)
        with patch.object(mesh_api, "MAX_WORK_ELEMENTS", 100):
            self.error(
                "subdivide_edges", "invalid_context", selector=ALL_EDGES, cuts=32
            )
        self.error(
            "transform",
            "invalid_arguments",
            selector=ALL_FACES,
            scale=[3e38, 3e38, 3e38],
        )
        self.assertEqual(self.obj.data, original)
        self.assertEqual(state(original), before)

    def test_missing_nonmesh_and_main_thread_execution(self) -> None:
        self.error("inspect", "object_not_found", object_name="Missing")
        bpy.ops.object.empty_add()
        self.error(
            "query",
            "object_not_mesh",
            object_name=bpy.context.object.name,
            selector=TOP,
        )
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            patch.object(operations.logger, "exception"),
        ):
            result = pool.submit(self.response, "inspect").result()
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "operation_failed")

    def test_repeated_edits_do_not_leak_orphan_meshes(self) -> None:
        count = len(bpy.data.meshes)
        for flag in [True, False, True, False]:
            self.call("mark_seam", selector=ALL_EDGES, seam=flag)
            self.assertEqual(len(bpy.data.meshes), count)

    def test_pins_sharp_edges_and_vertex_group_definitions_survive(self) -> None:
        group = self.obj.vertex_groups.new(name="Locked")
        group.lock_weight = True
        self.obj.vertex_groups.new(name="Other")
        self.obj.vertex_groups.active_index = 1
        for edge in self.obj.data.edges:
            edge.use_edge_sharp = True
        with mesh_api.snapshot(self.obj) as bm:
            layer = bm.loops.layers.uv.active
            bottom = next(face for face in bm.faces if face.normal.z < -0.99)
            for loop in bottom.loops:
                loop[layer].pin_uv = True
            bm.to_mesh(self.obj.data)
        self.call("extrude_faces", selector=TOP, offset=[0, 0, 1])
        self.assertEqual(
            [group.name for group in self.obj.vertex_groups], ["Locked", "Other"]
        )
        self.assertTrue(self.obj.vertex_groups["Locked"].lock_weight)
        self.assertEqual(self.obj.vertex_groups.active_index, 1)
        with mesh_api.snapshot(self.obj) as bm:
            layer = bm.loops.layers.uv.active
            bottom = next(face for face in bm.faces if face.normal.z < -0.99)
            self.assertTrue(all(loop[layer].pin_uv for loop in bottom.loops))
            self.assertTrue(all(not edge.smooth for edge in bottom.edges))

    def test_returned_region_indices_are_bounded_and_current(self) -> None:
        data = bpy.data.meshes.new("Grid")
        side = 20
        data.from_pydata(
            [(x, y, 0) for y in range(side + 1) for x in range(side + 1)],
            [],
            [
                (
                    y * (side + 1) + x,
                    y * (side + 1) + x + 1,
                    (y + 1) * (side + 1) + x + 1,
                    (y + 1) * (side + 1) + x,
                )
                for y in range(side)
                for x in range(side)
            ],
        )
        self.obj.data = data
        result = self.call("extrude_faces", selector=ALL_FACES, offset=[0, 0, 1])
        region = result["region_faces"]
        self.assertEqual(region["total"], 400)
        self.assertEqual(len(region["indices"]), 256)
        self.assertTrue(region["truncated"])
        faces = self.call(
            "query",
            selector={
                "domain": "face",
                "mode": "indices",
                "indices": region["indices"],
            },
            limit=256,
        )
        self.assertTrue(all(face["center"][2] == 1 for face in faces["elements"]))


suite = unittest.defaultTestLoader.loadTestsFromTestCase(MeshTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print(f"BLENDER_MESH_TESTS_PASSED {result.testsRun}")
