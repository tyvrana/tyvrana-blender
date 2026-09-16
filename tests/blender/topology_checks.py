"""Native graph selection, staged refinement and deformation-topology evidence."""

import importlib
import json
import math
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
PACKAGE = "bl_ext.user_default.tyvrana_blender."
adapter = importlib.import_module(PACKAGE + "blender")
operations = importlib.import_module(PACKAGE + "operations")
mesh_api = importlib.import_module(PACKAGE + "mesh")
sweep_api = importlib.import_module(PACKAGE + "deformation_sweep")


class TopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.backend = adapter.BlenderBackend()
        bpy.context.scene.render.use_simplify = False

    def response(self, name: str, /, **args: Any) -> Any:
        return operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="native-topology",
                operation="blender." + name,
                arguments=args,
            ),
        )

    def call(self, name: str, /, **args: Any) -> Any:
        result = self.response(name, **args)
        self.assertIsInstance(result, OperationSuccess, str(result))
        return result.result

    def error(self, name: str, /, **args: Any) -> Any:
        result = self.response(name, **args)
        self.assertIsInstance(result, OperationFailure, str(result))
        return result.error

    def grid(
        self, n: int = 4, m: int = 4, name: str = "Surface", uv: bool = False
    ) -> Any:
        points = [[x / n, y / m, 0] for y in range(m + 1) for x in range(n + 1)]
        faces = [
            [
                y * (n + 1) + x,
                y * (n + 1) + x + 1,
                (y + 1) * (n + 1) + x + 1,
                (y + 1) * (n + 1) + x,
            ]
            for y in range(m)
            for x in range(n)
        ]
        kwargs = (
            {"corner_uvs": [[points[v][0], points[v][1]] for f in faces for v in f]}
            if uv
            else {}
        )
        self.call("mesh.create", name=name, vertices=points, faces=faces, **kwargs)
        return bpy.data.objects[name]

    def tube(
        self, ys: list[float], n: int = 12, name: str = "Surface", caps: bool = False
    ) -> Any:
        points = [
            [
                0.3 * math.cos(i * 2 * math.pi / n),
                y,
                0.3 * math.sin(i * 2 * math.pi / n),
            ]
            for y in ys
            for i in range(n)
        ]
        faces = [
            [j * n + i, (j + 1) * n + i, (j + 1) * n + (i + 1) % n, j * n + (i + 1) % n]
            for j in range(len(ys) - 1)
            for i in range(n)
        ]
        if caps:
            for end, y in [(0, ys[0]), (len(ys) - 1, ys[-1])]:
                center = len(points)
                points.append([0, y, 0])
                for i in range(n):
                    face = [center, end * n + i, end * n + (i + 1) % n]
                    faces.append(face if end == 0 else list(reversed(face)))
        self.call("mesh.create", name=name, vertices=points, faces=faces)
        return bpy.data.objects[name]

    def edge(self, obj: Any, a: int, b: int) -> int:
        return int(next(e.index for e in obj.data.edges if set(e.vertices) == {a, b}))

    def cut(self, obj: Any, a: int, b: int, factors: list[float]) -> Any:
        return self.call(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[dict(edge=self.edge(obj, a, b), from_vertex=a, factors=factors)],
        )

    def bind(self, obj: Any, count: int = 2, envelope: float = 0.25) -> None:
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(
                    name=f"B{i}",
                    envelope_distance=envelope,
                    head=[0, -2 + 4 * i / count, 0],
                    tail=[0, -2 + 4 * (i + 1) / count, 0],
                    **(dict(parent=f"B{i - 1}", connected=True) if i else {}),
                    limits=dict(
                        x=dict(minimum=-1.5, maximum=1.5),
                        y=dict(minimum=-0.8, maximum=0.8),
                        z=dict(minimum=-0.8, maximum=0.8),
                    ),
                )
                for i in range(count)
            ],
        )
        self.call(
            "armature.bind",
            object_name=obj.name,
            armature_object="Rig",
            weights=dict(method="envelopes", bones=[f"B{i}" for i in range(count)]),
        )

    def sweep(
        self, names: list[str], regions: list[Any] | None = None, combined: bool = False
    ) -> Any:
        poses = [
            dict(name=name, bones=[dict(name="B1", rotation=rotation)])
            for name, rotation in [
                ("rest", [0, 0, 0]),
                ("mild", [0.25, 0, 0]),
                ("medium", [0.7, 0, 0]),
                ("near_limit", [1.45, 0, 0]),
            ]
        ]
        if combined:
            poses.append(
                dict(name="combined", bones=[dict(name="B1", rotation=[0.9, 0.5, 0.6])])
            )
        return self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=names,
            poses=poses,
            regions=regions or [],
            bone_names=["B0", "B1"],
            sample_limit=2,
        )

    def test_loop_ring_boundary_and_components(self) -> None:
        obj = self.grid()
        ring = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(
                domain="edge", mode="topology", path="ring", seed=self.edge(obj, 0, 5)
            ),
        )
        self.assertEqual(ring["matched_count"], 5)
        loop = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(
                domain="edge", mode="topology", path="loop", seed=self.edge(obj, 6, 7)
            ),
        )
        self.assertEqual(loop["matched_count"], 4)
        boundary = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(
                domain="edge",
                mode="topology",
                path="boundary_loop",
                seed=self.edge(obj, 0, 1),
            ),
        )
        self.assertEqual(boundary["matched_count"], 16)
        connected = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(domain="face", mode="connected", seed=0),
            limit=2,
        )
        self.assertEqual(connected["matched_count"], 16)
        self.assertTrue(connected["truncated"])

    def test_neighborhood_and_valence(self) -> None:
        obj = self.grid()
        row = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(domain="vertex", mode="neighborhood", vertex=12, steps=1),
        )
        self.assertEqual(row["matched_count"], 5)
        row = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(domain="vertex", mode="valence"),
        )
        self.assertEqual(row["matched_count"], 0)
        row = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(domain="vertex", mode="valence", include_boundary=True),
        )
        self.assertEqual(row["matched_count"], 16)

    def test_frame_selection_and_transforms(self) -> None:
        obj = self.grid()
        obj.location = (10, 3, 2)
        obj.rotation_euler.z = 0.4
        obj.scale = (2, 3, 1)
        bpy.context.view_layer.update()
        selector = dict(
            domain="vertex",
            mode="region",
            region=dict(
                frame=dict(kind="object", object=obj.name),
                min=[-0.01, -0.01, -0.01],
                max=[0.51, 0.51, 0.01],
            ),
        )
        row = self.call("mesh.query", object_name=obj.name, selector=selector)
        self.assertEqual(row["matched_count"], 9)
        self.call(
            "mesh.transform",
            object_name=obj.name,
            selector=selector,
            translation=[0, 0, 0.1],
        )
        self.assertEqual(sum(v.co.z > 0.09 for v in obj.data.vertices), 9)

    def test_seed_errors_are_structured(self) -> None:
        obj = self.grid()
        for selector in [
            dict(domain="edge", mode="topology", path="ring", seed=999),
            dict(
                domain="edge",
                mode="topology",
                path="boundary_loop",
                seed=self.edge(obj, 6, 7),
            ),
        ]:
            self.error("mesh.query", object_name=obj.name, selector=selector)

    def test_nonuniform_uv_weight_interpolation_and_flags(self) -> None:
        obj = self.grid(2, 1, uv=True)
        group = obj.vertex_groups.new(name="Mask")
        for v in obj.data.vertices:
            group.add([v.index], v.co.y, "REPLACE")
        for e in obj.data.edges:
            e.use_seam = True
        attribute = obj.data.attributes.new("Signal", "FLOAT", "POINT")
        for v in obj.data.vertices:
            attribute.data[v.index].value = v.co.y
        result = self.cut(obj, 0, 3, [0.2, 0.6, 0.9])
        self.assertEqual(result["created"]["vertices"], 9)
        self.assertEqual(result["removed"]["vertices"], 0)
        self.assertTrue(result["indices_invalidated"])
        self.assertEqual(len(obj.data.vertices), 15)
        for v in obj.data.vertices:
            self.assertAlmostEqual(
                next(
                    (
                        g.weight
                        for g in v.groups
                        if g.group == obj.vertex_groups["Mask"].index
                    ),
                    0,
                ),
                v.co.y,
                places=5,
            )
            self.assertAlmostEqual(
                obj.data.attributes["Signal"].data[v.index].value, v.co.y, places=5
            )
        for loop in obj.data.loops:
            uv = obj.data.uv_layers[0].uv[loop.index].vector
            self.assertAlmostEqual(
                uv.y, obj.data.vertices[loop.vertex_index].co.y, places=5
            )
        self.assertTrue(any(e.use_seam for e in obj.data.edges))

    def test_batch_disjoint_strips_and_overlap_rollback(self) -> None:
        obj = self.grid(2, 2)
        original = obj.data
        e = self.edge(obj, 0, 3)
        self.error(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[
                dict(edge=e, from_vertex=0, factors=[0.3]),
                dict(edge=e, from_vertex=0, factors=[0.7]),
            ],
        )
        self.assertEqual(obj.data, original)
        self.call(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[
                dict(edge=e, from_vertex=0, factors=[0.3]),
                dict(edge=self.edge(obj, 3, 6), from_vertex=3, factors=[0.7]),
            ],
        )
        self.assertEqual(len(obj.data.vertices), 15)

    def test_shape_keys_and_unknown_modifiers_refused(self) -> None:
        obj = self.grid(2, 1)
        original = obj.data
        obj.shape_key_add(name="Basis")
        self.error(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[dict(edge=self.edge(obj, 0, 3), from_vertex=0, factors=[0.5])],
        )
        self.assertEqual(obj.data, original)
        obj.shape_key_clear()
        obj.modifiers.new("Cage", "LATTICE")
        self.error(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[dict(edge=self.edge(obj, 0, 3), from_vertex=0, factors=[0.5])],
        )
        self.assertEqual(obj.data, original)

    def test_failure_after_staging_cleans_candidate(self) -> None:
        obj = self.grid(2, 1)
        original = obj.data
        count = len(bpy.data.meshes)
        with patch.object(mesh_api, "summary", side_effect=RuntimeError("injected")):
            self.error(
                "mesh.insert_loops",
                object_name=obj.name,
                cuts=[
                    dict(edge=self.edge(obj, 0, 3), from_vertex=0, factors=[0.2, 0.5])
                ],
            )
        self.assertEqual(obj.data, original)
        self.assertEqual(len(bpy.data.meshes), count)

    def test_bound_refinement_and_pose_restoration(self) -> None:
        obj = self.tube([-2, -0.8, 0.8, 2])
        self.bind(obj)
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B1", rotation=[0.123, 0, 0])],
        )
        before = tuple(bpy.data.objects["Rig"].pose.bones["B1"].rotation_euler)
        self.cut(obj, 12, 24, [0.25, 0.5, 0.75])
        weights = self.call("weights.inspect", object_name=obj.name)
        self.assertEqual(weights["non_normalized_vertex_count"], 0)
        result = self.sweep(
            [obj.name],
            regions=[
                dict(
                    name="joint",
                    frame=dict(kind="bone", object="Rig", bone="B1"),
                    min=[-1, -1, -1],
                    max=[1, 1, 1],
                )
            ],
        )
        self.assertTrue(result["restored"])
        self.assertEqual(
            tuple(bpy.data.objects["Rig"].pose.bones["B1"].rotation_euler), before
        )
        self.assertGreater(
            result["poses"][2]["meshes"][0]["regions"][0]["vertex_count"], 0
        )

    def test_sweep_failure_restores_all_pose_channels(self) -> None:
        obj = self.tube([-2, 0, 2])
        self.bind(obj)
        bone = bpy.data.objects["Rig"].pose.bones["B1"]
        bone.rotation_mode = "QUATERNION"
        bone.rotation_quaternion = (0.99, 0.1, 0, 0)
        saved = tuple(bone.rotation_quaternion)
        with patch.object(
            sweep_api.deformation_qa, "compare", side_effect=RuntimeError("injected")
        ):
            self.error(
                "deformation.sweep",
                armature_object="Rig",
                objects=[obj.name],
                poses=[
                    dict(name="bend", bones=[dict(name="B1", rotation=[0.7, 0, 0])])
                ],
            )
        self.assertEqual(bone.rotation_mode, "QUATERNION")
        self.assertEqual(tuple(bone.rotation_quaternion), saved)

    def test_hinge_comparison_and_multiaxis(self) -> None:
        obj = self.tube([-2, -0.8, 0.8, 2], caps=True)
        self.bind(obj)
        coarse = self.sweep([obj.name], combined=True)
        self.cut(obj, 12, 24, [0.2, 0.4, 0.5, 0.6, 0.8])
        refined = self.sweep([obj.name], combined=True)
        old = coarse["poses"][3]["meshes"][0]["qa"]
        new = refined["poses"][3]["meshes"][0]["qa"]
        self.assertLess(
            new["triangle_angle_change_radians"]["p95"],
            old["triangle_angle_change_radians"]["p95"],
        )
        self.assertLess(abs(new["volume_ratio"] - 1), abs(old["volume_ratio"] - 1))
        print(
            "TOPOLOGY_HINGE_COMPARISON",
            json.dumps(dict(coarse=coarse, refined=refined)),
        )
        self.assertEqual(len(refined["poses"]), 5)
        self.assertGreater(
            refined["poses"][3]["meshes"][0]["qa"]["triangle_angle_change_radians"][
                "count"
            ],
            0,
        )

    def test_surface_attachment_invalidation(self) -> None:
        self.call(
            "mesh.create",
            name="Surface",
            vertices=[
                [0, 0, 0],
                [0.5, 0, 0],
                [1, 0, 0],
                [0, 1, 0],
                [0.5, 1, 0],
                [1, 1, 0],
                [0.2, -0.3, 0],
            ],
            faces=[[0, 1, 4, 3], [1, 2, 5, 4], [1, 0, 6]],
        )
        obj = bpy.data.objects["Surface"]
        self.call(
            "curve.create",
            curves=[
                dict(
                    name="Guide",
                    splines=[dict(points=[dict(co=[0, 0, 0.1]), dict(co=[1, 0, 0.1])])],
                    bindings=[
                        dict(
                            spline=0,
                            point=0,
                            target=dict(
                                kind="surface",
                                object=obj.name,
                                face=2,
                                barycentric=[0.5, 0.25, 0.25],
                            ),
                        )
                    ],
                )
            ],
        )
        self.assertTrue(
            self.call("curve.inspect", names=["Guide"])["curves"][0]["valid"]
        )
        self.cut(obj, 0, 3, [0.2, 0.5])
        row = self.call("curve.inspect", names=["Guide"])["curves"][0]
        self.assertFalse(row["valid"])
        print("TOPOLOGY_ATTACHMENT", json.dumps(row["bindings"]))

    def test_subdivision_mirror_and_shrinkwrap_stacks(self) -> None:
        for stack in [
            [],
            ["subdivision_surface"],
            ["mirror", "subdivision_surface"],
            ["shrinkwrap", "subdivision_surface"],
        ]:
            self.setUp()
            obj = self.grid(4, 2)
            source = self.grid(8, 8, name="Source")
            for kind in stack:
                self.call(
                    "modifier.create",
                    object_name=obj.name,
                    type=kind,
                    settings=dict(target=source.name)
                    if kind == "shrinkwrap"
                    else dict(levels=1, render_levels=1)
                    if kind == "subdivision_surface"
                    else {},
                )
            self.cut(obj, 0, 5, [0.25, 0.5, 0.75])
            row = self.call("mesh.inspect_evaluated", object_name=obj.name)
            self.assertEqual(
                [m.type for m in obj.modifiers],
                [
                    dict(
                        subdivision_surface="SUBSURF",
                        mirror="MIRROR",
                        shrinkwrap="SHRINKWRAP",
                    )[k]
                    for k in stack
                ],
            )
            print("TOPOLOGY_STACK", stack, json.dumps(row))

    def test_source_projected_multicut_and_flow(self) -> None:
        source = self.grid(16, 16, name="Source")
        for v in source.data.vertices:
            v.co.z = 0.1 * math.sin(v.co.x * math.pi) * math.sin(v.co.y * math.pi)
        source.data.update()
        self.call("retopo.create_target", source_object=source.name, name="Target")
        self.call(
            "retopo.seed_patch",
            source_object=source.name,
            target_object="Target",
            center=[0.5, 0.5, 0.1],
            tangent_direction=[1, 0, 0],
            width=0.8,
            height=0.8,
            u_segments=4,
            v_segments=2,
        )
        target = bpy.data.objects["Target"]
        self.call(
            "modifier.create",
            object_name=target.name,
            type="shrinkwrap",
            settings=dict(target=source.name),
        )
        self.call(
            "modifier.create",
            object_name=target.name,
            type="subdivision_surface",
            settings=dict(levels=1, render_levels=1),
        )
        row = self.call(
            "retopo.insert_loop",
            source_object=source.name,
            target_object=target.name,
            edge=dict(mode="indices", domain="edge", indices=[self.edge(target, 0, 5)]),
            from_vertex=0,
            factors=[0.2, 0.5, 0.8],
        )
        self.assertLess(row["correspondence_after"]["vertices"]["max_distance"], 1e-5)
        self.assertEqual(row["flow_edges"]["total"], 12)
        self.call(
            "retopo.relax",
            source_object=source.name,
            target_object=target.name,
            selector=dict(mode="all", domain="vertex"),
            iterations=2,
        )
        print(
            "TOPOLOGY_SOURCE",
            json.dumps(
                self.call(
                    "retopo.inspect",
                    source_object=source.name,
                    target_object=target.name,
                )
            ),
        )

    def test_branch_patch_reconstruction_poles_and_subdivision(self) -> None:
        cells = {
            (x, y) for x in range(-3, 3) for y in range(-3, 2) if y >= 0 or x in {-1, 0}
        }
        coordinates = sorted(
            {
                (x + dx, y + dy, z)
                for x, y in cells
                for dx, dy in [(0, 0), (1, 0), (1, 1), (0, 1)]
                for z in [-0.2, 0.2]
            }
        )
        index = {p: i for i, p in enumerate(coordinates)}
        faces = []
        for x, y in sorted(cells):
            corners = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
            faces.append([index[(a, b, 0.2)] for a, b in corners])
            faces.append([index[(a, b, -0.2)] for a, b in reversed(corners)])
            for (a, b), (c, d), (dx, dy) in zip(
                corners,
                corners[1:] + corners[:1],
                [(0, -1), (1, 0), (0, 1), (-1, 0)],
                strict=True,
            ):
                if (x + dx, y + dy) not in cells:
                    faces.append(
                        [
                            index[(a, b, -0.2)],
                            index[(c, d, -0.2)],
                            index[(c, d, 0.2)],
                            index[(a, b, 0.2)],
                        ]
                    )
        for name in ["Source", "Junction"]:
            self.call(
                "mesh.create",
                name=name,
                vertices=[list(p) for p in coordinates],
                faces=faces,
            )
        obj = bpy.data.objects["Junction"]
        # A deliberately removed2x2 patch uses existing delete + grid-fill.
        self.call(
            "mesh.delete_elements",
            object_name=obj.name,
            selector=dict(
                domain="face",
                mode="box",
                min=[-0.01, -0.01, 0.19],
                max=[2.01, 2.01, 0.21],
            ),
            face_mode="faces_and_unused",
        )
        boundary = next(
            e
            for e in obj.data.edges
            if len(
                [p for p in obj.data.polygons if set(e.vertices).issubset(p.vertices)]
            )
            == 1
        )
        start = min(boundary.vertices)
        row = self.call(
            "retopo.fill_boundary",
            source_object="Source",
            target_object=obj.name,
            boundary=dict(
                mode="topology",
                domain="edge",
                path="boundary_loop",
                seed=boundary.index,
            ),
            corner_vertex=start,
            span=2,
        )
        self.assertEqual(row["after"]["boundary_edge_count"], 0)
        self.call(
            "modifier.create",
            object_name=obj.name,
            type="subdivision_surface",
            settings=dict(levels=1, render_levels=1),
        )
        poles = self.call("mesh.inspect_topology", object_name=obj.name, sample_limit=4)
        self.assertGreater(poles["pole_count"], 0)
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(name="B0", head=[0, -3, 0], tail=[0, 0, 0], envelope_distance=2),
                dict(
                    name="B1",
                    head=[0, 0, 0],
                    tail=[3, 1, 0],
                    parent="B0",
                    connected=True,
                    envelope_distance=2,
                    limits=dict(
                        x=dict(minimum=-1.5, maximum=1.5),
                        y=dict(minimum=-0.8, maximum=0.8),
                        z=dict(minimum=-0.8, maximum=0.8),
                    ),
                ),
                dict(
                    name="B2",
                    head=[0, 0, 0],
                    tail=[-3, 1, 0],
                    parent="B0",
                    connected=True,
                    envelope_distance=2,
                ),
            ],
        )
        self.call(
            "armature.bind",
            object_name=obj.name,
            armature_object="Rig",
            weights=dict(method="envelopes", bones=["B0", "B1", "B2"]),
        )
        print(
            "TOPOLOGY_BRANCH",
            json.dumps(
                dict(topology=poles, poses=self.sweep([obj.name], combined=True))
            ),
        )

    def test_long_chain_batched_refinement(self) -> None:
        obj = self.tube([-2 + i * 0.5 for i in range(9)], n=16)
        self.bind(obj, 8)
        cuts = [
            dict(
                edge=self.edge(obj, j * 16, (j + 1) * 16),
                from_vertex=j * 16,
                factors=[0.25, 0.5, 0.75],
            )
            for j in range(8)
        ]
        self.call("mesh.insert_loops", object_name=obj.name, cuts=cuts)
        poses = [
            dict(
                name=f"pose{level}",
                bones=[
                    dict(
                        name=f"B{i}",
                        rotation=[level * 0.09 * (-1) ** i, 0, level * 0.03],
                    )
                    for i in range(8)
                ],
            )
            for level in range(5)
        ]
        row = self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=[obj.name],
            poses=poses,
            sample_limit=1,
        )
        self.assertEqual(len(obj.data.vertices), 528)
        print("TOPOLOGY_CHAIN", json.dumps(row))

    def test_moderate_scale_bounded_results(self) -> None:
        n = 128
        m = 160
        points = [
            (
                0.3 * math.cos(i * 2 * math.pi / n),
                -2 + 4 * j / m,
                0.3 * math.sin(i * 2 * math.pi / n),
            )
            for j in range(m + 1)
            for i in range(n)
        ]
        faces = [
            (j * n + i, (j + 1) * n + i, (j + 1) * n + (i + 1) % n, j * n + (i + 1) % n)
            for j in range(m)
            for i in range(n)
        ]
        data = bpy.data.meshes.new("Scale")
        data.from_pydata(points, [], faces)
        data.update()
        obj = bpy.data.objects.new("Scale", data)
        bpy.context.scene.collection.objects.link(obj)
        self.bind(obj)
        stamp = time.perf_counter()
        query = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(
                mode="topology", domain="edge", path="ring", seed=self.edge(obj, 0, n)
            ),
            limit=4,
        )
        query_seconds = time.perf_counter() - stamp
        self.assertEqual(query["matched_count"], 128)
        self.assertEqual(len(query["elements"]), 4)
        stamp = time.perf_counter()
        self.cut(obj, 0, n, [i / 17 for i in range(1, 17)])
        refine_seconds = time.perf_counter() - stamp
        stamp = time.perf_counter()
        topology = self.call(
            "mesh.inspect_topology", object_name=obj.name, sample_limit=4
        )
        topology_seconds = time.perf_counter() - stamp
        stamp = time.perf_counter()
        sweep = self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=[obj.name],
            poses=[
                dict(name="rest"),
                dict(name="bend", bones=[dict(name="B1", rotation=[0.8, 0, 0])]),
            ],
            sample_limit=1,
        )
        sweep_seconds = time.perf_counter() - stamp
        self.assertLess(len(json.dumps(topology)), 5000)
        print(
            "TOPOLOGY_SCALE",
            json.dumps(
                dict(
                    vertices=len(points),
                    faces=len(faces),
                    query_seconds=query_seconds,
                    refine_seconds=refine_seconds,
                    refined_vertices=len(obj.data.vertices),
                    topology_seconds=topology_seconds,
                    sweep_seconds=sweep_seconds,
                    query_bytes=len(json.dumps(query)),
                    topology_bytes=len(json.dumps(topology)),
                    sweep_bytes=len(json.dumps(sweep)),
                )
            ),
        )

    def test_multiaxis_pole_flow_evidence(self) -> None:
        source = self.grid(6, 6, name="Source")
        obj = self.grid(6, 6)
        row = self.call(
            "retopo.rotate_edge",
            source_object=source.name,
            target_object=obj.name,
            edge=dict(mode="indices", domain="edge", indices=[self.edge(obj, 23, 30)]),
        )
        poles = self.call(
            "mesh.query",
            object_name=obj.name,
            selector=dict(mode="valence", domain="vertex"),
            limit=8,
        )
        self.assertEqual(poles["matched_count"], 4)
        self.bind(obj, envelope=10)
        result = self.sweep(
            [obj.name],
            combined=True,
            regions=[
                dict(
                    name="flow",
                    frame=dict(kind="bone", object="Rig", bone="B1"),
                    min=[0, 0, -1],
                    max=[1, 1, 1],
                )
            ],
        )
        print(
            "TOPOLOGY_MULTIAXIS",
            json.dumps(dict(poles=poles, rotation=row["flow_edges"], sweep=result)),
        )

    def test_support_spacing_variants_and_redistribution(self) -> None:
        variants = {}
        for label, factors in [
            ("few", []),
            ("poor", [0.03, 0.07, 0.12]),
            ("adequate", [0.25, 0.5, 0.75]),
            ("redistributed", [0.03, 0.07, 0.12]),
        ]:
            self.setUp()
            obj = self.tube([-2, -0.8, 0.8, 2], caps=True)
            self.bind(obj)
            if factors:
                self.cut(obj, 12, 24, factors)
            if label == "redistributed":
                for old, new in zip([0.03, 0.07, 0.12], [0.25, 0.5, 0.75], strict=True):
                    y = -0.8 + 1.6 * old
                    self.call(
                        "mesh.transform",
                        object_name=obj.name,
                        selector=dict(
                            mode="box",
                            domain="vertex",
                            min=[-1, y - 0.0001, -1],
                            max=[1, y + 0.0001, 1],
                        ),
                        translation=[0, 1.6 * (new - old), 0],
                    )
            self.call(
                "weights.assign",
                object_name=obj.name,
                layers=[
                    dict(
                        selector=dict(mode="all", domain="vertex"),
                        weights=dict(
                            mode="gradient",
                            start=[0, -0.8, 0],
                            end=[0, 0.8, 0],
                            start_influences=[dict(bone="B0", weight=1)],
                            end_influences=[dict(bone="B1", weight=1)],
                            interpolation="linear",
                        ),
                    )
                ],
            )
            variants[label] = self.sweep([obj.name])
        print("TOPOLOGY_SPACING", json.dumps(variants))
        poor = variants["poor"]["poses"][3]["meshes"][0]["qa"]
        better = variants["redistributed"]["poses"][3]["meshes"][0]["qa"]
        self.assertLess(abs(better["volume_ratio"] - 1), abs(poor["volume_ratio"] - 1))

    def test_shared_data_isolation_materials_and_refusal(self) -> None:
        obj = self.grid(2, 1, uv=True)
        material = bpy.data.materials.new("SurfaceMaterial")
        obj.data.materials.append(material)
        other = obj.copy()
        bpy.context.scene.collection.objects.link(other)
        original = obj.data
        self.cut(obj, 0, 3, [0.2, 0.8])
        self.assertEqual(other.data, original)
        self.assertEqual(len(other.data.vertices), 6)
        self.assertEqual(list(obj.data.materials), [material])
        self.assertTrue(all(f.material_index == 0 for f in obj.data.polygons))
        parent = bpy.data.objects.new("Dependent", None)
        bpy.context.scene.collection.objects.link(parent)
        parent.parent = obj
        parent.parent_type = "VERTEX"
        before = obj.data
        self.error(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[dict(edge=0, from_vertex=0, factors=[0.5])],
        )
        self.assertEqual(obj.data, before)

    def test_empty_regions_singular_frames_and_traversal_budget(self) -> None:
        obj = self.grid()
        topology_selection = importlib.import_module(PACKAGE + "topology_selection")
        with patch.object(topology_selection, "MAX_TRAVERSAL", 3):
            self.error(
                "mesh.query",
                object_name=obj.name,
                selector=dict(mode="connected", domain="face", seed=0),
            )
        obj.scale.x = 0
        bpy.context.view_layer.update()
        self.error(
            "mesh.query",
            object_name=obj.name,
            selector=dict(
                mode="region",
                domain="vertex",
                region=dict(
                    frame=dict(kind="object", object=obj.name),
                    min=[0, 0, 0],
                    max=[1, 1, 1],
                ),
            ),
        )
        obj.scale.x = 1
        bpy.context.view_layer.update()
        self.bind(obj, envelope=10)
        result = self.sweep(
            [obj.name],
            regions=[dict(name="empty", min=[100, 100, 100], max=[101, 101, 101])],
        )
        region = result["poses"][0]["meshes"][0]["regions"][0]
        self.assertEqual(region["vertex_count"], 0)
        self.assertIsNone(region["qa"]["edge_ratios"]["p95"])
        self.assertIsNone(region["qa"]["volume_ratio"])

    def test_pose_sample_budget_restores_state(self) -> None:
        obj = self.tube([-2, 0, 2])
        self.bind(obj)
        armature = bpy.data.objects["Rig"]
        armature.data.pose_position = "REST"
        with patch.object(sweep_api, "MAX_SWEEP_VERTEX_SAMPLES", 1):
            self.error(
                "deformation.sweep",
                armature_object="Rig",
                objects=[obj.name],
                poses=[dict(name="rest")],
            )
        self.assertEqual(armature.data.pose_position, "REST")

    def test_nonquad_refinement_and_frame_retopology(self) -> None:
        self.call(
            "mesh.create",
            name="Triangle",
            vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            faces=[[0, 1, 2]],
        )
        obj = bpy.data.objects["Triangle"]
        original = obj.data
        self.error(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[
                dict(
                    edge=0,
                    from_vertex=int(obj.data.edges[0].vertices[0]),
                    factors=[0.5],
                )
            ],
        )
        self.assertEqual(obj.data, original)
        source = self.grid(4, 4, name="Source")
        target = self.grid(2, 2, name="Target")
        row = self.call(
            "retopo.project",
            source_object=source.name,
            target_object=target.name,
            selector=dict(
                mode="region",
                domain="vertex",
                region=dict(
                    frame=dict(kind="object", object=target.name),
                    min=[0, 0, -0.1],
                    max=[0.5, 0.5, 0.1],
                ),
            ),
        )
        self.assertEqual(row["selected_count"], 4)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TopologyTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("TOPOLOGY_NATIVE_PASSED", result.testsRun)
