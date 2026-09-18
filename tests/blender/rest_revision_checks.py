"""Bound revision preserves bindings/actions and exposes stale corrective inputs."""

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.topology_checks import PACKAGE, TopologyTests  # noqa: E402

rig = importlib.import_module(PACKAGE + "rig")


class RestTests(TopologyTests):
    def test_bound_preview_revision_stale_correction_and_organization(self) -> None:
        self.call(
            "armature.create",
            name="Rig",
            bones=[dict(name="Joint", head=[0, 0, 0], tail=[0, 1, 0])],
        )
        self.call("object.create_primitive", primitive="cube", name="Skin")
        self.call(
            "armature.bind",
            object_name="Skin",
            armature_object="Rig",
            weights=dict(method="envelopes", bones=["Joint"]),
            allow_unweighted=True,
            preserve_volume=False,
        )
        self.call(
            "shape_keys.edit",
            object_name="Skin",
            keys=[
                dict(
                    name="Flex",
                    create=True,
                    correction=dict(
                        mode="sparse", deltas=[dict(vertex=0, delta=[0.1, 0, 0])]
                    ),
                )
            ],
        )
        self.call("deformation.capture_target", object_name="Skin", name="Target")
        self.call(
            "armature.pose",
            object_name="Rig",
            bones=[dict(name="Joint", rotation=[0, 0, 0])],
        )
        self.call(
            "action.edit",
            name="Move",
            create=True,
            channels=[
                dict(
                    target=dict(
                        kind="transform",
                        object_name="Rig",
                        bone="Joint",
                        property="rotation",
                        axis="x",
                    ),
                    keys=[dict(frame=1, value=0), dict(frame=10, value=0.4)],
                )
            ],
        )
        self.call("action.assign", name="Move")
        obj = bpy.data.objects["Rig"]
        skin = bpy.data.objects["Skin"]
        old = rig.rest_signature(obj)
        weights = rig.binding_summary(skin).weights_sha256
        action = obj.animation_data.action
        arguments = dict(
            object_name="Rig",
            bones=[dict(name="Joint", head=[0, 0, 0], tail=[0, 1.2, 0])],
            dependency_policy="preserve",
        )
        preview = self.call("armature.configure_rest", **arguments, preview=True)
        self.assertEqual(rig.rest_signature(obj), old)
        self.assertEqual(preview["rest_revision"]["binding_objects"], ["Skin"])
        self.assertEqual(preview["rest_revision"]["actions"], ["Move"])
        self.assertEqual(preview["rest_revision"]["captured_targets"], ["Target"])
        self.error("armature.configure_rest", **arguments)
        with patch.object(rig, "inspect", side_effect=RuntimeError("injected")):
            self.error("armature.configure_rest", **arguments, expected_rest_sha256=old)
        self.assertEqual(rig.rest_signature(obj), old)
        result = self.call(
            "armature.configure_rest", **arguments, expected_rest_sha256=old
        )
        self.assertNotEqual(result["rest_sha256"], old)
        self.assertEqual(rig.binding_summary(skin).weights_sha256, weights)
        self.assertEqual(obj.animation_data.action, action)
        shapes = self.call("shape_keys.inspect", object_name="Skin")
        self.assertTrue(shapes["rest_revision_unacknowledged"])
        self.error(
            "shape_keys.edit", object_name="Skin", keys=[dict(name="Flex", value=0.5)]
        )
        self.call(
            "shape_keys.edit",
            object_name="Skin",
            acknowledge_rest_sha256=result["rest_sha256"],
            keys=[dict(name="Flex", value=0)],
        )
        error = self.error(
            "shape_keys.edit",
            object_name="Skin",
            keys=[
                dict(
                    name="Stale",
                    create=True,
                    correction=dict(mode="captured_target", target="Target"),
                )
            ],
        )
        self.assertIn("recapture", error.message)
        self.call("collection.create_hierarchy", collections=[dict(name="Organized")])
        configured = self.call(
            "object_set.configure",
            objects=[dict(name="Skin", collections=["Organized"], hide_render=True)],
        )
        self.assertTrue(skin.hide_render)
        self.assertEqual([c.name for c in skin.users_collection], ["Organized"])
        self.assertTrue(configured["objects"][0]["visibility"]["hide_render"])
        self.error("object_set.configure", objects=[dict(name="Skin", rename="Unsafe")])
        self.assertEqual(rig.binding_summary(skin).weights_sha256, weights)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [RestTests("test_bound_preview_revision_stale_correction_and_organization")]
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("REST_REVISION_NATIVE_PASSED")
