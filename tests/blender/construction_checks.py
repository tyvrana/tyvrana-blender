"""Disposable native construction qualification and rollback injection."""

import importlib
import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
ops = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
construction = importlib.import_module(
    "bl_ext.user_default.tyvrana_blender.construction"
)
backend = adapter.BlenderBackend(adapter._runtime.worker.spool)
fixture_dir = Path(os.environ["TYVRANA_CONSTRUCTION_FIXTURE"])
fixture = json.loads((fixture_dir / "fixture.json").read_text())


def execute(operation: str, **arguments: Any) -> Any:
    return ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="construction",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )


def call(operation: str, **arguments: Any) -> Any:
    response = execute(operation, **arguments)
    assert isinstance(response, OperationSuccess), response
    return response.result


class ConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)
        if construction.STATE in bpy.context.scene:
            del bpy.context.scene[construction.STATE]
        bpy.context.scene.unit_settings.scale_length = 1
        for name in ("Front", "Side", "Top"):
            image = bpy.data.images.load(str(fixture_dir / f"{name}.png"))
            image.name = name
            image.pack()
        call(
            "reference.create",
            references=[{"name": n, "image": n} for n in ("Front", "Side", "Top")],
        )
        call("reference.register", registrations=fixture["registrations"])
        call("reference.observation.set", observations=fixture["observations"])

    def derive(self) -> Any:
        return call(
            "landmark.derive", landmarks=fixture["landmarks"], tolerance=0.00001
        )

    def test_three_view_ground_truth_provenance_repeatability(self) -> None:
        result = self.derive()
        self.assertEqual(result["solved"], 32)
        self.assertLess(result["max_residual"], 1e-6)
        for i, expected in enumerate(fixture["points"]):
            actual = bpy.data.objects[f"Corner.{i:02}"].matrix_world.translation
            for a, e in zip(actual, expected, strict=True):
                self.assertAlmostEqual(a, e, places=6)
        before = call("landmark.inspect", names=["Corner.00"], detail="provenance")
        self.assertEqual(len(before["provenance"][0]["observations"]), 3)
        self.assertIsNotNone(before["provenance"][0]["result"]["sigma"])
        self.derive()
        self.assertEqual(
            call("landmark.inspect", names=["Corner.00"], detail="provenance"), before
        )

    def test_rank_conflict_and_plane_constraint(self) -> None:
        points = [
            {
                "name": "Two",
                "source": {
                    "kind": "observations",
                    "observations": ["Front.00", "Side.00"],
                },
            },
            {
                "name": "One",
                "source": {"kind": "observations", "observations": ["Front.00"]},
            },
            {
                "name": "Plane",
                "source": {
                    "kind": "observations",
                    "observations": ["Front.00"],
                    "constraints": [{"axis": "y", "value": -0.5}],
                },
            },
        ]
        result = call("landmark.derive", landmarks=points)
        self.assertEqual((result["solved"], result["underconstrained"]), (2, 1))
        self.assertNotIn("One", bpy.data.objects)
        bad = {**fixture["observations"][0], "pixel": [100, 100]}
        call("reference.observation.set", observations=[bad])
        result = call("landmark.derive", landmarks=[fixture["landmarks"][0]])
        self.assertEqual(result["inconsistent"], 1)
        self.assertEqual(result["worst"][0]["worst_observation"], "Front.00")
        self.assertNotIn("Corner.00", bpy.data.objects)

    def test_staleness_and_semantic_binding_fingerprint(self) -> None:
        self.derive()
        bindings = importlib.import_module(
            "bl_ext.user_default.tyvrana_blender.bindings"
        )
        obj = bpy.data.objects["Corner.00"]
        before = bindings._fingerprint("object", obj)
        call(
            "reference.calibrate", name="Front", a=[0, 0], b=[200, 0], target_distance=3
        )
        inspected = call("landmark.inspect", names=["Corner.00"])["landmarks"][0]
        self.assertTrue(inspected["stale"])
        self.assertFalse(inspected["valid"])
        self.assertNotEqual(bindings._fingerprint("object", obj), before)
        result = execute(
            "measurement.inspect",
            queries=[
                {
                    "kind": "distance",
                    "name": "D",
                    "a": {"kind": "landmark", "name": "Corner.00"},
                    "b": {"kind": "world", "point": [0, 0, 0]},
                }
            ],
        )
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "stale_dependency")
        self.assertEqual(self.derive()["stale"], 32)

    def test_frame_mapping_reflection_and_frame_invalidation(self) -> None:
        call("landmark.set", landmarks=[{"name": "Frame", "point": [3, 4, 5]}])
        call("object.set_transform", name="Frame", rotation=[0, 0, 0.5])
        registrations = [{**r, "frame": "Frame"} for r in fixture["registrations"]]
        call("reference.register", registrations=registrations)
        call("landmark.derive", landmarks=fixture["landmarks"], frame="Frame")
        p = call("landmark.inspect", names=["Corner.00"], detail="provenance")[
            "provenance"
        ][0]
        for a, b in zip(p["point"], fixture["points"][0], strict=True):
            self.assertAlmostEqual(a, b, places=5)
        result = call(
            "landmark.derive",
            frame="Frame",
            landmarks=[
                {
                    "name": "Mirrored",
                    "source": {
                        "kind": "reflection",
                        "landmark": "Corner.00",
                        "axis": "x",
                    },
                }
            ],
        )
        self.assertEqual(result["solved"], 1)
        m = call("landmark.inspect", names=["Mirrored"], detail="provenance")[
            "provenance"
        ][0]
        self.assertAlmostEqual(m["point"][0], -p["point"][0], places=5)
        call("object.set_transform", name="Frame", location=[4, 4, 5])
        self.assertTrue(
            call("landmark.inspect", names=["Mirrored"])["landmarks"][0]["stale"]
        )

    def test_source_persistence_and_literal_override(self) -> None:
        self.derive()
        before = call(
            "reference.observation.inspect", names=["Front.00"], mapped_points=True
        )
        path = fixture_dir / "native-persist.blend"
        call("file.save", filepath=str(path))
        call("file.open", filepath=str(path), discard_current=True)
        self.assertEqual(
            call(
                "reference.observation.inspect", names=["Front.00"], mapped_points=True
            ),
            before,
        )
        self.assertFalse(
            call("landmark.inspect", names=["Corner.00"])["landmarks"][0]["stale"]
        )
        call("landmark.set", landmarks=[{"name": "Corner.00", "point": [1, 2, 3]}])
        self.assertNotIn(construction.DERIVATION, bpy.data.objects["Corner.00"])

    def test_projection_refusal_and_atomic_failure(self) -> None:
        before = call("reference.inspect")
        result = execute(
            "reference.register",
            registrations=[
                {**fixture["registrations"][0], "projection": "perspective"}
            ],
        )
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "unsupported_projection")
        self.assertEqual(call("reference.inspect"), before)
        with patch.object(
            construction,
            "identity",
            side_effect=RuntimeError("injected after native solve"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                construction.derive(
                    construction.LandmarkDeriveArguments.model_validate(
                        {"landmarks": fixture["landmarks"]}
                    )
                )
        self.assertFalse(any(n.startswith("Corner.") for n in bpy.data.objects.keys()))
        self.assertEqual(call("reference.inspect"), before)

    def test_reference_transform_source_points_and_occlusion(self) -> None:
        self.derive()
        before = call("reference.observation.inspect", names=["Front.00"])
        call("object.set_transform", name="Front", location=[1, 0, 0])
        after = call("reference.observation.inspect", names=["Front.00"])
        self.assertEqual(before, after)
        self.assertTrue(
            call("landmark.inspect", names=["Corner.00"])["landmarks"][0]["stale"]
        )
        call("reference.register", registrations=fixture["registrations"])
        call(
            "reference.observation.set",
            observations=[{**fixture["observations"][0], "visibility": "occluded"}],
        )
        result = call(
            "landmark.derive",
            landmarks=[
                {
                    "name": "Hidden",
                    "source": {"kind": "observations", "observations": ["Front.00"]},
                }
            ],
        )
        self.assertEqual(result["underconstrained"], 1)
        self.assertNotIn("Hidden", bpy.data.objects)

    def test_observation_remove_recreate_cannot_revive_old_solution(self) -> None:
        self.derive()
        first = call("reference.observation.inspect", names=["Front.00"])[
            "observations"
        ][0]
        call("reference.observation.remove", names=["Front.00"])
        self.assertTrue(
            call("landmark.inspect", names=["Corner.00"])["landmarks"][0]["stale"]
        )
        call("reference.observation.set", observations=[fixture["observations"][0]])
        second = call("reference.observation.inspect", names=["Front.00"])[
            "observations"
        ][0]
        self.assertGreater(second["revision"], first["revision"])
        self.assertTrue(
            call("landmark.inspect", names=["Corner.00"])["landmarks"][0]["stale"]
        )
        invalid = execute(
            "reference.observation.set",
            observations=[{**fixture["observations"][0], "pixel": [-1, 0]}],
        )
        self.assertIsInstance(invalid, OperationFailure)
        self.assertEqual(invalid.error.code, "observation_outside_source")
        self.assertEqual(
            call("reference.observation.inspect", names=["Front.00"])["observations"][
                0
            ],
            second,
        )

    def test_declared_plane_and_source_replacement(self) -> None:
        call(
            "reference.register",
            registrations=[{**fixture["registrations"][0], "projection": "plane"}],
        )
        result = call(
            "landmark.derive",
            landmarks=[
                {
                    "name": "Planar",
                    "source": {"kind": "observations", "observations": ["Front.00"]},
                }
            ],
        )
        self.assertEqual(result["solved"], 1)
        point = call("landmark.inspect", names=["Planar"])["landmarks"][0][
            "world_point"
        ]
        self.assertAlmostEqual(point[1], 0)
        # An external source replacement is detected without rewriting observations.
        bpy.data.objects["Front"].data = bpy.data.images["Side"]
        self.assertTrue(
            call("reference.observation.inspect", names=["Front.00"])["observations"][
                0
            ]["stale"]
        )
        self.assertTrue(
            call("landmark.inspect", names=["Planar"])["landmarks"][0]["stale"]
        )


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(ConstructionTests)
)
assert result.wasSuccessful()
print("CONSTRUCTION_NATIVE_PASSED", result.testsRun)
