"""Image and graph checks against native Blender datablocks and sockets."""

import importlib
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
shader = importlib.import_module("bl_ext.user_default.tyvrana_blender.shader")


class ImageStartupTests(unittest.TestCase):
    def test_render_and_compositor_buffers_have_no_input_colorspace(self) -> None:
        backend = adapter.BlenderBackend()
        viewers = [image for image in bpy.data.images if image.source == "VIEWER"]
        self.assertGreaterEqual(len(viewers), 2)
        for image in viewers:
            self.assertIsNone(adapter.image_summary(image).color_space)
            result = operations.execute(
                backend,
                OperationRequest(
                    type="operation.request",
                    request_id="viewer",
                    operation="blender.image.configure",
                    arguments={"name": image.name, "color_space": "sRGB"},
                ),
            )
            self.assertIsInstance(result, OperationFailure)
            self.assertEqual(result.error.code, "invalid_context")


class ShaderTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for collection in (bpy.data.objects, bpy.data.materials, bpy.data.images):
            for resource in list(collection):
                collection.remove(resource, do_unlink=True)
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend(adapter._runtime.worker.spool)

    def call(self, operation: str, **arguments: Any) -> Any:
        response = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="shader-test",
                operation="blender." + operation,
                arguments=arguments,
            ),
        )
        self.assertIsInstance(response, OperationSuccess, str(response))
        return response.result

    def error(self, operation: str, code: str, **arguments: Any) -> None:
        response = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="shader-error",
                operation="blender." + operation,
                arguments=arguments,
            ),
        )
        self.assertIsInstance(response, OperationFailure)
        self.assertEqual(response.error.code, code)

    def material(self) -> Any:
        self.call("material.create_principled", name="Material")
        return bpy.data.materials["Material"].node_tree

    def node(self, kind: str, name: str, **fields: Any) -> Any:
        return self.call(
            "shader.node.create",
            material_name="Material",
            node_type=kind,
            name=name,
            **fields,
        )

    def connect(
        self,
        source: str,
        source_socket: str,
        target: str,
        target_socket: str,
        **fields: Any,
    ) -> Any:
        return self.call(
            "shader.connect",
            material_name="Material",
            from_node=source,
            from_socket=source_socket,
            to_node=target,
            to_socket=target_socket,
            **fields,
        )

    def graph(self) -> Any:
        return self.call("shader.inspect", material_name="Material")

    def test_generated_types_dimensions_alpha_and_precision(self) -> None:
        self.assertEqual(self.call("image.inspect")["images"], [])
        for kind in ("blank", "uv_grid", "color_grid"):
            for alpha in (False, True):
                for floating in (False, True):
                    result = self.call(
                        "image.create_generated",
                        width=8,
                        height=4,
                        generated_type=kind,
                        alpha=alpha,
                        float_buffer=floating,
                    )
                    self.assertEqual((result["width"], result["height"]), (8, 4))
                    self.assertEqual(result["source"], "generated")
                    self.assertEqual(result["generated_type"], kind)
                    self.assertEqual(result["has_alpha"], alpha)
                    self.assertEqual(result["is_float"], floating)
                    self.assertEqual(result["users"], 0)
        names = [item["name"] for item in self.call("image.inspect")["images"]]
        self.assertEqual(names, sorted(names))

    def test_fill_gamma_encoding_and_byte_quantization(self) -> None:
        for floating in (False, True):
            result = self.call(
                "image.create_generated",
                width=2,
                height=2,
                color=[0.5, 0.25, 0, 0.75],
                float_buffer=floating,
            )
            image = bpy.data.images[result["name"]]
            expected = 0.214041 if floating else 128 / 255
            self.assertAlmostEqual(image.pixels[0], expected, places=5)
            self.assertAlmostEqual(
                image.pixels[3], 0.75 if floating else 191 / 255, places=5
            )

    def test_colorspaces_and_alpha_metadata(self) -> None:
        self.call(
            "image.create_generated",
            name="Image",
            width=4,
            height=4,
            color_space="sRGB",
        )
        image = bpy.data.images["Image"]
        for space in ("Non-Color", "ACEScg", "sRGB"):
            result = self.call("image.configure", name="Image", color_space=space)
            self.assertEqual(result["color_space"], space)
        pixels = list(image.pixels)
        for mode in ("premultiplied", "channel_packed", "none", "straight"):
            result = self.call("image.configure", name="Image", alpha_mode=mode)
            self.assertEqual(result["alpha_mode"], mode)
            self.assertEqual(list(image.pixels), pixels)
        self.assertEqual(self.call("image.configure", name="Image"), result)

    def test_image_errors_preserve_resources(self) -> None:
        self.call("image.create_generated", name="Image", width=4, height=4)
        before = self.call("image.inspect")
        for arguments in (
            {"name": "Image"},
            {"name": "x" * 300},
            {"color_space": "Unavailable"},
        ):
            self.error(
                "image.create_generated",
                "invalid_arguments",
                width=4,
                height=4,
                **arguments,
            )
        self.error("image.configure", "image_not_found", name="Missing")
        self.error(
            "image.configure",
            "invalid_arguments",
            name="Image",
            color_space="Unavailable",
            alpha_mode="none",
        )
        self.assertEqual(self.call("image.inspect"), before)

    def test_dirty_pixel_edits_protected_from_metadata_reload(self) -> None:
        self.call("image.create_generated", name="Image", width=4, height=4)
        image = bpy.data.images["Image"]
        image.pixels[0:4] = [0.6, 0.2, 0.4, 0.5]
        image.update()
        before = list(image.pixels)
        self.assertTrue(self.call("image.inspect")["images"][0]["dirty"])
        self.error(
            "image.configure",
            "invalid_context",
            name="Image",
            color_space="Non-Color",
            alpha_mode="none",
        )
        self.assertEqual(list(image.pixels), before)
        self.call("image.configure", name="Image", color_space="sRGB")
        self.call("image.configure", name="Image", alpha_mode="none")
        self.assertEqual(list(image.pixels), before)
        self.assertTrue(image.is_dirty)

    def test_image_failure_rollbacks(self) -> None:
        self.call("image.create_generated", name="Image", width=4, height=4)
        before = self.call("image.inspect")
        with patch.object(
            adapter, "image_summary", side_effect=RuntimeError("injected")
        ):
            with self.assertLogs(operations.logger, level="ERROR"):
                self.error(
                    "image.configure",
                    "operation_failed",
                    name="Image",
                    color_space="Non-Color",
                    alpha_mode="none",
                )
                self.error(
                    "image.create_generated",
                    "operation_failed",
                    name="Failed",
                    width=4,
                    height=4,
                )
        self.assertEqual(self.call("image.inspect"), before)

    def test_file_image_summary_and_dirty_alpha_guard(self) -> None:
        self.call("image.create_generated", name="Image", width=4, height=4)
        original = bpy.data.images["Image"]
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "texture.png"
        original.filepath_raw = str(path)
        original.file_format = "PNG"
        original.save()
        loaded = bpy.data.images.load(str(path), check_existing=False)
        result = adapter.image_summary(loaded).model_dump(mode="json")
        self.assertEqual(result["source"], "file")
        self.assertEqual(result["width"], 4)
        self.assertTrue(result["has_alpha"])
        self.assertNotIn(str(path), str(result))
        loaded.pixels[0:4] = [0.6, 0.2, 0.4, 0.5]
        loaded.update()
        pixels = list(loaded.pixels)
        self.error(
            "image.configure", "invalid_context", name=loaded.name, alpha_mode="none"
        )
        self.assertEqual(list(loaded.pixels), pixels)

    def test_inspect_canonical_and_empty_graph(self) -> None:
        tree = self.material()
        graph = self.graph()
        self.assertTrue(graph["node_tree_present"])
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["links"]), 1)
        self.assertEqual(
            [n["node_name"] for n in graph["nodes"]], sorted(n.name for n in tree.nodes)
        )
        tree.nodes.clear()
        self.assertEqual(self.graph()["nodes"], [])
        self.error("shader.inspect", "material_not_found", material_name="Missing")

    def test_all_auxiliary_nodes_and_settings(self) -> None:
        self.material()
        self.call("image.create_generated", name="Image", width=4, height=4)
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "image_texture",
                {
                    "image_name": "Image",
                    "interpolation": "closest",
                    "projection": "box",
                    "extension": "mirror",
                },
            ),
            ("texture_coordinate", {"from_instancer": True}),
            (
                "mapping",
                {
                    "location": [1, 2, 3],
                    "rotation": [0, 0, 1],
                    "scale": [2, 3, 4],
                    "vector_type": "texture",
                },
            ),
            ("normal_map", {"strength": 0.5, "space": "tangent", "uv_map": "UVMap"}),
            ("bump", {"strength": 0.5, "distance": 2, "invert": True}),
        ]
        for kind, fields in cases:
            node = self.node(kind, kind, **fields)
            for field, value in fields.items():
                self.assertEqual(node["settings"][field], value)
            unchanged = self.call(
                "shader.node.configure", material_name="Material", node_name=kind
            )
            self.assertEqual(node, unchanged)
        self.assertEqual(len(self.graph()["links"]), 1)
        self.assertEqual(self.call("image.inspect")["images"][0]["users"], 1)

    def test_every_image_node_enum(self) -> None:
        self.material()
        self.node("image_texture", "Image")
        for field, values in {
            "interpolation": ["linear", "closest", "cubic", "smart"],
            "projection": ["flat", "box", "sphere", "tube"],
            "extension": ["repeat", "extend", "clip", "mirror"],
        }.items():
            for value in values:
                result = self.call(
                    "shader.node.configure",
                    material_name="Material",
                    node_name="Image",
                    **{field: value},
                )
                self.assertEqual(result["settings"][field], value)

    def test_mapping_modes_and_inapplicable_location(self) -> None:
        self.material()
        self.node("mapping", "Mapping")
        for mode in ("point", "texture", "vector", "normal"):
            result = self.call(
                "shader.node.configure",
                material_name="Material",
                node_name="Mapping",
                vector_type=mode,
            )
            self.assertEqual(result["settings"]["vector_type"], mode)
        before = self.graph()
        self.error(
            "shader.node.configure",
            "invalid_arguments",
            material_name="Material",
            node_name="Mapping",
            location=[1, 2, 3],
        )
        self.assertEqual(self.graph(), before)

    def test_normal_spaces_and_socket_storage(self) -> None:
        self.material()
        self.node("normal_map", "Normal")
        self.node("bump", "Bump")
        for mode in ("tangent", "object", "world"):
            result = self.call(
                "shader.node.configure",
                material_name="Material",
                node_name="Normal",
                space=mode,
                strength=-2,
            )
            self.assertEqual(result["settings"]["space"], mode)
            self.assertEqual(result["settings"]["strength"], -2)
        self.error(
            "shader.node.configure",
            "invalid_arguments",
            material_name="Material",
            node_name="Normal",
            uv_map="UVMap",
        )
        result = self.call(
            "shader.node.configure",
            material_name="Material",
            node_name="Bump",
            strength=2,
            distance=-1,
        )
        self.assertEqual(result["settings"]["distance"], -1)

    def test_explicit_uv_pipeline_and_material_graph_boundary(self) -> None:
        self.material()
        self.call(
            "image.create_generated",
            name="Image",
            width=64,
            height=64,
            generated_type="uv_grid",
        )
        self.node("texture_coordinate", "Coordinates")
        self.node("mapping", "Mapping")
        self.node("image_texture", "Texture", image_name="Image")
        self.connect("Coordinates", "UV", "Mapping", "Vector")
        self.connect("Mapping", "Vector", "Texture", "Vector")
        self.connect("Texture", "Color", "Principled BSDF", "Base Color")
        self.assertEqual(len(self.graph()["links"]), 4)
        self.assertEqual(
            self.call("material.inspect")["materials"][0]["surface"], "custom"
        )
        self.error(
            "material.configure_principled",
            "unsupported_material_graph",
            name="Material",
            roughness=0.5,
        )
        result = self.call(
            "shader.disconnect",
            material_name="Material",
            to_node="Principled BSDF",
            to_socket="Base Color",
        )
        self.assertEqual(result["removed"], 1)
        self.assertEqual(
            self.call(
                "shader.disconnect",
                material_name="Material",
                to_node="Principled BSDF",
                to_socket="Base Color",
            )["removed"],
            0,
        )
        self.assertEqual(
            self.call("material.inspect")["materials"][0]["surface"], "principled"
        )

    def test_connection_replacement_is_explicit(self) -> None:
        self.material()
        self.node("texture_coordinate", "Coordinates")
        self.node("image_texture", "Texture")
        self.connect("Coordinates", "UV", "Texture", "Vector")
        before = self.graph()
        self.error(
            "shader.connect",
            "socket_already_connected",
            material_name="Material",
            from_node="Coordinates",
            from_socket="Generated",
            to_node="Texture",
            to_socket="Vector",
        )
        self.assertEqual(self.graph(), before)
        self.connect(
            "Coordinates", "Generated", "Texture", "Vector", replace_existing=True
        )
        target = (
            bpy.data.materials["Material"].node_tree.nodes["Texture"].inputs["Vector"]
        )
        self.assertEqual(target.links[0].from_socket.identifier, "Generated")

    def test_bad_nodes_sockets_directions_and_compatibility(self) -> None:
        self.material()
        self.node("image_texture", "Texture")
        cases = [
            ("Missing", "Color", "Texture", "Vector", "node_not_found"),
            ("Texture", "Missing", "Principled BSDF", "Base Color", "socket_not_found"),
            ("Texture", "Vector", "Principled BSDF", "Base Color", "socket_not_found"),
            ("Texture", "Color", "Texture", "Alpha", "socket_not_found"),
            ("Principled BSDF", "BSDF", "Texture", "Vector", "invalid_arguments"),
            (
                "Texture",
                "Color",
                "Material Output",
                "Displacement",
                "invalid_arguments",
            ),
            ("Texture", "Color", "Principled BSDF", "Weight", "invalid_arguments"),
        ]
        before = self.graph()
        for source, output, target, input_name, code in cases:
            self.error(
                "shader.connect",
                code,
                material_name="Material",
                from_node=source,
                from_socket=output,
                to_node=target,
                to_socket=input_name,
            )
        self.assertEqual(self.graph(), before)

    def test_cycles_rejected_without_mutation(self) -> None:
        self.material()
        self.node("mapping", "A")
        self.node("mapping", "B")
        self.connect("A", "Vector", "B", "Vector")
        before = self.graph()
        self.error(
            "shader.connect",
            "invalid_arguments",
            material_name="Material",
            from_node="B",
            from_socket="Vector",
            to_node="A",
            to_socket="Vector",
        )
        self.assertEqual(self.graph(), before)

    def test_node_defaults_cannot_override_connected_input(self) -> None:
        self.material()
        self.node("texture_coordinate", "Coordinates")
        self.node("mapping", "Mapping")
        self.connect("Coordinates", "UV", "Mapping", "Scale")
        before = self.graph()
        self.error(
            "shader.node.configure",
            "invalid_context",
            material_name="Material",
            node_name="Mapping",
            scale=[2, 2, 2],
        )
        self.assertEqual(self.graph(), before)

    def test_custom_nodes_labels_and_graph_preserved(self) -> None:
        tree = self.material()
        unknown = tree.nodes.new("ShaderNodeTexNoise")
        unknown.label = "Custom artist work"
        self.node("image_texture", "Texture")
        texture = tree.nodes["Texture"]
        texture.label = "Arbitrary label"
        texture.outputs["Color"].name = "Renamed display socket"
        self.connect("Texture", "Color", "Principled BSDF", "Base Color")
        self.assertEqual(unknown.label, "Custom artist work")
        self.error(
            "shader.node.configure",
            "node_type_unsupported",
            material_name="Material",
            node_name=unknown.name,
            strength=1,
        )
        self.error(
            "shader.node.delete",
            "node_type_unsupported",
            material_name="Material",
            node_name=unknown.name,
        )
        self.assertIsNotNone(tree.nodes.get(unknown.name))

    def test_auxiliary_delete_removes_only_its_links(self) -> None:
        tree = self.material()
        self.node("image_texture", "Texture")
        self.connect("Texture", "Color", "Principled BSDF", "Base Color")
        self.call("shader.node.delete", material_name="Material", node_name="Texture")
        self.assertEqual(len(tree.nodes), 2)
        self.assertEqual(len(tree.links), 1)
        for name in ("Principled BSDF", "Material Output"):
            self.error(
                "shader.node.delete",
                "node_protected",
                material_name="Material",
                node_name=name,
            )
        self.error(
            "shader.node.delete",
            "node_not_found",
            material_name="Material",
            node_name="Missing",
        )

    def test_bad_creation_rolls_back_and_preserves_selection(self) -> None:
        tree = self.material()
        before = self.graph()
        active = tree.nodes.active
        for fields, code in [
            ({"name": "Principled BSDF"}, "invalid_arguments"),
            ({"name": "x" * 300}, "invalid_arguments"),
            ({"image_name": "Missing"}, "image_not_found"),
        ]:
            self.error(
                "shader.node.create",
                code,
                material_name="Material",
                node_type="image_texture",
                **fields,
            )
            self.assertEqual(self.graph(), before)
            self.assertEqual(tree.nodes.active, active)

    def test_node_configuration_and_creation_rollback(self) -> None:
        self.material()
        self.node("mapping", "Mapping")
        before = self.graph()
        with patch.object(shader, "node_summary", side_effect=RuntimeError("injected")):
            with self.assertLogs(operations.logger, level="ERROR"):
                self.error(
                    "shader.node.configure",
                    "operation_failed",
                    material_name="Material",
                    node_name="Mapping",
                    location=[1, 2, 3],
                    scale=[4, 5, 6],
                )
                self.error(
                    "shader.node.create",
                    "operation_failed",
                    material_name="Material",
                    node_type="bump",
                )
        self.assertEqual(self.graph(), before)

    def test_replacement_rollback_restores_muted_link(self) -> None:
        tree = self.material()
        self.node("texture_coordinate", "Coordinates")
        self.node("image_texture", "Texture")
        self.connect("Coordinates", "UV", "Texture", "Vector")
        tree.nodes["Texture"].inputs["Vector"].links[0].is_muted = True
        before = self.graph()
        with patch.object(shader, "link_summary", side_effect=RuntimeError("injected")):
            with self.assertLogs(operations.logger, level="ERROR"):
                self.error(
                    "shader.connect",
                    "operation_failed",
                    material_name="Material",
                    from_node="Coordinates",
                    from_socket="Generated",
                    to_node="Texture",
                    to_socket="Vector",
                    replace_existing=True,
                )
        self.assertEqual(self.graph(), before)

    def test_linked_resources_inspect_and_reject_mutation(self) -> None:
        self.material()
        self.call("image.create_generated", name="Image", width=4, height=4)
        self.node("image_texture", "Texture", image_name="Image")
        material = bpy.data.materials["Material"]
        image = bpy.data.images["Image"]
        path = Path(os.environ["TYVRANA_TEST_CONTROL"]) / "shader-library.blend"
        bpy.data.libraries.write(str(path), {material, image})
        bpy.data.materials.remove(material)
        bpy.data.images.remove(image)
        with bpy.data.libraries.load(str(path), link=True) as (source, target):
            target.materials = ["Material"]
            target.images = ["Image"]
        before = self.graph()
        self.assertEqual(self.call("image.inspect")["images"][0]["name"], "Image")
        self.error(
            "image.configure", "invalid_context", name="Image", color_space="Non-Color"
        )
        self.error(
            "shader.node.configure",
            "invalid_context",
            material_name="Material",
            node_name="Texture",
            interpolation="closest",
        )
        self.error(
            "shader.node.create",
            "invalid_context",
            material_name="Material",
            node_type="bump",
        )
        self.error(
            "shader.disconnect",
            "invalid_context",
            material_name="Material",
            to_node="Material Output",
            to_socket="Surface",
        )
        self.assertEqual(self.graph(), before)

    def test_normal_and_bump_links(self) -> None:
        self.material()
        self.node("image_texture", "Texture")
        self.node("normal_map", "Normal")
        self.node("bump", "Bump")
        self.connect("Texture", "Color", "Normal", "Color")
        self.connect("Normal", "Normal", "Bump", "Normal")
        self.connect("Texture", "Color", "Bump", "Height")
        self.connect("Bump", "Normal", "Principled BSDF", "Normal")
        self.assertEqual(len(self.graph()["links"]), 5)

    def test_nonfinite_custom_defaults_are_json_safe(self) -> None:
        tree = self.material()
        self.node("mapping", "Mapping")
        tree.nodes["Mapping"].inputs["Scale"].default_value = [float("nan"), 1, 1]
        result = self.call(
            "shader.inspect", material_name="Material", include_sockets=True
        )
        mapping = next(
            node for node in result["nodes"] if node["node_name"] == "Mapping"
        )
        self.assertIsNone(mapping["settings"])
        scale = next(
            item for item in mapping["inputs"] if item["identifier"] == "Scale"
        )
        self.assertIsNone(scale["default_value"])
