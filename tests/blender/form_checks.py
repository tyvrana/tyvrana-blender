"""Native reference-field authoring, smooth fusion and atomic protection checks."""

import importlib
import json
import os
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

package = os.environ.get("TYVRANA_TEST_PACKAGE", "bl_ext.user_default.tyvrana_blender")
adapter = importlib.import_module(package + ".blender")
ops = importlib.import_module(package + ".operations")
jobs = importlib.import_module(package + ".form_jobs")
surfaces = importlib.import_module(package + ".surfaces")


def response(operation: str, *, _wait: bool = True, **arguments: Any) -> Any:
    result = ops.execute(
        adapter.BlenderBackend(),
        OperationRequest(
            type="operation.request",
            request_id="form-native",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    if (
        _wait
        and isinstance(result, OperationSuccess)
        and operation in {"form.create", "form.configure"}
    ):
        assert isinstance(result.result, dict)
        identifier = result.result["job_id"]
        assert isinstance(identifier, str)
        deadline = time.monotonic() + 60
        while jobs.busy():
            assert time.monotonic() < deadline
            jobs.tick()
        job = jobs.status(identifier)
        if job.error:
            return OperationFailure(
                type="operation.failure", request_id="form-native", error=job.error
            )
        assert job.state == "completed" and job.result is not None, job
        return result.model_copy(update={"result": job.result.model_dump(mode="json")})
    return result


def call(operation: str, **arguments: Any) -> Any:
    result = response(operation, **arguments)
    assert isinstance(result, OperationSuccess), result
    return result.result


def simple(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "voxel_size": 0.04,
        "parts": [
            {
                "id": "body",
                "kind": "ellipsoid",
                "center": [0, 0, 0],
                "radii": [0.8, 0.5, 0.4],
            },
            {
                "id": "branch",
                "kind": "ellipsoid",
                "center": [0.6, 0.2, 0.1],
                "radii": [0.8, 0.25, 0.2],
                "rotation": [0, 0, 0.35],
                "blend": 0.25,
            },
            {
                "id": "opening",
                "kind": "ellipsoid",
                "center": [-0.3, 0, 0],
                "radii": [0.2, 0.25, 0.9],
                "operation": "subtract",
                "blend": 0.08,
            },
        ],
    }


class FormChecks(unittest.TestCase):
    def test_publication_and_terminal_job_state_share_one_step(self) -> None:
        native = importlib.import_module(package + ".native_jobs")
        clock = iter(range(10000))
        with patch.object(
            native, "time", SimpleNamespace(perf_counter=lambda: next(clock))
        ):
            queued = response("form.create", _wait=False, forms=[simple("Terminal")])
            self.assertIsInstance(queued, OperationSuccess)
            identifier = queued.result["job_id"]
            for _ in range(1000):
                jobs.tick()
                state = jobs.status(identifier).state
                if "Terminal" in bpy.data.objects:
                    self.assertEqual(state, "completed")
                if state not in {"queued", "running"}:
                    break
            self.assertEqual(state, "completed")

    def test_guarded_jobs_identity_cancellation_and_publication_guard(self) -> None:
        mutations = importlib.import_module(package + ".mutation_jobs")
        models = importlib.import_module(package + ".mutation_models")
        attestations = importlib.import_module(package + ".attestation_jobs")
        call("project.bind", resources=[])

        def observation() -> Any:
            steps = attestations.guarded_steps()
            while True:
                try:
                    next(steps)
                except StopIteration as done:
                    return done.value.root

        def begin(name: str, wrong: bool = False) -> Any:
            before = observation()
            args = models.MutationArguments(
                mutation_id=name,
                operation="blender.form.create",
                arguments=dict(forms=[simple(name)]),
                **{
                    key: before[key]
                    for key in (
                        "host_session_id",
                        "document_session_id",
                        "project_id",
                        "format",
                        "digest",
                    )
                },
            )
            if wrong:
                args = args.model_copy(update={"host_session_id": "different-host"})
            request = OperationRequest(
                type="operation.request",
                request_id=name,
                operation="blender.document.mutate",
                arguments=args.model_dump(mode="json"),
            )
            return mutations.start(adapter.BlenderBackend(), args, request)

        def finish(identifier: str) -> Any:
            deadline = time.monotonic() + 30
            while mutations.busy():
                self.assertLess(time.monotonic(), deadline)
                mutations.tick()
                jobs.tick()
            return mutations.status(identifier)

        try:
            bad = begin("Wrong", wrong=True)
            self.assertEqual(finish(bad.job_id).error.code, "content_diverged")
            self.assertNotIn("Wrong", bpy.data.objects)
            valid = begin("Valid")
            result = finish(valid.job_id)
            self.assertEqual(result.state, "completed", result)
            self.assertEqual(result.result.root["result"]["state"], "completed")
            before = observation()["digest"]
            cancelled = begin("Cancelled")
            while not jobs.busy():
                mutations.tick()
            identifier = jobs._jobs.active
            cancelled_result = response("form.cancel", job_id=identifier)
            self.assertIsInstance(cancelled_result, OperationSuccess)
            self.assertEqual(finish(cancelled.job_id).error.code, "operation_cancelled")
            self.assertNotIn("Cancelled", bpy.data.objects)
            self.assertEqual(observation()["digest"], before)
            changed = begin("Changed")
            while not jobs.busy():
                mutations.tick()
            # Native implementation fixture simulates a user edit during preparation.
            bpy.data.objects["Valid"].location.x += 1
            bpy.context.view_layer.update()
            result = finish(changed.job_id)
            self.assertEqual(result.error.code, "content_diverged", result)
            self.assertNotIn("Changed", bpy.data.objects)
            self.assertEqual(bpy.data.objects["Valid"].location.x, 1)
            self.assertFalse(mutations.allows("blender.form.cancel"))
        finally:
            mutations.shutdown()
            jobs.shutdown()

    def test_cross_section_support_preserves_planes_and_requires_agreement(
        self,
    ) -> None:
        geometry = importlib.import_module(package + ".form_geometry")
        models = importlib.import_module(package + ".form_models")

        def stack(normal: list[int], value: float, positions: list[float]) -> Any:
            mask = SimpleNamespace(
                normal=np.array(normal),
                sample=lambda points: np.full(len(points), value),
            )
            return {"masks": [mask, mask], "positions": np.array(positions)}

        part = models.SectionMaskPart(
            id="body",
            kind="sections",
            masks=[models.ReferenceMask(reference=n) for n in ("Start", "End")],
        )
        primary = stack([1, 0, 0], 0.2, [0, 1])
        primary["missing_intervals"] = [(0, 1)]
        primary["complementary"] = [
            stack([0, 1, 0], -0.2, [-1, 1]),
            stack([0, 0, 1], -0.2, [-1, 1]),
        ]
        points = np.array([[0, 0, 0], [0.5, 0, 0], [1, 0, 0]])
        np.testing.assert_allclose(
            geometry.analytic(part, primary, points), [0.2, -0.2, 0.2], atol=1e-7
        )
        primary["complementary"][1] = stack([0, 0, 1], 0.1, [-1, 1])
        np.testing.assert_allclose(geometry.analytic(part, primary, points), 0.2)

    def test_section_support_fills_only_existing_matching_internal_planes(self) -> None:
        geometry = importlib.import_module(package + ".form_geometry")
        models = importlib.import_module(package + ".form_models")
        reference = importlib.import_module(package + ".form_reference")
        foreground = np.zeros((5, 5), dtype=bool)
        foreground[1:4, 1:4] = True

        def mask(position: float, lateral: float = 0) -> Any:
            return reference.Mask(
                np.ones((5, 5)),
                foreground,
                np.array([position, lateral, 0]),
                np.array([0, 1, 0]),
                np.array([0, 0, 1]),
                np.array([1, 0, 0]),
                (1.0, 1.0),
                {},
            )

        source = {
            "Start": mask(0),
            "Middle": mask(1),
            "End": mask(2),
            "Outside": mask(3),
            "OtherFrame": mask(0.5, 0.5),
        }
        part = models.SectionMaskPart(
            id="body",
            kind="sections",
            masks=[models.ReferenceMask(reference=n) for n in ("Start", "End")],
        )
        support = models.ReferenceSurfaceFit(
            masks=[models.ReferenceMask(reference=n) for n in source],
            radius=0.3,
            max_distance=0.2,
        )
        with patch.object(
            geometry, "compile_mask", side_effect=lambda m, _: source[m.reference]
        ):
            plain = geometry.prepare(part, 0.1, 0.4)
            filled = geometry.prepare(part, 0.1, 0.4, support)
            np.testing.assert_array_equal(plain["positions"], [0, 2])
            np.testing.assert_array_equal(filled["positions"], [0, 1, 2])
            reversed_part = part.model_copy(
                update={"masks": list(reversed(part.masks))}
            )
            with self.assertRaises(geometry.OperationError):
                geometry.prepare(reversed_part, 0.1, 0.4, support)

    def test_fit_displacement_field_extends_through_unsupported_points(self) -> None:
        fitting = importlib.import_module(package + ".form_fitting")
        y, x = np.mgrid[:7, :7]
        original = np.column_stack((x.ravel(), y.ravel(), np.zeros(49))) * 0.02
        ids = np.arange(49).reshape((7, 7))
        edges = np.concatenate(
            [
                np.column_stack((ids[:, :-1].ravel(), ids[:, 1:].ravel())),
                np.column_stack((ids[:-1].ravel(), ids[1:].ravel())),
            ]
        )
        supported = np.ones(49, dtype=bool)
        supported[24] = False
        proposed = original.copy()
        proposed[:, 2] = 0.2 + 0.04 * (-1.0) ** (x + y).ravel()
        proposed[24] = original[24]
        displacement = fitting.coherent_displacements(
            original, proposed, edges, supported, 0.16
        )
        self.assertGreater(displacement[24, 2], 0.15)
        self.assertLess(abs(displacement[24, 2] - 0.2), 0.01)
        self.assertLess(np.std(displacement[:, 2]), np.std(proposed[:, 2]) / 3)
        self.assertLess(abs(np.mean(displacement[:, 2]) - 0.2), 0.01)
        np.testing.assert_array_equal(displacement[:, :2], 0)

    def test_distance_fields_match_exact_anisotropic_grid_distances(self) -> None:
        distance = importlib.import_module(package + ".form_distance")
        rng = np.random.default_rng(900)
        for height, width in ((1, 1), (1, 7), (9, 1), (5, 8), (17, 13)):
            for sx, sy in ((1, 1), (0.25, 3), (7, 0.08)):
                for _ in range(10):
                    boundary = rng.random((height, width)) < 0.2
                    boundary[rng.integers(height), rng.integers(width)] = True
                    rows, columns = np.nonzero(boundary)
                    expected = np.array(
                        [
                            [
                                min((sx * (x - columns)) ** 2 + (sy * (y - rows)) ** 2)
                                ** 0.5
                                for x in range(width)
                            ]
                            for y in range(height)
                        ]
                    )
                    np.testing.assert_allclose(
                        distance.distance_grid(boundary, sx, sy),
                        expected,
                        rtol=1e-12,
                        atol=1e-10,
                    )

    def setUp(self) -> None:
        jobs.shutdown()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.meshes):
            if not data.users:
                bpy.data.meshes.remove(data)

    def sphere_observations(self) -> list[dict[str, str]]:
        masks = []
        size, span = 96, 2.4
        y, x = np.mgrid[:size, :size]
        u, v = (x + 0.5 - size / 2) * span / size, (y + 0.5 - size / 2) * span / size
        for axis in range(3):
            horizontal, vertical = [i for i in range(3) if i != axis]
            for index, position in enumerate((-0.8, -0.4, 0, 0.4, 0.8)):
                name = f"Sphere{axis}_{index}"
                image = bpy.data.images.new(name, width=size, height=size, alpha=True)
                pixels = np.zeros((size, size, 4), dtype=np.float32)
                pixels[:, :, :3] = 0.7
                pixels[:, :, 3] = u * u + v * v + position * position <= 1
                image.pixels.foreach_set(pixels.reshape(-1))
                image.pack()
                call(
                    "reference.create", references=[{"name": name, "image": image.name}]
                )
                origin = [0.0, 0.0, 0.0]
                origin[axis] = position
                call(
                    "reference.register",
                    registrations=[
                        {
                            "reference": name,
                            "projection": "plane",
                            "calibration": {
                                "a": [0, 0],
                                "b": [size, 0],
                                "distance": span,
                            },
                            "origin_pixel": [size / 2, size / 2],
                            "origin": origin,
                            "horizontal": "xyz"[horizontal],
                            "vertical": "xyz"[vertical],
                        }
                    ],
                )
                masks.append({"reference": name})
        return masks

    def fitted_sphere(self, name: str, masks: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "name": name,
            "voxel_size": 0.09,
            "parts": [
                {
                    "kind": "ellipsoid",
                    "id": "body",
                    "center": [0, 0, 0],
                    "radii": [1.08, 1.08, 1.08],
                }
            ],
            "surface_fit": {"masks": masks, "radius": 0.35, "max_distance": 0.15},
        }

    def test_adaptive_fit_improves_measured_surface_within_displacement_bound(
        self,
    ) -> None:
        masks = self.sphere_observations()
        spec = self.fitted_sphere("Adaptive", masks)
        spec["surface_fit"].update(adaptive_neighborhood=True, max_distance=0.06)
        plain = {**spec, "name": "PlainAdaptive", "surface_fit": None}
        result = call("form.create", forms=[plain, spec])["forms"][1]
        before = np.array(
            [v.co[:] for v in bpy.data.objects["PlainAdaptive"].data.vertices]
        )
        after = np.array([v.co[:] for v in bpy.data.objects["Adaptive"].data.vertices])
        self.assertLess(
            float(abs(np.linalg.norm(after, axis=1) - 1).mean()),
            float(abs(np.linalg.norm(before, axis=1) - 1).mean()) / 2,
        )
        self.assertLessEqual(
            float(np.linalg.norm(after - before, axis=1).max()), 0.060001
        )
        self.assertGreater(result["surface_fit"]["displacement_limited_vertices"], 0)
        self.assertEqual(
            surfaces.native_checks(bpy.data.objects["Adaptive"].data)[
                "non_manifold_edges"
            ],
            0,
        )

    def test_reference_fit_accuracy_bounds_persistence_and_freshness(self) -> None:
        masks = self.sphere_observations()
        spec = self.fitted_sphere("Fitted", masks)
        plain = {**spec, "name": "Plain", "surface_fit": None}
        clipped = deepcopy(spec)
        clipped["name"] = "Clipped"
        clipped["surface_fit"]["max_distance"] = 0.01
        result = call("form.create", forms=[plain, spec, clipped])["forms"]
        before = np.array([v.co[:] for v in bpy.data.objects["Plain"].data.vertices])
        fitted = np.array([v.co[:] for v in bpy.data.objects["Fitted"].data.vertices])
        after = np.array([v.co[:] for v in bpy.data.objects["Clipped"].data.vertices])
        self.assertLess(float(abs(np.linalg.norm(fitted, axis=1) - 1).mean()), 0.015)
        self.assertLess(
            float(abs(np.linalg.norm(fitted, axis=1) - 1).mean()),
            float(abs(np.linalg.norm(before, axis=1) - 1).mean()) / 3,
        )
        self.assertLessEqual(
            float(np.linalg.norm(after - before, axis=1).max()), 0.010001
        )
        self.assertGreater(result[2]["surface_fit"]["displacement_limited_vertices"], 0)
        self.assertEqual(result[1]["source_count"], 15)
        self.assertGreater(result[1]["surface_fit"]["reference_points"], 1000)
        self.assertEqual(
            surfaces.native_checks(bpy.data.objects["Fitted"].data)[
                "non_manifold_edges"
            ],
            0,
        )
        output = Path(os.environ["TYVRANA_TEST_OUTPUT"]) / "fitted-reference.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(output), load_ui=False, use_scripts=False)
        compiled = importlib.import_module(package + ".form_reference")
        form_schema = importlib.import_module(package + ".form_models")
        reference_api = importlib.import_module(package + ".references")
        for mask in masks:
            reference = bpy.data.objects[mask["reference"]]
            image = reference.data
            self.assertEqual(image.source, "FILE")
            image.buffers_free()
            self.assertFalse(image.has_data)
            reference_api.reference_summary(reference)
            self.assertFalse(image.has_data)
            compiled.compile_mask(form_schema.ReferenceMask(**mask), "plane")
            self.assertFalse(image.has_data)
        restored = call("form.inspect", names=["Fitted"])["forms"][0]
        self.assertEqual(restored, result[1])
        self.assertTrue(
            all(not bpy.data.objects[m["reference"]].data.has_data for m in masks)
        )
        call(
            "object.set_transform",
            name=masks[0]["reference"],
            location=[0.1, 0, 0],
        )
        self.assertFalse(call("form.inspect", names=["Fitted"])["forms"][0]["valid"])
        removed = call(
            "form.configure",
            forms=[
                {
                    "name": "Fitted",
                    "expected_revision": 1,
                    "surface_fit": None,
                }
            ],
        )["forms"][0]
        self.assertTrue(removed["valid"])
        self.assertIsNone(removed["surface_fit"])
        self.assertEqual(removed["source_count"], 0)

    def test_reference_fit_cancel_limits_and_failure_leave_no_partial_mesh(
        self,
    ) -> None:
        fitting: Any = importlib.import_module(package + ".form_fitting")
        masks = self.sphere_observations()
        spec = self.fitted_sphere("Cancelled", masks)
        baseline = len(bpy.data.meshes)
        started = response("form.create", _wait=False, forms=[spec])
        self.assertIsInstance(started, OperationSuccess)
        deadline = time.monotonic() + 30
        while bpy.data.meshes.get("Cancelled.Mesh") is None:
            self.assertLess(time.monotonic(), deadline)
            self.assertTrue(jobs.busy())
            jobs.tick()
        self.assertTrue(jobs.busy())
        call("form.cancel", job_id=started.result["job_id"])
        self.assertEqual(len(bpy.data.meshes), baseline)
        self.assertNotIn("Cancelled", bpy.data.objects)
        old_limit = fitting.WORK_LIMIT
        try:
            fitting.WORK_LIMIT = 1
            rejected = response("form.create", forms=[spec])
            self.assertEqual(rejected.error.code, "form_fit_limit")
        finally:
            fitting.WORK_LIMIT = old_limit
        self.assertEqual(len(bpy.data.meshes), baseline)
        old_points = fitting.POINT_LIMIT
        try:
            fitting.POINT_LIMIT = 16
            rejected = response("form.create", forms=[spec])
            self.assertEqual(rejected.error.code, "form_fit_limit")
        finally:
            fitting.POINT_LIMIT = old_points
        self.assertEqual(len(bpy.data.meshes), baseline)
        bad = deepcopy(spec)
        bad["name"] = "Far"
        bad["parts"][0]["center"] = [5, 0, 0]
        rejected = response("form.create", forms=[spec, bad])
        self.assertEqual(rejected.error.code, "form_fit_insufficient")
        self.assertEqual(len(bpy.data.meshes), baseline)
        self.assertNotIn("Cancelled", bpy.data.objects)
        self.assertNotIn("Far", bpy.data.objects)

    def test_fusion_revision_identity_and_downstream_protection(self) -> None:
        created = call("form.create", forms=[simple("Casting")])["forms"][0]
        obj = bpy.data.objects["Casting"]
        stats = surfaces.native_checks(obj.data)
        self.assertEqual(stats["boundary_edges"], 0)
        self.assertEqual(stats["non_manifold_edges"], 0)
        self.assertEqual(stats["components"], 1)
        before = surfaces.content_hash(obj.data)
        revised = call(
            "form.configure",
            forms=[
                {
                    "name": obj.name,
                    "expected_revision": 1,
                    "parts": [
                        {
                            "id": "recess",
                            "kind": "ellipsoid",
                            "center": [0.1, 0.2, 0.4],
                            "radii": [0.3, 0.2, 0.18],
                            "operation": "subtract",
                            "blend": 0.1,
                        }
                    ],
                }
            ],
        )["forms"][0]
        self.assertEqual(revised["component_id"], created["component_id"])
        self.assertEqual(revised["revision"], 2)
        self.assertNotEqual(surfaces.content_hash(obj.data), before)
        obj.data.uv_layers.new(name="ProductionUV")
        before = surfaces.content_hash(obj.data)
        rejected = response(
            "form.configure",
            forms=[{"name": obj.name, "expected_revision": 2, "voxel_size": 0.03}],
        )
        self.assertIsInstance(rejected, OperationFailure)
        self.assertEqual(rejected.error.code, "form_downstream_data")
        self.assertEqual(surfaces.content_hash(obj.data), before)

    def test_section_padding_does_not_consume_material_sampling_budget(self) -> None:
        from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

        outputs = []
        for size in (64, 256):
            names = []
            for index, z in enumerate((-0.2, 0.2)):
                name = f"Padded{size}_{index}"
                image = bpy.data.images.new(name, width=size, height=size, alpha=True)
                pixels = np.zeros((size, size, 4), dtype=np.float32)
                pixels[:, :, :3] = 0.7
                middle = size // 2
                pixels[middle - 6 : middle + 6, middle - 8 : middle + 8, 3] = 1
                image.pixels.foreach_set(pixels.reshape(-1))
                image.pack()
                call(
                    "reference.create", references=[{"name": name, "image": image.name}]
                )
                call(
                    "reference.register",
                    registrations=[
                        {
                            "reference": name,
                            "projection": "plane",
                            "calibration": {"a": [0, 0], "b": [1, 0], "distance": 0.04},
                            "origin_pixel": [middle, middle],
                            "origin": [0, 0, z],
                            "horizontal": "x",
                            "vertical": "y",
                        }
                    ],
                )
                names.append({"reference": name})
            result = call(
                "form.create",
                forms=[
                    {
                        "name": f"Bounded{size}",
                        "voxel_size": 0.037,
                        "max_voxels": 32768,
                        "parts": [
                            {"id": "observations", "kind": "sections", "masks": names}
                        ],
                    }
                ],
            )["forms"][0]
            mesh = bpy.data.objects[result["name"]].data
            self.assertEqual(surfaces.native_checks(mesh)["non_manifold_edges"], 0)
            outputs.append(mesh)
        # Registration uses native float transforms; compare geometric agreement
        # rather than requiring bit-identical meshing after a larger image offset.
        for first, second in (outputs, outputs[::-1]):
            tree = BVHTree.FromPolygons(
                [v.co for v in first.vertices], [p.vertices[:] for p in first.polygons]
            )
            self.assertLess(
                max(tree.find_nearest(v.co)[3] for v in second.vertices), 1e-5
            )

    def test_incremental_batch_cancel_and_failure_are_atomic(self) -> None:
        names = [f"Batch{i}" for i in range(8)]
        baseline = len(bpy.data.meshes)
        started = response("form.create", _wait=False, forms=[simple(n) for n in names])
        self.assertIsInstance(started, OperationSuccess)
        identifier = started.result["job_id"]
        self.assertEqual(started.result["state"], "queued")
        deadline = time.monotonic() + 30
        while jobs.status(identifier).prepared_forms < 1:
            self.assertTrue(jobs.busy())
            self.assertLess(time.monotonic(), deadline)
            jobs.tick()
        self.assertFalse(any(bpy.data.objects.get(n) for n in names))
        self.assertFalse(
            adapter.BlenderBackend().operation_allowed("blender.file.save")
        )
        self.assertTrue(
            adapter.BlenderBackend().operation_allowed("blender.form.cancel")
        )
        cancelled = call("form.cancel", job_id=identifier)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(len(bpy.data.meshes), baseline)
        call("form.create", forms=[simple("Original")])
        before = surfaces.content_hash(bpy.data.objects["Original"].data)
        started = response(
            "form.configure",
            _wait=False,
            forms=[{"name": "Original", "expected_revision": 1, "voxel_size": 0.025}],
        )
        jobs.tick()
        call("form.cancel", job_id=started.result["job_id"])
        self.assertEqual(
            surfaces.content_hash(bpy.data.objects["Original"].data), before
        )
        self.assertEqual(
            call("form.inspect", names=["Original"])["forms"][0]["revision"], 1
        )

    def test_serial_form_morphs_and_exception_preserve_identities(self) -> None:
        first = simple("First")
        last = simple("Last")
        last["parts"][0]["radii"] = [0.5, 0.8, 0.35]
        last["parts"][1]["center"] = [0.2, 0.5, 0.1]
        last["parts"][1]["radii"] = [0.3, 0.6, 0.18]
        last["parts"][1]["rotation"] = [0, 0, 0.6]
        last["parts"][2]["radii"] = [0.12, 0.3, 0.9]
        result = call(
            "assembly.create",
            name="Varied",
            max_vertices=524288,
            templates=[
                {"id": "first", "kind": "form", "spec": first},
                {"id": "last", "kind": "form", "spec": last},
            ],
            families=[
                {
                    "id": "series",
                    "template": "first",
                    "count": 15,
                    "path": [[0, 0, 0], [0, 36, 0]],
                    "morphs": [{"position": 1, "template": "last"}],
                    "overrides": [
                        {
                            "index": 7,
                            "shape": {
                                "form_parts": [
                                    {
                                        "id": "exception",
                                        "kind": "ellipsoid",
                                        "center": [0.4, 0.2, 0.45],
                                        "radii": [0.25, 0.2, 0.15],
                                        "blend": 0.15,
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        )
        self.assertEqual(result["component_count"], 15)
        self.assertTrue(result["valid"])
        values = call(
            "form.inspect",
            names=[f"Varied.series.{i:02}" for i in range(15)],
            include_spec=True,
        )["forms"]
        self.assertEqual(len({v["component_id"] for v in values}), 15)
        self.assertEqual(len({v["spec"]["parts"][0]["radii"][0] for v in values}), 15)
        self.assertEqual(len(values[7]["parts"]), 4)
        self.assertTrue(all(v["valid"] for v in values))
        counts = (len(bpy.data.objects), len(bpy.data.meshes))
        revised = call(
            "assembly.configure",
            name="Varied",
            expected_revision=1,
            topology_policy="rebuild",
            members=[{"family": "series", "index": 7, "offset": [0, 0, 0.1]}],
        )
        self.assertTrue(revised["valid"])
        again = call(
            "form.inspect", names=[f"Varied.series.{i:02}" for i in range(15)]
        )["forms"]
        self.assertEqual(
            [v["component_id"] for v in values], [v["component_id"] for v in again]
        )
        self.assertEqual(counts, (len(bpy.data.objects), len(bpy.data.meshes)))

    def test_topology_compatible_loft_family_shape_morph(self) -> None:
        first: dict[str, Any] = {
            "name": "First",
            "sections": [
                {"id": "start", "center": [0, 0, 0], "radii": [0.3, 0.2, 0.25, 0.2]},
                {
                    "id": "middle",
                    "center": [0, 1, 0.1],
                    "radii": [0.18, 0.15, 0.13, 0.12],
                },
                {"id": "end", "center": [0, 2, 0], "radii": [0.25, 0.3, 0.2, 0.2]},
            ],
            "sides": 16,
            "subdivisions": 6,
        }
        last = deepcopy(first)
        last["name"] = "Last"
        last["sections"][1]["center"] = [0.3, 1, 0.4]
        last["sections"][1]["twist"] = 0.6
        # All discrete controls must be explicit/equal across morph keys.
        first["sections"][1]["twist"] = 0.0
        last["sections"][2]["radii"] = [0.5, 0.2, 0.4, 0.25]
        result = call(
            "assembly.create",
            name="MorphLofts",
            templates=[
                {"id": "first", "kind": "loft", "spec": first},
                {"id": "last", "kind": "loft", "spec": last},
            ],
            families=[
                {
                    "id": "series",
                    "template": "first",
                    "count": 15,
                    "path": [[0, 0, 0], [35, 0, 0]],
                    "morphs": [{"position": 1, "template": "last"}],
                }
            ],
        )
        self.assertTrue(result["valid"])
        meshes = [bpy.data.objects[f"MorphLofts.series.{i:02}"].data for i in range(15)]
        self.assertEqual(
            len({tuple(tuple(p.vertices) for p in mesh.polygons) for mesh in meshes}), 1
        )
        self.assertEqual(len({surfaces.content_hash(mesh) for mesh in meshes}), 15)

    def test_failed_batch_and_voxel_preflight_preserve_scene(self) -> None:
        broken = simple("TooDense")
        broken["voxel_size"] = 0.00001
        counts = len(bpy.data.objects), len(bpy.data.meshes)
        result = response("form.create", forms=[simple("Valid"), broken])
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "form_limit")
        self.assertEqual(counts, (len(bpy.data.objects), len(bpy.data.meshes)))

    def test_calibrated_section_masks_and_staleness(self) -> None:
        root = Path(os.environ["TYVRANA_TEST_OUTPUT"])
        sources = []
        for index, z in enumerate((-0.5, 0.0, 0.5)):
            name = f"Section{index}"
            y, x = np.mgrid[:128, :128]
            outer = ((x - 64) / (48 - 5 * index)) ** 2 + (
                (y - 64) / (40 + 3 * index)
            ) ** 2 < 1
            opening = ((x - 55 - 3 * index) / 12) ** 2 + ((y - 64) / 17) ** 2 < 1
            pixels = np.ones((128, 128, 4), dtype=np.float32)
            pixels[:, :, 3] = outer & ~opening
            image = bpy.data.images.new(name, width=128, height=128, alpha=True)
            image.pixels.foreach_set(pixels.reshape(-1))
            image.filepath_raw = str(root / (name + ".png"))
            image.file_format = "PNG"
            image.save()
            image.pack()
            call(
                "reference.create",
                references=[{"name": name, "image": image.name, "hidden": True}],
            )
            call(
                "reference.register",
                registrations=[
                    {
                        "reference": name,
                        "projection": "plane",
                        "calibration": {"a": [0, 0], "b": [128, 0], "distance": 2.56},
                        "origin_pixel": [64, 64],
                        "horizontal": "x",
                        "vertical": "y",
                        "origin": [0, 0, z],
                    }
                ],
            )
            sources.append({"reference": name})
        # Excluded reference transforms must survive a native save/reopen.
        saved = root / "hidden-references.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False)
        invalid = response(
            "form.create",
            forms=[
                {
                    "name": "InvalidMask",
                    "voxel_size": 0.04,
                    "parts": [
                        {
                            "id": "sections",
                            "kind": "sections",
                            "masks": [dict(m, channel="luminance") for m in sources],
                        }
                    ],
                }
            ],
        )
        self.assertIsInstance(invalid, OperationFailure)
        self.assertEqual(invalid.error.code, "reference_mask_invalid")
        self.assertEqual(invalid.error.details["alpha_range"], [0, 1])
        self.assertEqual(invalid.error.details["foreground_pixels"], 128 * 128)
        value = call(
            "form.create",
            forms=[
                {
                    "name": "SectionForm",
                    "voxel_size": 0.025,
                    "parts": [{"id": "evidence", "kind": "sections", "masks": sources}],
                }
            ],
        )["forms"][0]
        self.assertTrue(value["valid"])
        self.assertEqual(value["source_count"], 3)
        stats = surfaces.native_checks(bpy.data.objects["SectionForm"].data)
        self.assertEqual(stats["boundary_edges"], 0)
        self.assertEqual(stats["non_manifold_edges"], 0)
        self.assertEqual(stats["components"], 1)
        (root / "section-form.json").write_text(
            json.dumps({"summary": value, "geometry": stats}, indent=2)
        )
        comparison = {
            "id": "middle",
            "objects": ["SectionForm"],
            "mask": {"reference": "Section1"},
        }
        matched = call(
            "reference.compare", comparisons=[comparison], resolution=128, overlay=False
        )["comparisons"][0]
        self.assertGreater(matched["overlap_iou"], 0.96)
        self.assertLess(matched["boundary_p95"], 0.04)
        self.assertLess(matched["boundary_max"], 0.041)
        call("object.set_transform", name="SectionForm", location=[0.25, 0, 0])
        changed = call(
            "reference.compare", comparisons=[comparison], resolution=128, overlay=False
        )["comparisons"][0]
        self.assertLess(changed["overlap_iou"], 0.8)
        self.assertGreater(changed["boundary_p95"], 0.15)
        (root / "reference-comparison.json").write_text(
            json.dumps({"matched": matched, "shifted": changed}, indent=2)
        )
        transform = bpy.data.objects["Section0"].matrix_world.copy()
        transform.translation.x += 0.1
        bpy.data.objects["Section0"].matrix_world = transform
        bpy.context.view_layer.update()
        result = call("form.inspect", names=["SectionForm"])["forms"][0]
        self.assertFalse(result["valid"])
        rejected = response(
            "form.configure",
            forms=[{"name": "SectionForm", "expected_revision": 1, "voxel_size": 0.03}],
        )
        self.assertIsInstance(rejected, OperationFailure)
        self.assertEqual(rejected.error.code, "reference_stale")


def run() -> None:
    adapter.unregister()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(FormChecks)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Native form checks failed")
    print("FORM_NATIVE_PASSED", result.testsRun, flush=True)


if __name__ == "__main__":
    run()
