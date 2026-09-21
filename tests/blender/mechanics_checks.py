"""Geometric truth, unilateral contact and bounded large-scene motion qualification."""

import importlib
import json
import math
import unittest
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

PACKAGE = "bl_ext.user_default.tyvrana_blender."
backend = importlib.import_module(PACKAGE + "blender").BlenderBackend()
operations = importlib.import_module(PACKAGE + "operations")
contact_api = importlib.import_module(PACKAGE + "mechanics_contact")
metrics: dict[str, Any] = {}


def call(op: str, /, **args: Any) -> Any:
    result = operations.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="mechanics-test",
            operation="blender." + op,
            arguments=args,
        ),
    )
    assert isinstance(result, OperationSuccess), result
    return result.result


def world(point: Any) -> dict[str, Any]:
    return dict(kind="world", point=list(point))


def mesh(name: str, points: list[Any], faces: list[Any]) -> Any:
    data = bpy.data.meshes.new(name)
    data.from_pydata(points, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def shell(
    name: str,
    radius: float,
    low: float,
    high: float,
    rings: int = 12,
    sides: int = 48,
    inward: bool = False,
) -> Any:
    points = [
        (
            radius * math.sin(t) * math.cos(a),
            radius * math.sin(t) * math.sin(a),
            radius * math.cos(t),
        )
        for t in [low + (high - low) * i / rings for i in range(rings + 1)]
        for a in [2 * math.pi * j / sides for j in range(sides)]
    ]
    faces = []
    for i in range(rings):
        for j in range(sides):
            f = (
                i * sides + j,
                i * sides + (j + 1) % sides,
                (i + 1) * sides + (j + 1) % sides,
                (i + 1) * sides + j,
            )
            # This traversal faces inward by construction.
            faces.append(f if inward else tuple(reversed(f)))
    return mesh(name, points, faces)


def snapshot() -> Any:
    bpy.context.view_layer.update()
    return (
        bpy.context.scene.frame_current,
        bpy.context.scene.frame_subframe,
        [
            (
                o.name,
                [list(r) for r in o.matrix_world],
                list(o.location),
                list(o.rotation_euler),
                list(o.scale),
                [
                    (p.name, list(p.location), list(p.rotation_euler), list(p.scale))
                    for p in o.pose.bones
                ]
                if o.type == "ARMATURE"
                else None,
            )
            for o in sorted(bpy.context.scene.objects, key=lambda o: o.name)
        ],
    )


class MechanicsTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        for key in list(bpy.context.scene.keys()):
            if str(key).startswith(("tyvrana", "_tyvrana")):
                del bpy.context.scene[key]
        bpy.context.scene.frame_set(1)

    def fit(self, **args: Any) -> Any:
        return call("geometry.fit", fits=[dict(name="Fit", **args)])["fits"][0]

    def contact(self, **args: Any) -> Any:
        return call(
            "contact.inspect",
            envelopes=[
                dict(
                    name="Interface",
                    source=dict(object_name="Probe"),
                    target=dict(object_name="Target"),
                    **args,
                )
            ],
            max_tests=2000000,
        )["envelopes"][0]

    def test_sphere_truth_frame_and_freshness(self) -> None:
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16, radius=2, location=(1, 2, 3)
        )
        obj = bpy.context.object
        obj.name = "Ball"
        spec = dict(
            method="sphere",
            regions=[dict(object_name="Ball")],
            axis=dict(start=world([1, 2, 3]), end=world([1, 3, 3])),
            secondary=dict(start=world([0, 0, 0]), end=world([1, 0, 0])),
            tolerance=0.0001,
        )
        fit = self.fit(**spec)
        self.assertEqual(fit["status"], "FITTED")
        error = (Vector(fit["center"]) - Vector((1, 2, 3))).length
        self.assertLess(error, 2e-6)
        self.assertAlmostEqual(fit["radius"], 2, places=5)
        self.assertIsNotNone(fit["frame"])
        self.assertEqual(fit, self.fit(**spec))
        no_axis = self.fit(method="sphere", regions=spec["regions"])
        self.assertIsNone(no_axis["axis"])
        self.assertIsNone(no_axis["frame"])
        obj.data.vertices[0].co.z += 0.02
        obj.data.update()
        stale = self.fit(**spec, expected_sha256=fit["source_sha256"])
        self.assertEqual(stale["status"], "STALE")
        self.assertIsNone(stale["frame"])
        metrics["sphere_center_error"] = error

    def test_cylinder_circle_plane_and_partial_noisy_fit(self) -> None:
        points = []
        for z in [-2, -1, 0, 1, 2]:
            for j in range(48):
                a = 2 * math.pi * j / 48
                points.append((math.cos(a), math.sin(a), z))
        faces = [
            (
                i * 48 + j,
                i * 48 + (j + 1) % 48,
                (i + 1) * 48 + (j + 1) % 48,
                (i + 1) * 48 + j,
            )
            for i in range(4)
            for j in range(48)
        ]
        obj = mesh("Cylinder", points, faces)
        obj.rotation_euler = (0.3, 0.5, 0.2)
        obj.location = (3, -1, 2)
        bpy.context.view_layer.update()
        result = self.fit(
            method="cylinder", regions=[dict(object_name="Cylinder")], tolerance=0.0001
        )
        self.assertEqual(result["status"], "FITTED", result)
        true_axis = obj.matrix_world.to_3x3() @ Vector((0, 0, 1))
        axis_error = min(
            (Vector(result["axis"]) - true_axis).length,
            (Vector(result["axis"]) + true_axis).length,
        )
        self.assertLess(axis_error, 1e-5)
        self.assertLess((Vector(result["center"]) - obj.location).length, 1e-5)
        self.assertAlmostEqual(result["radius"], 1, places=5)
        ring = [
            world(
                (
                    0.3 + math.cos(2 * math.pi * i / 32),
                    -0.2 + math.sin(2 * math.pi * i / 32),
                    0.7,
                )
            )
            for i in range(32)
        ]
        circle = self.fit(method="circle", points=ring, tolerance=0.0001)
        plane = self.fit(method="plane", points=ring, tolerance=0.0001)
        self.assertEqual(circle["status"], "FITTED")
        self.assertEqual(plane["status"], "FITTED")
        self.assertLess(
            (Vector(circle["center"]) - Vector((0.3, -0.2, 0.7))).length, 1e-5
        )
        patch_points = []
        for i in range(8):
            for j in range(8):
                t = 0.6 + 0.9 * i / 7
                a = 0.2 + 2 * j / 7
                r = 1 + 0.0003 * math.sin(i * 7 + j)
                patch_points.append(
                    world(
                        (
                            1 + r * math.sin(t) * math.cos(a),
                            2 + r * math.sin(t) * math.sin(a),
                            3 + r * math.cos(t),
                        )
                    )
                )
        partial = self.fit(method="sphere", points=patch_points, tolerance=0.002)
        self.assertEqual(partial["status"], "FITTED", partial)
        error = (Vector(partial["center"]) - Vector((1, 2, 3))).length
        self.assertLess(error, 0.005)
        metrics.update(
            cylinder_axis_error=axis_error,
            partial_noisy_center_error=error,
            partial_maximum_residual=partial["maximum_error"],
        )

    def test_degenerate_and_orientation_evidence_uncertain(self) -> None:
        result = self.fit(method="sphere", points=[world((i, 0, 0)) for i in range(8)])
        self.assertEqual(result["status"], "UNCERTAIN")
        self.assertIsNone(result["center"])
        result = self.fit(method="plane", points=[world((i, 0, 0)) for i in range(8)])
        self.assertEqual(result["status"], "UNCERTAIN")
        self.assertIsNone(result["axis"])
        result = self.fit(
            method="landmarks",
            points=[world((0, 0, 0))],
            axis=dict(start=world((0, 0, 0)), end=world((0, 1, 0))),
            secondary=dict(start=world((0, 0, 0)), end=world((0, 1, 0))),
        )
        self.assertEqual(result["status"], "UNCERTAIN")

    def plane_probe(self, x: float) -> Any:
        return mesh(
            "Probe",
            [(x, -0.1, -0.1), (x, 0.1, -0.1), (x, 0.1, 0.1), (x, -0.1, 0.1)],
            [(0, 1, 2, 3)],
        )

    def test_closed_contact_separation_and_depth(self) -> None:
        call("object.create_primitive", name="Target", primitive="cube")
        obj = self.plane_probe(1.001)
        permitted = self.contact(minimum_gap=-0.005, maximum_gap=0.01)
        self.assertEqual(permitted["classification"], "PERMITTED_CONTACT", permitted)
        obj.location.x = -0.002
        bpy.context.view_layer.update()
        allowed_penetration = self.contact(minimum_gap=-0.005, maximum_gap=0.01)
        self.assertEqual(allowed_penetration["classification"], "PERMITTED_CONTACT")
        obj.location.x = 0.199
        bpy.context.view_layer.update()
        separated = self.contact(maximum_gap=0.01)
        self.assertEqual(separated["classification"], "SEPARATED")
        obj.location.x = -0.201
        bpy.context.view_layer.update()
        penetration = self.contact(minimum_gap=-0.005, maximum_gap=0.01)
        self.assertEqual(penetration["classification"], "INVALID_PENETRATION")
        self.assertAlmostEqual(penetration["penetration_max"], 0.2, places=5)
        metrics["solid_penetration_depth"] = penetration["penetration_max"]

    def test_open_socket_seating_penetration_rim_and_reflection(self) -> None:
        target = shell("Target", 1, math.pi / 2, math.pi - 0.15, inward=True)
        source = shell("Probe", 0.98, 2.0, 2.2, rings=2, sides=32)
        kwargs = dict(
            mode="oriented_patch", maximum_gap=0.04, minimum_gap=-0.01, sample_limit=96
        )
        seated = self.contact(**kwargs)
        self.assertEqual(seated["classification"], "PERMITTED_CONTACT", seated)
        for v in source.data.vertices:
            v.co *= 1.08 / 0.98
        source.data.update()
        penetrated = self.contact(**kwargs)
        self.assertEqual(
            penetrated["classification"], "INVALID_PENETRATION", penetrated
        )
        self.assertGreater(penetrated["penetration_max"], 0.07)
        transform = (
            Matrix.Translation((2, 3, 4))
            @ Matrix.Rotation(0.7, 4, "Y")
            @ Matrix.Diagonal((-1, 1, 1, 1))
        )
        source.matrix_world = transform
        target.matrix_world = transform
        bpy.context.view_layer.update()
        reflected = self.contact(**kwargs)
        self.assertEqual(reflected["classification"], "INVALID_PENETRATION")
        self.assertAlmostEqual(
            reflected["penetration_max"], penetrated["penetration_max"], places=5
        )
        bpy.data.objects.remove(source, do_unlink=True)
        rim = shell("Probe", 1, math.pi / 2, math.pi / 2 + 0.001, rings=1, sides=24)
        rim.matrix_world = transform
        unclear = self.contact(**kwargs)
        self.assertEqual(unclear["classification"], "UNCERTAIN", unclear)
        closed = self.contact(mode="closed_solid")
        self.assertEqual(closed["classification"], "UNCERTAIN")
        metrics["socket"] = {
            k: v for k, v in penetrated.items() if k not in ["worst", "source_sha256"]
        }

    def test_large_scoped_motion_and_forced_failure_restore(self) -> None:
        for i in range(320):
            obj = bpy.data.objects.new(f"Unrelated{i:03}", None)
            bpy.context.scene.collection.objects.link(obj)
        call(
            "armature.create",
            name="Rig",
            sample_limit=0,
            bones=[
                dict(
                    name="A",
                    head=[0, 0, 0],
                    tail=[0, 1, 0],
                    limits=dict(x=dict(minimum=-1, maximum=1)),
                ),
                dict(
                    name="B",
                    head=[0, 1, 0],
                    tail=[0, 2, 0],
                    parent="A",
                    connected=True,
                    limits=dict(x=dict(minimum=-1, maximum=1)),
                ),
            ],
        )

        def channel(bone: str) -> Any:
            return dict(
                kind="transform",
                object_name="Rig",
                bone=bone,
                property="rotation",
                axis="x",
            )

        call(
            "coupling.configure",
            couplings=[
                dict(
                    name="Follow",
                    source=channel("A"),
                    target=channel("B"),
                    mapping=dict(
                        kind="linear", scale=0.5, clamp=dict(minimum=-1, maximum=1)
                    ),
                )
            ],
        )
        call(
            "action.edit",
            name="Sweep",
            create=True,
            channels=[
                dict(
                    target=channel("A"),
                    keys=[dict(frame=1, value=0), dict(frame=17, value=0.6)],
                )
            ],
        )
        call("action.assign", name="Sweep")
        call(
            "object.create_primitive",
            name="Rigid",
            primitive="cube",
            location=[0, 0.5, 0],
            scale=[0.1, 0.5, 0.1],
        )
        call(
            "constraint.configure",
            constraints=[
                dict(
                    name="RigidFollow",
                    owner=dict(object_name="Rigid"),
                    settings=dict(
                        kind="child_of", target=dict(object_name="Rig", bone="A")
                    ),
                )
            ],
        )
        call(
            "object.create_primitive",
            name="Target",
            primitive="cube",
            location=[10, 0, 0],
        )
        self.plane_probe(11.001)

        def point(bone: str, endpoint: str) -> Any:
            return dict(kind="bone", object="Rig", bone=bone, endpoint=endpoint)

        args: Any = dict(
            range=dict(start=1, end=17),
            armature_object="Rig",
            bones=["A", "B"],
            objects=["Rigid"],
            couplings=["Follow"],
            measurements=[
                dict(
                    kind="distance",
                    name="Closure",
                    a=point("A", "tail"),
                    b=point("B", "head"),
                    comparison=dict(target=0, tolerance=0.0001),
                ),
                dict(
                    kind="distance",
                    name="Length",
                    a=point("A", "head"),
                    b=point("A", "tail"),
                    comparison=dict(target=1, tolerance=0.0001),
                ),
            ],
            contacts=[
                dict(
                    name="Seat",
                    source=dict(object_name="Probe"),
                    target=dict(object_name="Target"),
                    maximum_gap=0.01,
                )
            ],
            contact_max_tests=2000000,
        )
        unrelated = bpy.data.objects["Unrelated000"]
        unrelated.location.x = 2
        unrelated.keyframe_insert(data_path="location", index=0, frame=1)
        unrelated.location.x = 8
        unrelated.keyframe_insert(data_path="location", index=0, frame=17)
        bpy.context.scene.frame_set(5, subframe=0.25)
        unrelated.location.x = 42
        before = snapshot()
        result = call("motion.sample", **args)
        self.assertEqual(snapshot(), before)
        self.assertEqual(result["scoped_object_count"], 4, result)
        self.assertEqual(result["restoration_object_count"], 5)
        self.assertEqual(unrelated.location.x, 42)
        self.assertEqual(len(result["sampled_frames"]), 17)
        self.assertEqual(result["violation_count"], 0, result)
        self.assertEqual(result["contacts"][0]["counts"]["PERMITTED_CONTACT"], 17)
        orig = contact_api.evaluate
        count = 0

        def failure(*a: Any, **kw: Any) -> Any:
            nonlocal count
            count += 1
            if count == 8:
                raise RuntimeError("forced mid-evaluation failure")
            return orig(*a, **kw)

        with patch.object(contact_api, "evaluate", side_effect=failure):
            response = operations.execute(
                backend,
                OperationRequest(
                    type="operation.request",
                    request_id="forced",
                    operation="blender.motion.sample",
                    arguments=args,
                ),
            )
        self.assertIsInstance(response, OperationFailure)
        self.assertEqual(snapshot(), before)
        metrics["scoped_motion"] = {
            k: result[k]
            for k in [
                "scoped_object_count",
                "restoration_object_count",
                "sampled_frames",
                "violation_count",
                "contacts",
            ]
        }
        metrics["scoped_motion"]["total_scene_objects"] = len(bpy.context.scene.objects)
        metrics["scoped_motion"]["forced_failure_restored"] = True


suite = unittest.defaultTestLoader.loadTestsFromTestCase(MechanicsTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
print("MECHANICS_METRICS", json.dumps(metrics))
if not result.wasSuccessful():
    raise RuntimeError("Native mechanics qualification failed")
print("MECHANICS_NATIVE_PASSED", result.testsRun)
