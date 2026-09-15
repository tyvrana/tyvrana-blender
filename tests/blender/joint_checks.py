"""Native structural joints, dependency protection and atomicity checks."""

import importlib
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

PACKAGE = "bl_ext.user_default.tyvrana_blender."
adapter = importlib.import_module(PACKAGE + "blender")
ops = importlib.import_module(PACKAGE + "operations")
joints = importlib.import_module(PACKAGE + "joints")
rig = importlib.import_module(PACKAGE + "rig")
backend = adapter.BlenderBackend()


def execute(operation: str, arguments: Any) -> Any:
    return ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="joint-test",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )


def call(operation: str, /, **arguments: Any) -> Any:
    response = execute(operation, arguments)
    assert isinstance(response, OperationSuccess), response
    return response.result


def reject(operation: str, /, **arguments: Any) -> Any:
    response = execute(operation, arguments)
    assert isinstance(response, OperationFailure), response
    return response.error


def limits(axis: str = "x", low: float = -0.4, high: float = 0.6) -> dict[str, Any]:
    return {
        a: {"minimum": low if a == axis else 0, "maximum": high if a == axis else 0}
        for a in "xyz"
    }


def segment(name: str = "Pivot", **kwargs: Any) -> dict[str, Any]:
    return dict(name=name, head=[0, 0, 0], tail=[0, 1, 0], **kwargs)


class JointTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.armatures):
            bpy.data.armatures.remove(data)

    def make(self, **kwargs: Any) -> Any:
        call(
            "armature.create",
            name="Structure",
            bones=[segment(limits=limits(), **kwargs)],
        )
        return bpy.data.objects["Structure"]

    def inspect(self, **kwargs: Any) -> Any:
        return call(
            "armature.inspect_structure",
            object_name="Structure",
            fields=["rest", "frame", "limits", "pose"],
            **kwargs,
        )

    def pose(self, rotation: list[float], **kwargs: Any) -> Any:
        return call(
            "armature.pose",
            object_name="Structure",
            bones=[dict(name="Pivot", rotation=rotation, **kwargs)],
        )

    def vector(self, actual: Any, expected: Any) -> None:
        self.assertLess((Vector(actual) - Vector(expected)).length, 2e-5)

    def allocations(self) -> tuple[int, int]:
        return len(bpy.data.objects), len(bpy.data.armatures)

    def test_hinge_inside_boundary_outside_preserves_requested_channels(self) -> None:
        obj = self.make()
        for requested, evaluated in [
            (-0.8, -0.4),
            (-0.4, -0.4),
            (0.2, 0.2),
            (0.6, 0.6),
            (0.9, 0.6),
        ]:
            with self.subTest(angle=requested):
                row = self.pose([requested, 0, 0])["bones"][0]["joint"]
                self.vector(row["requested_rotation"], [requested, 0, 0])
                self.vector(row["evaluated_rotation"], [evaluated, 0, 0])
                self.assertEqual(row["changed_by_constraint"], requested != evaluated)
                self.assertAlmostEqual(
                    obj.pose.bones["Pivot"].rotation_euler.x, requested, places=5
                )
                self.vector(
                    self.inspect()["segments"][0]["pose"]["tail"],
                    [0, math.cos(evaluated), math.sin(evaluated)],
                )

    def test_two_and_three_axis_native_clamping(self) -> None:
        self.make()
        variants: list[dict[str, Any]] = [
            dict(
                x={"minimum": -0.2, "maximum": 0.3},
                y={"minimum": -0.3, "maximum": 0.4},
                z={"minimum": 0, "maximum": 0},
            ),
            dict(
                x={"minimum": -0.2, "maximum": 0.3},
                y={"minimum": -0.3, "maximum": 0.4},
                z={"minimum": -0.1, "maximum": 0.2},
            ),
        ]
        for values in variants:
            call(
                "armature.configure_joints",
                object_name="Structure",
                joints=[dict(name="Pivot", limits=values)],
            )
            row = self.pose([0.7, 0.8, 0.5])["bones"][0]["joint"]
            expected = [0.3, 0.4, values["z"]["maximum"]]
            self.vector(row["evaluated_rotation"], expected)

    def test_different_frames_and_parent_pose(self) -> None:
        call(
            "armature.create",
            name="Structure",
            bones=[
                dict(
                    name="A",
                    head=[0, 0, 0],
                    tail=[0, 1, 0],
                    x_reference=[0, 0, 2],
                    limits=limits("x"),
                ),
                dict(
                    name="B",
                    head=[0, 1, 0],
                    tail=[1, 1, 0],
                    parent="A",
                    connected=True,
                    x_reference=[0, 1, 0],
                    limits=limits("y"),
                ),
                dict(
                    name="C",
                    head=[1, 1, 0],
                    tail=[1, 1, 1],
                    parent="B",
                    connected=True,
                    x_reference=[1, 0, 0],
                    limits=limits("z"),
                ),
            ],
        )
        result = call(
            "armature.pose",
            object_name="Structure",
            bones=[
                dict(name="A", rotation=[0.8, 0, 0]),
                dict(name="B", rotation=[0, 0.3, 0]),
                dict(name="C", rotation=[0, 0, -0.8]),
            ],
        )
        for row, expected in zip(
            result["bones"], [[0.6, 0, 0], [0, 0.3, 0], [0, 0, -0.4]], strict=True
        ):
            self.vector(row["joint"]["evaluated_rotation"], expected)
        obj = bpy.data.objects["Structure"]
        a, b, c = [obj.data.bones[n] for n in "ABC"]
        ma = a.matrix_local @ Euler((0.6, 0, 0)).to_matrix().to_4x4()
        mb = (
            ma
            @ a.matrix_local.inverted()
            @ b.matrix_local
            @ Euler((0, 0.3, 0)).to_matrix().to_4x4()
        )
        mc = (
            mb
            @ b.matrix_local.inverted()
            @ c.matrix_local
            @ Euler((0, 0, -0.4)).to_matrix().to_4x4()
        )
        self.vector(
            self.inspect(names=["C"])["segments"][0]["pose"]["tail"],
            mc @ Vector((0, 1, 0)),
        )
        self.vector(
            self.inspect(names=["B"], space="parent")["segments"][0]["rest"]["head"],
            [0, 1, 0],
        )

    def test_frame_projection_roll_and_world_conversion(self) -> None:
        obj = self.make(x_reference=[2, 7, 0])
        self.vector(self.inspect()["segments"][0]["frame"]["x"], [1, 0, 0])
        obj.matrix_world = (
            Matrix.Translation((3, 4, 5))
            @ Euler((0.2, 0.3, 0.4)).to_matrix().to_4x4()
            @ Matrix.Scale(2, 4)
        )
        row = self.inspect()["segments"][0]
        self.vector(row["frame"]["center"], [3, 4, 5])
        self.vector(row["frame"]["x"], obj.matrix_world.to_3x3().col[0].normalized())
        desired = obj.matrix_world @ Vector((0, 2, 0))
        reference = obj.matrix_world.to_3x3() @ Vector((0, 0, 1))
        call(
            "armature.configure_rest",
            object_name="Structure",
            space="world",
            bones=[
                dict(
                    name="Pivot",
                    head=[3, 4, 5],
                    tail=list(desired),
                    x_reference=list(reference),
                    limits=limits(),
                )
            ],
        )
        local = self.inspect(space="armature")["segments"][0]
        self.vector(local["rest"]["tail"], [0, 2, 0])
        self.vector(local["frame"]["x"], [0, 0, 1])
        obj.scale = (1, 2, 3)
        self.assertEqual(
            reject(
                "armature.inspect_structure", object_name="Structure", fields=["frame"]
            ).code,
            "joint_context_invalid",
        )
        self.inspect(space="armature")

    def test_mirrored_geometry_has_right_handed_frames(self) -> None:
        for side in [-1, 1]:
            call(
                "armature.create",
                name=f"Side{side}",
                bones=[
                    dict(
                        name="Segment",
                        head=[side, 0, 0],
                        tail=[side * 2, 1, 0],
                        x_reference=[0, 0, 1],
                        limits=limits("z"),
                    )
                ],
            )
            frame = call(
                "armature.inspect_structure",
                object_name=f"Side{side}",
                fields=["frame"],
            )["segments"][0]["frame"]
            self.vector(Vector(frame["x"]).cross(Vector(frame["y"])), frame["z"])
            self.assertAlmostEqual(
                Vector(frame["x"]).dot(Vector(frame["y"])), 0, places=6
            )

    def test_rest_add_rename_roll_and_selection_preserved(self) -> None:
        obj = self.make()
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        call(
            "armature.configure_rest",
            object_name="Structure",
            renames=[dict(name="Pivot", rename="Base")],
            bones=[
                dict(
                    name="Tip",
                    head=[0, 1, 0],
                    tail=[0, 2, 0],
                    parent="Base",
                    connected=True,
                    roll=0.5,
                )
            ],
        )
        result = self.inspect()
        self.assertEqual(result["bone_count"], 2)
        self.assertEqual(result["joint_count"], 1)
        self.assertEqual(result["segments"][1]["parent"], "Base")
        self.vector(
            result["segments"][1]["frame"]["x"], [math.cos(0.5), 0, -math.sin(0.5)]
        )
        self.assertEqual(bpy.context.view_layer.objects.active, obj)
        self.assertEqual(list(bpy.context.selected_objects), [obj])
        self.assertEqual(bpy.context.mode, "OBJECT")
        self.assertEqual(self.allocations(), (1, 1))
        self.assertIsNotNone(joints.constraint(obj.pose.bones["Base"]))

    def test_rest_reparent_disconnected_and_cycle_rollback(self) -> None:
        self.make()
        call(
            "armature.configure_rest",
            object_name="Structure",
            bones=[
                dict(
                    name="Other",
                    head=[2, 0, 0],
                    tail=[2, 1, 0],
                    parent="Pivot",
                    connected=False,
                )
            ],
        )
        call(
            "armature.configure_rest",
            object_name="Structure",
            bones=[
                dict(name="Other", head=[2, 0, 0], tail=[2, 1, 0]),
                segment(parent="Other", limits=limits()),
            ],
        )
        before = self.inspect()
        reject(
            "armature.configure_rest",
            object_name="Structure",
            bones=[dict(name="Other", head=[2, 0, 0], tail=[2, 1, 0], parent="Pivot")],
        )
        self.assertEqual(before, self.inspect())
        reject(
            "armature.configure_rest",
            object_name="Structure",
            renames=[dict(name="Pivot", rename="Other")],
        )
        self.assertEqual(before, self.inspect())

    def test_posed_bound_shared_and_external_dependents_block_rest(self) -> None:
        obj = self.make()
        args = dict(
            object_name="Structure", renames=[dict(name="Pivot", rename="Renamed")]
        )
        self.pose([0.2, 0, 0])
        reject("armature.configure_rest", **args)
        call("armature.pose", object_name="Structure", reset=True)
        other = bpy.data.objects.new("Other", obj.data)
        bpy.context.scene.collection.objects.link(other)
        reject("armature.configure_rest", **args)
        bpy.data.objects.remove(other, do_unlink=True)
        mesh = bpy.data.meshes.new("Dependent")
        dependent = bpy.data.objects.new("Dependent", mesh)
        bpy.context.scene.collection.objects.link(dependent)
        modifier = dependent.modifiers.new("Binding", "ARMATURE")
        modifier.object = obj
        reject("armature.configure_rest", **args)
        dependent.modifiers.remove(modifier)
        external = dependent.constraints.new("COPY_LOCATION")
        external.target = obj
        external.subtarget = "Pivot"
        reject("armature.configure_rest", **args)
        self.assertEqual(self.inspect()["segments"][0]["name"], "Pivot")

    def test_driver_and_external_constraint_guards(self) -> None:
        obj = self.make()
        c = obj.pose.bones["Pivot"].constraints.new("LIMIT_LOCATION")
        reject("armature.pose", object_name="Structure", reset=True)
        reject(
            "armature.configure_joints",
            object_name="Structure",
            joints=[dict(name="Pivot", limits=limits())],
        )
        self.assertFalse(self.inspect()["valid"])
        obj.pose.bones["Pivot"].constraints.remove(c)
        obj.driver_add("location", 0)
        reject("armature.configure_rest", object_name="Structure", bones=[segment()])

    def test_repair_modified_owned_settings_and_remove_limits(self) -> None:
        obj = self.make()
        c = joints.constraint(obj.pose.bones["Pivot"])
        c.owner_space = "WORLD"
        self.assertFalse(self.inspect()["valid"])
        reject("armature.pose", object_name="Structure", reset=True)
        call(
            "armature.configure_joints",
            object_name="Structure",
            joints=[dict(name="Pivot", limits=limits("z"))],
        )
        self.assertTrue(self.inspect()["valid"])
        call(
            "armature.configure_joints",
            object_name="Structure",
            joints=[dict(name="Pivot", limits=None)],
        )
        self.assertEqual(self.inspect()["joint_count"], 0)
        self.assertEqual(len(obj.pose.bones["Pivot"].constraints), 0)
        self.pose([2, 2, 2], location=[1, 0, 0])

    def test_invalid_native_ownership_is_visible(self) -> None:
        obj = self.make()
        p = obj.pose.bones["Pivot"]
        p.constraints.remove(joints.constraint(p))
        result = self.inspect()
        self.assertFalse(result["valid"])
        self.assertIn("invalid_joint_ownership", result["segments"][0]["issues"])
        reject(
            "armature.configure_joints",
            object_name="Structure",
            joints=[dict(name="Pivot", limits=limits())],
        )

    def test_pose_principal_branch_and_fixed_center_guards(self) -> None:
        self.make()
        before = self.inspect()
        variants: list[dict[str, Any]] = [
            dict(rotation=[0, math.pi / 2, 0]),
            dict(rotation=[math.pi, 0, 0]),
            dict(location=[1, 0, 0]),
            dict(scale=[1, 2, 1]),
        ]
        for channels in variants:
            reject(
                "armature.pose",
                object_name="Structure",
                bones=[dict(name="Pivot", **channels)],
            )
            self.assertEqual(before, self.inspect())

    def test_joint_configuration_failure_restores_native_state(self) -> None:
        obj = self.make()
        c = joints.constraint(obj.pose.bones["Pivot"])
        c.mute = True
        before = joints.native_limit_state(obj.pose.bones["Pivot"])
        with patch.object(
            rig, "inspect", side_effect=RuntimeError("injected output failure")
        ):
            reject(
                "armature.configure_joints",
                object_name="Structure",
                joints=[dict(name="Pivot", limits=limits("z"))],
            )
        self.assertEqual(before, joints.native_limit_state(obj.pose.bones["Pivot"]))

    def test_rest_publish_failure_restores_data_constraints_context(self) -> None:
        obj = self.make()
        old = obj.data
        before = self.inspect()
        allocations = self.allocations()
        with patch.object(
            rig, "inspect", side_effect=RuntimeError("injected output failure")
        ):
            reject(
                "armature.configure_rest",
                object_name="Structure",
                renames=[dict(name="Pivot", rename="Renamed")],
                bones=[dict(name="Extra", head=[0, 2, 0], tail=[0, 3, 0])],
            )
        self.assertEqual(obj.data, old)
        self.assertEqual(before, self.inspect())
        self.assertEqual(allocations, self.allocations())
        self.assertEqual(bpy.context.mode, "OBJECT")

    def test_rest_stage_failure_does_not_publish(self) -> None:
        self.make()
        before = self.inspect()
        allocations = self.allocations()
        with patch.object(
            joints, "orient_bone", side_effect=RuntimeError("injected geometry failure")
        ):
            reject(
                "armature.configure_rest", object_name="Structure", bones=[segment()]
            )
        self.assertEqual(before, self.inspect())
        self.assertEqual(allocations, self.allocations())

    def test_failed_creation_leaks_no_data(self) -> None:
        before = self.allocations()
        with patch.object(
            joints, "initialize", side_effect=RuntimeError("injected joint failure")
        ):
            reject(
                "armature.create", name="Structure", bones=[segment(limits=limits())]
            )
        self.assertEqual(before, self.allocations())
        reject(
            "armature.create",
            name="Structure",
            bones=[
                dict(
                    name="Bad",
                    head={"kind": "landmark", "name": "Absent"},
                    tail=[0, 1, 0],
                )
            ],
        )
        self.assertEqual(before, self.allocations())

    def test_bone_measurements_and_rename_identity(self) -> None:
        self.make()
        query: dict[str, Any] = dict(
            kind="distance",
            name="Length",
            a=dict(kind="bone", object="Structure", bone="Pivot"),
            b=dict(kind="bone", object="Structure", bone="Pivot", endpoint="tail"),
        )
        result = call("measurement.inspect", queries=[query])
        self.assertAlmostEqual(result["measurements"][0]["value"], 1)
        call(
            "armature.configure_rest",
            object_name="Structure",
            renames=[dict(name="Pivot", rename="Current")],
        )
        reject("measurement.inspect", queries=[query])
        for endpoint in ["a", "b"]:
            query[endpoint]["bone"] = "Current"
        self.assertAlmostEqual(
            call("measurement.inspect", queries=[query])["measurements"][0]["value"], 1
        )

    def test_linked_rest_data_is_preserved(self) -> None:
        obj = self.make()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "library.blend")
            bpy.data.libraries.write(path, {obj})
            bpy.data.objects.remove(obj, do_unlink=True)
            with bpy.data.libraries.load(path, link=True) as (source, target):
                target.objects = source.objects
            linked = target.objects[0]
            bpy.context.scene.collection.objects.link(linked)
            reject(
                "armature.configure_rest",
                object_name=linked.name,
                renames=[dict(name="Pivot", rename="Renamed")],
            )
            self.assertIn("Pivot", linked.data.bones)

    def test_configuring_scaled_parent_is_rejected(self) -> None:
        call(
            "armature.create",
            name="Structure",
            bones=[
                segment(),
                dict(
                    name="Child",
                    head=[0, 1, 0],
                    tail=[0, 2, 0],
                    parent="Pivot",
                    connected=True,
                ),
            ],
        )
        call(
            "armature.pose",
            object_name="Structure",
            bones=[dict(name="Pivot", scale=[2, 1, 1])],
        )
        reject(
            "armature.configure_joints",
            object_name="Structure",
            joints=[dict(name="Child", limits=limits())],
        )
        self.assertEqual(self.inspect()["joint_count"], 0)

    def test_owned_joint_allows_existing_binding_and_deformation_inspection(
        self,
    ) -> None:
        self.make()
        call(
            "mesh.create",
            name="Surface",
            vertices=[[-0.1, 0.5, 0], [0.1, 0.5, 0], [0, 1, 0]],
            faces=[[0, 1, 2]],
        )
        call(
            "armature.bind",
            object_name="Surface",
            armature_object="Structure",
            weights=dict(
                method="explicit",
                vertices=[
                    dict(vertex=i, influences=[dict(bone="Pivot", weight=1)])
                    for i in range(3)
                ],
            ),
        )
        self.pose([0.9, 0, 0])
        result = call(
            "deformation.inspect", armature_object="Structure", objects=["Surface"]
        )
        self.assertGreater(result["meshes"][0]["displacement_max"], 0.5)
        call(
            "armature.configure_joints",
            object_name="Structure",
            joints=[dict(name="Pivot", limits=limits(high=0.3))],
        )
        self.vector(
            self.inspect()["segments"][0]["pose"]["evaluated_rotation"], [0.3, 0, 0]
        )
        reject("armature.configure_rest", object_name="Structure", bones=[segment()])

    def test_unsampled_invalid_bone_affects_whole_structure_qa(self) -> None:
        obj = self.make()
        call(
            "armature.configure_rest",
            object_name="Structure",
            bones=[dict(name="Other", head=[1, 0, 0], tail=[1, 1, 0])],
        )
        obj.pose.bones["Other"].constraints.new("LIMIT_LOCATION")
        result = self.inspect(names=["Pivot"], limit=1)
        self.assertEqual(len(result["segments"]), 1)
        self.assertTrue(result["segments"][0]["valid"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["invalid_bone_count"], 1)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(JointTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Native joint checks failed")
print("JOINT_NATIVE_PASSED", result.testsRun)
