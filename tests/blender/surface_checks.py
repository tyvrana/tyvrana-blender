"""Native independent surface construction, topology, persistence and rollback."""

import importlib
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.surface_fixtures import fixtures

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
ops = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
surfaces = importlib.import_module("bl_ext.user_default.tyvrana_blender.surfaces")
bindings = importlib.import_module("bl_ext.user_default.tyvrana_blender.bindings")
backend = adapter.BlenderBackend(adapter._runtime.worker.spool)


def execute(name: str, **args: Any) -> Any:
    return ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="surface",
            operation="blender." + name,
            arguments=args,
        ),
    )


def call(name: str, **args: Any) -> Any:
    result = execute(name, **args)
    assert isinstance(result, OperationSuccess), result
    return result.result


class SurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if not mesh.users:
                bpy.data.meshes.remove(mesh)

    def test_thickness_failure_identifies_patch_and_local_scale(self) -> None:
        spec = fixtures()[0]
        spec["thickness"] = 10
        spec["patches"][0].pop("thickness")
        before = (len(bpy.data.objects), len(bpy.data.meshes))
        result = execute("surface.create", surfaces=[spec])
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "surface_degenerate")
        self.assertIn("Thickness inverts", result.error.message)
        details = result.error.details
        self.assertEqual(details["patch_id"], "plate")
        self.assertEqual(len(details["thicknesses"]), 3)
        self.assertEqual(len(details["edge_lengths"]), 3)
        self.assertEqual(details["length_units"], "scene_units")
        self.assertLess(len(str(details)), 1000)
        self.assertEqual(before, (len(bpy.data.objects), len(bpy.data.meshes)))

    def test_hard_fixtures_manifold_genus_regions_bounds_and_determinism(self) -> None:
        specs = fixtures()
        result = call("surface.create", surfaces=specs)
        for summary, spec in zip(result["surfaces"], specs, strict=True):
            self.assertTrue(summary["valid"])
            self.assertEqual(summary["components"], 1)
            self.assertEqual(summary["non_manifold_edges"], 0)
            self.assertEqual(summary["boundary_edges"], 0)
            self.assertEqual(summary["opening_count"], len(spec["openings"]))
            # Thickened disk with N real through-openings has Euler characteristic 2-2N.
            self.assertEqual(
                summary["vertex_count"] - summary["edge_count"] + summary["face_count"],
                2 - 2 * len(spec["openings"]),
            )
            self.assertLessEqual(summary["max_vertex_valence"], 16)
            self.assertLess(summary["vertex_count"], 10000)
            expected = {
                "PerforatedShell": (4.4, 3.5),
                "BranchedSupport": (5.6, 3.5),
                "SocketMount": (6.0, 3.2),
                "SectionHousing": (4.3, 4.0),
            }[spec["name"]]
            for axis, size in enumerate(expected):
                measured = summary["bounds_max"][axis] - summary["bounds_min"][axis]
                self.assertAlmostEqual(measured, size, delta=0.15)
            if spec["name"] == "SocketMount":
                self.assertLess(summary["bounds_min"][2], -0.3)

            mesh = bpy.data.objects[spec["name"]].data
            points = [v.co for v in mesh.vertices]
            self.assertGreater(
                max(v.z for v in points) - min(v.z for v in points), 0.25
            )
            sig = surfaces.content_hash(mesh)
            rebuilt, _, _ = surfaces.build(surfaces.SurfaceSpec.model_validate(spec))
            self.assertEqual(sig, surfaces.content_hash(rebuilt))
            bpy.data.meshes.remove(rebuilt)
            summary = call("surface.inspect", names=[spec["name"]], region_limit=64)[
                "surfaces"
            ][0]
            self.assertTrue(all(r["face_count"] > 0 for r in summary["regions"]))

    def test_revision_and_rebuild_dependency_protection(self) -> None:
        spec = fixtures()[1]
        name = spec["name"]
        first = call("surface.create", surfaces=[spec])["surfaces"][0]
        obj = bpy.data.objects[name]
        pointer = obj.data.as_pointer()
        before = bindings._fingerprint("object", obj)
        edit = {
            "name": name,
            "expected_revision": 1,
            "nodes": [
                {"id": "e", "point": [-0.8, -2.9, 1.0]},
                {"id": "f", "point": [0.8, -2.9, 1.0]},
            ],
            "features": [{**spec["features"][1], "height": 0.14}],
            "thickness": 0.04,
        }
        updated = call("surface.configure", surfaces=[edit])["surfaces"][0]
        self.assertFalse(updated["topology_changed"])
        self.assertEqual(pointer, obj.data.as_pointer())
        self.assertEqual(first["surface_id"], updated["surface_id"])
        self.assertNotEqual(before, bindings._fingerprint("object", obj))
        opening = {**spec["openings"][0], "radii": [0.22, 0.17]}
        change = {"name": name, "expected_revision": 2, "openings": [opening]}
        rejected = execute("surface.configure", surfaces=[change])
        self.assertIsInstance(rejected, OperationFailure)
        self.assertEqual(rejected.error.code, "surface_topology_change_required")
        obj.vertex_groups.new(name="Downstream")
        rejected = execute(
            "surface.configure", surfaces=[{**change, "topology_policy": "rebuild"}]
        )
        self.assertEqual(rejected.error.code, "surface_revision_invalid")
        obj.vertex_groups.clear()
        rebuilt = call(
            "surface.configure", surfaces=[{**change, "topology_policy": "rebuild"}]
        )["surfaces"][0]
        self.assertTrue(rebuilt["topology_changed"])
        self.assertEqual(rebuilt["topology_revision"], 2)
        self.assertEqual(first["surface_id"], rebuilt["surface_id"])
        obj.data.vertices[0].co.z += 0.01
        self.assertFalse(call("surface.inspect", names=[name])["surfaces"][0]["valid"])
        self.assertEqual(
            execute(
                "surface.configure",
                surfaces=[{"name": name, "expected_revision": 3, "thickness": 0.035}],
            ).error.code,
            "surface_stale_geometry",
        )

    def test_failure_atomicity(self) -> None:
        for fault in ("contour", "junction", "budget", "self_conflict", "opening"):
            good = fixtures()[0]
            bad = deepcopy(good)
            bad["name"] = "Invalid"
            if fault == "contour":
                bad["patches"][0]["boundaries"].reverse()
                bad["curves"][0]["end"] = "c"
            if fault == "junction":
                bad = fixtures()[1]
                bad["name"] = "Invalid"
                bad["patches"].append({**deepcopy(bad["patches"][0]), "id": "extra"})
            if fault == "budget":
                bad["patches"][0]["resolution"] = 100
            if fault == "self_conflict":
                bad["nodes"][2]["point"] = bad["nodes"][0]["point"]
            if fault == "opening":
                bad["openings"][1]["center"] = bad["openings"][0]["center"]
            before = (len(bpy.data.objects), len(bpy.data.meshes))
            result = execute("surface.create", surfaces=[good, bad])
            self.assertIsInstance(result, OperationFailure, (fault, result))
            self.assertEqual(
                before, (len(bpy.data.objects), len(bpy.data.meshes)), fault
            )

    def test_injected_creation_and_topology_swap_rollback(self) -> None:
        specs = fixtures()[1:3]
        original = surfaces.state
        counter = 0

        def inject(*args: Any, **kwargs: Any) -> Any:
            nonlocal counter
            counter += 1
            if counter == 2:
                raise RuntimeError("injected commit failure")
            return original(*args, **kwargs)

        before = (len(bpy.data.objects), len(bpy.data.meshes))
        with patch.object(surfaces, "state", side_effect=inject):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                surfaces.create(
                    surfaces.SurfaceCreateArguments.model_validate({"surfaces": specs})
                )
        self.assertEqual(before, (len(bpy.data.objects), len(bpy.data.meshes)))
        call("surface.create", surfaces=specs)
        objects = [bpy.data.objects[s["name"]] for s in specs]
        signatures = [surfaces.content_hash(o.data) for o in objects]
        pointers = [o.data.as_pointer() for o in objects]
        count = len(bpy.data.meshes)
        counter = 0
        edits = [
            {
                "name": specs[0]["name"],
                "expected_revision": 1,
                "openings": [{**specs[0]["openings"][0], "radii": [0.22, 0.17]}],
                "topology_policy": "rebuild",
            },
            {"name": specs[1]["name"], "expected_revision": 1, "thickness": 0.04},
        ]
        with patch.object(surfaces, "state", side_effect=inject):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                surfaces.configure(
                    surfaces.SurfaceConfigureArguments.model_validate(
                        {"surfaces": edits}
                    )
                )
        self.assertEqual(count, len(bpy.data.meshes))
        self.assertEqual(signatures, [surfaces.content_hash(o.data) for o in objects])
        self.assertEqual(pointers, [o.data.as_pointer() for o in objects])

    def test_injected_commit_rollback_and_save_reopen(self) -> None:
        specs = fixtures()[:2]
        call("surface.create", surfaces=specs)
        names = [s["name"] for s in specs]
        before = [surfaces.content_hash(bpy.data.objects[n].data) for n in names]
        count = len(bpy.data.meshes)
        original = surfaces.state
        counter = 0

        def inject(*args: Any, **kwargs: Any) -> Any:
            nonlocal counter
            counter += 1
            if counter == 2:
                raise RuntimeError("injected commit failure")
            return original(*args, **kwargs)

        with patch.object(surfaces, "state", side_effect=inject):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                surfaces.configure(
                    surfaces.SurfaceConfigureArguments.model_validate(
                        {
                            "surfaces": [
                                {"name": n, "expected_revision": 1, "thickness": 0.04}
                                for n in names
                            ]
                        }
                    )
                )
        self.assertEqual(count, len(bpy.data.meshes))
        self.assertEqual(
            before, [surfaces.content_hash(bpy.data.objects[n].data) for n in names]
        )
        path = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "surfaces.blend")
        expected = call("surface.inspect", names=names)["surfaces"]
        call("file.save", filepath=path)
        call("file.open", filepath=path, discard_current=True)
        self.assertEqual(call("surface.inspect", names=names)["surfaces"], expected)


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceTests)
)
assert result.wasSuccessful()
print("SURFACE_NATIVE_PASSED", result.testsRun)
