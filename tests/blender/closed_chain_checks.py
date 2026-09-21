"""Native mixed-DOF closure, conditioning, continuity and transaction tests."""

import importlib
import json
import math
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.closed_chain_fixture import commands, definition, sample_args  # noqa: E402

PACKAGE = "bl_ext.user_default.tyvrana_blender."
backend = importlib.import_module(PACKAGE + "blender").BlenderBackend()
operations = importlib.import_module(PACKAGE + "operations")
solver = importlib.import_module(PACKAGE + "coupling_solver")
mechanisms = importlib.import_module(PACKAGE + "coupling_mechanisms")
metrics: dict[str, Any] = {}


def response(op: str, **args: Any) -> Any:
    return operations.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="chain-test",
            operation="blender." + op,
            arguments=args,
        ),
    )


def call(op: str, /, **args: Any) -> Any:
    r = response(op, **args)
    assert isinstance(r, OperationSuccess), r
    return r.result


def snapshot() -> Any:
    bpy.context.view_layer.update()
    return (
        bpy.context.scene.frame_current,
        bpy.context.scene.frame_subframe,
        [
            (
                o.name,
                [list(v) for v in o.matrix_world],
                list(o.location),
                list(o.rotation_euler),
                list(o.scale),
                [
                    (b.name, list(b.location), list(b.rotation_euler), list(b.scale))
                    for b in o.pose.bones
                ]
                if o.type == "ARMATURE"
                else None,
            )
            for o in sorted(bpy.context.scene.objects, key=lambda x: x.name)
        ],
    )


class ClosedChainTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        for key in list(bpy.context.scene.keys()):
            if str(key).startswith(("tyvrana", "_tyvrana")):
                del bpy.context.scene[key]
        bpy.context.scene.frame_set(1)

    def build(self, **kwargs: Any) -> None:
        for op, args in commands(**kwargs):
            call(op, **args)

    def test_slider_crank_analytical_and_deterministic(self) -> None:
        self.build()
        before = snapshot()
        batch = call("motion.sample", **sample_args())
        self.assertEqual(before, snapshot())
        self.assertEqual(batch["mechanism"]["solved_samples"], 33, batch)
        self.assertEqual(batch["mechanism"]["failed_samples"], 0)
        self.assertEqual(batch["violation_count"], 0, batch)
        self.assertEqual(batch["contacts"][0]["counts"]["PERMITTED_CONTACT"], 33)
        # Native reference check; the MCP client does not perform this per-angle loop.
        errors = []
        previous = None
        spec = mechanisms.definition("Linkage")
        for frame in range(1, 34):
            bpy.context.scene.frame_set(frame)
            result, previous = solver.solve(
                spec, solver.EvaluationBudget(2048), previous
            )
            self.assertEqual(result.status, "SOLVED", result)
            angle = bpy.data.objects["Rig"].pose.bones["Crank"].rotation_euler.z
            expected = math.cos(angle) + math.sqrt(4 - math.sin(angle) ** 2)
            errors.append(abs(result.values["Slider"] - expected))
        self.assertLess(max(errors), 0.00003)
        first = call("coupling.solve", name="Linkage")
        second = call("coupling.solve", name="Linkage")
        self.assertEqual(first, second)
        applied = call("coupling.solve", name="Linkage", apply=True)
        self.assertTrue(applied["applied"])
        self.assertTrue(
            call("coupling.inspect", names=["Linkage"])["mechanisms"][0][
                "solution_current"
            ]
        )
        metrics["slider_crank"] = {
            **batch["mechanism"],
            "analytical_error": max(errors),
            "contact": batch["contacts"],
            "restored": batch["restored"],
        }

    def test_second_spatial_inverted_drive(self) -> None:
        self.build(driven_slider=True)
        # Existing native fixed parent/frame construction is an isolated fixture.
        root = bpy.data.objects.new("Frame", None)
        bpy.context.scene.collection.objects.link(root)
        root.location = (3, 2, 1)
        root.rotation_euler = (0.4, 0.6, 0.3)
        for n in ["Rig", "Slider", "Rail"]:
            bpy.data.objects[n].parent = root
        bpy.data.objects["Rig"].pose.bones["Crank"].rotation_euler.z = 0.7
        bpy.data.objects["Rig"].pose.bones["Rod"].rotation_euler.z = -1.1
        before = snapshot()
        r = call("motion.sample", **sample_args(17))
        self.assertEqual(snapshot(), before)
        self.assertEqual(r["mechanism"]["solved_samples"], 17, r)
        self.assertEqual(r["violation_count"], 0, r)
        metrics["spatial_inverted_drive"] = r["mechanism"]

    def test_spatial_offset_mixed_unknowns(self) -> None:
        self.build()
        root = bpy.data.objects.new("OffsetFrame", None)
        bpy.context.scene.collection.objects.link(root)
        root.location = (3, 2, 1)
        root.rotation_euler = (0.4, 0.6, 0.3)
        for name in ["Rig", "Slider", "Rail"]:
            bpy.data.objects[name].parent = root
        slider = bpy.data.objects["Slider"]
        slider.location.y = 0.3
        limit = next(c for c in slider.constraints if c.type == "LIMIT_LOCATION")
        limit.min_y = limit.max_y = 0.3
        slider.update_tag()
        bpy.data.objects["Rail"].location.y += 0.3
        spec = mechanisms.definition("Linkage")
        solved_types = {v.channel.property for v in spec.variables if v.role == "solve"}
        self.assertEqual(solved_types, {"rotation", "location"})
        before = snapshot()
        result = call("motion.sample", **sample_args(17))
        self.assertEqual(snapshot(), before)
        self.assertEqual(result["mechanism"]["solved_samples"], 17, result)
        self.assertEqual(result["violation_count"], 0, result)
        self.assertEqual(result["contacts"][0]["counts"]["PERMITTED_CONTACT"], 17)
        metrics["spatial_offset_mixed_unknowns"] = result["mechanism"]

    def test_incompatible_limits_restore(self) -> None:
        self.build()
        spec = definition()
        spec["variables"][2]["maximum"] = 1.2
        call("coupling.configure", mechanisms=[spec], replace=True)
        before = snapshot()
        r = call("coupling.solve", name="Linkage", apply=True)
        self.assertNotEqual(r["status"], "SOLVED", r)
        self.assertFalse(r["applied"])
        self.assertEqual(snapshot(), before)
        self.assertGreater(r["closure_residual"], 0.1)
        metrics["infeasible"] = r
        call("coupling.configure", mechanisms=[definition()], replace=True)
        rig = bpy.data.objects["Rig"]
        constraint = next(
            c for c in rig.pose.bones["Crank"].constraints if c.type == "LIMIT_ROTATION"
        )
        constraint.min_z = -0.1
        constraint.max_z = 0.1
        rig.update_tag()
        before = snapshot()
        clamped = call("coupling.solve", name="Linkage", apply=True)
        self.assertNotEqual(clamped["status"], "SOLVED", clamped)
        self.assertGreater(clamped["limit_residual"], 0.7)
        self.assertEqual(snapshot(), before)
        metrics["native_limit_clamp"] = clamped

    def test_near_toggle_is_uncertain(self) -> None:
        self.build()
        call("action.assign", name="Drive", detach=True)
        bpy.context.view_layer.objects.active = bpy.data.objects["Rig"]
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.data.objects["Rig"].data.edit_bones["Rod"].tail = (2, 0, 0)
        bpy.ops.object.mode_set(mode="OBJECT")
        angle = math.pi / 2 - 0.0001
        bpy.data.objects["Rig"].pose.bones["Crank"].rotation_euler.z = angle
        bpy.data.objects["Rig"].pose.bones["Rod"].rotation_euler.z = -2 * angle
        bpy.data.objects["Slider"].location.x = 0.01001
        spec = definition()
        spec["variables"][0].update(minimum=-2, maximum=2)
        spec["variables"][2].update(minimum=0.01, maximum=2.1)
        spec["maximum_condition"] = 1000
        call("coupling.configure", mechanisms=[spec], replace=True)
        before = snapshot()
        r = call("coupling.solve", name="Linkage", apply=True)
        self.assertEqual(r["status"], "SINGULAR", r)
        self.assertEqual(snapshot(), before)
        metrics["toggle"] = r

    def test_large_scene_and_failure_restoration(self) -> None:
        self.build(unrelated=320)
        bpy.context.scene.frame_set(7, subframe=0.25)
        before = snapshot()
        r = call("motion.sample", **sample_args(17))
        self.assertEqual(snapshot(), before)
        self.assertEqual(r["scoped_object_count"], 4, r)
        self.assertEqual(r["mechanism"]["solved_samples"], 17)
        original = solver.solve
        count = 0

        def fail(*args: Any, **kwargs: Any) -> Any:
            nonlocal count
            count += 1
            result = original(*args, **kwargs)
            if count == 9:
                raise RuntimeError("forced post-solve sample failure")
            return result

        with patch.object(solver, "solve", side_effect=fail):
            failed = response("motion.sample", **sample_args(17))
        self.assertIsInstance(failed, OperationFailure)
        self.assertEqual(snapshot(), before)
        exhausted = response(
            "coupling.solve", name="Linkage", max_evaluations=1, apply=True
        )
        self.assertIsInstance(exhausted, OperationFailure)
        self.assertEqual(snapshot(), before)
        metrics["large_scene"] = {
            "objects": len(bpy.context.scene.objects),
            "scope": r["scoped_object_count"],
            "samples": r["mechanism"],
            "forced_failure_restored": True,
        }

    def test_branch_jump_rejected_and_ownership(self) -> None:
        self.build()
        spec = definition()
        spec["variables"][1]["continuity_limit"] = 0.001
        call("coupling.configure", mechanisms=[spec], replace=True)
        r = call("motion.sample", **sample_args())
        self.assertGreater(r["mechanism"]["branch_discontinuities"], 0, r)
        self.assertGreater(r["mechanism"]["failed_samples"], 0)
        metrics["branch_rejection"] = r["mechanism"]
        call("action.assign", name="Drive", detach=True)
        bpy.data.objects["Slider"].name = "Renamed"
        inspected = call("coupling.inspect", names=["Linkage"])
        self.assertEqual(
            inspected["mechanisms"][0]["definition"]["variables"][2]["channel"][
                "object_name"
            ],
            "Renamed",
        )
        call("coupling.remove", names=["Linkage"])
        self.assertEqual(call("coupling.inspect")["mechanisms"], [])
        # Missing native identities must never silently attach to a reused name.
        spec = definition()
        spec["variables"][2]["channel"]["object_name"] = "Renamed"
        spec["closures"][0]["b"]["object"] = "Renamed"
        call("coupling.configure", mechanisms=[spec])
        bpy.data.objects.remove(bpy.data.objects["Renamed"], do_unlink=True)
        replacement = bpy.data.objects.new("Renamed", None)
        bpy.context.scene.collection.objects.link(replacement)
        self.assertIsInstance(
            response("coupling.solve", name="Linkage"), OperationFailure
        )
        call("coupling.remove", names=["Linkage"])


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(ClosedChainTests)
)
print("CLOSED_CHAIN_METRICS", json.dumps(metrics))
if not result.wasSuccessful():
    raise RuntimeError("Closed-chain native qualification failed")
print("CLOSED_CHAIN_NATIVE_PASSED", result.testsRun)
