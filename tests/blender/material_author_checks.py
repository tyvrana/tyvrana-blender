"""Native staged material, ownership, link and assignment invariants."""

import importlib
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender."
adapter = importlib.import_module(PACKAGE + "blender")
models = importlib.import_module(PACKAGE + "material_author_models")
author = importlib.import_module(PACKAGE + "material_author")


class MaterialAuthorTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material, do_unlink=True)
        for image in list(bpy.data.images):
            if image.name.startswith("Test"):
                bpy.data.images.remove(image)
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend(adapter._runtime.worker.spool)

    def semantic(self, **values: Any) -> Any:
        return self.backend.material_author(
            models.MaterialAuthorArguments.model_validate({"name": "Test", **values})
        )

    def graph(self, **values: Any) -> Any:
        return self.backend.shader_author(
            models.GraphAuthorArguments.model_validate({"name": "Test", **values})
        )

    def mesh(self, name: str) -> Any:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8)
        obj = bpy.context.object
        obj.name = name
        return obj

    def image(self, color_space: str = "Non-Color") -> Any:
        image = bpy.data.images.new("TestImage", width=8, height=8)
        image.colorspace_settings.name = color_space
        image.pixels[0] = 0.5
        image.pack()
        return image

    def basic_graph(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": "p", "type": "principled"},
                {"id": "out", "type": "output"},
            ],
            "links": [
                {
                    "source": {"node": "p", "socket": "bsdf"},
                    "target": {"node": "out", "socket": "surface"},
                }
            ],
        }

    def test_semantic_patch_preserves_other_branches(self) -> None:
        self.image()
        first = self.semantic(
            parameters={
                "anisotropy": 0.6,
                "thin_film_thickness": 500,
                "subsurface_anisotropy": -0.3,
            },
            textures=[{"channel": "roughness", "image": "TestImage"}],
            variation={
                "channel": "base_color",
                "low": [0.1, 0.2, 0.3],
                "high": [0.8, 0.4, 0.1],
            },
        )
        updated = self.semantic(mode="update", parameters={"coat_weight": 0.5})
        self.assertEqual(updated.graph.node_count, first.graph.node_count)
        self.assertEqual(updated.graph.textures, first.graph.textures)
        self.assertNotEqual(updated.graph.fingerprint, first.graph.fingerprint)
        self.assertEqual(updated.graph.ownership, "semantic")
        self.assertEqual(
            updated.graph.parameters.anisotropy, first.graph.parameters.anisotropy
        )

    def test_color_space_error_no_mutation_or_leak(self) -> None:
        image = self.image("sRGB")
        before = len(bpy.data.materials)
        with self.assertRaisesRegex(adapter.OperationError, "color space"):
            self.semantic(textures=[{"channel": "normal", "image": image.name}])
        self.assertEqual(len(bpy.data.materials), before)
        self.assertEqual(image.colorspace_settings.name, "sRGB")
        accepted = self.semantic(
            textures=[
                {"channel": "roughness", "image": image.name, "color_space": "sRGB"}
            ]
        )
        self.assertTrue(
            any("non-default color" in warning for warning in accepted.graph.warnings)
        )

    def test_normal_bump_displacement_and_branch_removal(self) -> None:
        image = self.image()
        result = self.semantic(
            textures=[
                {"channel": channel, "image": image.name}
                for channel in ["normal", "bump", "displacement"]
            ],
            settings={"displacement": "both"},
        )
        self.assertTrue(result.graph.displacement_connected)
        tree = bpy.data.materials["Test"].node_tree
        self.assertEqual(
            tree.nodes["bump"].inputs["Normal"].links[0].from_node.name, "normal"
        )
        result = self.semantic(
            mode="update",
            remove_textures=["bump", "displacement"],
            settings={"displacement": "bump"},
        )
        self.assertFalse(result.graph.displacement_connected)
        self.assertNotIn("bump", result.graph.features)
        self.assertEqual(bpy.data.images[image.name], image)

    def test_tangent_normal_requires_uv(self) -> None:
        self.image()
        with self.assertRaisesRegex(adapter.OperationError, "flat UV"):
            self.semantic(
                coordinates={"source": "generated"},
                textures=[{"channel": "normal", "image": "TestImage"}],
            )
        self.assertNotIn("Test", bpy.data.materials)

    def test_shared_update_and_independent_copy(self) -> None:
        a, b = self.mesh("A"), self.mesh("B")
        self.image()
        first = self.semantic(
            textures=[{"channel": "roughness", "image": "TestImage"}],
            assignments=[{"object_name": "A"}, {"object_name": "B"}],
        )
        original = a.active_material
        with self.assertRaisesRegex(adapter.OperationError, "affect_shared"):
            self.semantic(mode="update", parameters={"metallic": 1})
        self.assertEqual(a.active_material, original)
        copied = self.backend.material_copy(
            models.MaterialCopyArguments(source="Test", name="Variant")
        )
        self.assertEqual(copied.graph.fingerprint, first.graph.fingerprint)
        self.backend.material_author(
            models.MaterialAuthorArguments(
                name="Variant", mode="update", parameters={"metallic": 1}
            )
        )
        self.assertEqual(author.fingerprint(original)[0], first.graph.fingerprint)
        self.semantic(mode="update", affect_shared=True, parameters={"metallic": 0.5})
        self.assertEqual(a.active_material, b.active_material)
        self.assertEqual(a.active_material.name, "Test")
        self.assertEqual(len(bpy.data.images.get("TestImage").packed_files), 1)

    def test_shared_geometry_counts_as_shared_material(self) -> None:
        a = self.mesh("A")
        self.semantic()
        a.data.materials.append(bpy.data.materials["Test"])
        b = bpy.data.objects.new("B", a.data)
        bpy.context.collection.objects.link(b)
        with self.assertRaisesRegex(adapter.OperationError, "affect_shared"):
            self.semantic(mode="update", parameters={"roughness": 0.2})

    def test_external_edit_stale_fingerprint_and_recovery(self) -> None:
        result = self.semantic()
        bpy.data.materials["Test"].node_tree.nodes["surface"].inputs[
            "Roughness"
        ].default_value = 0.25
        self.assertEqual(
            author.inspect(bpy.data.materials["Test"]).ownership, "modified"
        )
        with self.assertRaisesRegex(adapter.OperationError, "fingerprint"):
            self.semantic(mode="update", expected_fingerprint=result.graph.fingerprint)
        with self.assertRaisesRegex(adapter.OperationError, "unchanged semantic"):
            self.semantic(mode="update")
        repaired = self.graph(
            mode="patch",
            nodes=[
                {
                    "id": "surface",
                    "type": "principled",
                    "parameters": {"roughness": 0.4},
                }
            ],
        )
        self.assertEqual(repaired.graph.ownership, "declarative")

    def test_patch_preserves_untargeted_nodes_and_small_values(self) -> None:
        first = self.graph(**self.basic_graph())
        changed = self.graph(
            mode="patch",
            nodes=[
                {"id": "p", "type": "principled", "parameters": {"roughness": 0.25}}
            ],
        )
        self.assertEqual(changed.graph.node_count, 2)
        self.assertNotEqual(first.graph.fingerprint, changed.graph.fingerprint)
        self.assertEqual(
            bpy.data.materials["Test"].node_tree.nodes["out"].type, "OUTPUT_MATERIAL"
        )

    def test_bad_link_cycle_missing_image_leave_original(self) -> None:
        original = self.graph(**self.basic_graph()).graph.fingerprint
        cases: list[dict[str, Any]] = [
            {
                "links": [
                    {
                        "source": {"node": "p", "socket": "bsdf"},
                        "target": {"node": "p", "socket": "roughness"},
                    }
                ]
            },
            {
                "nodes": [{"id": "m", "type": "math"}],
                "links": [
                    {
                        "source": {"node": "m", "socket": "value"},
                        "target": {"node": "m", "socket": "a"},
                    }
                ],
            },
            {
                "nodes": [
                    {
                        "id": "img",
                        "type": "image_texture",
                        "image": "Missing",
                        "color_space": "sRGB",
                    }
                ]
            },
            {"remove_nodes": ["out"]},
            {"nodes": [{"id": "p", "type": "math"}]},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(adapter.OperationError):
                self.graph(mode="patch", **values)
            self.assertEqual(
                author.fingerprint(bpy.data.materials["Test"])[0], original
            )
            self.assertEqual(len(bpy.data.materials), 1)

    def test_unowned_replace_requires_explicit_intent(self) -> None:
        material = bpy.data.materials.new("Test")
        material.node_tree.nodes.new("ShaderNodeTexVoronoi")
        signature = author.fingerprint(material)[0]
        with self.assertRaisesRegex(adapter.OperationError, "replace_unowned"):
            self.graph(
                mode="replace", expected_fingerprint=signature, **self.basic_graph()
            )
        result = self.graph(
            mode="replace",
            replace_unowned=True,
            expected_fingerprint=signature,
            **self.basic_graph(),
        )
        self.assertEqual(result.graph.node_count, 2)

    def test_publication_failure_restores_resource_identity_and_slots(self) -> None:
        a = self.mesh("A")
        self.semantic(assignments=[{"object_name": "A"}])
        original = a.active_material
        signature = author.fingerprint(original)[0]
        publish = author.publish

        def fail_after_publish(*args: Any) -> None:
            publish(*args)
            raise RuntimeError("injected publication failure")

        with patch.object(author, "publish", side_effect=fail_after_publish):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.semantic(mode="update", parameters={"roughness": 0.1})
        self.assertEqual(a.active_material, original)
        self.assertEqual(original.name, "Test")
        self.assertEqual(author.fingerprint(original)[0], signature)
        self.assertEqual(len(bpy.data.materials), 1)

    def test_failed_assignment_restores_graph_slots_and_face_indices(self) -> None:
        a = self.mesh("A")
        a.data.polygons[0].material_index = 7
        self.semantic()
        original = bpy.data.materials["Test"]
        signature = author.fingerprint(original)[0]
        with self.assertRaises(adapter.OperationError):
            self.semantic(
                mode="update",
                parameters={"metallic": 1},
                assignments=[
                    {"object_name": "A"},
                    {"object_name": "A", "slot_index": 4},
                ],
            )
        self.assertEqual(len(a.material_slots), 0)
        self.assertEqual(a.data.polygons[0].material_index, 7)
        self.assertEqual(bpy.data.materials["Test"], original)
        self.assertEqual(author.fingerprint(original)[0], signature)

    def test_batch_failure_removes_geometry_copy(self) -> None:
        a = self.mesh("A")
        b = bpy.data.objects.new("B", a.data)
        bpy.context.collection.objects.link(b)
        self.semantic()
        original = a.data
        count = len(bpy.data.meshes)
        with self.assertRaises(adapter.OperationError):
            self.backend.material_assign_batch(
                models.AssignBatchArguments(
                    material_name="Test",
                    assignments=[
                        {"object_name": "A"},
                        {"object_name": "B", "slot_index": 4},
                    ],
                )
            )
        self.assertEqual(a.data, original)
        self.assertEqual(len(a.material_slots), 0)
        self.assertEqual(len(bpy.data.meshes), count)

    def test_remove_preserves_shared_image_and_refuses_used_material(self) -> None:
        image = self.image()
        self.mesh("A")
        self.semantic(
            textures=[{"channel": "roughness", "image": image.name}],
            assignments=[{"object_name": "A"}],
        )
        with self.assertRaisesRegex(adapter.OperationError, "users"):
            self.backend.material_remove(models.MaterialRemoveArguments(name="Test"))
        self.backend.material_copy(
            models.MaterialCopyArguments(source="Test", name="Copy")
        )
        self.backend.material_remove(models.MaterialRemoveArguments(name="Copy"))
        self.assertEqual(bpy.data.images[image.name], image)
        self.assertIsNotNone(image.packed_file)

    def test_existing_group_copy_and_cleanup_preserve_shared_group(self) -> None:
        material = bpy.data.materials.new("Test")
        group = bpy.data.node_groups.new("SharedShader", "ShaderNodeTree")
        node = material.node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        copied = self.backend.material_copy(
            models.MaterialCopyArguments(source="Test", name="Copy")
        )
        self.assertFalse(copied.graph.fingerprint_complete)
        self.assertEqual(group.users, 2)
        self.backend.material_remove(models.MaterialRemoveArguments(name="Copy"))
        self.assertEqual(group.users, 1)
        self.assertEqual(node.node_tree, group)

    def test_linked_and_animated_material_rejected(self) -> None:
        self.semantic()
        material = bpy.data.materials["Test"]
        material.node_tree.animation_data_create()
        with self.assertRaisesRegex(adapter.OperationError, "Animated"):
            self.semantic(mode="update")
        material.node_tree.animation_data_clear()
        filepath = str(
            Path(os.environ["TYVRANA_TEST_CONTROL"]) / "material-library.blend"
        )
        bpy.data.libraries.write(filepath, {material})
        bpy.data.materials.remove(material)
        with bpy.data.libraries.load(filepath, link=True) as (source, target):
            target.materials = source.materials
        with self.assertRaisesRegex(adapter.OperationError, "local editable"):
            self.semantic(mode="update")

    def test_missing_uv_rejects_assignment_without_publishing(self) -> None:
        obj = self.mesh("A")
        obj.data.uv_layers.remove(obj.data.uv_layers[0])
        self.image()
        with self.assertRaisesRegex(adapter.OperationError, "required UV"):
            self.semantic(
                textures=[{"channel": "normal", "image": "TestImage"}],
                assignments=[{"object_name": "A"}],
            )
        self.assertNotIn("Test", bpy.data.materials)
        self.assertEqual(len(obj.material_slots), 0)

    def test_all_supported_node_families_and_dynamic_sockets(self) -> None:
        self.image()
        graph = self.basic_graph()
        for kind in author.NODES:
            if kind in {"principled", "output"}:
                continue
            node: dict[str, Any] = {"id": kind, "type": kind}
            if kind == "image_texture":
                node.update(image="TestImage", color_space="Non-Color")
            graph["nodes"].append(node)
        result = self.graph(**graph)
        self.assertEqual(result.graph.node_count, len(author.NODES))
        tree = bpy.data.materials["Test"].node_tree
        settings = adapter.shader.settings
        self.assertEqual(settings(tree.nodes["noise"]).noise_type, "fbm")
        self.assertEqual(settings(tree.nodes["mix_color"]).data_type, "rgba")
        self.assertEqual(settings(tree.nodes["math"]).operation, "add")
        self.assertEqual(settings(tree.nodes["tangent"]).direction, "radial")
        self.assertEqual(settings(tree.nodes["normal_map"]).convention, "opengl")
        self.assertEqual(settings(tree.nodes["color_ramp"]).stop_count, 2)
        with self.assertRaisesRegex(adapter.OperationError, "unavailable"):
            self.graph(
                mode="patch",
                nodes=[
                    {
                        "id": "vector_math",
                        "type": "vector_math",
                        "operation": "normalize",
                        "b": [1, 0, 0],
                    }
                ],
            )
