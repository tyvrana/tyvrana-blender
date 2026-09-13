"""Native sculpt-region contracts, geometric effects and state preservation."""

import math
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.sculpt_checks import (  # noqa: E402
    NativeCase,
    adapter,
    authored,
    coordinates,
    models,
    sculpt,
)


def rms(before: list[Any], after: list[Any], indices: list[int]) -> float:
    return math.sqrt(
        sum((after[i] - before[i]).length_squared for i in indices) / len(indices)
    )


def radius_variance(points: list[Any]) -> float:
    radii = [p.length for p in points]
    mean = sum(radii) / len(radii)
    return float(sum((r - mean) ** 2 for r in radii) / len(radii))


class RegionsCase(NativeCase):
    def mask(self, **args: Any) -> Any:
        return self.call(
            "sculpt.mask.stroke",
            **{
                "mode": "add",
                "samples": [{"location": [0, 0, 1]}] * 16,
                "radius": 0.65,
                "strength": 1.0,
                **args,
            },
        )

    def base_mask(self) -> list[float]:
        attr = self.obj.data.attributes.get(".sculpt_mask")
        return (
            [v.value for v in attr.data]
            if attr
            else [0.0] * len(self.obj.data.vertices)
        )

    def filter(self, kind: str = "scale", **args: Any) -> Any:
        return self.call("sculpt.filter", **{"type": kind, "strength": 0.1, **args})

    def noisy(self) -> None:
        self.sphere()
        for v in self.obj.data.vertices:
            v.co *= 1 + 0.06 * math.sin(19 * v.co.x) * math.cos(21 * v.co.y) * math.sin(
                17 * v.co.z
            )
        self.obj.data.update()


class FaceSetTests(RegionsCase):
    def test_default_assignment_reassignment_and_bounds(self) -> None:
        initial = self.call("sculpt.face_sets.inspect")
        self.assertFalse(initial["authored"])
        self.assertEqual(initial["face_sets"][0]["id"], 1)
        self.assertEqual(initial["face_sets"][0]["face_count"], 6)
        before = authored(self.obj)
        first = self.call(
            "sculpt.face_sets.assign",
            selector={"mode": "indices", "domain": "face", "indices": [0, 1]},
        )
        self.assertEqual((first["assigned_id"], first["assigned_face_count"]), (2, 2))
        self.call(
            "sculpt.face_sets.assign",
            face_set_id=7,
            selector={
                "mode": "normal",
                "domain": "face",
                "direction": [0, 0, 1],
                "min_dot": 0.9,
            },
        )
        final = self.call(
            "sculpt.face_sets.assign",
            face_set_id=2,
            selector={"mode": "indices", "domain": "face", "indices": [2]},
        )
        self.assertEqual(sum(s["face_count"] for s in final["summary"]["face_sets"]), 6)
        self.assertEqual(authored(self.obj), before)
        self.assertEqual(
            [s["id"] for s in final["summary"]["face_sets"]],
            sorted(s["id"] for s in final["summary"]["face_sets"]),
        )

    def test_shared_mesh_isolation_and_multires_ownership(self) -> None:
        sibling = self.obj.copy()
        bpy.context.scene.collection.objects.link(sibling)
        original = self.obj.data
        result = self.call(
            "sculpt.face_sets.assign", selector={"mode": "all", "domain": "face"}
        )
        self.assertTrue(result["mesh_isolated"])
        self.assertEqual(sibling.data, original)
        self.assertIsNone(sibling.data.attributes.get(".sculpt_face_set"))
        self.add_multires(3)
        for level in (1, 3, 0, 2):
            self.call("multires.configure", viewport_level=level, sculpt_level=level)
            self.assertEqual(
                self.call("sculpt.face_sets.inspect")["face_sets"][0]["id"], 2
            )
        self.error(
            "mesh.subdivide_edges",
            "invalid_context",
            selector={"mode": "all", "domain": "edge"},
            cuts=1,
        )

    def test_initialization_modes_and_hidden_preservation(self) -> None:
        self.obj.data.polygons[0].material_index = 1
        self.obj.data.polygons[1].hide = True
        self.call(
            "sculpt.face_sets.assign",
            face_set_id=17,
            selector={"mode": "indices", "domain": "face", "indices": [1]},
        )
        for mode in ("materials", "loose_parts", "uv_seams", "sharp_edges"):
            if mode == "uv_seams":
                for edge in self.obj.data.edges:
                    edge.use_seam = True
            if mode == "sharp_edges":
                for edge in self.obj.data.edges:
                    edge.use_edge_sharp = True
            result = self.call("sculpt.face_sets.initialize", mode=mode)
            self.assertEqual(
                self.obj.data.attributes[".sculpt_face_set"].data[1].value, 17
            )
            self.assertTrue(self.obj.data.polygons[1].hide)
            self.assertEqual(sum(s["face_count"] for s in result["face_sets"]), 6)
            if mode in {"uv_seams", "sharp_edges"}:
                self.assertEqual(len(result["face_sets"]), 6)

    def test_invalid_and_readonly_state(self) -> None:
        before = authored(self.obj)
        for identity in (-1, 0, True, 2**31):
            self.error(
                "sculpt.face_sets.assign",
                "invalid_arguments",
                face_set_id=identity,
                selector={"mode": "all", "domain": "face"},
            )
        self.error(
            "sculpt.face_sets.assign",
            "invalid_arguments",
            selector={"mode": "indices", "domain": "face", "indices": [100]},
        )
        self.assertEqual(authored(self.obj), before)
        self.assertIsNone(self.obj.data.attributes.get(".sculpt_face_set"))
        self.obj.animation_data_create()
        self.error(
            "sculpt.face_sets.assign",
            "invalid_context",
            selector={"mode": "all", "domain": "face"},
        )

    def test_linked_mesh_rejects_face_set_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tyvrana-region-library-") as directory:
            path = str(Path(directory) / "source.blend")
            name = self.obj.data.name
            bpy.data.libraries.write(path, {self.obj.data})
            with bpy.data.libraries.load(path, link=True) as (_, loaded):
                loaded.meshes = [name]
            linked = loaded.meshes[0]
            self.obj.data = linked
            self.assertIsNotNone(linked.library)
            self.call("sculpt.face_sets.inspect")
            self.error(
                "sculpt.face_sets.assign",
                "invalid_context",
                selector={"mode": "all", "domain": "face"},
            )
            self.assertIsNone(linked.attributes.get(".sculpt_face_set"))

    def test_background_masks_and_filters_reject(self) -> None:
        if not bpy.app.background:
            self.skipTest("Background-only guard")
        for operation in ("sculpt.mask.clear", "sculpt.mask.invert"):
            self.error(operation, "invalid_context")
        self.error("sculpt.filter", "invalid_context", type="smooth", strength=0.5)


class MaskTests(RegionsCase):
    def test_base_add_subtract_invert_clear_and_summary(self) -> None:
        self.sphere()
        initial = self.call("sculpt.mask.inspect")
        self.assertFalse(initial["base_mesh"]["present"])
        self.assertEqual(initial["base_mesh"]["unmasked_fraction"], 1)
        added = self.mask()["mask"]["base_mesh"]
        self.assertGreater(added["mean"], 0)
        self.assertEqual(added["max"], 1)
        self.assertGreater(added["fully_masked_fraction"], 0)
        subtracted = self.mask(mode="subtract")["mask"]["base_mesh"]
        self.assertLess(subtracted["mean"], added["mean"])
        before = self.base_mask()
        inverted = self.call("sculpt.mask.invert")
        self.assertAlmostEqual(
            inverted["base_mesh"]["mean"], 1 - subtracted["mean"], places=6
        )
        self.assertLess(
            max(
                abs(a - (1 - b)) for a, b in zip(self.base_mask(), before, strict=True)
            ),
            1e-7,
        )
        cleared = self.call("sculpt.mask.clear")
        self.assertEqual(cleared["base_mesh"]["max"], 0)
        self.assertEqual(self.call("sculpt.mask.clear"), cleared)

    def test_radius_pressure_symmetry_and_preserving_existing_mask(self) -> None:
        self.sphere()
        means = []
        for radius, pressure in ((0.3, 1), (0.7, 1), (0.7, 0.25)):
            self.call("sculpt.mask.clear")
            self.mask(
                radius=radius, samples=[{"location": [0, 0, 1], "pressure": pressure}]
            )
            means.append(sum(self.base_mask()))
        self.assertGreater(means[1], means[0])
        self.assertLess(means[2], means[1])
        self.call("sculpt.mask.clear")
        self.mask(samples=[{"location": [0.6, 0, 0.8]}] * 8, radius=0.3)
        asymmetric = self.base_mask()
        self.call("sculpt.mask.clear")
        self.mask(
            samples=[{"location": [0.6, 0, 0.8]}] * 8, radius=0.3, symmetry={"x": True}
        )
        symmetric = self.base_mask()
        left = [
            v.index for v in self.obj.data.vertices if v.co.x < -0.3 and v.co.z > 0.5
        ]
        self.assertEqual(sum(asymmetric[i] for i in left), 0)
        self.assertGreater(sum(symmetric[i] for i in left), 1)
        self.mask(strength=0)
        self.assertEqual(self.base_mask(), symmetric)

    def test_multires_grid_mask_survives_levels_and_brushes(self) -> None:
        self.sphere()
        self.add_multires(2)
        before_authored = authored(self.obj)
        self.mask()
        self.assertFalse(self.call("sculpt.mask.inspect")["effective_values_available"])
        self.call("multires.configure", viewport_level=1, sculpt_level=1)
        self.call("multires.configure", viewport_level=2, sculpt_level=2)
        self.mask(strength=0)
        before = coordinates(self.obj)
        top = [
            i
            for i, p in enumerate(before)
            if p.z > 0.95 and abs(p.x) < 0.05 and abs(p.y) < 0.05
        ]
        self.call(
            "sculpt.stroke",
            brush="draw",
            samples=[{"location": [0, 0, 1]}] * 8,
            radius=0.3,
            strength=1,
        )
        after = coordinates(self.obj)
        self.assertLess(rms(before, after, top), 2e-6)
        self.call("sculpt.mask.clear")
        self.call(
            "sculpt.stroke",
            brush="draw",
            samples=[{"location": [0, 0, 1]}] * 8,
            radius=0.3,
            strength=1,
        )
        self.assertGreater(rms(after, coordinates(self.obj), top), 0.001)
        self.assertEqual(authored(self.obj), before_authored)

    def test_multires_invert_and_clear_filter_respect(self) -> None:
        self.sphere()
        self.add_multires(2)
        self.mask()
        self.call("sculpt.mask.invert")
        before = coordinates(self.obj)
        good = [i for i, p in enumerate(before) if p.z < 0]
        bad = [i for i, p in enumerate(before) if p.z > 0.95]
        self.filter()
        after = coordinates(self.obj)
        self.assertLess(rms(before, after, good), 2e-6)
        self.assertGreater(rms(before, after, bad), 0.03)
        self.call("sculpt.mask.clear")
        self.filter()
        self.assertGreater(rms(after, coordinates(self.obj), good), 0.03)

    def test_hidden_geometry_and_partial_mask(self) -> None:
        self.sphere()
        mask = self.obj.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        for v, m in zip(self.obj.data.vertices, mask.data, strict=True):
            m.value = 0.5 if v.co.z > 0 else 0
            v.hide = v.co.x < -0.7
        before_masks = self.base_mask()
        self.call("sculpt.mask.invert")
        hidden = [v.index for v in self.obj.data.vertices if v.hide]
        self.assertEqual(
            [self.base_mask()[i] for i in hidden], [before_masks[i] for i in hidden]
        )
        self.call("sculpt.mask.clear")
        self.assertEqual(
            [self.base_mask()[i] for i in hidden], [before_masks[i] for i in hidden]
        )
        for v, m in zip(
            self.obj.data.vertices,
            self.obj.data.attributes[".sculpt_mask"].data,
            strict=True,
        ):
            m.value = 0.5 if v.co.z > 0 else 0
        self.obj.data.update()
        before = coordinates(self.obj)
        self.filter()
        after = coordinates(self.obj)
        self.assertLess(rms(before, after, hidden), 1e-7)
        for i, p in enumerate(before):
            if i not in hidden:
                expected = p * (1.05 if p.z > 0 else 1.1)
                self.assertLess((after[i] - expected).length, 3e-7)

    def test_partial_masks_reduce_existing_sculpt_stroke(self) -> None:
        effects = []
        for value in (0.0, 0.5, 1.0):
            self.setUp()
            self.sphere()
            attr = self.obj.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
            attr.data.foreach_set("value", [value] * len(attr.data))
            self.obj.data.update()
            before = coordinates(self.obj)
            self.call(
                "sculpt.stroke",
                brush="draw",
                samples=[{"location": [0, 0, 1]}] * 4,
                radius=0.35,
                strength=0.5,
            )
            effects.append(
                max(p.z for p in coordinates(self.obj)) - max(p.z for p in before)
            )
        self.assertGreater(effects[0], 0.01)
        self.assertGreater(effects[1], 0.001)
        self.assertLess(effects[1], effects[0] * 0.8)
        self.assertLess(abs(effects[2]), 1e-7)

    def test_native_hidden_multires_points_and_masks_survive(self) -> None:
        outputs = []
        for implementation in ("adapter", "native"):
            self.setUp()
            self.sphere()
            self.add_multires(2)
            self.mask()
            bpy.ops.object.mode_set(mode="SCULPT")
            area, region, _ = sculpt.view_context()
            with bpy.context.temp_override(area=area, region=region):
                self.assertIn("FINISHED", bpy.ops.paint.hide_show_masked(action="HIDE"))
            bpy.ops.object.mode_set(mode="OBJECT")
            before = coordinates(self.obj)
            top = [
                i
                for i, p in enumerate(before)
                if p.z > 0.95 and abs(p.x) < 0.05 and abs(p.y) < 0.05
            ]
            hidden = (
                [v.hide for v in self.obj.data.vertices],
                [f.hide for f in self.obj.data.polygons],
            )
            self.assertTrue(any(hidden[0]))
            for action in ("clear", "invert", "filter"):
                if implementation == "adapter":
                    if action == "filter":
                        self.filter()
                    else:
                        self.call("sculpt.mask." + action)
                else:
                    bpy.ops.object.mode_set(mode="SCULPT")
                    with bpy.context.temp_override(area=area, region=region):
                        if action == "filter":
                            bpy.ops.sculpt.mesh_filter(
                                type="SCALE", strength=0.1, iteration_count=1
                            )
                        else:
                            bpy.ops.paint.mask_flood_fill(
                                mode="VALUE" if action == "clear" else "INVERT", value=0
                            )
                    bpy.ops.object.mode_set(mode="OBJECT")
                after = coordinates(self.obj)
                self.assertEqual(
                    hidden,
                    (
                        [v.hide for v in self.obj.data.vertices],
                        [f.hide for f in self.obj.data.polygons],
                    ),
                )
                self.assertLess(rms(before, after, top), 2e-6)
                if action != "filter":
                    self.assertLess(rms(before, after, list(range(len(before)))), 2e-6)
            outputs.append(coordinates(self.obj))
            if implementation == "adapter":
                bpy.ops.object.mode_set(mode="SCULPT")
                with bpy.context.temp_override(area=area, region=region):
                    bpy.ops.paint.hide_show_all(action="SHOW")
                bpy.ops.object.mode_set(mode="OBJECT")
                before = coordinates(self.obj)
                self.filter()
                self.assertLess(rms(before, coordinates(self.obj), top), 2e-6)
                self.call("sculpt.mask.clear")
                before = coordinates(self.obj)
                self.filter()
                self.assertGreater(rms(before, coordinates(self.obj), top), 0.03)
        # Grid synchronization can affect the hide boundary: compare it to the
        # actual native workflow rather than assuming all points are locked.
        self.assertLess(rms(outputs[0], outputs[1], list(range(len(outputs[0])))), 1e-7)

    def test_malformed_native_mask_rejects_before_execution(self) -> None:
        self.sphere()
        attr = self.obj.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        attr.data[0].value = 2.0
        before = authored(self.obj)
        self.error("sculpt.filter", "invalid_context", type="smooth", strength=0.5)
        self.error("sculpt.mask.clear", "invalid_context")
        self.assertEqual(before, authored(self.obj))
        self.assertEqual(bpy.context.mode, "OBJECT")

    def test_state_restore_failure_and_worker_guard(self) -> None:
        self.sphere()
        area, _, rv = sculpt.view_context()
        overlay = area.spaces.active.overlay.show_sculpt_mask
        brush = bpy.context.tool_settings.sculpt.brush
        state = (
            bpy.context.scene,
            bpy.context.view_layer.objects.active,
            tuple(rv.view_rotation),
            tuple(rv.view_location),
            rv.view_distance,
            sculpt.symmetry(self.obj),
        )
        brushes = set(b.as_pointer() for b in bpy.data.brushes)
        for op, args in (
            (
                "sculpt.mask.stroke",
                dict(
                    mode="add",
                    samples=[{"location": [0, 0, 1]}],
                    radius=0.5,
                    strength=1,
                ),
            ),
            ("sculpt.mask.invert", {}),
            ("sculpt.mask.clear", {}),
            ("sculpt.filter", dict(type="smooth", strength=0.2)),
        ):
            self.call(op, **args)
            self.assertEqual(
                state,
                (
                    bpy.context.scene,
                    bpy.context.view_layer.objects.active,
                    tuple(rv.view_rotation),
                    tuple(rv.view_location),
                    rv.view_distance,
                    sculpt.symmetry(self.obj),
                ),
            )
            self.assertEqual(bpy.context.tool_settings.sculpt.brush, brush)
            self.assertEqual(set(b.as_pointer() for b in bpy.data.brushes), brushes)
            self.assertEqual(area.spaces.active.overlay.show_sculpt_mask, overlay)
        original = sculpt.surface
        calls = 0

        def fail_after(obj: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Post-execution test failure")
            return original(obj)

        with (
            patch.object(sculpt, "surface", side_effect=fail_after),
            patch.object(sculpt.logger, "exception"),
        ):
            result = self.error(
                "sculpt.filter", "sculpt_failed", type="inflate", strength=0.1
            )
        self.assertTrue(result.error.details["mutation_possible"])
        self.assertEqual(bpy.context.mode, "OBJECT")
        with ThreadPoolExecutor(max_workers=1) as executor:
            for name in ("sculpt_mask_inspect", "sculpt_face_sets_inspect"):
                method = getattr(self.backend, name)
                argument = (
                    models.MaskInspectArguments(object_name=self.obj.name)
                    if "mask" in name
                    else models.FaceSetsInspectArguments(object_name=self.obj.name)
                )
                with self.assertRaises(RuntimeError):
                    executor.submit(method, argument).result()


class FilterTests(RegionsCase):
    def test_smooth_and_surface_smooth_geometry(self) -> None:
        metrics = {}
        for kind in ("smooth", "surface_smooth"):
            self.setUp()
            self.noisy()
            before = coordinates(self.obj)
            self.filter(kind, strength=0.5, iterations=12)
            after = coordinates(self.obj)
            self.assertLess(radius_variance(after), radius_variance(before) * 0.8)
            metrics[kind] = abs(
                sum(p.length for p in after) / len(after)
                - sum(p.length for p in before) / len(before)
            )
        self.assertLess(metrics["surface_smooth"], metrics["smooth"])

    def test_inflate_and_scale_native_geometry(self) -> None:
        self.sphere()
        before = coordinates(self.obj)
        self.filter("inflate", strength=0.1)
        after = coordinates(self.obj)
        self.assertGreater(sum(p.length for p in after), sum(p.length for p in before))
        self.filter("scale", strength=0.2)
        scaled = coordinates(self.obj)
        self.assertLess(
            max((p * 1.2 - q).length for p, q in zip(after, scaled, strict=True)), 5e-7
        )

    def test_relax_distribution(self) -> None:
        bpy.data.objects.remove(self.obj, do_unlink=True)
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=17, y_subdivisions=17, size=2)
        self.obj = bpy.context.object
        for v in self.obj.data.vertices:
            if abs(v.co.x) < 0.95 and abs(v.co.y) < 0.95:
                v.co.x += 0.04 * math.sin(v.index * 2.1)
                v.co.y += 0.04 * math.cos(v.index * 1.7)
        self.obj.data.update()

        def spread() -> float:
            lengths = [
                (
                    self.obj.data.vertices[e.vertices[0]].co
                    - self.obj.data.vertices[e.vertices[1]].co
                ).length
                for e in self.obj.data.edges
            ]
            mean = sum(lengths) / len(lengths)
            return float(sum((v - mean) ** 2 for v in lengths) / len(lengths))

        before = spread()
        self.filter("relax", strength=0.5, iterations=12)
        self.assertLess(spread(), before)

    def test_axis_and_world_orientation(self) -> None:
        self.sphere()
        self.obj.rotation_euler.z = math.pi / 2
        bpy.context.view_layer.update()
        before = coordinates(self.obj)
        self.filter(axes={"x": True, "y": False, "z": False}, orientation="world")
        after = coordinates(self.obj)
        self.assertLess(
            max(abs(a.x - b.x) for a, b in zip(before, after, strict=True)), 1e-6
        )
        self.assertGreater(
            max(abs(a.y - b.y) for a, b in zip(before, after, strict=True)), 0.05
        )
        self.assertLess(
            max(abs(a.z - b.z) for a, b in zip(before, after, strict=True)), 1e-6
        )

    def test_masked_multires_regional_smoothing_preserves_face_sets(self) -> None:
        self.noisy()
        self.add_multires(2)
        self.call(
            "sculpt.face_sets.assign",
            face_set_id=9,
            selector={
                "mode": "normal",
                "domain": "face",
                "direction": [0, 0, 1],
                "min_dot": 0,
            },
        )
        sets = self.call("sculpt.face_sets.inspect")
        self.mask(radius=1.3)
        self.call("sculpt.mask.invert")
        before = coordinates(self.obj)
        base = authored(self.obj)
        bad = [i for i, p in enumerate(before) if p.z > 0.8]
        good = [i for i, p in enumerate(before) if p.z < -0.1]
        self.filter("smooth", strength=0.7, iterations=30)
        after = coordinates(self.obj)
        self.assertLess(
            radius_variance([after[i] for i in bad]),
            radius_variance([before[i] for i in bad]) * 0.99,
        )
        self.assertLess(rms(before, after, good), 2e-6)
        self.assertEqual(authored(self.obj), base)
        self.assertEqual(self.call("sculpt.face_sets.inspect"), sets)
        # Copying native Mesh data for Face Set writes must preserve grid masks.
        self.call(
            "sculpt.face_sets.assign",
            face_set_id=12,
            selector={"mode": "indices", "domain": "face", "indices": [0]},
        )
        before = coordinates(self.obj)
        self.filter()
        after = coordinates(self.obj)
        self.assertLess(rms(before, after, good), 2e-6)
        self.assertGreater(rms(before, after, bad), 0.01)


def run() -> None:
    runtime = adapter._runtime
    worker = runtime.worker if runtime else None
    adapter.unregister()
    if worker:
        assert worker.process.returncode == 0
        assert not worker.spool.root.exists()
    classes = [FaceSetTests] if bpy.app.background else [MaskTests, FilterTests]
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert not bpy.app.timers.is_registered(adapter.pump)
    if result.wasSuccessful():
        print("BLENDER_REGIONAL_TESTS_PASSED", result.testsRun, flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    elif not result.wasSuccessful():
        raise RuntimeError("Regional sculpt checks failed")


if bpy.app.background:
    run()
else:
    bpy.app.timers.register(run, first_interval=3)
