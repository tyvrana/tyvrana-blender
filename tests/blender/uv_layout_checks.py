"""Native production UV preservation, packing, evaluation and diagnostic checks."""

import importlib
import unittest
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

package = "bl_ext.user_default.tyvrana_blender"
operations = importlib.import_module(package + ".operations")
adapter = importlib.import_module(package + ".blender")
uv = importlib.import_module(package + ".uv")
layout = importlib.import_module(package + ".uv_layout")
checker = importlib.import_module(package + ".uv_checker")
models = importlib.import_module(package + ".models")
artifacts = importlib.import_module(package + ".artifacts")


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.spool = artifacts.ArtifactSpool()
        self.backend = adapter.BlenderBackend(self.spool)
        self.counter = 0
        for name, x in [("A", 0), ("B", 3)]:
            bpy.ops.mesh.primitive_plane_add(location=(x, 0, 0))
            bpy.context.object.name = name
        self.a = bpy.data.objects["A"]
        self.b = bpy.data.objects["B"]

    def tearDown(self) -> None:
        self.spool.close()

    def call(self, operation: str, **arguments: Any) -> Any:
        self.counter += 1
        result = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id=str(self.counter),
                operation="blender." + operation,
                arguments=arguments,
            ),
        )
        self.assertIsInstance(result, OperationSuccess, str(result))
        return result

    def test_joint_density_margin_mirror_and_geometry(self) -> None:
        first = self.call("mesh.inspect", object_name="A").result
        self.call(
            "modifier.create",
            object_name="A",
            type="mirror",
            name="Symmetry",
            settings={"axes": ["x"], "merge": False, "uv_flip_u": True},
        )
        self.a.hide_set(True)
        self.call(
            "mesh.mark_seam",
            object_name="A",
            selector={"domain": "edge", "mode": "indices", "indices": [0, 1]},
            seam=True,
        )
        self.call(
            "uv.unwrap", object_name="A", method="minimum_stretch", correct_aspect=False
        )
        self.assertTrue(self.a.hide_get())
        self.call(
            "uv.pack_islands",
            objects=[
                {"object_name": "A", "density_weight": 1},
                {"object_name": "B", "density_weight": 2},
            ],
            bounds_max=[0.5, 1],
            resolution=4096,
            padding_pixels=16,
        )
        result = self.call(
            "uv.inspect_layout", objects=["A", "B"], resolution=4096
        ).result
        self.assertEqual(result["overlap_pair_count"], 0)
        self.assertGreaterEqual(result["minimum_island_gap_pixels"], 31.99)
        self.assertGreaterEqual(result["minimum_tile_border_pixels"], 15.99)
        density = {i["object_name"]: i["texel_density"] for i in result["islands"]}
        self.assertAlmostEqual(density["B"] / density["A"], 2, places=5)
        second = self.call("mesh.inspect", object_name="A").result
        self.assertEqual(first["geometry_sha256"], second["geometry_sha256"])
        self.assertEqual(self.a.modifiers[0].type, "MIRROR")
        evaluated = self.call(
            "uv.inspect_layout", objects=["A", "B"], evaluated=True, layout_image=True
        ).result
        self.assertEqual(evaluated["overlap_pair_count"], 0)
        self.assertEqual(evaluated["flipped_face_count"], 0)
        self.assertEqual(evaluated["degenerate_face_count"], 0)
        self.assertEqual(evaluated["face_count"], 3)
        self.assertGreaterEqual(evaluated["minimum_island_gap_pixels"], 31.99)

    def test_shared_mesh_and_map_roles_preserved(self) -> None:
        original = self.a.data
        self.b.data = original
        before = [tuple(p.vector) for p in original.uv_layers.active.uv]
        self.call(
            "uv.create_map",
            object_name="A",
            name="Other",
            set_active=False,
            set_render=False,
        )
        self.assertEqual(self.a.data.uv_layers.active.name, "UVMap")
        self.call(
            "uv.pack_islands",
            objects=[{"object_name": "A", "uv_map": "Other"}],
            bounds_max=[0.5, 1],
        )
        self.assertEqual(
            [tuple(p.vector) for p in self.b.data.uv_layers.active.uv], before
        )
        self.assertEqual(self.a.data.uv_layers.active.name, "UVMap")
        self.assertTrue(self.a.data.uv_layers["UVMap"].active_render)

    def test_batch_failure_rolls_back_every_object(self) -> None:
        originals = [obj.data for obj in [self.a, self.b]]
        before = [
            [tuple(p.vector) for p in data.uv_layers.active.uv] for data in originals
        ]
        normal = uv.inspect

        def fail(obj: object) -> object:
            if obj == self.b:
                raise RuntimeError("injected post-edit inspection failure")
            return normal(obj)

        with patch.object(uv, "inspect", side_effect=fail):
            with self.assertRaises(RuntimeError):
                layout.pack(
                    layout.UVPackArguments.model_validate(
                        {"objects": [{"object_name": "A"}, {"object_name": "B"}]}
                    )
                )
        self.assertEqual([obj.data for obj in [self.a, self.b]], originals)
        self.assertEqual(
            [
                [tuple(p.vector) for p in obj.data.uv_layers.active.uv]
                for obj in [self.a, self.b]
            ],
            before,
        )

    def test_checker_failure_restores_materials_visibility_and_resources(self) -> None:
        material = bpy.data.materials.new("User material")
        self.a.data.materials.append(material)
        self.a.material_slots[0].link = "OBJECT"
        self.a.material_slots[0].material = material
        original = self.a.data
        counts = (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images))
        hidden = [(o, o.hide_render, o.hide_get()) for o in [self.a, self.b]]
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with checker.display(
                models.UVCheckerRenderOptions(
                    objects=["A"], uv_map="UVMap", exclude_objects=["B"]
                )
            ):
                self.assertTrue(self.b.hide_render)
                self.assertNotEqual(self.a.data, original)
                self.assertEqual(self.a.data.uv_layers.active.name, "UVMap")
                raise RuntimeError("injected render failure")
        self.assertEqual(self.a.data, original)
        self.assertEqual(self.a.material_slots[0].link, "OBJECT")
        self.assertEqual(self.a.material_slots[0].material, material)
        self.assertEqual(
            counts,
            (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images)),
        )
        self.assertEqual(
            hidden, [(o, o.hide_render, o.hide_get()) for o in [self.a, self.b]]
        )

    def test_checker_render_artifact_and_state(self) -> None:
        self.b.hide_render = True
        bpy.ops.object.camera_add(location=(0, 0, 4))
        bpy.context.scene.camera = bpy.context.object
        camera = bpy.context.object
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = 3
        bpy.ops.object.light_add(type="AREA", location=(0, 0, 3))
        bpy.context.object.data.energy = 100
        bpy.context.scene.render.engine = "CYCLES"
        original = self.a.data
        counts = (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images))
        result = self.call(
            "render.image",
            width=128,
            height=128,
            uv_checker={"objects": ["A"], "uv_map": "UVMap", "exclude_objects": ["B"]},
        )
        self.assertEqual(result.result["width"], 128)
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(self.a.data, original)
        self.assertEqual(bpy.context.scene.render.engine, "CYCLES")
        self.assertTrue(self.b.hide_render)
        self.assertEqual(counts[:2], (len(bpy.data.meshes), len(bpy.data.materials)))
        self.assertFalse(
            any(image.name.startswith("UV diagnostic") for image in bpy.data.images)
        )


suite = unittest.defaultTestLoader.loadTestsFromTestCase(LayoutTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print("BLENDER_UV_LAYOUT_TESTS_PASSED", result.testsRun)
