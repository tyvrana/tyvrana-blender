"""Native raster decoding and packed image independence checks."""

import hashlib
import importlib
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import (
    ArtifactBegin,
    ArtifactChunk,
    ArtifactDescriptor,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.png import checker_png

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
incoming = importlib.import_module("bl_ext.user_default.tyvrana_blender.incoming")
artifacts = importlib.import_module("bl_ext.user_default.tyvrana_blender.artifacts")


class InputImageTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for image in list(bpy.data.images):
            bpy.data.images.remove(image, do_unlink=True)
        self.spool = artifacts.ArtifactSpool()
        self.inputs = incoming.InputStore(self.spool.root)
        self.backend = adapter.BlenderBackend(self.spool)
        self.root = Path(os.environ["TYVRANA_TEST_CONTROL"])

    def tearDown(self) -> None:
        self.inputs.clear()
        self.spool.close()
        self.assertFalse(self.spool.root.exists())

    def admit(
        self, data: bytes, media_type: str = "image/png", **arguments: Any
    ) -> Any:
        descriptor = ArtifactDescriptor(
            artifact_id=uuid4().hex,
            name="Texture",
            media_type=media_type,
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        message = ArtifactBegin(
            type="artifact.begin",
            request_id=uuid4().hex,
            transfer_id=uuid4().hex,
            descriptor=descriptor,
        )
        self.inputs.begin(message)
        for offset in range(0, len(data), 65536):
            self.inputs.write(
                ArtifactChunk(
                    message.transfer_id, offset, data[offset : offset + 65536]
                )
            )
        self.inputs.complete(message.transfer_id)
        request = OperationRequest(
            type="operation.request",
            request_id=message.request_id,
            operation="blender.image.create_from_artifact",
            arguments={"artifact_id": descriptor.artifact_id, **arguments},
            artifacts=(descriptor,),
        )
        self.inputs.claim(request)
        return request

    def call(self, request: Any) -> Any:
        response = operations.execute(self.backend, request)
        self.inputs.discard_request(request.request_id)
        return response

    def test_png_packed_exact_bytes_without_any_filepath(self) -> None:
        data = checker_png(128, 64)
        response = self.call(
            self.admit(data, name="Surface", color_space="sRGB", alpha_mode="straight")
        )
        self.assertIsInstance(response, OperationSuccess, str(response))
        result = response.result
        self.assertEqual((result["width"], result["height"]), (128, 64))
        self.assertEqual(result["source"], "file")
        self.assertTrue(result["packed"])
        self.assertIsNone(result["generated_type"])
        image = bpy.data.images["Surface"]
        self.assertEqual(
            hashlib.sha256(image.packed_file.data).hexdigest(),
            hashlib.sha256(data).hexdigest(),
        )
        self.assertEqual(image.filepath, "")
        self.assertTrue(all(item.filepath == "" for item in image.packed_files))
        self.assertFalse(list(self.spool.root.iterdir()))
        before = list(image.pixels[:64])
        image.buffers_free()
        self.assertEqual(list(image.pixels[:64]), before)
        self.assertNotIn(str(self.spool.root), response.model_dump_json())

    def test_jpeg_decode_pack_and_buffer_reload(self) -> None:
        generated = bpy.data.images.new("Fixture", width=64, height=32)
        generated.generated_color = (0.7, 0.2, 0.1, 1)
        path = self.root / "source.jpg"
        settings = bpy.context.scene.render.image_settings
        previous = settings.file_format
        try:
            settings.file_format = "JPEG"
            generated.save_render(str(path), scene=bpy.context.scene)
        finally:
            settings.file_format = previous
        data = path.read_bytes()
        path.unlink()
        bpy.data.images.remove(generated)
        response = self.call(
            self.admit(data, "image/jpeg", name="Photo", alpha_mode="none")
        )
        self.assertIsInstance(response, OperationSuccess, str(response))
        self.assertEqual(
            (response.result["width"], response.result["height"]), (64, 32)
        )
        self.assertTrue(response.result["packed"])
        image = bpy.data.images["Photo"]
        self.assertEqual(
            hashlib.sha256(image.packed_file.data).hexdigest(),
            hashlib.sha256(data).hexdigest(),
        )
        image.buffers_free()
        self.assertGreater(image.pixels[0], 0.5)
        self.assertFalse(path.exists())

    def test_saved_blend_contains_image_after_all_input_files_deleted(self) -> None:
        response = self.call(self.admit(checker_png(), name="Surface"))
        self.assertIsInstance(response, OperationSuccess, str(response))
        image = bpy.data.images["Surface"]
        image.use_fake_user = True
        path = self.root / "packed.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        image = bpy.data.images["Surface"]
        self.assertTrue(image.packed_file)
        self.assertEqual(tuple(image.size), (128, 128))
        self.assertGreater(image.pixels[0], 0.7)
        self.assertEqual(image.filepath, "")
        path.unlink()

    def test_duplicate_name_and_invalid_metadata_leave_no_image(self) -> None:
        first = self.call(self.admit(checker_png(), name="Surface"))
        self.assertIsInstance(first, OperationSuccess)
        for arguments in (
            {"name": "Surface"},
            {"color_space": "Unavailable"},
            {"name": "x" * 300},
        ):
            with self.subTest(arguments=arguments):
                names = set(bpy.data.images.keys())
                result = self.call(self.admit(checker_png(), **arguments))
                self.assertIsInstance(result, OperationFailure)
                self.assertEqual(result.error.code, "invalid_arguments")
                self.assertEqual(set(bpy.data.images.keys()), names)

    def test_missing_and_unattached_artifact(self) -> None:
        request = self.admit(checker_png())
        result = operations.execute(
            self.backend, request.model_copy(update={"artifacts": ()})
        )
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "artifact_not_attached")
        self.inputs.discard_request(request.request_id)
        result = operations.execute(self.backend, request)
        self.assertIsInstance(result, OperationFailure)
        self.assertEqual(result.error.code, "artifact_not_found")

    def test_unsupported_mismatched_truncated_and_corrupt_rasters(self) -> None:
        data = checker_png()
        corrupt = data[:45] + b"x" * 10 + data[55:]
        for payload, media_type, code in [
            (data, "application/octet-stream", "unsupported_artifact_media_type"),
            (data, "image/jpeg", "artifact_decode_failed"),
            (b"not png", "image/png", "artifact_decode_failed"),
            (data[:-10], "image/png", "artifact_decode_failed"),
            (corrupt, "image/png", "artifact_decode_failed"),
        ]:
            with self.subTest(media_type=media_type, size=len(payload)):
                names = set(bpy.data.images.keys())
                result = self.call(self.admit(payload, media_type))
                self.assertIsInstance(result, OperationFailure, str(result))
                self.assertEqual(result.error.code, code)
                self.assertEqual(set(bpy.data.images.keys()), names)
                self.assertNotIn(str(self.spool.root), result.model_dump_json())

    def test_default_names_are_clean_and_unique(self) -> None:
        results = [self.call(self.admit(checker_png())) for _ in range(2)]
        self.assertTrue(all(isinstance(result, OperationSuccess) for result in results))
        self.assertEqual(
            [result.result["name"] for result in results], ["Image", "Image.001"]
        )

    def test_four_k_image_is_supported(self) -> None:
        generated = bpy.data.images.new("Fixture", width=4096, height=4096)
        generated.generated_color = (0.5, 0.3, 0.1, 1)
        generated.file_format = "PNG"
        path = self.root / "source-4k.png"
        generated.filepath_raw = str(path)
        generated.save()
        data = path.read_bytes()
        path.unlink()
        bpy.data.images.remove(generated)
        response = self.call(self.admit(data, name="Large"))
        self.assertIsInstance(response, OperationSuccess, str(response))
        self.assertEqual(
            (response.result["width"], response.result["height"]), (4096, 4096)
        )
        self.assertTrue(response.result["packed"])


suite = unittest.defaultTestLoader.loadTestsFromTestCase(InputImageTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
adapter.unregister()
assert not bpy.app.timers.is_registered(adapter.pump)
if not result.wasSuccessful():
    raise SystemExit(1)
print(f"BLENDER_INPUT_IMAGE_TESTS_PASSED {result.testsRun}")
