"""Animated matching, keyed spaces, timeline replay and transactional rollback."""

import importlib
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

api = importlib.import_module(PACKAGE + "rig_constraints")
controls = importlib.import_module(PACKAGE + "control_rig")
spaces = importlib.import_module(PACKAGE + "rig_spaces")


def ref(name: str, bone: str | None = None) -> dict[str, Any]:
    return dict(object_name=name, bone=bone)


class AnimatedControlsTests(TopologyTests):
    def setUp(self) -> None:
        super().setUp()
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action)
        bpy.context.scene.frame_set(1)

    def frame(self, frame: int) -> None:
        self.call("timeline.configure", frame=frame)

    def matrix(self, name: str, bone: str | None = None) -> Any:
        return api.world(api.RigEndpoint(**ref(name, bone)))

    def rig(self) -> list[dict[str, Any]]:
        bones: list[dict[str, Any]] = []
        for prefix in ("F", "I", "D"):
            bones += [
                dict(
                    name=prefix + "0",
                    head=[0, 0, 0],
                    tail=[0, 1, 0],
                    x_reference=[1, 0, 0],
                ),
                dict(
                    name=prefix + "1",
                    head=[0, 1, 0],
                    tail=[0, 2, 0.1],
                    parent=prefix + "0",
                    connected=True,
                    x_reference=[1, 0, 0],
                ),
            ]
        self.call("armature.create", name="Rig", bones=bones)
        for name, location in [("Goal", [0, 1.7, 0.5]), ("Pole", [0, 0, 2])]:
            self.call(
                "object.create_primitive",
                primitive="cube",
                name=name,
                location=location,
            )
        self.call(
            "control_rig.configure",
            object_name="Rig",
            name="Chain",
            fk=["F0", "F1"],
            ik=["I0", "I1"],
            deform=["D0", "D1"],
            target=ref("Goal"),
            pole=ref("Pole"),
        )
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[
                dict(name="F0", rotation=[0.2, 0, 0.15]),
                dict(name="F1", rotation=[0.7, 0, 0]),
            ],
        )
        self.call(
            "action.edit",
            name="Shot",
            create=True,
            channels=[
                dict(
                    target=dict(
                        kind="transform",
                        **ref("Rig", "F0"),
                        property="rotation",
                        axis="x",
                    ),
                    keys=[dict(frame=1, value=0.2), dict(frame=20, value=0.5)],
                )
            ],
        )
        self.call("action.assign", name="Shot")
        return bones

    def test_keyed_fk_ik_replay_and_rollback(self) -> None:
        bones = self.rig()
        original = self.call("action.inspect", name="Shot")
        self.frame(5)
        before = [self.matrix("Rig", n) for n in ("D0", "D1")]
        self.error("control_rig.switch", object_name="Rig", name="Chain", mode="IK")
        result = self.call(
            "control_rig.switch",
            object_name="Rig",
            name="Chain",
            mode="IK",
            keying=dict(action_name="Shot", anchor_frame=4),
        )
        self.assertTrue(result["valid"])
        self.assertLess(result["maximum_match_error"], 0.001)
        for n, m in zip(("D0", "D1"), before, strict=True):
            self.assertLess(api.matrix_error(self.matrix("Rig", n), m), 0.001)
        self.frame(3)
        self.assertEqual(
            self.call("control_rig.inspect", object_name="Rig", name="Chain")["mode"],
            "FK",
        )
        self.frame(6)
        self.assertEqual(
            self.call("control_rig.inspect", object_name="Rig", name="Chain")["mode"],
            "IK",
        )
        self.frame(9)
        self.call(
            "control_rig.switch",
            object_name="Rig",
            name="Chain",
            mode="FK",
            keying=dict(action_name="Shot", anchor_frame=8),
        )
        self.frame(6)
        self.assertEqual(
            self.call("control_rig.inspect", object_name="Rig", name="Chain")["mode"],
            "IK",
        )
        self.frame(10)
        self.assertEqual(
            self.call("control_rig.inspect", object_name="Rig", name="Chain")["mode"],
            "FK",
        )
        keys = self.call("action.inspect", name="Shot", limit=64, key_limit=16)[
            "channels"
        ][0]["keys"]
        self.assertTrue(all(key in keys for key in original["channels"][0]["keys"]))
        action = bpy.data.actions["Shot"]
        snapshot = self.call("action.inspect", name="Shot", limit=64, key_limit=16)
        before = [self.matrix("Rig", n) for n in ("D0", "D1")]
        original_inspect = controls.inspect
        count = 0

        def injected(args: Any) -> Any:
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("injected after action activation")
            return original_inspect(args)

        with patch.object(controls, "inspect", side_effect=injected):
            self.error(
                "control_rig.switch",
                object_name="Rig",
                name="Chain",
                mode="IK",
                keying=dict(action_name="Shot", anchor_frame=9),
            )
        self.assertEqual(bpy.data.actions["Shot"], action)
        self.assertEqual(
            self.call("action.inspect", name="Shot", limit=64, key_limit=16), snapshot
        )
        for n, m in zip(("D0", "D1"), before, strict=True):
            self.assertLess(api.matrix_error(self.matrix("Rig", n), m), 1e-6)
        revision = dict(
            object_name="Rig",
            bones=[
                dict(b, tail=[0, 2.1, 0.1]) if b["name"].endswith("1") else b
                for b in bones
            ],
            dependency_policy="preserve",
        )
        preview = self.call("armature.configure_rest", **revision, preview=True)
        result = self.call(
            "armature.configure_rest",
            **revision,
            expected_rest_sha256=preview["rest_sha256"],
        )
        self.assertIn("Shot", result["rest_revision"]["actions"])
        self.assertTrue(result["rest_revision"]["requires_motion_revalidation"])
        self.assertEqual(bpy.data.actions["Shot"], action)

    def test_keyed_spaces_matching_and_driver_guard(self) -> None:
        for name, location in [
            ("ParentA", [1, 0, 0]),
            ("ParentB", [0, 3, 1]),
            ("Control", [2, 1, 0]),
            ("Source", [4, 2, 1]),
            ("Match", [0, 0, 0]),
        ]:
            self.call(
                "object.create_primitive",
                primitive="cube",
                name=name,
                location=location,
            )
        self.call(
            "constraint.configure",
            constraints=[
                dict(
                    name="Space",
                    owner=ref("Control"),
                    settings=dict(kind="child_of", target=ref("ParentA")),
                )
            ],
        )
        self.call(
            "action.edit",
            name="Shot",
            create=True,
            channels=[
                dict(
                    target=dict(
                        kind="transform",
                        **ref("ParentA"),
                        property="location",
                        axis="x",
                    ),
                    keys=[dict(frame=1, value=1), dict(frame=20, value=4)],
                )
            ],
        )
        self.call("action.assign", name="Shot")
        self.frame(3)
        earlier = self.matrix("Control")
        self.frame(5)
        before = self.matrix("Control")
        result = self.call(
            "constraint.switch_space",
            owner=ref("Control"),
            constraint="Space",
            target=ref("ParentB"),
            keying=dict(action_name="Shot", anchor_frame=4),
        )
        self.assertEqual(len(result["constraints"]), 2)
        self.assertLess(api.matrix_error(self.matrix("Control"), before), 1e-5)
        self.frame(3)
        self.assertLess(api.matrix_error(self.matrix("Control"), earlier), 1e-5)
        self.frame(7)
        before = self.matrix("Control")
        self.call(
            "constraint.switch_space",
            owner=ref("Control"),
            constraint="Space",
            target=ref("ParentA"),
            keying=dict(action_name="Shot", anchor_frame=6),
        )
        self.assertLess(api.matrix_error(self.matrix("Control"), before), 1e-5)
        self.frame(5)
        self.assertEqual(bpy.data.objects["Control"].constraints["Space"].influence, 0)
        self.frame(8)
        self.assertEqual(bpy.data.objects["Control"].constraints["Space"].influence, 1)
        self.call(
            "armature.match",
            matches=[dict(source=ref("Source"), target=ref("Match"))],
            keying=dict(action_name="Shot", anchor_frame=7),
        )
        self.assertLess(
            api.matrix_error(self.matrix("Source"), self.matrix("Match")), 1e-5
        )
        self.frame(10)
        before = self.matrix("Control")
        action = bpy.data.actions["Shot"]
        count = len(bpy.data.objects["Control"].constraints)
        with patch.object(
            spaces.native, "summary", side_effect=RuntimeError("injected")
        ):
            self.error(
                "constraint.switch_space",
                owner=ref("Control"),
                constraint="Space",
                target=ref("Source"),
                keying=dict(action_name="Shot", anchor_frame=9),
            )
        self.assertEqual(bpy.data.actions["Shot"], action)
        self.assertEqual(len(bpy.data.objects["Control"].constraints), count)
        self.assertLess(api.matrix_error(self.matrix("Control"), before), 1e-5)
        self.call(
            "coupling.configure",
            couplings=[
                dict(
                    name="Driven",
                    source=dict(
                        kind="transform", **ref("Source"), property="location", axis="x"
                    ),
                    target=dict(
                        kind="transform",
                        **ref("ParentB"),
                        property="location",
                        axis="x",
                    ),
                    mapping=dict(kind="linear"),
                )
            ],
        )
        self.error(
            "armature.match",
            matches=[dict(source=ref("Source"), target=ref("ParentB"))],
            keying=dict(action_name="Shot", anchor_frame=9),
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [
                AnimatedControlsTests(n)
                for n in [
                    "test_keyed_fk_ik_replay_and_rollback",
                    "test_keyed_spaces_matching_and_driver_guard",
                ]
            ]
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("ANIMATED_CONTROLS_NATIVE_PASSED")
