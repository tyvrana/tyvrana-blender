"""Native PNG16, linear HDR EXR, passes/AOVs and restoration."""

import importlib
import struct
import sys
import unittest
from pathlib import Path

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.scene import prepare_scene  # noqa: E402
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

renderer = importlib.import_module(PACKAGE + "render")
output = importlib.import_module(PACKAGE + "render_output")
models = importlib.import_module(PACKAGE + "models")
artifacts = importlib.import_module(PACKAGE + "artifacts")


class OutputTests(TopologyTests):
    def test_production_formats_passes_hdr_and_restoration(self) -> None:
        prepare_scene()
        scene = bpy.context.scene
        material = bpy.data.materials["RenderRed"]
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Emission Color"].default_value = (1, 0.2, 0.1, 1)
        shader.inputs["Emission Strength"].default_value = 20
        self.call(
            "shader.node.create",
            material_name="RenderRed",
            node_type="output_aov",
            name="DiagnosticOutput",
            aov_name="DiagnosticMask",
            aov_value=0.75,
        )
        settings = (
            scene.render.resolution_x,
            scene.render.image_settings.file_format,
            scene.render.image_settings.color_depth,
            scene.view_settings.view_transform,
            bpy.context.view_layer.use_pass_normal,
        )
        spool = artifacts.ArtifactSpool()
        try:
            for fmt, depth in [
                ("png", 16),
                ("exr", 16),
                ("exr", 32),
                ("exr_multilayer", 16),
            ]:
                multi = fmt == "exr_multilayer"
                args = models.RenderArguments.model_validate(
                    dict(
                        width=1100 if fmt == "png" else 64,
                        height=64,
                        format=fmt,
                        bit_depth=depth,
                        cycles=dict(samples=1),
                        passes=["z", "normal"] if multi else [],
                        aovs=[dict(name="DiagnosticMask", type="VALUE")]
                        if multi
                        else [],
                    )
                )
                result, descriptor = renderer.render_image(args, spool)
                path = artifacts.artifact_path(spool.root, descriptor)
                if fmt == "png":
                    header = path.read_bytes()[:29]
                    self.assertEqual(struct.unpack("!II", header[16:24]), (1100, 64))
                    self.assertEqual(header[24], 16)
                else:
                    width, height, channels = output.exr_header(path)
                    self.assertEqual((width, height), (64, 64))
                    self.assertTrue(channels)
                    self.assertEqual(
                        result.color_management.output_encoding, "scene_linear"
                    )
                    if multi:
                        self.assertTrue(
                            any(".DiagnosticMask." in c for c in channels), channels
                        )
                        self.assertTrue(any(".Normal." in c for c in channels))
                    else:
                        self.assertEqual(
                            set(channels.values()), {1 if depth == 16 else 2}
                        )
                        image = bpy.data.images.load(str(path), check_existing=False)
                        try:
                            self.assertTrue(image.is_float)
                            self.assertGreater(max(image.pixels), 2)
                        finally:
                            bpy.data.images.remove(image)
                spool.release((descriptor,))
                self.assertEqual(
                    settings,
                    (
                        scene.render.resolution_x,
                        scene.render.image_settings.file_format,
                        scene.render.image_settings.color_depth,
                        scene.view_settings.view_transform,
                        bpy.context.view_layer.use_pass_normal,
                    ),
                )
                self.assertEqual(len(bpy.context.view_layer.aovs), 0)
        finally:
            spool.close()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [OutputTests("test_production_formats_passes_hdr_and_restoration")]
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("RENDER_OUTPUT_NATIVE_PASSED")
