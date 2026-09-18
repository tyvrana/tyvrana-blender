"""Native owned constraint mechanics, matching and failure preservation."""

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


def ref(name: str, bone: str | None = None) -> dict[str, Any]:
    return dict(object_name=name, bone=bone)


class ConstraintTests(TopologyTests):
    def objects(self) -> None:
        for name, location in [
            ("Source", [1, 2, 3]),
            ("Driven", [0, 0, 0]),
            ("Other", [4, 1, 0]),
        ]:
            self.call(
                "object.create_primitive",
                primitive="cube",
                name=name,
                location=location,
            )

    def test_copy_limits_contact_and_ownership(self) -> None:
        self.objects()

        def spec(name: str, settings: dict[str, Any]) -> dict[str, Any]:
            return dict(name=name, owner=ref("Driven"), settings=settings)

        self.call(
            "constraint.configure",
            constraints=[
                spec("Follow", dict(kind="copy_transforms", target=ref("Source")))
            ],
        )
        row = self.call(
            "constraint.inspect", owners=[ref("Driven")], include_definitions=True
        )["constraints"][0]
        self.assertTrue(row["valid"])
        self.assertLess(
            (
                api.world(api.RigEndpoint(object_name="Driven")).translation
                - bpy.data.objects["Source"].location
            ).length,
            1e-5,
        )
        self.error(
            "constraint.configure",
            constraints=[
                dict(
                    name="Cycle",
                    owner=ref("Source"),
                    settings=dict(kind="copy_transforms", target=ref("Driven")),
                )
            ],
        )
        self.assertEqual(len(bpy.data.objects["Source"].constraints), 0)
        self.call(
            "constraint.remove", constraints=[dict(owner=ref("Driven"), name="Follow")]
        )
        self.call(
            "constraint.configure",
            constraints=[
                spec(
                    "Limit", dict(kind="limit_location", x=dict(minimum=-1, maximum=1))
                )
            ],
        )
        bpy.data.objects["Driven"].location.x = 5
        bpy.context.view_layer.update()
        self.assertAlmostEqual(
            api.world(api.RigEndpoint(object_name="Driven")).translation.x, 1
        )
        self.call(
            "constraint.remove", constraints=[dict(owner=ref("Driven"), name="Limit")]
        )
        self.call(
            "constraint.configure",
            constraints=[
                spec(
                    "Contact",
                    dict(kind="floor", target=ref("Source"), axis="Z", offset=0.2),
                )
            ],
        )
        self.assertAlmostEqual(
            api.world(api.RigEndpoint(object_name="Driven")).translation.z,
            3.2,
            places=5,
        )
        bpy.data.objects["Driven"].constraints["Contact"].offset = 0.5
        self.assertFalse(
            self.call("constraint.inspect", owners=[ref("Driven")])["constraints"][0][
                "valid"
            ]
        )
        self.error(
            "constraint.remove", constraints=[dict(owner=ref("Driven"), name="Contact")]
        )

    def test_missing_target_can_be_removed_without_adopting_external_edits(
        self,
    ) -> None:
        self.objects()
        self.call(
            "constraint.configure",
            constraints=[
                dict(
                    name="Follow",
                    owner=ref("Driven"),
                    settings=dict(kind="copy_location", target=ref("Source")),
                )
            ],
        )
        self.call("object.delete", name="Source")
        self.assertFalse(
            self.call("constraint.inspect", owners=[ref("Driven")])["constraints"][0][
                "valid"
            ]
        )
        native = bpy.data.objects["Driven"].constraints["Follow"]
        native.use_x = False
        self.error(
            "constraint.remove", constraints=[dict(owner=ref("Driven"), name="Follow")]
        )
        self.assertIn("Follow", bpy.data.objects["Driven"].constraints)
        native.use_x = True
        self.call(
            "constraint.remove", constraints=[dict(owner=ref("Driven"), name="Follow")]
        )
        self.assertEqual(len(bpy.data.objects["Driven"].constraints), 0)

    def test_child_spaces_and_matching(self) -> None:
        self.objects()
        endpoint = api.RigEndpoint(object_name="Driven")
        before = api.world(endpoint)
        self.call(
            "constraint.configure",
            constraints=[
                dict(
                    name="Space",
                    owner=ref("Driven"),
                    settings=dict(kind="child_of", target=ref("Source")),
                )
            ],
        )
        self.assertLess(api.matrix_error(api.world(endpoint), before), 1e-6)
        bpy.data.objects["Source"].location.x += 2
        bpy.context.view_layer.update()
        moved = api.world(endpoint)
        self.assertAlmostEqual(moved.translation.x, 2)
        self.call(
            "constraint.switch_space",
            owner=ref("Driven"),
            constraint="Space",
            target=ref("Other"),
        )
        self.assertLess(api.matrix_error(api.world(endpoint), moved), 1e-5)
        bpy.data.objects["Other"].location.y += 1
        bpy.context.view_layer.update()
        self.assertAlmostEqual(api.world(endpoint).translation.y, 1)
        result = self.call(
            "armature.match", matches=[dict(source=ref("Driven"), target=ref("Source"))]
        )
        self.assertLess(result["maximum_matrix_error"], 1e-5)

    def test_ik_target_pole_and_rollback(self) -> None:
        self.call(
            "armature.create",
            name="Rig",
            bones=[
                dict(name="A", head=[0, 0, 0], tail=[0, 1, 0], x_reference=[1, 0, 0]),
                dict(
                    name="B",
                    head=[0, 1, 0],
                    tail=[0, 2, 0.1],
                    parent="A",
                    connected=True,
                    x_reference=[1, 0, 0],
                ),
            ],
        )
        self.call(
            "object.create_primitive",
            primitive="cube",
            name="Goal",
            location=[0.5, 1.2, 0.2],
        )
        self.call(
            "object.create_primitive", primitive="cube", name="Pole", location=[0, 0, 2]
        )
        spec = dict(
            name="Solve",
            owner=ref("Rig", "B"),
            settings=dict(
                kind="ik", target=ref("Goal"), pole=ref("Pole"), chain_length=2
            ),
        )
        self.call(
            "armature.configure_joints",
            object_name="Rig",
            joints=[
                dict(
                    name="A",
                    limits=None,
                    ik=dict(y=dict(locked=True), z=dict(stiffness=0.2)),
                )
            ],
        )
        self.assertTrue(bpy.data.objects["Rig"].pose.bones["A"].lock_ik_y)
        self.call("constraint.configure", constraints=[spec])
        evaluated = bpy.data.objects["Rig"].evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        )
        self.assertLess(
            (evaluated.pose.bones["B"].tail - bpy.data.objects["Goal"].location).length,
            0.001,
        )
        self.assertTrue(
            self.call("constraint.inspect", owners=[ref("Rig", "B")])["constraints"][0][
                "valid"
            ]
        )
        raw = str(bpy.data.objects["Rig"].pose.bones["B"][api.KEY])
        changed = dict(
            spec,
            settings=dict(
                kind="ik",
                target=ref("Goal"),
                pole=ref("Pole"),
                chain_length=2,
                iterations=128,
            ),
        )
        with patch.object(api, "summary", side_effect=RuntimeError("injected")):
            self.error("constraint.configure", constraints=[changed], replace=True)
        self.assertEqual(bpy.data.objects["Rig"].pose.bones["B"][api.KEY], raw)
        self.assertEqual(
            bpy.data.objects["Rig"].pose.bones["B"].constraints["Solve"].iterations, 64
        )

    def test_control_rig_matching_both_directions(self) -> None:
        bones: list[dict[str, Any]] = []
        for group in ("F", "I", "D"):
            bones.extend(
                [
                    dict(
                        name=group + "0",
                        head=[0, 0, 0],
                        tail=[0, 1, 0],
                        x_reference=[1, 0, 0],
                    ),
                    dict(
                        name=group + "1",
                        head=[0, 1, 0],
                        tail=[0, 2, 0.1],
                        parent=group + "0",
                        connected=True,
                        x_reference=[1, 0, 0],
                    ),
                ]
            )
        self.call("armature.create", name="Rig", bones=bones)
        for name, location in [("Target", [0, 1.7, 0.5]), ("Pole", [0, 0, 2])]:
            self.call(
                "object.create_primitive",
                primitive="cube",
                name=name,
                location=location,
            )
        self.call(
            "control_rig.configure",
            object_name="Rig",
            name="Limb",
            fk=["F0", "F1"],
            ik=["I0", "I1"],
            deform=["D0", "D1"],
            target=ref("Target"),
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
        before = [
            api.world(api.RigEndpoint(object_name="Rig", bone=n)) for n in ("D0", "D1")
        ]
        result = self.call(
            "control_rig.switch", object_name="Rig", name="Limb", mode="IK"
        )
        self.assertTrue(result["valid"], result)
        self.assertLess(result["maximum_match_error"], 0.001)
        for i, n in enumerate(("D0", "D1")):
            self.assertLess(
                api.matrix_error(
                    before[i], api.world(api.RigEndpoint(object_name="Rig", bone=n))
                ),
                0.001,
            )
        bpy.data.objects["Target"].location.z += 0.1
        bpy.context.view_layer.update()
        result = self.call(
            "control_rig.switch", object_name="Rig", name="Limb", mode="FK"
        )
        self.assertTrue(result["valid"], result)
        args = dict(
            object_name="Rig",
            dependency_policy="preserve",
            bones=[
                dict(b, tail=[0, 2.1, 0.1]) if b["name"].endswith("1") else b
                for b in bones
            ],
        )
        preview = self.call("armature.configure_rest", **args, preview=True)
        self.call(
            "armature.configure_rest",
            **args,
            expected_rest_sha256=preview["rest_sha256"],
        )
        self.assertTrue(
            self.call("control_rig.inspect", object_name="Rig", name="Limb")["valid"]
        )
        self.call("control_rig.remove", object_name="Rig", name="Limb")
        self.assertEqual(
            sum(len(p.constraints) for p in bpy.data.objects["Rig"].pose.bones), 0
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [
                ConstraintTests(n)
                for n in [
                    "test_copy_limits_contact_and_ownership",
                    "test_child_spaces_and_matching",
                    "test_missing_target_can_be_removed_without_adopting_external_edits",
                    "test_ik_target_pole_and_rollback",
                    "test_control_rig_matching_both_directions",
                ]
            ]
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("CONSTRAINT_NATIVE_PASSED")
