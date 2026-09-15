"""Native regional weights, contact QA, retargeting and rollback checks."""

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender."
backend = importlib.import_module(PACKAGE + "blender")
rig = importlib.import_module(PACKAGE + "rig")
rm = importlib.import_module(PACKAGE + "rig_models")
wm = importlib.import_module(PACKAGE + "weight_models")
im = importlib.import_module(PACKAGE + "instance_models")
fm = importlib.import_module(PACKAGE + "file_models")
qa = importlib.import_module(PACKAGE + "deformation_qa")
ops = importlib.import_module(PACKAGE + "operations")


def constant(bone: str) -> dict[str, Any]:
    return {"mode": "constant", "influences": [{"bone": bone, "weight": 1.0}]}


def box(lo: float, hi: float) -> dict[str, Any]:
    return {
        "domain": "vertex",
        "mode": "box",
        "min": [-1.0, lo, -1.0],
        "max": [1.0, hi, 1.0],
    }


class WeightTests(unittest.TestCase):
    def setUp(self) -> None:
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        self.b = backend.BlenderBackend()
        v = [[x, i / 10, 0.0] for i in range(21) for x in [-0.1, 0.1]]
        f = [[2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2] for i in range(20)]
        self.b.mesh_create(
            im.MeshCreateArguments(
                name="Surface",
                vertices=v,
                faces=f,
                corner_uvs=[[v[i][0] + 0.5, v[i][1] / 2] for face in f for i in face],
            )
        )
        self.obj = bpy.data.objects["Surface"]
        self.create = rm.ArmatureCreateArguments.model_validate(
            {
                "name": "Rig",
                "bones": [
                    {
                        "name": "Anchor",
                        "head": [-0.1, 0.0, 0.0],
                        "tail": [0.0, 0.0, 0.0],
                    },
                    {
                        "name": "Upper",
                        "head": [0.0, 0.0, 0.0],
                        "tail": [0.0, 1.0, 0.0],
                        "parent": "Anchor",
                        "connected": True,
                    },
                    {
                        "name": "Lower",
                        "head": [0.0, 1.0, 0.0],
                        "tail": [0.0, 2.0, 0.0],
                        "parent": "Upper",
                        "connected": True,
                    },
                ],
            }
        )
        self.b.armature_create(self.create)
        self.b.armature_bind(
            rm.ArmatureBindArguments.model_validate(
                {
                    "object_name": "Surface",
                    "armature_object": "Rig",
                    "weights": {
                        "method": "envelopes",
                        "bones": ["Anchor", "Upper", "Lower"],
                    },
                }
            )
        )
        self.layers = [
            {
                "selector": {"mode": "all", "domain": "vertex"},
                "weights": constant("Upper"),
            },
            {
                "selector": box(0.0, 0.2),
                "weights": {
                    "mode": "gradient",
                    "start": [0.0, 0.0, 0.0],
                    "end": [0.0, 0.2, 0.0],
                    "start_influences": [{"bone": "Anchor", "weight": 1.0}],
                    "end_influences": [{"bone": "Upper", "weight": 1.0}],
                },
            },
            {
                "selector": box(0.8, 2.0),
                "weights": {
                    "mode": "gradient",
                    "start": [0.0, 0.8, 0.0],
                    "end": [0.0, 1.2, 0.0],
                    "start_influences": [{"bone": "Upper", "weight": 1.0}],
                    "end_influences": [{"bone": "Lower", "weight": 1.0}],
                },
            },
        ]

    def assign(self, **changes: Any) -> Any:
        return self.b.weights_assign(
            wm.WeightsAssignArguments.model_validate(
                {"object_name": "Surface", "layers": self.layers, **changes}
            )
        )

    def inspect(self, **changes: Any) -> Any:
        return self.b.weights_inspect(
            wm.WeightsInspectArguments.model_validate(
                {"object_name": "Surface", "sample_limit": 32, **changes}
            )
        )

    def test_gradient_region_pose_and_fixed_anchor(self) -> None:
        authored = self.obj.data.vertices[0].co.copy()
        result = self.assign()
        self.assertEqual(result.selected_vertex_count, 42)
        data = self.obj.data
        self.assertEqual(self.assign().changed_vertex_count, 0)
        self.assertIs(self.obj.data, data)
        s = self.inspect()
        self.assertEqual(s.non_normalized_vertex_count, 0)
        self.assertEqual(s.samples[0].influences[0].bone, "Anchor")
        middle = self.inspect(
            selector={"domain": "vertex", "mode": "indices", "indices": [20]}
        )
        self.assertAlmostEqual(middle.samples[0].influences[0].weight, 0.5, places=5)
        self.b.armature_pose(
            rm.ArmaturePoseArguments.model_validate(
                {
                    "object_name": "Rig",
                    "bones": [
                        {"name": "Upper", "rotation": [0.4, 0.0, 0.0]},
                        {"name": "Lower", "rotation": [0.0, 0.0, 1.0]},
                    ],
                }
            )
        )
        result = self.b.deformation_inspect(
            rm.DeformationInspectArguments(
                armature_object="Rig",
                objects=["Surface"],
                bone_names=["Anchor", "Upper", "Lower"],
            )
        )
        q = result.meshes[0].qa
        self.assertGreater(result.meshes[0].displacement_max, 0.5)
        self.assertLess(q.edge_ratios.p95, 1.2)
        self.assertIsNone(q.volume_ratio)
        self.assertEqual({r.bone for r in q.bone_regions}, {"Anchor", "Upper", "Lower"})
        self.assertLess(
            next(r for r in q.bone_regions if r.bone == "Anchor").displacement_max,
            0.021,
        )
        evaluated = rig.snapshot(self.obj, bpy.context.evaluated_depsgraph_get())[0]
        self.assertLess((evaluated[0] - authored).length, 1e-7)
        self.assertEqual(self.obj.data.vertices[0].co, authored)

    def test_smoothing_changes_transition_preserves_fixed_and_unselected(self) -> None:
        self.assign(
            layers=[
                {
                    "selector": {"mode": "all", "domain": "vertex"},
                    "weights": constant("Upper"),
                },
                {"selector": box(1.0, 2.0), "weights": constant("Lower")},
            ]
        )
        before = self.inspect(selector=box(0.0, 0.1)).model_dump()
        result = self.assign(
            layers=[{"selector": box(0.7, 1.3), "weights": {"mode": "normalize"}}],
            smooth_iterations=3,
            fixed_selector=box(0.0, 0.1),
        )
        after = self.inspect(selector=box(0.0, 0.1))
        self.assertEqual(before["samples"], after.model_dump()["samples"])
        self.assertGreater(result.changed_vertex_count, 0)
        self.assertEqual(after.non_normalized_vertex_count, 0)

    def test_normalization_filtered_inspection_and_unrelated_groups(self) -> None:
        group = self.obj.vertex_groups.new(name="Unrelated")
        group.add([0], 0.37, "REPLACE")
        self.obj.vertex_groups["Upper"].add([0], 1.0, "REPLACE")
        self.obj.vertex_groups["Lower"].add([0], 1.0, "REPLACE")
        self.assertGreater(
            self.inspect(filter="non_normalized").matching_vertex_count, 0
        )
        self.assign(
            layers=[
                {
                    "selector": {"mode": "all", "domain": "vertex"},
                    "weights": {"mode": "normalize"},
                }
            ]
        )
        self.assertEqual(self.inspect(filter="non_normalized").matching_vertex_count, 0)
        self.assertAlmostEqual(
            self.obj.vertex_groups["Unrelated"].weight(0), 0.37, places=6
        )

    def test_bad_regions_bones_locks_leave_data_untouched(self) -> None:
        data = self.obj.data
        before = rig.binding_summary(self.obj).weights_sha256
        for layer in [
            {"selector": box(5.0, 6.0), "weights": constant("Upper")},
            {
                "selector": {"domain": "vertex", "mode": "indices", "indices": [999]},
                "weights": constant("Upper"),
            },
            {"selector": box(0.0, 2.0), "weights": constant("Missing")},
        ]:
            with self.assertRaises(ops.OperationError):
                self.assign(layers=[layer])
            self.assertIs(self.obj.data, data)
            self.assertEqual(rig.binding_summary(self.obj).weights_sha256, before)
        self.obj.vertex_groups["Upper"].lock_weight = True
        with self.assertRaises(ops.OperationError):
            self.assign()
        self.assertIs(self.obj.data, data)

    def test_staged_publication_failure_rolls_back(self) -> None:
        data = self.obj.data
        before = rig.binding_summary(self.obj).weights_sha256
        counts = (len(bpy.data.meshes), len(bpy.data.objects))
        original = rig.binding_summary

        def injected(obj: Any) -> Any:
            if obj.data != data:
                raise RuntimeError("publish check failed")
            return original(obj)

        with (
            patch.object(rig, "binding_summary", side_effect=injected),
            self.assertRaisesRegex(RuntimeError, "publish check"),
        ):
            self.assign()
        self.assertIs(self.obj.data, data)
        self.assertEqual(rig.binding_summary(self.obj).weights_sha256, before)
        self.assertEqual(counts, (len(bpy.data.meshes), len(bpy.data.objects)))
        self.assertEqual(bpy.context.mode, "OBJECT")

    def test_explicit_retarget_and_failure_restores_old_target(self) -> None:
        self.b.armature_create(self.create.model_copy(update={"name": "Replacement"}))
        args = rm.ArmatureBindArguments.model_validate(
            {
                "object_name": "Surface",
                "armature_object": "Replacement",
                "weights": {"method": "envelopes", "bones": ["Upper", "Lower"]},
            }
        )
        with self.assertRaises(ops.OperationError):
            self.b.armature_bind(args)
        data = self.obj.data
        old = bpy.data.objects["Rig"]
        original = rig.binding_summary

        def injected(obj: Any) -> Any:
            if obj.data != data:
                raise RuntimeError("retarget failure")
            return original(obj)

        with (
            patch.object(rig, "binding_summary", side_effect=injected),
            self.assertRaises(RuntimeError),
        ):
            self.b.armature_bind(
                args.model_copy(update={"replace_binding_target": True})
            )
        self.assertIs(rig.binding_modifier(self.obj).object, old)
        self.b.armature_bind(args.model_copy(update={"replace_binding_target": True}))
        self.assertEqual(rig.binding_modifier(self.obj).object.name, "Replacement")

    def test_contact_distinguishes_static_root_and_moving_region(self) -> None:
        self.assign()
        self.b.mesh_create(
            im.MeshCreateArguments(
                name="Support",
                vertices=[
                    [-3.0, -3.0, -0.1],
                    [3.0, -3.0, -0.1],
                    [3.0, 3.0, -0.1],
                    [-3.0, 3.0, -0.1],
                ],
                faces=[[0, 1, 2, 3]],
            )
        )
        self.b.armature_pose(
            rm.ArmaturePoseArguments.model_validate(
                {
                    "object_name": "Rig",
                    "bones": [{"name": "Upper", "rotation": [0.5, 0.0, 0.0]}],
                }
            )
        )
        contacts = [
            {
                "source_object": "Surface",
                "target_object": "Support",
                "rest_min": [-1.0, lo, -1.0],
                "rest_max": [1.0, hi, 1.0],
            }
            for lo, hi in [(0.0, 0.01), (1.5, 2.0)]
        ]
        args = rm.DeformationInspectArguments.model_validate(
            {"armature_object": "Rig", "objects": ["Surface"], "contacts": contacts}
        )
        result = self.b.deformation_inspect(args)
        self.assertAlmostEqual(result.contacts[0].separation_increase_max, 0, places=6)
        self.assertGreater(result.contacts[1].separation_increase_max, 0.7)
        with (
            patch.object(qa, "contact", side_effect=RuntimeError("probe failed")),
            self.assertRaises(RuntimeError),
        ):
            self.b.deformation_inspect(args)
        self.assertEqual(bpy.data.objects["Rig"].data.pose_position, "POSE")
        with self.assertRaises(ops.OperationError):
            self.b.deformation_inspect(
                args.model_copy(update={"bone_names": ["Missing"]})
            )

    def test_volume_percentiles_and_closed_topology_proxy(self) -> None:
        pts = [
            Vector((x, y, z))
            for z in (0.0, 1.0)
            for y in (0.0, 1.0)
            for x in (0.0, 1.0)
        ]
        faces = [
            (0, 2, 3),
            (0, 3, 1),
            (4, 5, 7),
            (4, 7, 6),
            (0, 1, 5),
            (0, 5, 4),
            (2, 6, 7),
            (2, 7, 3),
            (0, 4, 6),
            (0, 6, 2),
            (1, 3, 7),
            (1, 7, 5),
        ]
        self.assertAlmostEqual(qa.volume(pts, faces), 1)
        edges = sorted(
            {tuple(sorted((f[i], f[(i + 1) % 3]))) for f in faces for i in range(3)}
        )
        q = qa.compare(
            (pts, edges, faces, []), ([p * 0.5 for p in pts], edges, faces, []), 4
        )
        self.assertAlmostEqual(q.volume_ratio, 0.125)
        self.assertAlmostEqual(q.edge_ratios.p95, 0.5)
        self.assertAlmostEqual(q.triangle_area_ratios.p95, 0.25)
        self.assertIsNone(qa.volume(pts, faces[:-1]))
        collapsed = qa.compare(
            (pts, edges, faces, []), ([p * 0 for p in pts], edges, faces, []), 4
        )
        self.assertEqual(collapsed.volume_ratio, 0)
        self.assertEqual(collapsed.edge_ratios.maximum, 0)

    def test_regional_weights_save_reopen_and_wrong_type(self) -> None:
        self.assign(smooth_iterations=2, fixed_selector=box(0.0, 0.01))
        before = self.inspect().model_dump()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "weights.blend")
            self.b.file_save(fm.FileSaveArguments(filepath=path))
            self.b.file_open(fm.FileOpenArguments(filepath=path, discard_current=True))
            self.assertEqual(before, self.inspect().model_dump())
        with self.assertRaises(ops.OperationError):
            self.b.weights_inspect(wm.WeightsInspectArguments(object_name="Rig"))

    def test_explicit_all_unweighted_requires_permission_and_can_be_normalized(
        self,
    ) -> None:
        args = rm.ArmatureBindArguments.model_validate(
            {
                "object_name": "Surface",
                "armature_object": "Rig",
                "weights": {"method": "explicit", "vertices": []},
            }
        )
        with self.assertRaises(ops.OperationError):
            self.b.armature_bind(args)
        self.b.armature_bind(args.model_copy(update={"allow_unweighted": True}))
        self.assertEqual(self.inspect(filter="unweighted").matching_vertex_count, 42)
        self.assign(
            layers=[
                {
                    "selector": {"mode": "all", "domain": "vertex"},
                    "weights": {"mode": "normalize"},
                }
            ],
            allow_unweighted=True,
        )
        self.assertEqual(self.inspect().unweighted_vertex_count, 42)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(WeightTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Native regional weight tests failed")
    print("BLENDER_WEIGHT_TESTS_PASSED", result.testsRun)
