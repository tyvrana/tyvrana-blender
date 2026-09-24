"""Native independent truth checks for bounded rigid assembly seating."""

import importlib
import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

PACKAGE = "bl_ext.user_default.tyvrana_blender"
api = importlib.import_module(PACKAGE + ".operations")
backend = importlib.import_module(PACKAGE + ".blender").BlenderBackend()
seating = importlib.import_module(PACKAGE + ".seating")
metrics: dict[str, Any] = {}


def call(name: str, **arguments: Any) -> Any:
    r = api.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="seat",
            operation="blender." + name,
            arguments=arguments,
        ),
    )
    assert isinstance(r, OperationSuccess), r
    return r.result


def fixture(prefix: str = "", mirrored: bool = False) -> dict[str, Any]:
    sign = -1 if mirrored else 1
    call(
        "object_set.create",
        objects=[
            dict(
                kind="empty",
                key="root",
                name=prefix + "Root",
                location=[sign * 0.2, -0.15, 0.3],
                rotation=[0.12, -sign * 0.08, sign * 0.2],
            ),
            *[
                dict(
                    kind="primitive",
                    primitive="plane",
                    key=f"s{i}",
                    name=prefix + f"Source{i}",
                    parent=dict(key="root"),
                    location=[sign * x, 0, 0.05],
                    scale=[0.2, 0.2, 0.2],
                )
                for i, x in enumerate((-0.5, 0.5))
            ],
            *[
                dict(
                    kind="primitive",
                    primitive="plane",
                    key=f"t{i}",
                    name=prefix + f"Target{i}",
                    location=[sign * x, 0, 0],
                    scale=[0.4, 0.4, 0.4],
                )
                for i, x in enumerate((-0.5, 0.5))
            ],
            *[
                dict(
                    kind="empty",
                    key=f"c{i}",
                    name=prefix + f"Chain{i}",
                    parent=dict(key="root" if i == 0 else f"c{i - 1}"),
                    location=[sign * 0.05, 0.02, 0.01],
                )
                for i in range(9)
            ],
            dict(
                kind="empty",
                key="independent",
                name=prefix + "Independent",
                location=[sign * 0.1, 0.2, 0.4],
            ),
        ],
    )
    return dict(
        members=[prefix + "Root", prefix + "Independent"],
        interfaces=[
            dict(
                kind="surface",
                name=f"seat{i}",
                source=dict(object_name=prefix + f"Source{i}"),
                target=dict(object_name=prefix + f"Target{i}"),
                gap=0.05,
                normals="parallel",
                align_centers=True,
                tolerance=0.0001,
                angular_tolerance=0.002,
            )
            for i in range(2)
        ],
        translation_limit=[1, 1, 1],
        rotation_limit=[0.6, 0.6, 0.6],
    )


def matrices() -> dict[str, Any]:
    bpy.context.view_layer.update()
    return {o.name: o.matrix_world.copy() for o in bpy.data.objects}


def maximum(a: Any, b: Any) -> float:
    return float(max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)))


class SeatingChecks(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_two_interfaces_chain_rotation_and_determinism(self) -> None:
        spec = fixture()
        before = matrices()
        a = call("object_set.place", seating={**spec, "apply": False})["seating"]
        b = call("object_set.place", seating={**spec, "apply": False})["seating"]
        self.assertEqual(a["status"], "SOLVED", a)
        self.assertEqual(a["world_delta"], b["world_delta"])
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "SOLVED", result)
        self.assertEqual(result["moved_member_count"], 13)
        delta = Matrix(result["world_delta"])
        after = matrices()
        moved = [n for n in before if not n.startswith("Target")]
        for name in moved:
            self.assertLess(maximum(after[name], delta @ before[name]), 2e-6)
        for name in moved:
            for other in moved:
                self.assertLess(
                    maximum(
                        before[name].inverted() @ before[other],
                        after[name].inverted() @ after[other],
                    ),
                    4e-6,
                )
        self.assertAlmostEqual(delta.to_3x3().determinant(), 1, places=6)
        self.assertGreater(sum(abs(v) for v in result["rotation_vector"]), 0.1)
        contact = call(
            "contact.inspect",
            envelopes=[
                dict(
                    name=str(i),
                    source=dict(object_name=f"Source{i}"),
                    target=dict(object_name=f"Target{i}"),
                    mode="oriented_patch",
                    maximum_gap=0.0501,
                )
                for i in range(2)
            ],
        )
        self.assertTrue(
            all(
                e["classification"] == "PERMITTED_CONTACT" for e in contact["envelopes"]
            ),
            contact,
        )
        metrics["two_interfaces_chain_orientation"] = result

    def test_collision_guard(self) -> None:
        spec = fixture()
        for term in spec["interfaces"]:
            term["tolerance"] = 0.07
        call(
            "object_set.create",
            objects=[
                dict(
                    kind="primitive",
                    primitive="cube",
                    key="o",
                    name="Obstacle",
                    location=[0, 0, -0.12],
                    scale=[2, 2, 0.2],
                )
            ],
        )
        spec["guards"] = [
            dict(
                name=f"guard{i}",
                source=dict(object_name=f"Source{i}"),
                target=dict(object_name="Obstacle"),
                minimum_clearance=0.02,
            )
            for i in range(2)
        ]
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "SOLVED", result)
        self.assertGreaterEqual(result["worst_clearance"], 0.02 - 1e-6)
        for i in range(2):
            obj = bpy.data.objects[f"Source{i}"]
            self.assertGreaterEqual(
                min((obj.matrix_world @ v.co).z for v in obj.data.vertices), 0.1 - 1e-6
            )
        metrics["collision_guard"] = result

    def test_enclosed_obstacle_is_rejected(self) -> None:
        spec = fixture()
        call(
            "object_set.create",
            objects=[
                dict(
                    kind="primitive",
                    primitive="cube",
                    key="container",
                    name="Container",
                    parent=dict(name="Root"),
                    scale=[1.5, 1.5, 1.5],
                ),
                dict(
                    kind="primitive",
                    primitive="cube",
                    key="obstacle",
                    name="Enclosed",
                    scale=[0.1, 0.1, 0.1],
                ),
            ],
        )
        spec["guards"] = [
            dict(
                name="containment",
                source=dict(object_name="Container"),
                target=dict(object_name="Enclosed"),
            )
        ]
        before = matrices()
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "INFEASIBLE", result)
        self.assertFalse(result["applied"])
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))
        metrics["enclosed_obstacle"] = result

    def test_infeasible_and_bounds(self) -> None:
        spec = fixture()
        before = matrices()
        spec["translation_limit"] = [0, 0, 0]
        spec["rotation_limit"] = [0, 0, 0]
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "INFEASIBLE", result)
        self.assertFalse(result["applied"])
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))
        spec["translation_limit"] = [1, 1, 1]
        spec["rotation_limit"] = [0.6, 0.6, 0.6]
        bpy.data.objects["Target1"].location.x = 2
        bpy.context.view_layer.update()
        before = matrices()
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "INFEASIBLE", result)
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))
        metrics["infeasible"] = result

    def test_mirrored_second_side(self) -> None:
        left = fixture("Left")
        right = fixture("Right", True)
        a = call("object_set.place", seating=left)["seating"]
        self.assertEqual(a["status"], "SOLVED", a)
        right["initial"] = dict(
            translation=a["translation"],
            rotation_vector=a["rotation_vector"],
            mirror_axis="X",
        )
        b = call("object_set.place", seating=right)["seating"]
        self.assertEqual(b["status"], "SOLVED", b)
        self.assertLess(
            max(
                abs(b["translation"][i] - a["translation"][i] * (-1 if i == 0 else 1))
                for i in range(3)
            ),
            0.0002,
        )
        for name in [
            "Root",
            "Source0",
            "Source1",
            "Independent",
            *[f"Chain{i}" for i in range(9)],
        ]:
            a_position = bpy.data.objects["Left" + name].matrix_world.translation
            b_position = bpy.data.objects["Right" + name].matrix_world.translation
            self.assertLess(
                max(
                    abs(b_position[i] - a_position[i] * (-1 if i == 0 else 1))
                    for i in range(3)
                ),
                0.0002,
            )
        metrics["mirrored"] = b

    def test_large_scene_and_frame_correspondence(self) -> None:
        spec = fixture()
        for i in range(320):
            obj = bpy.data.objects.new(f"Unrelated{i}", None)
            bpy.context.scene.collection.objects.link(obj)
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "SOLVED", result)
        self.assertLessEqual(result["evaluated_vertices"], 16)
        self.assertLess(result["processing_seconds"], 10)
        self.assertLess(len(json.dumps(result)), 6000)
        metrics["large_scene"] = result
        spec["interfaces"] = [
            dict(
                kind="frame",
                name=f"f{i}",
                source_object=f"Source{i}",
                target_object=f"Target{i}",
            )
            for i in range(2)
        ]
        result = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(result["status"], "SOLVED", result)
        metrics["frame_correspondence"] = result

    def test_apply_failure_rolls_back(self) -> None:
        spec = fixture()
        before = matrices()
        with patch.object(
            seating,
            "verify_pose",
            side_effect=seating.OperationError(
                "placement_invalid", "Injected commit failure"
            ),
        ) as verify:
            result = api.execute(
                backend,
                OperationRequest(
                    type="operation.request",
                    request_id="rollback",
                    operation="blender.object_set.place",
                    arguments={"seating": spec},
                ),
            )
        verify.assert_called_once()
        self.assertNotIsInstance(result, OperationSuccess)
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))

    def test_transform_dependent_deformation_rolls_back(self) -> None:
        spec = fixture()
        call(
            "object_set.create",
            objects=[
                dict(
                    kind="primitive",
                    primitive="cube",
                    key="deformed",
                    name="Deformed",
                    parent=dict(name="Root"),
                    scale=[0.2, 0.2, 0.2],
                )
            ],
        )
        obj = bpy.data.objects["Deformed"]
        modifier = obj.modifiers.new("Fixed support", "SHRINKWRAP")
        modifier.target = bpy.data.objects["Target0"]
        before = matrices()
        result = api.execute(
            backend,
            OperationRequest(
                type="operation.request",
                request_id="dependent",
                operation="blender.object_set.place",
                arguments={"seating": spec},
            ),
        )
        self.assertNotIsInstance(result, OperationSuccess)
        self.assertIn("deformed a member", result.error.message)
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))

    def test_points_condition_and_evaluation_budget(self) -> None:
        spec = fixture()
        spec["interfaces"] = [
            dict(
                kind="point",
                name=f"p{i}",
                source=dict(kind="object", object=f"Source{i}", point=[0, 0, 0]),
                target=dict(kind="object", object=f"Target{i}", point=[0, 0, 0]),
            )
            for i in range(2)
        ]
        before = matrices()
        poor = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(poor["status"], "POORLY_CONDITIONED", poor)
        self.assertTrue(all(maximum(before[n], m) == 0 for n, m in matrices().items()))
        spec["interfaces"].append(
            dict(
                kind="point",
                name="third",
                source=dict(kind="object", object="Source0", point=[0, 0.5, 0]),
                target=dict(kind="object", object="Target0", point=[0, 0.25, 0]),
            )
        )
        limited = call("object_set.place", seating={**spec, "max_evaluations": 20})[
            "seating"
        ]
        self.assertEqual(limited["status"], "NO_CONVERGENCE", limited)
        self.assertFalse(limited["applied"])
        solved = call("object_set.place", seating=spec)["seating"]
        self.assertEqual(solved["status"], "SOLVED", solved)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SeatingChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    output = (
        Path(os.environ.get("TYVRANA_TEST_CONTROL", "/tmp")) / "seating-native.json"
    )
    output.write_text(json.dumps(metrics, indent=2))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("SEATING_NATIVE_PASSED", result.testsRun)
