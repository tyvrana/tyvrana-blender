"""Saved dependency audit detects missing data and verifies explicit outputs."""

import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

renderer = importlib.import_module(PACKAGE + "render_host")


class DeliveryTests(TopologyTests):
    def test_external_images_actions_and_unbaked_cache(self) -> None:
        self.call("object.create_primitive", primitive="cube", name="CachedAsset")
        obj = bpy.data.objects["CachedAsset"]
        image = bpy.data.images.new("MissingExternal", width=2, height=2)
        image.source = "FILE"
        image.filepath = "//absent-texture.png"
        image.use_fake_user = True
        retained = bpy.data.actions.new("RetainedAction")
        retained.use_fake_user = True
        orphan = bpy.data.actions.new("OrphanAction")
        obj.modifiers.new("Unbaked", "CLOTH")
        try:
            report = self.call("file.audit", required_actions=[retained.name], limit=64)
            rows = {r["name"]: r for r in report["dependencies"]}
            self.assertEqual(rows[image.name]["status"], "missing")
            self.assertEqual(rows[retained.name]["status"], "embedded")
            self.assertEqual(rows[orphan.name]["status"], "unsaved")
            self.assertEqual(rows["CachedAsset/Unbaked"]["status"], "unverified")
            self.assertFalse(report["ready"])
        finally:
            bpy.data.images.remove(image)
            bpy.data.actions.remove(retained)
            bpy.data.actions.remove(orphan)

    def test_saved_resources_and_missing_output(self) -> None:
        self.call("object.create_primitive", primitive="cube", name="Asset")
        self.call("material.create_principled", name="DeliveryMaterial")
        self.call(
            "material.assign", object_name="Asset", material_name="DeliveryMaterial"
        )
        material = bpy.data.materials["DeliveryMaterial"]
        image = bpy.data.images.new("Generated", width=2, height=2)
        image.pixels[:] = [0.25, 0.5, 0.75, 1] * 4
        material.node_tree.nodes.new("ShaderNodeTexImage").image = image
        try:
            unsaved = self.call("file.audit")
            self.assertFalse(unsaved["ready"])
            self.assertGreaterEqual(unsaved["counts"]["unsaved"], 2)
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                output = root / "output.bin"
                output.write_bytes(b"verified output")
                expected = hashlib.sha256(output.read_bytes()).hexdigest()
                image.pack()
                self.call("file.save", filepath=str(root / "delivery.blend"))
                report = self.call(
                    "file.audit",
                    required_files=[dict(filepath=str(output), sha256=expected)],
                    limit=32,
                )
                self.assertTrue(report["ready"], report)
                self.assertEqual(report["counts"]["packed"], 1)
                self.assertEqual(report["hashed_bytes"], output.stat().st_size)
                missing = self.call(
                    "file.audit",
                    required_files=[dict(filepath=str(root / "missing.png"))],
                    required_actions=["MissingAction"],
                    limit=1,
                )
                self.assertFalse(missing["ready"])
                self.assertEqual(missing["counts"]["missing"], 2)
                self.assertTrue(missing["truncated"])
                bad = self.call(
                    "file.audit",
                    required_files=[dict(filepath=str(output), sha256="0" * 64)],
                )
                self.assertFalse(bad["ready"])
                self.error(
                    "file.audit",
                    required_files=[dict(filepath=str(output), sha256=expected)],
                    max_hash_bytes=1,
                )
                self.error("file.audit", max_resources=1)
                saved = json.dumps(report, sort_keys=True)
                self.assertIn("delivery.blend", saved)
        finally:
            bpy.data.images.remove(image)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            DeliveryTests(name)
            for name in (
                "test_external_images_actions_and_unbaked_cache",
                "test_saved_resources_and_missing_output",
            )
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("DELIVERY_NATIVE_PASSED")
