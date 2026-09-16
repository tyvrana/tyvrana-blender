"""Native deformation-transfer and corrective fixtures through typed dispatch."""

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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import TopologyTests

PACKAGE = "bl_ext.user_default.tyvrana_blender."
correctives = importlib.import_module(PACKAGE + "correctives")
geo = importlib.import_module(PACKAGE + "deformation_geometry")


class CorrectiveTests(TopologyTests):
    def shape(self, obj: Any, **key: Any) -> Any:
        return self.call("shape_keys.edit", object_name=obj.name, keys=[key])

    def test_sparse_regions_masks_and_stack(self) -> None:
        obj = self.grid()
        self.call(
            "vertex_groups.configure",
            object_name=obj.name,
            groups=[
                dict(
                    name="Mask",
                    create=True,
                    layers=[
                        dict(selector=dict(mode="all", domain="vertex"), weight=0.5)
                    ],
                )
            ],
        )
        result = self.call(
            "shape_keys.edit",
            object_name=obj.name,
            keys=[
                dict(
                    name="A",
                    create=True,
                    value=0.5,
                    vertex_group="Mask",
                    correction=dict(mode="region", delta=[0, 0, 0.4]),
                ),
                dict(
                    name="B",
                    create=True,
                    value=0.25,
                    correction=dict(
                        mode="sparse", deltas=[dict(vertex=12, delta=[0, 0, 0.8])]
                    ),
                ),
            ],
        )
        self.assertEqual(result["changed"], ["A", "B"])
        points, _ = geo.evaluated(obj)
        self.assertAlmostEqual(points[12].z, 0.3, places=6)
        self.assertAlmostEqual(points[0].z, 0.1, places=6)
        info = self.call(
            "shape_keys.inspect", object_name=obj.name, detail_key="B", detail_limit=1
        )
        self.assertEqual(info["total"], 3)
        self.assertEqual(info["detail_total"], 1)
        before = geo.coordinates(
            [p.co for p in obj.data.shape_keys.key_blocks["A"].data]
        )
        self.error(
            "shape_keys.edit",
            object_name=obj.name,
            keys=[dict(name="A", value=0.2), dict(name="Missing", value=1)],
        )
        self.assertEqual(obj.data.shape_keys.key_blocks["A"].value, 0.5)
        self.assertEqual(
            before,
            geo.coordinates([p.co for p in obj.data.shape_keys.key_blocks["A"].data]),
        )
        self.error(
            "vertex_groups.configure",
            object_name=obj.name,
            groups=[dict(name="Mask", remove=True)],
        )
        self.shape(
            obj,
            name="B",
            correction=dict(
                mode="region",
                selector=dict(mode="indices", domain="vertex", indices=[12]),
                delta=[0, 0, 0],
            ),
        )
        self.assertEqual(
            self.call("shape_keys.inspect", object_name=obj.name, names=["B"])["keys"][
                0
            ]["affected_vertices"],
            0,
        )

    def test_capture_no_pose_double_bake(self) -> None:
        obj = self.grid()
        self.call(
            "armature.create",
            name="Rig",
            bones=[dict(name="B", head=[0, 0, 0], tail=[0, 1, 0], envelope_distance=5)],
        )
        self.call(
            "armature.bind",
            object_name=obj.name,
            armature_object="Rig",
            preserve_volume=False,
            weights=dict(method="envelopes", bones=["B"]),
        )
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B", rotation=[0.7, 0.2, 0])],
        )
        basis = geo.coordinates([v.co for v in obj.data.vertices])
        pose = bpy.data.objects["Rig"].pose.bones["B"].matrix_basis.copy()
        self.call("deformation.capture_target", object_name=obj.name, name="Desired")
        self.call(
            "mesh.transform",
            object_name="Desired",
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 0.15],
        )
        before = self.call(
            "deformation.compare", pairs=[dict(object_name=obj.name, target="Desired")]
        )
        result = self.shape(
            obj,
            name="Correction",
            create=True,
            value=1,
            correction=dict(mode="captured_target", target="Desired"),
        )
        after = self.call(
            "deformation.compare", pairs=[dict(object_name=obj.name, target="Desired")]
        )
        self.assertLess(result["capture_max_error"], 1e-5)
        self.assertLess(after["comparisons"][0]["rms"], 1e-5)
        self.assertGreater(before["comparisons"][0]["rms"], 0.1)
        self.assertEqual(basis, geo.coordinates([v.co for v in obj.data.vertices]))
        self.assertEqual(pose, bpy.data.objects["Rig"].pose.bones["B"].matrix_basis)
        self.shape(obj, name="Correction", value=0)
        actual, _ = geo.evaluated(obj)
        self.assertAlmostEqual(actual[0].length, 0, places=6)
        print(
            "CORRECTIVE_CAPTURE",
            json.dumps(dict(before=before, after=after, result=result)),
        )

    def test_hinge_volume_correction_and_sweep(self) -> None:
        obj = self.tube([-2, -1, 0, 1, 2], caps=True)
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(name=n, head=[0, 0, 0], tail=[0, 2, 0], envelope_distance=5)
                for n in ["A", "B"]
            ],
        )
        self.call(
            "armature.bind",
            object_name=obj.name,
            armature_object="Rig",
            preserve_volume=False,
            weights=dict(
                method="explicit",
                vertices=[
                    dict(
                        vertex=i,
                        influences=[
                            dict(bone="A", weight=0.5),
                            dict(bone="B", weight=0.5),
                        ],
                    )
                    for i in range(len(obj.data.vertices))
                ],
            ),
        )

        def bones(angle: float) -> list[dict[str, Any]]:
            return [
                dict(name="A", rotation=[angle, 0, 0]),
                dict(name="B", rotation=[-angle, 0, 0]),
            ]

        self.call("armature.pose", object_name="Rig", bones=bones(1))
        self.call("deformation.capture_target", object_name=obj.name, name="Desired")
        self.call(
            "mesh.transform",
            object_name="Desired",
            selector=dict(mode="all", domain="vertex"),
            scale=[1, 1 / math.cos(1), 1 / math.cos(1)],
            pivot="origin",
        )
        before = self.call(
            "deformation.inspect",
            armature_object="Rig",
            objects=[obj.name],
            sample_limit=0,
        )
        self.shape(
            obj,
            name="Support",
            create=True,
            value=1,
            correction=dict(mode="captured_target", target="Desired"),
        )
        after = self.call(
            "deformation.compare", pairs=[dict(object_name=obj.name, target="Desired")]
        )
        poses = []
        for name, angle, value in [
            ("rest", 0, 0),
            ("mild", 0.3, (1 / math.cos(0.3) - 1) / (1 / math.cos(1) - 1)),
            ("target", 1, 1),
            ("neighbor", 0.9, (1 / math.cos(0.9) - 1) / (1 / math.cos(1) - 1)),
            ("opposite", -1, 0),
        ]:
            poses.append(
                dict(
                    name=name,
                    bones=bones(angle),
                    shape_values=[
                        dict(object_name=obj.name, key="Support", value=value)
                    ],
                )
            )
        sweep = self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=[obj.name],
            poses=poses,
            sample_limit=0,
        )
        self.assertLess(after["comparisons"][0]["rms"], 1e-5)
        self.assertGreater(sweep["poses"][2]["meshes"][0]["qa"]["volume_ratio"], 0.999)
        self.assertLess(sweep["poses"][4]["meshes"][0]["qa"]["volume_ratio"], 0.4)
        self.assertEqual(obj.data.shape_keys.key_blocks["Support"].value, 1)
        print(
            "CORRECTIVE_HINGE",
            json.dumps(dict(before=before, after=after, sweep=sweep)),
        )

    def test_surface_driver_layers_and_invalidation(self) -> None:
        driver = self.grid(2, 2, name="Driver")
        outer = self.grid(4, 4, name="Outer")
        self.call(
            "mesh.transform",
            object_name="Outer",
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 0.1],
        )
        result = self.call(
            "surface_deform.bind",
            driver="Driver",
            driven=["Outer"],
            bind_state="current",
        )
        self.assertTrue(result["bindings"][0]["valid"])
        before, _ = geo.evaluated(outer)
        self.call(
            "mesh.transform",
            object_name="Driver",
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 0.25],
        )
        after, _ = geo.evaluated(outer)
        self.assertAlmostEqual(after[12].z - before[12].z, 0.25, places=5)
        self.error(
            "mesh.subdivide_edges",
            object_name="Driver",
            selector=dict(mode="all", domain="edge"),
            cuts=1,
        )
        # Deliberate external corruption in isolated fixture proves stale-bind QA.
        driver.data.polygons[0].vertices[0] = 1
        info = self.call("surface_deform.inspect", objects=["Outer"])
        self.assertFalse(info["bindings"][0]["valid"])
        self.error("mesh.inspect_evaluated", object_name="Outer")
        self.call("surface_deform.unbind", objects=["Outer"])
        self.assertFalse(
            self.call("surface_deform.inspect", objects=["Outer"])["bindings"][0][
                "bound"
            ]
        )

    def test_surface_driver_transform_and_armature_pose(self) -> None:
        driver = self.grid(2, 2, name="Driver")
        outer = self.grid(4, 4, name="Outer")
        self.call(
            "surface_deform.bind",
            driver=driver.name,
            driven=[outer.name],
            bind_state="current",
        )
        self.call("object.set_transform", name=driver.name, location=[0, 0, 0.3])
        points, _ = geo.evaluated(outer)
        # Native Surface Deform ignores subsequent driver object transforms.
        self.assertAlmostEqual(points[12].z, 0, places=5)
        self.call("object.set_transform", name=driver.name, location=[0, 0, 0])
        self.call(
            "armature.create",
            name="Rig",
            bones=[dict(name="B", head=[0, 0, 0], tail=[0, 1, 0], envelope_distance=5)],
        )
        self.call(
            "armature.bind",
            object_name=driver.name,
            armature_object="Rig",
            preserve_volume=False,
            weights=dict(method="envelopes", bones=["B"]),
        )
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B", rotation=[0.6, 0.2, 0.1])],
        )
        points, _ = geo.evaluated(outer)
        driver_points, _ = geo.evaluated(driver)
        self.assertLess((points[12] - driver_points[4]).length, 1e-5)
        self.assertGreater(abs(points[12].z), 0.1)
        self.assertTrue(
            self.call("surface_deform.inspect", objects=[outer.name])["bindings"][0][
                "valid"
            ]
        )

    def test_world_delta_nonuniform_scale_and_value_extrapolation(self) -> None:
        obj = self.grid()
        self.call("object.set_transform", name=obj.name, scale=[2, 3, 4])
        self.shape(
            obj,
            name="Offset",
            create=True,
            minimum=-1,
            maximum=2,
            value=2,
            correction=dict(
                mode="sparse",
                space="world",
                deltas=[dict(vertex=12, delta=[0, 0, 0.4])],
            ),
        )
        points, _ = geo.evaluated(obj)
        self.assertAlmostEqual((obj.matrix_world @ points[12]).z, 0.8, places=6)
        self.shape(obj, name="Offset", value=-1)
        points, _ = geo.evaluated(obj)
        self.assertAlmostEqual((obj.matrix_world @ points[12]).z, -0.4, places=6)
        self.error(
            "shape_keys.edit", object_name=obj.name, keys=[dict(name="Offset", value=3)]
        )
        self.assertEqual(obj.data.shape_keys.key_blocks["Offset"].value, -1)

    def test_weight_transfer_groups_mirror_and_locks(self) -> None:
        source = self.grid(2, 2, name="Source")
        target = self.grid(4, 4, name="Target")
        self.call(
            "vertex_groups.configure",
            object_name="Source",
            groups=[
                dict(
                    name="A",
                    create=True,
                    layers=[
                        dict(selector=dict(mode="all", domain="vertex"), weight=0.75)
                    ],
                ),
                dict(
                    name="B",
                    create=True,
                    layers=[
                        dict(selector=dict(mode="all", domain="vertex"), weight=0.25)
                    ],
                ),
            ],
        )
        result = self.call(
            "weights.transfer",
            source="Source",
            target="Target",
            groups=[dict(source="A", target="A"), dict(source="B", target="B")],
            max_distance=0.01,
        )
        self.assertEqual(result["selected_vertices"], 25)
        self.assertAlmostEqual(target.vertex_groups["A"].weight(12), 0.75)
        self.call(
            "vertex_groups.configure",
            object_name="Target",
            groups=[dict(name="A", locked=True)],
        )
        self.error(
            "weights.transfer",
            source="Source",
            target="Target",
            groups=[dict(source="A", target="A")],
            max_distance=0.01,
        )
        self.assertAlmostEqual(target.vertex_groups["A"].weight(12), 0.75)
        self.call(
            "weights.transfer",
            source="Source",
            target="Source",
            groups=[dict(source="A", target="C")],
            mirror=dict(axis="x", origin=[0.5, 0, 0]),
            max_distance=0.01,
            normalize=False,
        )
        self.assertAlmostEqual(source.vertex_groups["C"].weight(4), 0.75)

    def test_topology_and_reference_safety(self) -> None:
        obj = self.grid()
        self.shape(obj, name="A", create=True)
        self.shape(obj, name="B", create=True, relative_to="A")
        self.error("shape_keys.remove", object_name=obj.name, names=["A"])
        self.error(
            "shape_keys.edit",
            object_name=obj.name,
            keys=[dict(name="A", relative_to="B")],
        )
        self.error(
            "mesh.insert_loops",
            object_name=obj.name,
            cuts=[dict(edge=0, from_vertex=0, factors=[0.5])],
        )
        self.error(
            "shape_keys.edit",
            object_name=obj.name,
            expected_topology_sha256="0" * 64,
            keys=[dict(name="A", value=1)],
        )
        self.call("shape_keys.remove", object_name=obj.name, names=["A", "B"])
        self.call(
            "shape_keys.remove",
            object_name=obj.name,
            names=["Basis"],
            remove_basis=True,
        )
        self.assertIsNone(obj.data.shape_keys)

    def test_stale_capture_and_key_rollback(self) -> None:
        obj = self.grid()
        self.call("deformation.capture_target", object_name=obj.name, name="Desired")
        self.call(
            "mesh.transform",
            object_name=obj.name,
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 0.1],
        )
        self.error(
            "shape_keys.edit",
            object_name=obj.name,
            keys=[
                dict(
                    name="Stale",
                    create=True,
                    correction=dict(mode="captured_target", target="Desired"),
                )
            ],
        )
        self.assertIsNone(obj.data.shape_keys)
        self.call("deformation.capture_target", object_name=obj.name, name="Fresh")
        self.call(
            "mesh.transform",
            object_name="Fresh",
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 0.1],
        )
        original = obj.data
        count = len(bpy.data.meshes)
        native = geo.evaluated
        calls = 0

        def broken(*a: Any, **k: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected residual evaluation failure")
            return native(*a, **k)

        with patch.object(geo, "evaluated", side_effect=broken):
            self.error(
                "shape_keys.edit",
                object_name=obj.name,
                keys=[
                    dict(
                        name="Broken",
                        create=True,
                        correction=dict(mode="captured_target", target="Fresh"),
                    )
                ],
            )
        self.assertEqual(obj.data, original)
        self.assertIsNone(obj.data.shape_keys)
        self.assertEqual(len(bpy.data.meshes), count)

    def test_binding_batch_rollback_and_dependencies(self) -> None:
        driver = self.grid(2, 2, name="Driver")
        a = self.grid(2, 2, name="A")
        b = self.grid(2, 2, name="B")
        api = importlib.import_module(PACKAGE + "surface_deform")
        native = api.native_bind
        calls = 0

        def broken(obj: Any, mod: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected second bind failure")
            return native(obj, mod)

        with patch.object(api, "native_bind", side_effect=broken):
            self.error(
                "surface_deform.bind",
                driver="Driver",
                driven=["A", "B"],
                bind_state="current",
            )
        self.assertEqual(len(a.modifiers) + len(b.modifiers), 0)
        self.assertNotIn(api.KEY, a)
        self.assertNotIn(api.KEY, b)
        self.call(
            "surface_deform.bind", driver="Driver", driven=["A"], bind_state="current"
        )
        self.error(
            "surface_deform.bind", driver="A", driven=["Driver"], bind_state="current"
        )
        self.assertEqual(len(driver.modifiers), 0)

    def test_multiaxis_regional_correction_and_neighboring_pose(self) -> None:
        obj = self.tube([-2, -1, -0.25, 0, 0.25, 1, 2], caps=True)
        self.bind(obj)
        self.call(
            "armature.bind",
            object_name=obj.name,
            armature_object="Rig",
            preserve_volume=False,
            weights=dict(method="envelopes", bones=["B0", "B1"]),
        )
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B1", rotation=[0.7, 0.3, 0.2])],
        )
        self.call("deformation.capture_target", object_name=obj.name, name="Desired")
        region = dict(mode="neighborhood", domain="vertex", vertex=36, steps=2)
        self.call(
            "mesh.transform",
            object_name="Desired",
            selector=region,
            translation=[0.025, 0, 0.04],
        )
        self.shape(
            obj,
            name="Local",
            create=True,
            value=1,
            correction=dict(mode="captured_target", target="Desired", selector=region),
        )
        sweep = self.call(
            "deformation.sweep",
            armature_object="Rig",
            objects=[obj.name],
            sample_limit=0,
            poses=[
                dict(
                    name="rest",
                    shape_values=[dict(object_name=obj.name, key="Local", value=0)],
                ),
                dict(
                    name="target",
                    bones=[dict(name="B1", rotation=[0.7, 0.3, 0.2])],
                    shape_values=[dict(object_name=obj.name, key="Local", value=1)],
                    targets=[dict(object_name=obj.name, target="Desired")],
                ),
                dict(
                    name="neighbor",
                    bones=[dict(name="B1", rotation=[0.6, 0.25, 0.15])],
                    shape_values=[dict(object_name=obj.name, key="Local", value=0.7)],
                ),
            ],
        )
        self.assertLess(sweep["poses"][1]["target_deviations"][0]["rms"], 1e-5)
        self.assertAlmostEqual(
            sweep["poses"][0]["meshes"][0]["qa"]["edge_ratios"]["maximum"], 1, places=5
        )
        print("CORRECTIVE_MULTIAXIS", json.dumps(sweep))

    def test_guide_follows_corrected_transferred_layer(self) -> None:
        from tests.blender.curve_checks import anchor, spline

        self.grid(2, 2, name="Driver")
        outer = self.grid(4, 4, name="Outer")
        mod = self.call("modifier.create", object_name="Outer", type="triangulate")
        self.call("modifier.apply", object_name="Outer", modifier_name=mod["name"])
        self.call(
            "surface_deform.bind",
            driver="Driver",
            driven=["Outer"],
            bind_state="current",
        )
        self.call(
            "curve.create",
            curves=[
                dict(
                    name="Guide",
                    splines=[spline(coords=[[0, 0, 0], [0, 0, 0.2]])],
                    bindings=[
                        dict(
                            spline=0,
                            target=anchor(
                                "surface",
                                "Outer",
                                face=0,
                                barycentric=[0.5, 0.25, 0.25],
                            ),
                            follow="spline",
                        )
                    ],
                )
            ],
        )
        before = self.call("curve.inspect", names=["Guide"])["curves"][0]
        self.shape(
            bpy.data.objects["Driver"],
            name="Lift",
            create=True,
            value=1,
            correction=dict(mode="region", delta=[0, 0, 0.2]),
        )
        after = self.call("curve.inspect", names=["Guide"])["curves"][0]
        self.assertTrue(after["valid"])
        self.assertAlmostEqual(
            after["bindings"][0]["evaluated_world"][2]
            - before["bindings"][0]["evaluated_world"][2],
            0.2,
            places=5,
        )
        self.assertAlmostEqual(geo.evaluated(outer)[0][0].z, 0.2, places=5)

    def test_moderate_capture_summary_and_qa(self) -> None:
        obj = self.grid(80, 64)
        self.call(
            "mesh.subdivide_edges",
            object_name=obj.name,
            selector=dict(mode="all", domain="edge"),
            cuts=1,
        )
        start = time.perf_counter()
        self.call("deformation.capture_target", object_name=obj.name, name="Desired")
        snapshot_seconds = time.perf_counter() - start
        self.call(
            "mesh.transform",
            object_name="Desired",
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 0.02],
        )
        start = time.perf_counter()
        created = self.shape(
            obj,
            name="Offset",
            create=True,
            value=1,
            correction=dict(mode="captured_target", target="Desired"),
        )
        capture_seconds = time.perf_counter() - start
        start = time.perf_counter()
        summary = self.call("shape_keys.inspect", object_name=obj.name)
        inspect_seconds = time.perf_counter() - start
        start = time.perf_counter()
        comparison = self.call(
            "deformation.compare",
            pairs=[dict(object_name=obj.name, target="Desired")],
            sample_limit=0,
        )
        compare_seconds = time.perf_counter() - start
        self.assertLess(comparison["comparisons"][0]["rms"], 1e-5)
        self.assertLess(len(json.dumps(summary)), 2500)
        print(
            "CORRECTIVE_SCALE",
            json.dumps(
                dict(
                    vertices=len(obj.data.vertices),
                    faces=len(obj.data.polygons),
                    snapshot_seconds=snapshot_seconds,
                    capture_seconds=capture_seconds,
                    inspect_seconds=inspect_seconds,
                    compare_seconds=compare_seconds,
                    edit_bytes=len(json.dumps(created)),
                    summary_bytes=len(json.dumps(summary)),
                    compare_bytes=len(json.dumps(comparison)),
                )
            ),
        )

    def test_corrective_smooth_order_and_settings(self) -> None:
        obj = self.tube([-2, -1, -0.25, 0, 0.25, 1, 2], caps=True)
        self.bind(obj)
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="B1", rotation=[1.2, 0, 0])],
        )
        before = self.call(
            "deformation.inspect",
            armature_object="Rig",
            objects=[obj.name],
            sample_limit=0,
        )
        result = self.call(
            "modifier.create",
            object_name=obj.name,
            type="corrective_smooth",
            settings=dict(
                factor=0.5,
                iterations=8,
                scale=1,
                smooth_type="length_weighted",
                pin_boundaries=True,
            ),
        )
        self.assertEqual(result["settings"]["rest_source"], "original")
        after = self.call(
            "deformation.inspect",
            armature_object="Rig",
            objects=[obj.name],
            sample_limit=0,
        )
        self.assertNotEqual(
            before["meshes"][0]["posed_geometry_sha256"],
            after["meshes"][0]["posed_geometry_sha256"],
        )
        self.call(
            "modifier.create",
            object_name=obj.name,
            type="subdivision_surface",
            settings=dict(levels=1, render_levels=1),
        )
        self.error(
            "modifier.move", object_name=obj.name, modifier_name=result["name"], index=2
        )
        print(
            "CORRECTIVE_SMOOTH",
            json.dumps(
                dict(before=before["meshes"][0]["qa"], after=after["meshes"][0]["qa"])
            ),
        )

    def test_sweep_failure_restores_keys_and_all_pose_channels(self) -> None:
        obj = self.grid()
        self.bind(obj, envelope=5)
        self.shape(
            obj,
            name="K",
            create=True,
            value=0.4,
            correction=dict(mode="region", delta=[0, 0, 0.01]),
        )
        rig = bpy.data.objects["Rig"]
        bone = rig.pose.bones["B1"]
        bone.rotation_mode = "QUATERNION"
        bone.rotation_quaternion = (0.99, 0.1, 0, 0)
        rig.data.pose_position = "REST"
        saved = (
            bone.rotation_mode,
            tuple(bone.rotation_quaternion),
            rig.data.pose_position,
            obj.data.shape_keys.key_blocks["K"].value,
        )
        # Target topology matches; failure is injected after pose/key mutation.
        target = self.grid(name="Target")
        with patch.object(
            correctives,
            "compare",
            side_effect=RuntimeError("injected comparison failure"),
        ):
            self.error(
                "deformation.sweep",
                armature_object="Rig",
                objects=[obj.name],
                poses=[
                    dict(
                        name="P",
                        bones=[dict(name="B1", rotation=[0.5, 0, 0])],
                        shape_values=[dict(object_name=obj.name, key="K", value=1)],
                        targets=[dict(object_name=obj.name, target=target.name)],
                    )
                ],
            )
        self.assertEqual(
            saved,
            (
                bone.rotation_mode,
                tuple(bone.rotation_quaternion),
                rig.data.pose_position,
                obj.data.shape_keys.key_blocks["K"].value,
            ),
        )

    def test_uv_material_and_existing_key_survive_group_and_shape_edits(self) -> None:
        obj = self.grid(uv=True)
        material = bpy.data.materials.new("Surface Material")
        obj.data.materials.append(material)
        uv = [tuple(d.uv) for d in obj.data.uv_layers[0].data]
        self.shape(
            obj,
            name="A",
            create=True,
            value=0.2,
            correction=dict(mode="region", delta=[0, 0, 0.1]),
        )
        self.call(
            "vertex_groups.configure",
            object_name=obj.name,
            groups=[
                dict(
                    name="Mask",
                    create=True,
                    layers=[
                        dict(selector=dict(mode="all", domain="vertex"), weight=0.75)
                    ],
                )
            ],
        )
        self.shape(
            obj,
            name="B",
            create=True,
            relative_to="A",
            correction=dict(
                mode="sparse", deltas=[dict(vertex=12, delta=[0, 0, 0.02])]
            ),
        )
        self.shape(obj, name="A", rename="Base Motion")
        self.assertEqual(
            obj.data.shape_keys.key_blocks["B"].relative_key.name, "Base Motion"
        )
        self.assertEqual(obj.data.materials[0], material)
        self.assertEqual(uv, [tuple(d.uv) for d in obj.data.uv_layers[0].data])
        self.assertAlmostEqual(obj.vertex_groups["Mask"].weight(12), 0.75)
        self.assertAlmostEqual(obj.data.shape_keys.key_blocks["Base Motion"].value, 0.2)
        self.error(
            "shape_keys.edit",
            object_name=obj.name,
            keys=[dict(name="B", minimum=2, maximum=1)],
        )

    def test_transfer_selection_reflection_and_failed_distance_are_atomic(self) -> None:
        source = self.grid(2, 2, name="Source")
        target = self.grid(4, 4, name="Target")
        self.call(
            "vertex_groups.configure",
            object_name="Source",
            groups=[
                dict(
                    name="A",
                    create=True,
                    layers=[
                        dict(
                            selector=dict(
                                mode="indices", domain="vertex", indices=[1, 4, 7]
                            ),
                            weight=0.5,
                        ),
                        dict(
                            selector=dict(
                                mode="indices", domain="vertex", indices=[2, 5, 8]
                            ),
                            weight=1,
                        ),
                    ],
                )
            ],
        )
        self.call(
            "vertex_groups.configure",
            object_name="Target",
            groups=[
                dict(
                    name="A",
                    create=True,
                    layers=[
                        dict(selector=dict(mode="all", domain="vertex"), weight=0.25)
                    ],
                ),
                dict(
                    name="Unrelated",
                    create=True,
                    layers=[
                        dict(selector=dict(mode="all", domain="vertex"), weight=0.4)
                    ],
                ),
            ],
        )
        self.call(
            "weights.transfer",
            source="Source",
            target="Target",
            groups=[dict(source="A", target="A")],
            selector=dict(mode="neighborhood", domain="vertex", vertex=12, steps=0),
            normalize=False,
            max_distance=0.01,
        )
        self.assertAlmostEqual(target.vertex_groups["A"].weight(12), 0.5)
        self.assertAlmostEqual(target.vertex_groups["A"].weight(0), 0.25)
        self.assertAlmostEqual(target.vertex_groups["Unrelated"].weight(12), 0.4)
        self.call(
            "weights.transfer",
            source="Source",
            target="Source",
            groups=[dict(source="A", target="Mirrored")],
            mirror=dict(axis="x", origin=[0.5, 0, 0]),
            normalize=False,
            max_distance=0.01,
        )
        self.assertAlmostEqual(source.vertex_groups["Mirrored"].weight(0), 1)
        self.call(
            "mesh.transform",
            object_name="Target",
            selector=dict(mode="all", domain="vertex"),
            translation=[0, 0, 1],
        )
        data = target.data
        self.error(
            "weights.transfer",
            source="Source",
            target="Target",
            groups=[dict(source="A", target="A")],
            max_distance=0.01,
        )
        self.assertEqual(data, target.data)
        self.assertAlmostEqual(target.vertex_groups["A"].weight(12), 0.5)

    def test_capture_rejects_constructive_and_non_linear_stacks(self) -> None:
        obj = self.grid()
        self.bind(obj, envelope=5)
        self.error(
            "deformation.capture_target", object_name=obj.name, name="Rejected Volume"
        )
        self.call(
            "modifier.create",
            object_name=obj.name,
            type="subdivision_surface",
            settings=dict(levels=1, render_levels=1),
        )
        self.shape(
            obj,
            name="A",
            create=True,
            value=1,
            correction=dict(mode="region", delta=[0, 0, 0.01]),
        )
        self.error(
            "deformation.capture_target", object_name=obj.name, name="Rejected Topology"
        )
        self.assertNotIn("Rejected Topology", bpy.data.objects)
        self.assertGreater(
            self.call("mesh.inspect_evaluated", object_name=obj.name)["vertex_count"],
            len(obj.data.vertices),
        )

    def test_invalid_driver_budget_is_reported_without_evaluating(self) -> None:
        driver = self.grid(2, 2, name="Driver")
        self.grid(2, 2, name="Outer")
        self.call(
            "surface_deform.bind",
            driver="Driver",
            driven=["Outer"],
            bind_state="current",
        )
        mod = driver.modifiers.new("External Generator", "SUBSURF")
        mod.levels = 8
        result = self.call("surface_deform.inspect", objects=["Outer"])
        self.assertFalse(result["bindings"][0]["valid"])
        self.assertIn("unsafe", result["bindings"][0]["reason"])
        driver.modifiers.remove(mod)
        self.call("surface_deform.unbind", objects=["Outer"])

    def test_key_name_and_shared_data_guards(self) -> None:
        obj = self.grid()
        copy = obj.copy()
        bpy.context.scene.collection.objects.link(copy)
        self.error(
            "shape_keys.edit", object_name=obj.name, keys=[dict(name="A", create=True)]
        )
        bpy.data.objects.remove(copy, do_unlink=True)
        self.error(
            "shape_keys.edit",
            object_name=obj.name,
            keys=[dict(name="界" * 30, create=True)],
        )
        self.assertIsNone(obj.data.shape_keys)


if __name__ == "__main__":
    suite = unittest.TestSuite(
        CorrectiveTests(name)
        for name in sorted(CorrectiveTests.__dict__)
        if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("CORRECTIVE_NATIVE_PASSED")
