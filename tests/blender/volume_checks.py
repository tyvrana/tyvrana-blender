"""Native swept-volume construction and layer QA on controlled synthetic geometry."""

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
from mathutils import Matrix  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

layers = importlib.import_module(PACKAGE + "layers")
geometry = importlib.import_module(PACKAGE + "layer_geometry")


class VolumeTests(TopologyTests):
    def setUp(self) -> None:
        super().setUp()
        for key in list(bpy.context.scene.keys()):
            if key.startswith(layers.KEY):
                del bpy.context.scene[key]

    def volume(self, name: str, **kwargs: Any) -> Any:
        return self.call("volume.inspect", objects=[dict(object_name=name, **kwargs)])[
            "volumes"
        ][0]

    def pair(self, source: str = "Outer", target: str = "Inner", **kwargs: Any) -> Any:
        return self.call(
            "layer.inspect",
            queries=[dict(mode="current", source=source, target=target)],
            **kwargs,
        )["layers"][0]

    def reference(self, name: str = "Pair", **kwargs: Any) -> Any:
        return self.call(
            "layer.inspect", queries=[dict(mode="reference", name=name)], **kwargs
        )["layers"][0]

    def planes(self, n: int = 8) -> tuple[Any, Any]:
        inner = self.grid(n, n, "Inner")
        inner.scale = (4, 4, 1)
        inner.location = (-1, -1, 0)
        outer = self.grid(n, n, "Outer")
        outer.location.z = 0.2
        bpy.context.view_layer.update()
        return inner, outer

    def capture(self) -> Any:
        return self.call(
            "layer.capture_reference",
            references=[dict(name="Pair", source="Outer", target="Inner")],
        )

    def guide(self, name: str = "Volume", curved: bool = False) -> Any:
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(name="A", head=[0, 0, 0], tail=[0, 1, 0]),
                dict(name="B", head=[0, 2, 0], tail=[0, 3, 0]),
            ],
        )
        points = [
            dict(co=[0, 0, 0], radius=0.4),
            dict(co=[0.4 if curved else 0, 1, 0], radius=1),
            dict(co=[0, 2, 0], radius=0.4),
        ]
        return self.call(
            "curve.create",
            curves=[
                dict(
                    name=name,
                    splines=[dict(type="BEZIER" if curved else "POLY", points=points)],
                    settings=dict(
                        resolution=8,
                        profile=dict(
                            kind="circle", radius=0.25, resolution=16, caps=True
                        ),
                    ),
                    bindings=[
                        dict(
                            spline=0,
                            point=i,
                            target=dict(kind="bone", object="Rig", bone=bone),
                        )
                        for i, bone in [(0, "A"), (2, "B")]
                    ],
                )
            ],
        )

    def test_two_point_attachment_length_volume_and_snapshot(self) -> None:
        self.guide()
        rest = self.volume("Volume")
        self.assertAlmostEqual(rest["path"]["length"], 2, places=5)
        self.assertTrue(rest["evaluated"]["closed_consistent"])
        self.call("volume.snapshot", objects=[dict(source="Volume", name="Rest")])
        for label, location in [
            ("short", [0, -0.4, 0]),
            ("long", [0, 0.6, 0]),
            ("off_axis", [0.5, 0, 0.3]),
        ]:
            self.call(
                "armature.pose",
                object_name="Rig",
                reset=True,
                bones=[dict(name="B", location=location)],
            )
            row = self.volume("Volume", reference="Rest")
            self.assertLess(row["path"]["attachment_error_max"], 1e-5)
            self.assertGreater(row["volume_ratio"], 0)
            self.assertNotAlmostEqual(row["path"]["length_ratio"], 1, places=3)
            print("VOLUME_ATTACHMENT", json.dumps(dict(pose=label, result=row)))
        frozen = self.volume("Rest")
        self.assertAlmostEqual(
            frozen["evaluated"]["volume"], rest["evaluated"]["volume"], places=6
        )
        self.assertEqual(len(bpy.data.objects["Rest"].modifiers), 0)

    def test_curved_profile_multi_pose_sweep_restores(self) -> None:
        self.guide(curved=True)
        self.call("volume.snapshot", objects=[dict(source="Volume", name="Rest")])
        rig = bpy.data.objects["Rig"]
        original = rig.pose.bones["B"].matrix_basis.copy()
        poses = [
            dict(name=n, bones=[dict(name="B", location=[x, y, z], rotation=[x, 0, z])])
            for n, x, y, z in [
                ("rest", 0, 0, 0),
                ("mild", 0.1, -0.1, 0),
                ("medium", 0.3, -0.3, 0.1),
                ("near_limit", 0.7, -0.6, 0.3),
                ("combined", -0.3, 0.4, -0.2),
            ]
        ]
        result = self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=[],
            poses=poses,
            volumes=[dict(object_name="Volume", reference="Rest")],
            sample_limit=0,
        )
        self.assertEqual(rig.pose.bones["B"].matrix_basis, original)
        for pose in result["poses"]:
            row = pose["volumes"][0]
            self.assertGreater(row["evaluated"]["volume"], 0)
            self.assertLess(row["path"]["attachment_error_max"], 1e-5)
        self.assertGreater(
            max(p["volumes"][0]["path"]["length"] for p in result["poses"])
            - min(p["volumes"][0]["path"]["length"] for p in result["poses"]),
            0.3,
        )
        print("VOLUME_SWEEP", json.dumps(result))

    def test_known_cube_area_volume_open_and_reversed_winding(self) -> None:
        self.call("object.create_primitive", primitive="cube", name="Box")
        row = self.volume("Box")
        self.assertAlmostEqual(row["evaluated"]["volume"], 8, places=5)
        self.assertAlmostEqual(row["evaluated"]["surface_area"], 24, places=5)
        self.grid(name="Open")
        self.assertIsNone(self.volume("Open")["evaluated"]["volume"])
        self.call(
            "mesh.recalculate_normals",
            object_name="Box",
            inside=True,
        )
        self.assertAlmostEqual(self.volume("Box")["evaluated"]["volume"], 8, places=5)

    def test_parallel_gap_ray_penetration_contact_and_winding(self) -> None:
        inner, outer = self.planes()
        result = self.pair(
            ray_direction="opposite_source_normal", minimum_separation=0.21
        )
        self.assertAlmostEqual(result["separation"]["mean"], 0.2, places=6)
        self.assertAlmostEqual(result["normal_ray_distance"]["mean"], 0.2, places=6)
        self.assertEqual(result["normal_ray_misses"], 0)
        self.assertEqual(result["negative_side_samples"], 0)
        self.assertEqual(result["below_minimum_samples"], 81)
        for depth in [0, -0.0001, -0.1]:
            outer.location.z = depth
            bpy.context.view_layer.update()
            result = self.pair()
            self.assertEqual(result["negative_side_samples"], 81 if depth < 0 else 0)
            self.assertAlmostEqual(result["negative_side_depth_max"], -depth, places=6)
            self.assertEqual(result["contact_samples"], 81 if depth >= -0.001 else 0)
        outer.location.z = 0.0001
        bpy.context.view_layer.update()
        self.assertEqual(self.pair()["contact_samples"], 81)
        self.call(
            "mesh.recalculate_normals",
            object_name="Inner",
            inside=True,
        )
        self.assertEqual(self.pair()["negative_side_samples"], 81)

    def test_ray_distance_is_not_nearest_distance(self) -> None:
        inner, outer = self.planes()
        outer.rotation_euler.x = 0.3
        bpy.context.view_layer.update()
        row = self.pair(ray_direction="opposite_source_normal")
        self.assertGreater(
            row["normal_ray_distance"]["mean"], row["separation"]["mean"] + 0.005
        )
        self.assertEqual(
            self.pair(ray_direction="source_normal")["normal_ray_misses"], 81
        )

    def test_known_sliding_sign_and_rigid_motion_invariance(self) -> None:
        inner, outer = self.planes()
        self.capture()
        outer.location.x = 0.25
        outer.location.y = -0.1
        outer.location.z = 0.23
        bpy.context.view_layer.update()
        row = self.reference(worst_limit=32)
        self.assertAlmostEqual(
            row["tangential_movement"]["mean"], math.hypot(0.25, 0.1), places=5
        )
        self.assertAlmostEqual(row["normal_change"]["mean"], 0.03, places=5)
        # All grid target triangles have diagonal or horizontal X frames; signed
        # components are independently projected against their captured frames.
        data = layers.reference_data(layers.records()["Pair"])
        with geometry.SurfaceCache() as cache:
            target = cache.get("Inner")
            from mathutils import Vector

            for sample in row["worst"]:
                saved = next(s for s in data.samples if s.vertex == sample["vertex"])
                x, y, _ = layers.frame(target, saved.triangle)
                delta = Vector((0.25, -0.1, 0))
                self.assertAlmostEqual(
                    sample["tangential_delta"][0], delta.dot(x), places=5
                )
                self.assertAlmostEqual(
                    sample["tangential_delta"][1], delta.dot(y), places=5
                )
        transform = Matrix.Translation((2, -1, 3)) @ Matrix.Rotation(0.7, 4, "Y")
        for obj in (inner, outer):
            obj.matrix_world = transform @ obj.matrix_world
        bpy.context.view_layer.update()
        moved = self.reference()
        self.assertAlmostEqual(
            moved["tangential_movement"]["mean"],
            row["tangential_movement"]["mean"],
            places=5,
        )
        self.assertAlmostEqual(moved["normal_change"]["mean"], 0.03, places=5)

    def test_reference_rename_topology_and_remove(self) -> None:
        inner, outer = self.planes()
        self.capture()
        inner.name = "Renamed"
        self.assertTrue(self.reference()["valid"])
        self.error(
            "layer.capture_reference",
            references=[dict(name="Pair", source="Outer", target="Renamed")],
        )
        self.call(
            "mesh.subdivide_edges",
            object_name=outer.name,
            selector=dict(mode="all", domain="edge"),
            cuts=1,
        )
        row = self.reference()
        self.assertFalse(row["valid"])
        self.assertIn("topology", row["reason"])
        self.call(
            "layer.capture_reference",
            references=[
                dict(name="Pair", source="Outer", target="Renamed", replace=True)
            ],
        )
        self.assertTrue(self.reference()["valid"])
        self.error("layer.remove_reference", names=["Pair", "Missing"])
        self.assertEqual(self.call("layer.inspect")["references"], ["Pair"])
        self.call("layer.remove_reference", names=["Pair"])
        self.assertEqual(self.call("layer.inspect")["references"], [])

    def test_surface_transfer_three_layers_pose_and_topology_guard(self) -> None:
        inner, middle = self.planes()
        outer = self.grid(16, 16, "Shell")
        outer.location.z = 0.4
        bpy.context.view_layer.update()
        self.bind(inner, envelope=8)
        self.call(
            "surface_deform.bind",
            driver="Inner",
            driven=["Outer"],
            bind_state="current",
        )
        self.call(
            "surface_deform.bind",
            driver="Outer",
            driven=["Shell"],
            bind_state="current",
        )
        self.capture()
        result = self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=["Inner"],
            sample_limit=0,
            volumes=[
                dict(object_name="Inner"),
                dict(object_name="Outer"),
                dict(object_name="Shell"),
            ],
            layers=dict(
                queries=[
                    dict(mode="reference", name="Pair"),
                    dict(mode="current", source="Shell", target="Outer"),
                ],
                worst_limit=0,
            ),
            poses=[
                dict(name=n, bones=[dict(name="B1", rotation=[a, 0, 0])])
                for n, a in [
                    ("rest", 0),
                    ("mild", 0.2),
                    ("medium", 0.6),
                    ("near_limit", 1.4),
                    ("combined", 0.9),
                ]
            ],
        )
        self.assertTrue(all(p["layers"][0]["valid"] for p in result["poses"]))
        self.assertNotEqual(
            result["poses"][0]["volumes"][2]["evaluated"]["bounds_max"],
            result["poses"][2]["volumes"][2]["evaluated"]["bounds_max"],
        )
        print("LAYER_TRANSFER_SWEEP", json.dumps(result))
        # Controlled native external edit simulates stale binding; normal typed
        # mesh operations already refuse unsafe topology edits on bound objects.
        inner.data.polygons[0].vertices = list(
            reversed(inner.data.polygons[0].vertices)
        )
        row = self.call("surface_deform.inspect", objects=["Outer"])
        self.assertFalse(row["bindings"][0]["valid"])
        self.assertFalse(self.reference()["valid"])

    def test_linear_twist_volume_loss_and_explicit_corrective(self) -> None:
        obj = self.tube([-2, -1, 0, 1, 2], caps=True)
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(name=n, head=[0, -2, 0], tail=[0, 2, 0], envelope_distance=3)
                for n in ["Fixed", "Twist"]
            ],
        )
        self.call(
            "armature.bind",
            object_name="Surface",
            armature_object="Rig",
            weights=dict(method="envelopes", bones=["Fixed", "Twist"]),
            preserve_volume=False,
        )
        self.call(
            "weights.assign",
            object_name="Surface",
            layers=[
                dict(
                    selector=dict(mode="all", domain="vertex"),
                    weights=dict(
                        mode="constant",
                        influences=[
                            dict(bone="Fixed", weight=0.5),
                            dict(bone="Twist", weight=0.5),
                        ],
                    ),
                )
            ],
        )
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="Twist", rotation=[0, 2.4, 0])],
        )
        before = self.volume("Surface")
        self.assertLess(before["volume_ratio"], 0.14)
        # Analytic rigid-axis blend has radial shrink cos(angle/2); an explicit
        # corrective supplies its inverse at this tested pose only.
        factor = 1 / math.cos(1.2) - 1
        self.call(
            "shape_keys.edit",
            object_name="Surface",
            keys=[
                dict(
                    name="RestoreSection",
                    create=True,
                    value=1,
                    correction=dict(
                        mode="sparse",
                        deltas=[
                            dict(
                                vertex=v.index,
                                delta=[factor * v.co.x, 0, factor * v.co.z],
                            )
                            for v in obj.data.vertices
                        ],
                    ),
                )
            ],
        )
        after = self.volume("Surface")
        self.assertAlmostEqual(after["volume_ratio"], 1, places=5)
        print("VOLUME_TWIST_CORRECTION", json.dumps(dict(before=before, after=after)))

    def test_volume_collapse_corrective_and_snapshot_payload(self) -> None:
        obj = self.tube([-2, -1, 0, 1, 2], caps=True)
        self.bind(obj, envelope=3)
        rest = self.volume("Surface")["evaluated"]["volume"]
        self.call(
            "shape_keys.edit",
            object_name="Surface",
            keys=[
                dict(
                    name="Collapse",
                    create=True,
                    value=1,
                    correction=dict(
                        mode="sparse",
                        deltas=[
                            dict(
                                vertex=v.index, delta=[-0.7 * v.co.x, 0, -0.7 * v.co.z]
                            )
                            for v in obj.data.vertices
                        ],
                    ),
                )
            ],
        )
        before = self.volume("Surface")
        self.assertLess(before["volume_ratio"], 0.11)
        self.call(
            "shape_keys.edit",
            object_name="Surface",
            keys=[
                dict(
                    name="Repair",
                    create=True,
                    value=1,
                    correction=dict(
                        mode="sparse",
                        deltas=[
                            dict(vertex=v.index, delta=[0.7 * v.co.x, 0, 0.7 * v.co.z])
                            for v in obj.data.vertices
                        ],
                    ),
                )
            ],
        )
        after = self.volume("Surface")
        self.assertAlmostEqual(after["evaluated"]["volume"], rest, places=5)
        self.call("volume.snapshot", objects=[dict(source="Surface", name="Frozen")])
        self.assertEqual(
            list(obj.vertex_groups.keys()),
            list(bpy.data.objects["Frozen"].vertex_groups.keys()),
        )
        print("VOLUME_CORRECTION", json.dumps(dict(before=before, after=after)))

    def test_snapshot_transaction_cleanup_and_invalid_surface(self) -> None:
        self.planes()
        before = set(bpy.data.objects.keys())
        self.error(
            "volume.snapshot",
            objects=[
                dict(source="Outer", name="Copy"),
                dict(source="Missing", name="Bad"),
            ],
        )
        self.assertEqual(set(bpy.data.objects.keys()), before)
        with patch.object(geometry, "MAX_VERTICES", 10):
            self.error("volume.inspect", objects=[dict(object_name="Outer")])
        self.error(
            "layer.inspect",
            queries=[
                dict(
                    mode="current",
                    source="Outer",
                    target="Inner",
                    selector=dict(mode="indices", domain="vertex", indices=[99999]),
                )
            ],
        )
        self.capture()
        bpy.data.objects.remove(bpy.data.objects["Inner"], do_unlink=True)
        self.assertFalse(self.reference()["valid"])

    def test_regional_selector_and_snapshot_uv_material_payload(self) -> None:
        inner = self.grid(4, 4, "Inner", uv=True)
        outer = self.grid(4, 4, "Outer", uv=True)
        outer.location.z = 0.2
        bpy.context.view_layer.update()
        self.bind(inner, envelope=4)
        self.call("material.create_principled", name="Shared")
        self.call("material.assign", object_name="Outer", material_name="Shared")
        result = self.call(
            "layer.inspect",
            queries=[
                dict(
                    mode="current",
                    source="Outer",
                    target="Inner",
                    selector=dict(
                        mode="region",
                        domain="vertex",
                        region=dict(
                            frame=dict(kind="world"), min=[0, 0, 0.1], max=[0.5, 1, 0.3]
                        ),
                    ),
                    sample_count=5,
                )
            ],
        )
        self.assertEqual(result["layers"][0]["selected_vertices"], 15)
        self.assertEqual(result["layers"][0]["sampled_vertices"], 5)
        self.call(
            "volume.snapshot",
            objects=[dict(source="Outer", name="Copy")],
            role="outer",
            tags=["fixture"],
        )
        copy = bpy.data.objects["Copy"]
        self.assertEqual(len(copy.data.uv_layers), 1)
        self.assertEqual(copy.data.materials[0], outer.data.materials[0])
        for face in copy.data.polygons:
            for loop in face.loop_indices:
                vertex = copy.data.vertices[copy.data.loops[loop].vertex_index]
                uv = copy.data.uv_layers[0].data[loop].uv
                self.assertAlmostEqual(uv.x, vertex.co.x)
                self.assertAlmostEqual(uv.y, vertex.co.y)
        self.assertEqual(copy.matrix_world, outer.matrix_world)

    def test_snapshot_late_failure_rolls_back_allocations(self) -> None:
        self.planes()
        module = importlib.import_module(PACKAGE + "organization")
        error = importlib.import_module(PACKAGE + "operations").OperationError
        counts = (len(bpy.data.objects), len(bpy.data.meshes))
        with patch.object(
            module,
            "metadata",
            side_effect=[None, error("fixture", "Injected metadata failure")],
        ):
            self.error(
                "volume.snapshot",
                objects=[
                    dict(source="Inner", name="One"),
                    dict(source="Outer", name="Two"),
                ],
            )
        self.assertEqual((len(bpy.data.objects), len(bpy.data.meshes)), counts)

    def test_sweep_diagnostic_failure_restores_pose(self) -> None:
        self.guide()
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B", location=[0.4, 0.2, 0.1])],
        )
        rig = bpy.data.objects["Rig"]
        original = rig.pose.bones["B"].matrix_basis.copy()
        with patch.object(geometry, "MAX_WORK", 1):
            self.error(
                "deformation.sweep",
                armature_object="Rig",
                volumes=[dict(object_name="Volume")],
                poses=[dict(name="rest")],
            )
        self.assertEqual(original, rig.pose.bones["B"].matrix_basis)
        self.assertEqual(rig.data.pose_position, "POSE")

    def test_sweep_total_budget_checks_before_next_surface(self) -> None:
        self.guide()
        sweep = importlib.import_module(PACKAGE + "deformation_sweep")
        count = self.volume("Volume")["evaluated"]["vertex_count"]
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B", location=[0.1, 0.2, 0.3])],
        )
        original = bpy.data.objects["Rig"].pose.bones["B"].matrix_basis.copy()
        with patch.object(sweep, "MAX_SWEEP_VERTEX_SAMPLES", count + 1):
            result = self.error(
                "deformation.sweep",
                armature_object="Rig",
                volumes=[dict(object_name="Volume")],
                poses=[dict(name="one"), dict(name="two")],
            )
        self.assertIn("remaining sweep budget", result.message)
        self.assertEqual(original, bpy.data.objects["Rig"].pose.bones["B"].matrix_basis)

    def test_guide_surface_topology_guard_and_section_bounds(self) -> None:
        self.call(
            "mesh.create",
            name="Anchor",
            vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            faces=[[0, 1, 2]],
        )
        self.call(
            "curve.create",
            curves=[
                dict(
                    name="Guide",
                    splines=[
                        dict(
                            type="POLY", points=[dict(co=[0, 0, 0]), dict(co=[0, 0, 2])]
                        )
                    ],
                    settings=dict(profile=dict(kind="circle", radius=0.1)),
                    bindings=[
                        dict(
                            spline=0,
                            point=0,
                            target=dict(
                                kind="surface",
                                object="Anchor",
                                face=0,
                                barycentric=[1, 0, 0],
                            ),
                        )
                    ],
                )
            ],
        )
        row = self.call(
            "volume.inspect", objects=[dict(object_name="Guide")], section_samples=4
        )["volumes"][0]
        self.assertEqual(len(row["path"]["sections"]), 4)
        self.call(
            "mesh.subdivide_edges",
            object_name="Anchor",
            selector=dict(mode="all", domain="edge"),
            cuts=1,
        )
        result = self.error("volume.inspect", objects=[dict(object_name="Guide")])
        self.assertIn("invalid", result.message.lower())

    def test_reference_corruption_and_degenerate_frame_are_invalid(self) -> None:
        inner, outer = self.planes()
        self.capture()
        saved = bpy.context.scene[layers.KEY]
        values = json.loads(saved)
        values["Pair"]["samples"][0]["barycentric"] = [2, 0, 0]
        bpy.context.scene[layers.KEY] = json.dumps(values)
        self.assertFalse(self.reference()["valid"])
        bpy.context.scene[layers.KEY] = saved
        for vertex in inner.data.vertices:
            vertex.co.y = 0
        inner.data.update()
        bpy.context.view_layer.update()
        self.assertFalse(self.reference()["valid"])

    def test_region_sampling_scale_and_compact_batch(self) -> None:
        stamp = time.perf_counter()
        specs: list[dict[str, Any]] = []
        for i in range(12):
            specs.append(
                dict(
                    name=f"V{i}",
                    role="padding",
                    tags=["scale"],
                    location=[i * 0.55, 0, 0],
                    splines=[
                        dict(
                            type="BEZIER",
                            points=[
                                dict(
                                    co=[0, j * 0.2, 0.05 * math.sin(j)],
                                    radius=0.6 + 0.4 * math.sin(math.pi * j / 31),
                                )
                                for j in range(32)
                            ],
                        )
                    ],
                    settings=dict(
                        resolution=12,
                        profile=dict(
                            kind="circle", radius=0.3, resolution=32, caps=True
                        ),
                    ),
                )
            )
        self.call("curve.create", curves=specs, sample_limit=0)
        generation = time.perf_counter() - stamp
        edits = []
        for spec in specs:
            points = [
                dict(
                    point,
                    co=[0.04 * math.sin(j / 3), point["co"][1], point["co"][2] * 1.5],
                )
                for j, point in enumerate(spec["splines"][0]["points"])
            ]
            edits.append(
                dict(name=spec["name"], splines=[dict(type="BEZIER", points=points)])
            )
        stamp = time.perf_counter()
        self.call("curve.configure", curves=edits, sample_limit=0)
        deformation = time.perf_counter() - stamp
        names = [f"V{i}" for i in range(12)]
        organized = self.call("object_set.inspect", names=names, limit=16)
        self.assertEqual(len(organized["objects"]), 12)
        volumes = self.call(
            "volume.inspect", objects=[dict(object_name=n) for n in names]
        )
        result = self.call(
            "layer.inspect",
            queries=[
                dict(
                    mode="current",
                    source=f"V{i}",
                    target=f"V{i + 1}",
                    sample_count=2048,
                )
                for i in range(8)
            ],
            worst_limit=4,
            contact_distance=0.02,
        )
        self.assertEqual(len(result["layers"]), 8)
        self.assertGreater(result["evaluated_vertices"], 50000)
        self.assertLess(len(json.dumps(result)), 24000)
        self.assertTrue(all(p["sampled_vertices"] == 2048 for p in result["layers"]))
        print(
            "VOLUME_SCALE",
            json.dumps(
                dict(
                    generation_seconds=generation,
                    deformation_seconds=deformation,
                    volume=volumes,
                    layer=result,
                    response_bytes=len(json.dumps(result).encode()),
                )
            ),
        )


if __name__ == "__main__":
    suite = unittest.TestSuite(
        VolumeTests(name) for name in VolumeTests.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("VOLUME_NATIVE_PASSED", result.testsRun)
