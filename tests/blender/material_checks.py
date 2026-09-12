"""Material tests run in real Blender with its installed extension."""

import importlib
import os
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import (
    JsonValue,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
)

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
models = importlib.import_module("bl_ext.user_default.tyvrana_blender.material_models")
objects = importlib.import_module("bl_ext.user_default.tyvrana_blender.models")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
numeric = importlib.import_module("bl_ext.user_default.tyvrana_blender.numeric")


class MaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material, do_unlink=True)
        adapter.register()
        adapter.pump()
        self.backend = adapter.BlenderBackend(adapter._runtime.worker.spool)

    def call(
        self, operation: str, arguments: JsonValue
    ) -> OperationSuccess | OperationFailure:
        result = operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="material-test",
                operation="blender.material." + operation,
                arguments=arguments,
            ),
        )
        assert isinstance(result, OperationSuccess | OperationFailure)
        return result

    def failure(self, operation: str, arguments: JsonValue, code: str) -> None:
        result = self.call(operation, arguments)
        self.assertIsInstance(result, OperationFailure)
        assert isinstance(result, OperationFailure)
        self.assertEqual(result.error.code, code)

    def create(self, name: str = "Material", **fields: Any) -> Any:
        return self.backend.material_create(
            models.MaterialCreateArguments(name=name, **fields)
        )

    def configure(self, name: str = "Material", /, **fields: Any) -> Any:
        return self.backend.material_configure(
            models.MaterialConfigureArguments(name=name, **fields)
        )

    def mesh(self, name: str = "Body") -> Any:
        self.backend.create(objects.CreateArguments(primitive="cube", name=name))
        return bpy.data.objects[name]

    def assign(
        self, name: str = "Body", material: str = "Material", index: int = 0
    ) -> Any:
        return self.backend.material_assign(
            models.MaterialAssignArguments(
                object_name=name, material_name=material, slot_index=index
            )
        )

    def graph(self) -> tuple[Any, Any, Any]:
        tree = bpy.data.materials["Material"].node_tree
        return (
            tree,
            next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"),
            next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"),
        )

    def test_empty_and_native_defaults(self) -> None:
        self.assertEqual(
            self.backend.material_inspect().model_dump(), {"materials": []}
        )
        created = self.create()
        tree, shader, output = self.graph()
        self.assertEqual(len(tree.nodes), 2)
        self.assertEqual(len(tree.links), 1)
        self.assertEqual(output.inputs["Surface"].links[0].from_node, shader)
        self.assertEqual(created.surface, "principled")
        self.assertEqual(created.assignments, [])
        self.assertEqual(bpy.data.materials["Material"].users, 0)
        self.assertEqual(created.principled, adapter.principled_values(shader))
        self.assertEqual(
            created.principled.model_dump(),
            {
                "base_color": [numeric.binary32(0.8)] * 3,
                "metallic": 0,
                "roughness": 0.5,
                "ior": 1.5,
                "alpha": 1,
                "subsurface_weight": 0,
                "subsurface_radius": [1, numeric.binary32(0.2), numeric.binary32(0.1)],
                "subsurface_scale": numeric.binary32(0.005),
                "transmission_weight": 0,
                "coat_weight": 0,
                "coat_roughness": numeric.binary32(0.03),
                "emission_color": [1, 1, 1],
                "emission_strength": 0,
            },
        )

    def test_every_field_and_partial_update(self) -> None:
        before = self.create()
        values = {
            "base_color": [0.2, 0.4, 0.6],
            "metallic": 0.2,
            "roughness": 0.8,
            "ior": 1.4,
            "alpha": 0.75,
            "subsurface_weight": 0.3,
            "subsurface_radius": [1.2, 0.5, 0.25],
            "subsurface_scale": 0.01,
            "transmission_weight": 0.4,
            "coat_weight": 0.6,
            "coat_roughness": 0.2,
            "emission_color": [2, 1, 0],
            "emission_strength": 3,
        }
        changed = self.configure(**values)
        expected = models.PrincipledSummary.model_validate(values)
        self.assertEqual(changed.principled, expected)
        self.assertNotEqual(changed.principled, before.principled)
        partial = self.configure(roughness=0.5)
        self.assertEqual(
            partial.principled.model_dump(exclude={"roughness"}),
            expected.model_dump(exclude={"roughness"}),
        )
        self.assertEqual(self.configure(), partial)
        self.assertEqual(len(self.graph()[0].nodes), 2)

    def test_dynamic_hard_limits_and_precision(self) -> None:
        self.create()
        maximum = numeric.FLOAT32_MAX
        for field in models.PRINCIPLED_SOCKETS:
            values = (
                [0, maximum, 2]
                if field in {"base_color", "emission_color"}
                else [-maximum, 101, maximum]
                if field == "subsurface_radius"
                else maximum
            )
            result = self.configure(**{field: values})
            self.assertEqual(getattr(result.principled, field), values)
            if field not in {"base_color", "emission_color", "subsurface_radius"}:
                self.assertEqual(
                    getattr(self.configure(**{field: -maximum}).principled, field),
                    -maximum,
                )
        self.assertEqual(
            self.configure(roughness=0.1).principled.roughness, numeric.binary32(0.1)
        )
        self.assertNotEqual(self.configure(roughness=1e-40).principled.roughness, 0)
        before = self.backend.material_inspect()
        self.failure(
            "configure_principled",
            {"name": "Material", "base_color": [-1, 0, 0]},
            "invalid_arguments",
        )
        self.failure(
            "configure_principled",
            {"name": "Material", "roughness": 1e300},
            "invalid_arguments",
        )
        self.assertEqual(self.backend.material_inspect(), before)

    def test_rgb_preserves_fourth_socket_component_and_alpha_is_separate(self) -> None:
        self.create()
        _, shader, _ = self.graph()
        shader.inputs["Base Color"].default_value = [1, 1, 1, 0.125]
        shader.inputs["Emission Color"].default_value = [1, 1, 1, 0.25]
        changed = self.configure(
            base_color=[2, 3, 4], emission_color=[4, 3, 2], alpha=0.5
        )
        self.assertEqual(
            list(shader.inputs["Base Color"].default_value), [2, 3, 4, 0.125]
        )
        self.assertEqual(
            list(shader.inputs["Emission Color"].default_value), [4, 3, 2, 0.25]
        )
        self.assertEqual(changed.principled.alpha, 0.5)

    def test_explicit_duplicate_and_unstorable_names_are_atomic(self) -> None:
        self.create()
        self.failure("create_principled", {"name": "Material"}, "invalid_arguments")
        self.failure("create_principled", {"name": "A" * 1000}, "invalid_arguments")
        self.failure(
            "create_principled", {"name": "Bad\u0000Name"}, "invalid_arguments"
        )
        self.assertEqual(len(bpy.data.materials), 1)
        first = self.backend.material_create(models.MaterialCreateArguments())
        second = self.backend.material_create(models.MaterialCreateArguments())
        self.assertNotEqual(first.name, second.name)

    def test_empty_graph_and_disconnected_surface(self) -> None:
        self.create()
        tree, shader, output = self.graph()
        tree.links.clear()
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "none")
        tree.nodes.clear()
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "none")
        self.failure(
            "configure_principled",
            {"name": "Material", "roughness": 0.4},
            "unsupported_material_graph",
        )
        self.assertEqual(len(tree.nodes), 0)

    def test_disconnected_nodes_and_labels_do_not_choose_the_wrong_shader(self) -> None:
        self.create()
        tree, shader, output = self.graph()
        shader.name = "Renamed"
        shader.inputs["Roughness"].name = "Label"
        extra = tree.nodes.new("ShaderNodeBsdfPrincipled")
        extra.inputs["Roughness"].default_value = 0.125
        inactive = tree.nodes.new("ShaderNodeOutputMaterial")
        output.is_active_output = True
        self.assertFalse(inactive.is_active_output)
        self.assertEqual(self.configure(roughness=0.75).principled.roughness, 0.75)
        self.assertEqual(extra.inputs["Roughness"].default_value, 0.125)
        self.assertEqual(len(tree.nodes), 4)

    def test_linked_shader_inputs_are_custom_without_graph_rewrite(self) -> None:
        self.create()
        tree, shader, _ = self.graph()
        value = tree.nodes.new("ShaderNodeValue")
        tree.links.new(value.outputs["Value"], shader.inputs["Roughness"])
        before = [
            (link.from_node.name, link.to_node.name, link.to_socket.identifier)
            for link in tree.links
        ]
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "custom")
        self.failure(
            "configure_principled",
            {"name": "Material", "base_color": [1, 0, 0]},
            "unsupported_material_graph",
        )
        self.assertEqual(
            before,
            [
                (link.from_node.name, link.to_node.name, link.to_socket.identifier)
                for link in tree.links
            ],
        )

    def test_mixed_rerouted_and_muted_graphs_are_custom(self) -> None:
        for kind in ("ShaderNodeMixShader", "NodeReroute"):
            with self.subTest(kind=kind):
                if "Material" in bpy.data.materials:
                    bpy.data.materials.remove(bpy.data.materials["Material"])
                self.create()
                tree, shader, output = self.graph()
                intermediate = tree.nodes.new(kind)
                tree.links.new(
                    shader.outputs["BSDF"],
                    next(
                        socket
                        for socket in intermediate.inputs
                        if socket.type == "SHADER"
                    )
                    if kind == "ShaderNodeMixShader"
                    else intermediate.inputs["Input"],
                )
                tree.links.new(
                    intermediate.outputs["Shader"]
                    if kind == "ShaderNodeMixShader"
                    else intermediate.outputs["Output"],
                    output.inputs["Surface"],
                )
                self.assertEqual(
                    self.backend.material_inspect().materials[0].surface, "custom"
                )
                self.failure(
                    "configure_principled",
                    {"name": "Material"},
                    "unsupported_material_graph",
                )
        tree.links.clear()
        tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        shader.mute = True
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "custom")

    def test_engine_specific_outputs_and_volume_links_are_custom(self) -> None:
        self.create()
        tree, shader, output = self.graph()
        second = tree.nodes.new("ShaderNodeOutputMaterial")
        second.target = "CYCLES"
        second.is_active_output = True
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "custom")
        tree.nodes.remove(second)
        output.is_active_output = True
        volume = tree.nodes.new("ShaderNodeVolumePrincipled")
        tree.links.new(volume.outputs["Volume"], output.inputs["Volume"])
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "custom")

    def test_animated_and_nonfinite_materials_inspect_safely(self) -> None:
        self.create()
        tree, shader, _ = self.graph()
        shader.inputs["Roughness"].keyframe_insert("default_value", frame=1)
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "custom")
        self.failure(
            "configure_principled", {"name": "Material"}, "unsupported_material_graph"
        )
        tree.animation_data_clear()
        shader.inputs["Roughness"].default_value = float("nan")
        self.assertEqual(self.backend.material_inspect().materials[0].surface, "custom")
        self.failure(
            "configure_principled", {"name": "Material"}, "unsupported_material_graph"
        )

    def test_failed_configuration_and_creation_roll_back(self) -> None:
        original = self.create()
        with (
            patch.object(
                adapter,
                "material_summary",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.configure(base_color=[0, 1, 0], roughness=0.25, emission_strength=10)
        self.assertEqual(self.backend.material_inspect().materials[0], original)
        actual_apply = adapter.apply_principled_values
        attempts = 0

        def fail_once(node: Any, values: dict[str, JsonValue]) -> None:
            nonlocal attempts
            attempts += 1
            actual_apply(node, values)
            if attempts == 1:
                raise RuntimeError("write failed")

        with (
            patch.object(adapter, "apply_principled_values", side_effect=fail_once),
            self.assertRaises(RuntimeError),
        ):
            self.configure(base_color=[1, 0, 0], alpha=0.25)
        self.assertEqual(self.backend.material_inspect().materials[0], original)
        with (
            patch.object(
                adapter,
                "material_summary",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.create("Failed")
        self.assertEqual(len(bpy.data.materials), 1)

    def test_verification_failure_restores_requested_values(self) -> None:
        before = self.create()
        actual_apply = adapter.apply_principled_values
        attempts = 0

        def altered_once(node: Any, values: dict[str, JsonValue]) -> None:
            nonlocal attempts
            attempts += 1
            actual_apply(node, values)
            if attempts == 1:
                adapter.shader_socket(node.inputs, "Roughness").default_value = 0.125

        with patch.object(adapter, "apply_principled_values", side_effect=altered_once):
            self.failure(
                "configure_principled",
                {"name": "Material", "roughness": 0.75, "base_color": [1, 0, 0]},
                "operation_failed",
            )
        self.assertEqual(self.backend.material_inspect().materials[0], before)

    def test_slots_default_replace_append_and_empty_entries(self) -> None:
        obj = self.mesh()
        self.create("A")
        self.create("B")
        self.assertEqual(self.assign(material="A").slots, ["A"])
        self.assertEqual(self.assign(material="B").slots, ["B"])
        self.assertEqual(self.assign(material="A", index=1).slots, ["B", "A"])
        self.assertEqual(self.assign(material="B", index=1).slots, ["B", "B"])
        obj.data.materials.append(None)
        self.assertEqual(self.assign(material="A", index=0).slots, ["A", "B", None])
        self.failure(
            "assign",
            {"object_name": "Body", "material_name": "A", "slot_index": 4},
            "invalid_arguments",
        )
        self.assertEqual(len(obj.material_slots), 3)
        self.assertTrue(all(p.material_index == 0 for p in obj.data.polygons))

    def test_shared_material_resource_stays_shared_and_assignments_sorted(self) -> None:
        self.mesh("Z")
        self.mesh("A")
        self.create()
        self.assign("Z")
        self.assign("A")
        self.assign("A", index=1)
        changed = self.configure(base_color=[0.2, 0.4, 0.6])
        self.assertEqual(
            [(a.object, a.slot) for a in changed.assignments],
            [("A", 0), ("A", 1), ("Z", 0)],
        )
        self.assertEqual(len(bpy.data.materials), 1)
        self.assertEqual(bpy.data.materials["Material"].users, 3)
        self.assertEqual(
            bpy.data.objects["Z"].material_slots[0].material,
            bpy.data.objects["A"].material_slots[0].material,
        )
        self.backend.delete(objects.DeleteArguments(name="Z"))
        self.assertEqual(
            len(self.backend.material_inspect().materials[0].assignments), 2
        )

    def test_shared_geometry_append_isolated_and_replacement_object_linked(
        self,
    ) -> None:
        obj = self.mesh("A")
        self.create("First")
        self.create("Second")
        obj.data.materials.append(bpy.data.materials["First"])
        other = bpy.data.objects.new("B", obj.data)
        bpy.context.collection.objects.link(other)
        self.assign("A", "Second")
        self.assertEqual(obj.data, other.data)
        self.assertEqual(other.material_slots[0].material.name, "First")
        original = obj.data
        obj.data.polygons[0].material_index = 0
        self.assign("A", "First", index=1)
        self.assertNotEqual(obj.data, original)
        self.assertEqual(other.data, original)
        self.assertEqual(len(other.material_slots), 1)
        self.assertEqual(
            [p.material_index for p in obj.data.polygons],
            [p.material_index for p in original.polygons],
        )
        self.assertEqual(len(bpy.data.materials), 2)

    def test_assignment_failure_restores_slots_and_shared_data(self) -> None:
        obj = self.mesh()
        self.create("A")
        self.create("B")
        self.assign(material="A")
        for index in (0, 1):
            with (
                patch.object(
                    adapter,
                    "assignment_result",
                    side_effect=RuntimeError("summary unavailable"),
                ),
                self.assertRaises(RuntimeError),
            ):
                self.assign(material="B", index=index)
            self.assertEqual([s.material.name for s in obj.material_slots], ["A"])
        other = bpy.data.objects.new("Other", obj.data)
        bpy.context.collection.objects.link(other)
        meshes = len(bpy.data.meshes)
        with (
            patch.object(
                adapter,
                "assignment_result",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.assign(material="B", index=1)
        self.assertEqual(obj.data, other.data)
        self.assertEqual(len(bpy.data.meshes), meshes)
        self.assertEqual([s.material.name for s in obj.material_slots], ["A"])

    def test_material_capable_types_use_real_slots(self) -> None:
        self.create()
        data = [
            bpy.data.meshes.new("Mesh"),
            bpy.data.curves.new("Curve", "CURVE"),
            bpy.data.curves.new("Surface", "SURFACE"),
            bpy.data.curves.new("Text", "FONT"),
            bpy.data.hair_curves.new("Hair"),
            bpy.data.pointclouds.new("Points"),
            bpy.data.volumes.new("Volume"),
        ]
        observed = set()
        for item in data:
            obj = bpy.data.objects.new(item.name, item)
            bpy.context.collection.objects.link(obj)
            observed.add(obj.type)
            self.assertEqual(self.assign(obj.name).slots, ["Material"])
            self.assertEqual(
                self.assign(obj.name, index=1).slots, ["Material", "Material"]
            )
        self.assertEqual(observed, adapter.MATERIAL_OBJECT_TYPES)

    def test_lookup_errors_and_non_capable_objects(self) -> None:
        self.create()
        self.failure("configure_principled", {"name": "Missing"}, "material_not_found")
        self.failure(
            "assign",
            {"object_name": "Missing", "material_name": "Material"},
            "object_not_found",
        )
        self.mesh()
        self.failure(
            "assign",
            {"object_name": "Body", "material_name": "Missing"},
            "material_not_found",
        )
        for item in (
            None,
            bpy.data.cameras.new("Camera"),
            bpy.data.lights.new("Light", "POINT"),
            bpy.data.metaballs.new("Meta"),
            bpy.data.grease_pencils.new("Ink"),
        ):
            obj = bpy.data.objects.new("Incapable", item)
            bpy.context.collection.objects.link(obj)
            self.failure(
                "assign",
                {"object_name": obj.name, "material_name": "Material"},
                "object_not_material_capable",
            )

    def test_linked_materials_readable_assignable_but_not_configurable(self) -> None:
        self.create()
        filepath = str(
            Path(os.environ["TYVRANA_TEST_CONTROL"]) / "linked-material.blend"
        )
        bpy.data.libraries.write(filepath, {bpy.data.materials["Material"]})
        bpy.data.materials.remove(bpy.data.materials["Material"])
        with bpy.data.libraries.load(filepath, link=True) as (source, target):
            target.materials = source.materials
        self.assertEqual(
            self.backend.material_inspect().materials[0].surface, "principled"
        )
        self.failure(
            "configure_principled",
            {"name": "Material", "roughness": 0.3},
            "invalid_context",
        )
        self.mesh()
        self.assign()
        self.assertEqual(
            bpy.data.objects["Body"].material_slots[0].material.library.filepath,
            filepath,
        )
        local = bpy.data.materials.new("Material")
        self.failure("configure_principled", {"name": "Material"}, "invalid_arguments")
        bpy.data.materials.remove(local)

    def test_assignment_in_edit_mode_is_rejected(self) -> None:
        self.create()
        self.mesh()
        bpy.ops.object.mode_set(mode="EDIT")
        self.failure(
            "assign",
            {"object_name": "Body", "material_name": "Material"},
            "invalid_context",
        )
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual(len(bpy.data.objects["Body"].material_slots), 0)

    def test_main_thread_and_cancelled_creation(self) -> None:
        errors = []

        def forbidden() -> None:
            for action in (
                self.backend.material_inspect,
                self.create,
                self.configure,
                self.assign,
            ):
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)

        worker = threading.Thread(target=forbidden)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 4)
        self.assertTrue(all(isinstance(error, RuntimeError) for error in errors))
        queue = adapter._runtime.queue
        queue.submit(
            OperationRequest(
                type="operation.request",
                request_id="cancel-material",
                operation="blender.material.create_principled",
                arguments={},
            )
        )
        queue.cancel("cancel-material")
        queue.drain(lambda response: self.fail("Cancelled creation completed"))
        self.assertEqual(len(bpy.data.materials), 0)

    def test_failed_append_preserves_even_out_of_range_face_indices(self) -> None:
        obj = self.mesh()
        self.create()
        obj.data.polygons[0].material_index = 7
        original = [face.material_index for face in obj.data.polygons]
        with (
            patch.object(
                adapter,
                "assignment_result",
                side_effect=RuntimeError("summary unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.assign()
        self.assertEqual([face.material_index for face in obj.data.polygons], original)
        self.assertEqual(len(obj.material_slots), 0)
        self.assign()
        self.assertEqual([face.material_index for face in obj.data.polygons], original)

    def test_failed_append_preserves_curve_and_text_indices(self) -> None:
        self.create()
        for kind in ("CURVE", "SURFACE", "FONT"):
            data = bpy.data.curves.new(kind, kind)
            if kind == "FONT":
                data.body = "Text"
                item = data.body_format[0]
            else:
                item = data.splines.new("NURBS")
            item.material_index = 7
            original_index = item.material_index
            obj = bpy.data.objects.new(kind, data)
            bpy.context.collection.objects.link(obj)
            with (
                patch.object(
                    adapter,
                    "assignment_result",
                    side_effect=RuntimeError("summary unavailable"),
                ),
                self.assertRaises(RuntimeError),
            ):
                self.assign(obj.name)
            self.assertEqual(item.material_index, original_index)
            self.assertEqual(len(obj.material_slots), 0)

    def test_linked_geometry_allows_object_override_but_not_slot_extension(
        self,
    ) -> None:
        obj = self.mesh()
        self.create()
        obj.data.materials.append(bpy.data.materials["Material"])
        filepath = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "linked-slots.blend")
        bpy.data.libraries.write(filepath, {obj})
        bpy.data.objects.remove(obj, do_unlink=True)
        with bpy.data.libraries.load(filepath, link=True) as (source, target):
            target.objects = source.objects
        linked = target.objects[0]
        bpy.context.collection.objects.link(linked)
        self.create("Replacement")
        self.failure(
            "assign",
            {"object_name": linked.name, "material_name": "Replacement"},
            "invalid_context",
        )
        local = bpy.data.objects.new("Local", linked.data)
        bpy.context.collection.objects.link(local)
        original = linked.material_slots[0].material
        self.assertEqual(self.assign("Local", "Replacement").slots, ["Replacement"])
        self.assertEqual(linked.material_slots[0].material, original)
        self.assertEqual(local.data, linked.data)
        self.failure(
            "assign",
            {"object_name": "Local", "material_name": "Replacement", "slot_index": 1},
            "invalid_context",
        )
        self.assertEqual(len(local.material_slots), 1)
