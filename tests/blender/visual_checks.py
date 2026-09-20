"""Native regional diagnostics and reference-driven visual workflow checks."""

import importlib
import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

package = os.environ.get("TYVRANA_TEST_PACKAGE", "bl_ext.user_default.tyvrana_blender")
adapter = importlib.import_module(package + ".blender")
ops = importlib.import_module(package + ".operations")
geometry = importlib.import_module(package + ".retopo_geometry")
render = importlib.import_module(package + ".render")
artifacts = importlib.import_module(package + ".artifacts")
models = importlib.import_module(package + ".models")
errors = importlib.import_module(package + ".errors")
backend = adapter.BlenderBackend()


def call(operation: str, **arguments: Any) -> Any:
    result = ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="visual-check",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(result, OperationSuccess), result
    return result.result


def cube(name: str, index: int = 0) -> Any:
    data = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.5)
    bm.to_mesh(data)
    bm.free()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (index % 5, index // 5, 0)
    return obj


class VisualChecks(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.meshes):
            if not data.users:
                bpy.data.meshes.remove(data)

    def test_regional_diagnostics_ignore_unrelated_scene_objects(self) -> None:
        objects = [cube(f"Part{i:02}", i) for i in range(20)]
        for i in range(360):
            bpy.context.scene.collection.objects.link(
                bpy.data.objects.new(f"Unrelated{i:03}", None)
            )
        bpy.context.view_layer.update()
        names = [o.name for o in objects]
        count = len(bpy.data.objects)
        result = call(
            "geometry.inspect",
            objects=[
                {"object_name": name, "self_intersection": True} for name in names
            ],
            worst_limit=0,
        )
        self.assertEqual(len(result["samples"][0]["objects"]), 20)
        layers = call(
            "layer.inspect",
            queries=[{"mode": "current", "source": names[0], "target": names[1]}],
            worst_limit=2,
        )
        self.assertEqual(len(layers["layers"]), 1)
        camera_data = bpy.data.cameras.new("DiagnosticCamera")
        camera = bpy.data.objects.new("DiagnosticCamera", camera_data)
        bpy.context.scene.collection.objects.link(camera)
        bpy.context.scene.camera = camera
        camera.location = (5, -8, 8)
        camera.rotation_euler = (
            (Vector((2, 1.5, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        )
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = 7
        bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
        root = Path(os.environ["TYVRANA_TEST_OUTPUT"])
        (root / "spool").mkdir(exist_ok=True)
        spool = artifacts.ArtifactSpool()
        try:
            value, descriptor = render.render_image(
                models.RenderArguments(
                    width=256,
                    height=256,
                    wireframe={"objects": names, "thickness": 0.008},
                ),
                spool,
            )
            self.assertEqual(value.width, 256)
            path = artifacts.artifact_path(spool.root, descriptor)
            (root / "regional-wire.png").write_bytes(path.read_bytes())
            self.assertGreater(descriptor.byte_size, 1000)
            value, _ = render.render_image(
                models.RenderArguments.model_validate(
                    {
                        "width": 256,
                        "height": 256,
                        "inspection": {
                            "views": [
                                {
                                    "name": "selected",
                                    "orientation": "top",
                                    "objects": names,
                                }
                            ]
                        },
                    }
                ),
                spool,
            )
            self.assertEqual(value.inspection_tiles[0].object_count, 20)
            self.assertEqual(len(bpy.data.objects), count + 1)
            self.assertTrue(all(not o.hide_render for o in objects))
        finally:
            spool.close()

    def test_reference_previews_preserve_color_and_source_state(self) -> None:
        preview = importlib.import_module(package + ".image_preview")
        schema = importlib.import_module(package + ".image_models")
        raster = importlib.import_module(package + ".raster")
        root = Path(os.environ["TYVRANA_TEST_OUTPUT"])
        path = root / "preview-source.png"
        encoded = raster.png_rgb(16, 16, bytes([128, 64, 32]) * 256)
        path.write_bytes(encoded)
        source = bpy.data.images.load(str(path), check_existing=False)
        source.pack()
        other = bpy.data.images.load(str(path), check_existing=False)
        other.buffers_free()
        self.assertFalse(other.has_data)
        self.assertEqual(tuple(source.size), (16, 16))
        self.assertTrue(source.has_data)
        before = (
            source.filepath,
            source.colorspace_settings.name,
            bytes(source.packed_file.data),
        )
        spool = artifacts.ArtifactSpool()
        try:
            result, descriptor = preview.preview(
                schema.ImagePreviewArguments(
                    names=[source.name, other.name], columns=2, tile_size=128
                ),
                spool,
            )
            self.assertFalse(other.has_data)
            self.assertTrue(source.has_data)
            # Sequential inspection/preview must not accumulate decoded boards.
            for _ in range(3):
                adapter.image_summary(other)
                self.assertFalse(other.has_data)
                preview.preview(
                    schema.ImagePreviewArguments(names=[other.name], tile_size=128),
                    spool,
                )
                self.assertFalse(other.has_data)

            def fail_after_decode(*args: Any) -> None:
                self.assertEqual(tuple(other.size), (16, 16))
                self.assertTrue(other.has_data)
                raise RuntimeError("injected")

            with patch.object(preview, "_preview", side_effect=fail_after_decode):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    preview.preview(
                        schema.ImagePreviewArguments(names=[other.name]), spool
                    )
            self.assertFalse(other.has_data)
            self.assertEqual((result.width, result.height), (256, 128))
            output = bpy.data.images.load(
                str(artifacts.artifact_path(spool.root, descriptor)),
                check_existing=False,
            )
            offset = (64 * 256 + 64) * 4
            values = list(output.pixels[offset : offset + 3])
            for actual, expected in zip(
                values, (128 / 255, 64 / 255, 32 / 255), strict=True
            ):
                self.assertAlmostEqual(actual, expected, places=5)
            bpy.data.images.remove(output)
            self.assertEqual(
                before,
                (
                    source.filepath,
                    source.colorspace_settings.name,
                    bytes(source.packed_file.data),
                ),
            )
            self.assertEqual(path.read_bytes(), encoded)
        finally:
            spool.close()
            bpy.data.images.remove(source)
            bpy.data.images.remove(other)

    def test_modifier_batch_preserves_geometry_and_rolls_back(self) -> None:
        objects = [cube(f"Finish{i:02}", i) for i in range(27)]
        native = importlib.import_module(package + ".modifiers")
        schema = importlib.import_module(package + ".modifier_models")
        before = [tuple(tuple(v.co) for v in o.data.vertices) for o in objects]
        arguments = schema.ModifierBatchCreateArguments.model_validate(
            {
                "modifiers": [
                    {
                        "object_name": o.name,
                        "type": "corrective_smooth",
                        "name": "Polish",
                        "settings": {"factor": 0.5, "iterations": 6, "scale": 0.01},
                    }
                    for o in objects
                ]
            }
        )
        original = native.create

        def fail_late(obj: Any, item: Any) -> Any:
            if obj == objects[-1]:
                raise RuntimeError("injected final modifier failure")
            return original(obj, item)

        with patch.object(native, "create", side_effect=fail_late):
            with self.assertRaisesRegex(RuntimeError, "final modifier failure"):
                native.create_batch(arguments)
        self.assertTrue(all(not o.modifiers for o in objects))
        result = call(
            "modifier.create_batch",
            **arguments.model_dump(mode="json", exclude_none=True),
        )
        self.assertEqual(len(result["created"]), 27)
        self.assertTrue(all(len(o.modifiers) == 1 for o in objects))
        self.assertEqual(
            before, [tuple(tuple(v.co) for v in o.data.vertices) for o in objects]
        )
        obj = objects[0]
        call("modifier.remove", object_name=obj.name, modifier_name="Polish")
        call(
            "modifier.create",
            object_name=obj.name,
            type="subdivision_surface",
            name="Subdivision",
            settings={"levels": 1, "render_levels": 1},
        )
        deps = bpy.context.evaluated_depsgraph_get()
        original_points = tuple(
            tuple(v.co) for v in obj.evaluated_get(deps).data.vertices
        )
        with self.assertRaisesRegex(errors.OperationError, "original coordinates"):
            native.create(
                obj,
                schema.CREATE.validate_python(
                    {
                        "object_name": obj.name,
                        "type": "corrective_smooth",
                    }
                ),
            )
        result = call(
            "modifier.create",
            object_name=obj.name,
            type="corrective_smooth",
            name="Fair",
            settings={"only_smooth": True, "iterations": 4},
        )
        self.assertTrue(result["settings"]["only_smooth"])
        deps = bpy.context.evaluated_depsgraph_get()
        self.assertNotEqual(
            original_points,
            tuple(tuple(v.co) for v in obj.evaluated_get(deps).data.vertices),
        )
        self.assertEqual(before[0], tuple(tuple(v.co) for v in obj.data.vertices))
        obj.modifiers["Fair"].rest_source = "BIND"
        with self.assertRaisesRegex(errors.OperationError, "original coordinates"):
            native.budget(obj, [(m, {}) for m in obj.modifiers])
        obj.modifiers["Fair"].rest_source = "ORCO"

    def test_dense_wire_focus_filters_before_edge_budget(self) -> None:
        obj = cube("DenseSurface")
        obj.location = (4, -2, 3)
        mod = obj.modifiers.new("Detail", "SUBSURF")
        mod.subdivision_type = "SIMPLE"
        mod.levels = mod.render_levels = 4
        bpy.context.view_layer.update()
        before = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
        basis = tuple(tuple(v.co) for v in obj.data.vertices)
        wire = importlib.import_module(package + ".wireframe")
        with self.assertRaisesRegex(errors.OperationError, "evaluated edges"):
            with wire.display(
                models.WireframeRenderOptions(objects=[obj.name], max_edges=512)
            ):
                self.fail("Full dense mesh exceeded its wire budget")
        spool = artifacts.ArtifactSpool()
        try:
            value, descriptor = render.render_image(
                models.RenderArguments.model_validate(
                    {
                        "width": 256,
                        "height": 256,
                        "inspection": {
                            "views": [
                                {
                                    "name": "corner topology",
                                    "orientation": "oblique",
                                    "objects": [obj.name],
                                    "wireframe": True,
                                    "wire_edge_limit": 512,
                                    "focus": {
                                        "minimum": [0.8, 0.8, 0.8],
                                        "maximum": [1, 1, 1],
                                    },
                                }
                            ]
                        },
                    }
                ),
                spool,
            )
            self.assertEqual(value.inspection_tiles[0].object_count, 1)
            self.assertGreater(descriptor.byte_size, 1000)
            (
                Path(os.environ["TYVRANA_TEST_OUTPUT"]) / "dense-regional-wire.png"
            ).write_bytes(artifacts.artifact_path(spool.root, descriptor).read_bytes())
        finally:
            spool.close()
        self.assertEqual(
            before,
            (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)),
        )
        self.assertEqual(basis, tuple(tuple(v.co) for v in obj.data.vertices))
        self.assertEqual(len(obj.modifiers), 1)
        self.assertFalse(obj.hide_render)

    def test_linear_premultiplied_preview_unpremultiplies_before_encoding(self) -> None:
        preview = importlib.import_module(package + ".image_preview")
        schema = importlib.import_module(package + ".image_models")
        source = bpy.data.images.new(
            "LinearPremultiplied", width=8, height=8, alpha=True, float_buffer=True
        )
        source.colorspace_settings.name = "Linear Rec.709"
        source.alpha_mode = "PREMUL"
        source.pixels[:] = [0.125, 0.125, 0.125, 0.5] * 64
        before = tuple(source.pixels[:])
        spool = artifacts.ArtifactSpool()
        try:
            _, descriptor = preview.preview(
                schema.ImagePreviewArguments(names=[source.name], tile_size=128), spool
            )
            image = bpy.data.images.load(
                str(artifacts.artifact_path(spool.root, descriptor)),
                check_existing=False,
            )
            try:
                expected = (
                    round(
                        ((1.055 * 0.25 ** (1 / 2.4) - 0.055) * 0.5 + 0.125 * 0.5) * 255
                    )
                    / 255
                )
                self.assertAlmostEqual(
                    image.pixels[(64 * 128 + 64) * 4], expected, places=5
                )
                self.assertEqual(tuple(source.pixels[:]), before)
            finally:
                bpy.data.images.remove(image)
        finally:
            spool.close()
            bpy.data.images.remove(source)

    def test_gpu_render_reports_configured_hardware(self) -> None:
        import _cycles  # type: ignore[import-not-found]

        native = _cycles.available_devices("OPTIX")
        if not any(row[1] == "OPTIX" for row in native):
            self.skipTest("No native OptiX device in this test host")
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "OPTIX"
        prefs.get_devices_for_type("OPTIX")
        for device in prefs.devices:
            device.use = device.type == "OPTIX"
        cube("GPUFixture")
        data = bpy.data.cameras.new("GPUCamera")
        camera = bpy.data.objects.new("GPUCamera", data)
        bpy.context.scene.collection.objects.link(camera)
        camera.location = (2, -3, 2)
        camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
        scene = bpy.context.scene
        scene.camera = camera
        before = (scene.render.engine, scene.cycles.device)
        root = Path(os.environ["TYVRANA_TEST_OUTPUT"])
        (root / "gpu-spool").mkdir(exist_ok=True)
        spool = artifacts.ArtifactSpool()
        try:
            value, descriptor = render.render_image(
                models.RenderArguments.model_validate(
                    {
                        "width": 256,
                        "height": 256,
                        "cycles": {"device": "gpu", "samples": 8},
                    }
                ),
                spool,
            )
            self.assertEqual(value.device.effective, "gpu")
            self.assertEqual(value.device.compute_backend, "OPTIX")
            self.assertTrue(value.device.enabled_devices)
            (root / "gpu-render.json").write_text(value.model_dump_json(indent=2))
            (root / "gpu-render.png").write_bytes(
                artifacts.artifact_path(spool.root, descriptor).read_bytes()
            )
            self.assertEqual(before, (scene.render.engine, scene.cycles.device))
        finally:
            spool.close()

    def test_native_review_packet_and_restoration(self) -> None:
        objects = [cube(f"ReviewPart{i:02}", i) for i in range(3)]
        objects[0].scale = (1, 2, 3)
        objects[1].scale = (2, 1, 1)
        bpy.context.view_layer.update()
        scene = bpy.context.scene
        before = (
            scene.camera,
            scene.render.engine,
            scene.render.resolution_x,
            scene.render.resolution_y,
            len(bpy.data.objects),
        )
        root = Path(os.environ["TYVRANA_TEST_OUTPUT"])
        (root / "review-spool").mkdir(exist_ok=True)
        spool = artifacts.ArtifactSpool()
        views: list[dict[str, Any]] = [
            {"name": name, "orientation": name}
            for name in ("left", "right", "front", "rear", "top", "bottom")
        ]
        views += [
            {"name": name, "orientation": name, "projection": "perspective"}
            for name in ("oblique", "reverse_oblique")
        ]
        views += [
            {
                "name": "regional",
                "orientation": "oblique",
                "focus": {"minimum": [0, 0, 0.5], "maximum": [1, 1, 1]},
                "objects": [objects[0].name],
            },
            {
                "name": "wire",
                "orientation": "oblique",
                "objects": [objects[0].name],
                "wireframe": True,
            },
        ]
        try:
            value, descriptor = render.render_image(
                models.RenderArguments.model_validate(
                    {
                        "width": 1600,
                        "height": 2048,
                        "inspection": {
                            "objects": [o.name for o in objects],
                            "views": views,
                            "packet": {
                                "directory": str(root / "review-packet"),
                                "overwrite": True,
                            },
                        },
                        "budget": {"max_total_pixels": 40000000},
                    }
                ),
                spool,
            )
            self.assertEqual(len(value.inspection_tiles), 10)
            self.assertEqual(value.review_directory, str(root / "review-packet"))
            packet = importlib.import_module(package + ".review_packet")
            self.assertEqual(len(packet.inventory(Path(value.review_directory))), 12)
            data = json.loads(
                (Path(value.review_directory) / "manifest.json").read_text()
            )
            for row in data["files"]:
                if "camera_world" not in row:
                    continue
                tile = next(v for v in value.inspection_tiles if v.name == row["name"])
                self.assertEqual(
                    tile.view.model_dump(),
                    {
                        k: row[k]
                        for k in (
                            "width",
                            "height",
                            "camera_world",
                            "projection_matrix",
                        )
                    },
                )
                self.assertEqual((row["width"], row["height"]), (1600, 2048))
                projection = (
                    Matrix(row["projection_matrix"])
                    @ Matrix(row["camera_world"]).inverted()
                )
                for name in row["objects"]:
                    obj = bpy.data.objects[name]
                    for corner in obj.bound_box:
                        if row.get("focus") and corner[2] < 0:
                            continue
                        clip = projection @ (obj.matrix_world @ Vector(corner)).to_4d()
                        self.assertLess(abs(clip.x / clip.w), 0.99)
                        self.assertLess(abs(clip.y / clip.w), 0.99)
            self.assertLess(
                descriptor.byte_size, sum(row["bytes"] for row in data["files"])
            )
            (root / "review-result.json").write_text(value.model_dump_json(indent=2))
            self.assertEqual(
                before,
                (
                    scene.camera,
                    scene.render.engine,
                    scene.render.resolution_x,
                    scene.render.resolution_y,
                    len(bpy.data.objects),
                ),
            )
            self.assertFalse(any(o.hide_render for o in objects))
        finally:
            spool.close()

    def test_dependency_budget_and_cycles_remain_enforced(self) -> None:
        target = cube("Selected")
        dependencies = [
            bpy.data.objects.new(f"ActualDependency{i:03}", None) for i in range(256)
        ]
        for obj in dependencies:
            bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        with patch.object(
            geometry,
            "dependencies",
            side_effect=lambda obj: dependencies if obj == target else [],
        ):
            with self.assertRaisesRegex(
                errors.OperationError, "Evaluation dependencies exceed 256"
            ):
                geometry.graph(target)
        with patch.object(geometry, "dependencies", side_effect=lambda obj: [target]):
            with self.assertRaisesRegex(errors.OperationError, "Cyclic"):
                geometry.graph(target)


def run() -> None:
    adapter.unregister()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(VisualChecks)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Native visual workflow checks failed")
    print("VISUAL_NATIVE_PASSED", result.testsRun, flush=True)


if __name__ == "__main__":
    run()
