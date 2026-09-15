"""Native selected-to-active data transfer, preservation and failure cleanup."""

import importlib
import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

PACKAGE = "bl_ext.user_default.tyvrana_blender"
operations = importlib.import_module(PACKAGE + ".operations")
adapter = importlib.import_module(PACKAGE + ".blender")
bake = importlib.import_module(PACKAGE + ".bake")
models = importlib.import_module(PACKAGE + ".bake_models")
artifacts = importlib.import_module(PACKAGE + ".artifacts")
render_models = importlib.import_module(PACKAGE + ".models")
checker = importlib.import_module(PACKAGE + ".uv_checker")


class BakeTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for image in list(bpy.data.images):
            if image.source != "VIEWER":
                bpy.data.images.remove(image)
        data = bpy.data.meshes.new("Low mesh")
        data.from_pydata(
            [(0, -1, 0), (1, -1, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)]
        )
        self.low = bpy.data.objects.new("Low", data)
        bpy.context.scene.collection.objects.link(self.low)
        uv = data.uv_layers.new(name="ProductionUV")
        for p, co in zip(
            uv.uv, [(0.05, 0.05), (0.45, 0.05), (0.45, 0.95), (0.05, 0.95)], strict=True
        ):
            p.vector = co
        mod = self.low.modifiers.new("Bilateral", "MIRROR")
        mod.use_mirror_u = True
        source = bpy.data.meshes.new("High mesh")
        source.from_pydata(
            [(-2, -2, -0.1), (2, -2, -0.1), (2, 2, 0.3), (-2, 2, 0.3)],
            [],
            [(0, 1, 2, 3)],
        )
        self.high = bpy.data.objects.new("High", source)
        bpy.context.scene.collection.objects.link(self.high)
        self.low.hide_set(True)
        bpy.context.view_layer.objects.active = self.high
        self.high.select_set(True)
        self.spool = artifacts.ArtifactSpool()
        self.backend = adapter.BlenderBackend(self.spool)
        self.count = 0
        self.target = dict(
            target="Low",
            sources=["High"],
            uv_map="ProductionUV",
            cage_extrusion=0.4,
            max_ray_distance=0.8,
        )
        bpy.context.view_layer.update()

    def tearDown(self) -> None:
        self.spool.close()

    def call(self, op: str, **args: Any) -> Any:
        self.count += 1
        if op == "bake.image":
            # Isolated background fixtures drive the same generator synchronously.
            value = bake.bake_image(models.BakeImageArguments.model_validate(args))
            return OperationSuccess(
                type="operation.success",
                request_id=str(self.count),
                result=value.model_dump(mode="json"),
            )
        result = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="bake-test-" + str(self.count),
                operation="blender." + op,
                arguments=args,
            ),
        )
        self.assertIsInstance(result, OperationSuccess, str(result))
        return result

    def state(self) -> str:
        return json.dumps(
            {
                "objects": sorted(
                    (
                        o.name,
                        o.data.name,
                        o.hide_get(),
                        o.select_get(),
                        list(o.matrix_world[0]),
                    )
                    for o in bpy.context.scene.objects
                ),
                "active": bpy.context.view_layer.objects.active.name,
                "engine": bpy.context.scene.render.engine,
                "meshes": len(bpy.data.meshes),
                "scenes": len(bpy.data.scenes),
                "materials": len(bpy.data.materials),
                "uv": bake.uv_hash(self.low.data, "ProductionUV"),
                "seam": bake.seam_hash(self.low.data),
                "modifiers": len(self.low.modifiers),
            },
            sort_keys=True,
        )

    def test_normal_transfer_mirror_precision_and_preservation(self) -> None:
        before = self.state()
        result = self.call("bake.inspect", targets=[self.target])
        report = result.result["targets"][0]
        self.assertEqual(report["ray_misses"], 0)
        result = self.call(
            "bake.image",
            targets=[self.target],
            name="Normal",
            resolution=64,
            margin=1,
            device="cpu",
        )
        qa = result.result["qa"]
        print("NATIVE_BAKE_QA", json.dumps(result.result))
        self.assertEqual(qa["invalid_normal_texels"], 0)
        self.assertEqual(qa["uncovered_alpha_texels"], 0)
        self.assertEqual(self.state(), before)
        image = bpy.data.images["Normal"]
        values = bake.image_pixels(image)
        self.assertLess(float(values[32, 16, 1]), 0.48)
        self.assertLess(float(values[32, 48, 1]), 0.48)
        self.assertGreater(float(values[32, 16, 2]), 0.99)
        dest = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "normal.png"
        out = self.call("image.save", name="Normal", filepath=str(dest), overwrite=True)
        self.assertEqual(dest.read_bytes()[24], 16)
        self.assertLess(out.result["maximum_roundtrip_error"], 2 / 65535)
        self.assertTrue(image.packed_file)
        self.assertEqual(len(out.artifacts), 1)
        self.spool.release(out.artifacts)
        self.assertEqual(self.state(), before)

    def test_native_bake_failure_cleans_every_temporary_resource(self) -> None:
        before = self.state()
        count = len(bpy.data.images)
        with patch.object(
            bake, "execute_native", side_effect=RuntimeError("injected bake failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                bake.bake_image(
                    models.BakeImageArguments(
                        targets=[self.target],
                        name="Failed",
                        resolution=64,
                        margin=1,
                        device="cpu",
                    )
                )
        self.assertEqual(self.state(), before)
        self.assertEqual(len(bpy.data.images), count)

    def test_ray_miss_is_reported(self) -> None:
        t = {**self.target, "cage_extrusion": 0.001, "max_ray_distance": 0.002}
        result = self.call("bake.inspect", targets=[t])
        self.assertGreater(result.result["targets"][0]["ray_misses"], 0)

    def test_evaluation_mismatch_is_blocked(self) -> None:
        self.low.modifiers[0].show_render = False
        with self.assertRaises(operations.OperationError):
            bake.inspect(models.BakeInspectArguments(targets=[self.target]))

    def test_export_does_not_overwrite_without_explicit_flag(self) -> None:
        image = bpy.data.images.new("Data", width=64, height=64, float_buffer=True)
        image.colorspace_settings.name = "Non-Color"
        dest = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "existing.png"
        dest.write_bytes(b"original")
        output = importlib.import_module(PACKAGE + ".image_output")
        with self.assertRaises(operations.OperationError):
            output.save(
                models.ImageSaveArguments(name="Data", filepath=str(dest)), self.spool
            )
        self.assertEqual(dest.read_bytes(), b"original")

    def test_remove_unused_image_protects_references(self) -> None:
        image = bpy.data.images.new("Discarded", width=64, height=64)
        image.use_fake_user = True
        material = bpy.data.materials.new("Image user")
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        with self.assertRaises(operations.OperationError):
            self.backend.image_remove(render_models.DeleteArguments(name=image.name))
        self.assertIs(node.image, image)
        node.image = None
        self.call("image.remove", name=image.name)
        self.assertIsNone(bpy.data.images.get("Discarded"))
        bpy.data.materials.remove(material)

    def test_surface_diagnostic_restores_on_failure(self) -> None:
        before = self.state()
        with self.assertRaisesRegex(RuntimeError, "diagnostic"):
            with checker.display(
                render_models.SurfaceRenderOptions(
                    objects=["Low"], exclude_objects=["High"]
                )
            ):
                self.assertTrue(self.high.hide_render)
                raise RuntimeError("diagnostic")
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BakeTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("BLENDER_BAKE_TESTS_PASSED", result.testsRun)
