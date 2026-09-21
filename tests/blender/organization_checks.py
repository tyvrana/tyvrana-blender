"""Native organization, ownership, rollback and persistence on disposable fixtures."""

import importlib
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
ops = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
org = importlib.import_module("bl_ext.user_default.tyvrana_blender.organization")
backend = adapter.BlenderBackend(adapter._runtime.worker.spool)


def call(operation: str, /, **arguments: Any) -> Any:
    r = ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="organization",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(r, OperationSuccess), r
    return r.result


def reject(operation: str, /, **arguments: Any) -> Any:
    r = ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="organization-error",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(r, OperationFailure), r
    return r.error


def part(name: str, **kwargs: Any) -> dict[str, Any]:
    return dict(key=name, name=name, kind="primitive", primitive="cube", **kwargs)


class OrganizationTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for scene in list(bpy.data.scenes):
            if scene != bpy.context.scene:
                bpy.data.scenes.remove(scene)
        for collection in list(bpy.data.collections):
            bpy.data.collections.remove(collection)
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for mat in list(bpy.data.materials):
            bpy.data.materials.remove(mat)
        call("material.create_principled", name="Metal")

    def hierarchy(self) -> None:
        call(
            "collection.create_hierarchy",
            collections=[
                {"name": "Parts", "parents": ["Assembly"]},
                {"name": "Assembly"},
                {"name": "Review"},
            ],
        )

    def create(self, *members: dict[str, Any], **kwargs: Any) -> Any:
        return call("object_set.create", objects=list(members), **kwargs)

    def same_matrix(self, a: Any, b: Any) -> None:
        self.assertLess(
            max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)), 2e-5
        )

    def test_nested_collection_multi_membership_visibility(self) -> None:
        self.hierarchy()
        self.create(part("Part", collections=["Parts", "Review"]))
        result = call(
            "collection.configure",
            collections=[
                {
                    "name": "Parts",
                    "parents": ["Assembly", "Review"],
                    "hide_render": True,
                    "hide_select": True,
                }
            ],
        )
        self.assertEqual(result["collections"][0]["parent_count"], 2)
        self.assertEqual(len(bpy.data.objects["Part"].users_collection), 2)
        self.assertTrue(bpy.data.collections["Parts"].hide_render)
        self.assertFalse(bpy.data.collections["Parts"].hide_viewport)
        self.assertEqual(
            call("collection.inspect", root="Assembly")["page"]["matched_count"], 2
        )

    def test_collection_cycles_and_invalid_parent_atomic(self) -> None:
        for specs in (
            [{"name": "A", "parents": ["B"]}, {"name": "B", "parents": ["A"]}],
            [{"name": "A"}, {"name": "B", "parents": ["missing"]}],
        ):
            reject("collection.create_hierarchy", collections=specs)
            self.assertEqual(len(bpy.data.collections), 0)
        self.hierarchy()
        reject(
            "collection.configure",
            collections=[{"name": "Assembly", "parents": ["Parts"]}],
        )
        self.assertIn("Assembly", bpy.context.scene.collection.children)

    def test_collection_hierarchy_reversal(self) -> None:
        self.hierarchy()
        call(
            "collection.configure",
            collections=[
                {"name": "Assembly", "parents": ["Parts"]},
                {"name": "Parts", "parents": [None]},
            ],
        )
        self.assertIn("Assembly", bpy.data.collections["Parts"].children)
        self.assertNotIn("Parts", bpy.data.collections["Assembly"].children)

    def test_collection_names_collision(self) -> None:
        self.hierarchy()
        reject("collection.create_hierarchy", collections=[{"name": "Review"}])
        reject(
            "collection.configure", collections=[{"name": "Parts", "rename": "Review"}]
        )
        self.assertEqual(len(bpy.data.collections), 3)

    def test_collection_rehome(self) -> None:
        self.hierarchy()
        self.create(part("Part"), collections=["Parts"])
        reject("collection.remove", name="Assembly")
        reject("collection.remove", name="Assembly", mode="rehome", target="Parts")
        result = call(
            "collection.remove", name="Assembly", mode="rehome", target="Review"
        )
        self.assertEqual(result["deleted"], ["Assembly"])
        self.assertIn("Parts", bpy.data.collections["Review"].children)
        call("collection.remove", name="Parts", mode="rehome")
        self.assertIn("Part", bpy.context.scene.collection.objects)

    def test_collection_shared_scene_and_instance_guards(self) -> None:
        self.hierarchy()
        other = bpy.data.scenes.new("Other")
        other.collection.children.link(bpy.data.collections["Parts"])
        reject(
            "collection.configure", collections=[{"name": "Parts", "hide_render": True}]
        )
        reject("collection.remove", name="Parts")
        other.collection.children.unlink(bpy.data.collections["Parts"])
        instance = bpy.data.objects.new("Instance", None)
        instance.instance_type = "COLLECTION"
        instance.instance_collection = bpy.data.collections["Parts"]
        bpy.context.scene.collection.objects.link(instance)
        reject("collection.remove", name="Parts")

    def test_staged_collection_failure_cleanup(self) -> None:
        original = org.collection_summary
        with patch.object(
            org, "collection_summary", side_effect=RuntimeError("injected")
        ):
            reject(
                "collection.create_hierarchy",
                collections=[{"name": "A"}, {"name": "B", "parents": ["A"]}],
            )
        self.assertEqual(len(bpy.data.collections), 0)
        self.assertIs(org.collection_summary, original)

    def test_mechanical_assembly_local_hierarchy(self) -> None:
        self.hierarchy()
        self.create(
            part("Effector", parent={"key": "Arm"}, location=[0, 0, 2]),
            part("Arm", parent={"key": "Pivot"}, location=[0, 0, 3]),
            dict(
                key="Pivot",
                name="Pivot",
                kind="empty",
                parent={"key": "Base"},
                location=[0, 0, 1],
            ),
            part("Base", location=[10, 0, 0], material="Metal"),
            collections=["Parts"],
        )
        self.assertEqual(
            list(bpy.data.objects["Effector"].matrix_world.translation), [10, 0, 6]
        )
        self.assertEqual(
            call("object_set.inspect", collection="Assembly")["page"]["matched_count"],
            4,
        )

    def test_parent_change_clear_preserves_world(self) -> None:
        self.create(
            part("A", location=[3, 4, 5], rotation=[0.2, 0.4, 0.3], scale=[2, 3, 4]),
            part("B", location=[9, 8, 7]),
            part("C", location=[-2, 5, 1]),
        )
        child = bpy.data.objects["B"]
        old = child.matrix_world.copy()
        for parent in ("A", "C", None):
            call("object_set.configure", objects=[{"name": "B", "parent": parent}])
            self.same_matrix(child.matrix_world, old)

    def test_parent_cycles_and_valid_reversal(self) -> None:
        self.create(part("A"), part("B", parent={"key": "A"}, location=[0, 0, 2]))
        reject("object_set.configure", objects=[{"name": "A", "parent": "B"}])
        a = bpy.data.objects["A"].matrix_world.copy()
        b = bpy.data.objects["B"].matrix_world.copy()
        call(
            "object_set.configure",
            objects=[{"name": "A", "parent": "B"}, {"name": "B", "parent": None}],
        )
        self.same_matrix(bpy.data.objects["A"].matrix_world, a)
        self.same_matrix(bpy.data.objects["B"].matrix_world, b)

    def test_invalid_authoring_and_no_resource_leaks(self) -> None:
        before = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
        for values in (
            [part("Good"), part("Bad", collections=["missing"])],
            [part("Good"), part("Bad", material="missing")],
            [part("Good"), part("Bad", parent={"name": "missing"})],
            [part("A", parent={"key": "B"}), part("B", parent={"key": "A"})],
        ):
            reject("object_set.create", objects=values)
            self.assertEqual(
                (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)),
                before,
            )

    def test_native_authoring_failure_cleans_new_objects_meshes_materials(self) -> None:
        self.create(part("Source", material="Metal"))
        before = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
        original = org.apply_member

        def injected(obj: Any, spec: Any, parent: Any) -> None:
            original(obj, spec, parent)
            if spec.key == "Bad":
                raise RuntimeError("injected")

        with patch.object(org, "apply_member", injected):
            reject(
                "object_set.create",
                objects=[
                    dict(
                        key="Copy",
                        name="Copy",
                        kind="copy",
                        source={"name": "Source"},
                        data="independent",
                        materials="independent",
                    ),
                    part("Bad"),
                ],
            )
        self.assertEqual(
            (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)),
            before,
        )

    def test_linked_independent_copies_and_materials(self) -> None:
        self.create(
            part("Source", material="Metal"),
            dict(key="Linked", name="Linked", kind="copy", source={"key": "Source"}),
            dict(
                key="Independent",
                name="Independent",
                kind="copy",
                source={"key": "Source"},
                data="independent",
                materials="independent",
            ),
        )
        source = bpy.data.objects["Source"]
        linked = bpy.data.objects["Linked"]
        independent = bpy.data.objects["Independent"]
        self.assertEqual(source.data, linked.data)
        self.assertNotEqual(source.data, independent.data)
        self.assertEqual(
            source.material_slots[0].material, linked.material_slots[0].material
        )
        self.assertNotEqual(
            source.material_slots[0].material, independent.material_slots[0].material
        )
        independent.data.vertices[0].co.x = 99
        self.assertNotEqual(source.data.vertices[0].co.x, 99)
        # Existing typed slot append isolates shared geometry, preserving siblings.
        call("material.create_principled", name="Paint")
        call(
            "material.assign", object_name="Linked", material_name="Paint", slot_index=1
        )
        self.assertEqual(len(source.material_slots), 1)
        self.assertEqual(len(linked.material_slots), 2)

    def test_copy_rejects_complex_or_domain_owned_sources(self) -> None:
        self.create(part("Source"))
        obj = bpy.data.objects["Source"]
        modifier = obj.modifiers.new("Subdivision", "SUBSURF")
        reject(
            "object_set.create",
            objects=[
                dict(key="Copy", name="Copy", kind="copy", source={"name": "Source"})
            ],
        )
        obj.modifiers.remove(modifier)
        obj["tyvrana_reserved"] = "protected"
        reject(
            "object_set.create",
            objects=[
                dict(key="Copy", name="Copy", kind="copy", source={"name": "Source"})
            ],
        )
        self.assertNotIn("Copy", bpy.data.objects)

    def test_copy_camera_light_empty_and_invalid_type(self) -> None:
        for kind, store in [("LIGHT", bpy.data.lights), ("CAMERA", bpy.data.cameras)]:
            data = store.new(kind, type="POINT") if kind == "LIGHT" else store.new(kind)
            obj = bpy.data.objects.new(kind, data)
            bpy.context.scene.collection.objects.link(obj)
            self.create(
                dict(
                    key=kind + "Copy",
                    name=kind + "Copy",
                    kind="copy",
                    source={"name": kind},
                    data="independent",
                )
            )
            self.assertNotEqual(obj.data, bpy.data.objects[kind + "Copy"].data)
        curve = bpy.data.curves.new("Curve", "CURVE")
        obj = bpy.data.objects.new("Curve", curve)
        bpy.context.scene.collection.objects.link(obj)
        reject(
            "object_set.create",
            objects=[
                dict(key="Bad", name="Bad", kind="copy", source={"name": "Curve"})
            ],
        )
        self.create(
            dict(key="Empty", name="Empty", kind="empty"),
            dict(
                key="EmptyCopy", name="EmptyCopy", kind="copy", source={"key": "Empty"}
            ),
        )

    def test_metadata_filter_rename_and_omission(self) -> None:
        self.create(
            part("A", role="fastener", tags=["metal", "small"]),
            part("B", role="structure"),
        )
        call("object_set.configure", objects=[{"name": "A", "rename": "Bolt"}])
        focused = call(
            "object_set.inspect", role="fastener", tags=["small"], fields=["metadata"]
        )
        self.assertEqual([o["name"] for o in focused["objects"]], ["Bolt"])
        self.assertEqual(focused["objects"][0]["metadata"]["tags"], ["metal", "small"])
        call(
            "object_set.configure", objects=[{"name": "Bolt", "role": None, "tags": []}]
        )
        self.assertNotIn(org.ROLE, bpy.data.objects["Bolt"])
        self.assertNotIn(org.TAGS, bpy.data.objects["Bolt"])

    def test_membership_replace_link_unlink_move_and_selection(self) -> None:
        self.hierarchy()
        self.create(part("Part"), collections=["Parts"])
        obj = bpy.data.objects["Part"]
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        call(
            "object_set.configure",
            objects=[{"name": "Part", "collections": ["Parts", "Review"]}],
        )
        self.assertEqual(len(obj.users_collection), 2)
        call(
            "object_set.configure",
            objects=[{"name": "Part", "collections": ["Review"]}],
        )
        self.assertEqual([c.name for c in obj.users_collection], ["Review"])
        self.create(part("Another"))
        self.assertTrue(obj.select_get())
        self.assertEqual(bpy.context.view_layer.objects.active, obj)
        self.assertFalse(bpy.data.objects["Another"].select_get())

    def test_configure_preflight_and_native_rollback(self) -> None:
        self.hierarchy()
        self.create(part("A"), part("B"))
        reject(
            "object_set.configure",
            objects=[
                {"name": "A", "role": "changed"},
                {"name": "B", "parent": "missing"},
            ],
        )
        self.assertNotIn(org.ROLE, bpy.data.objects["A"])
        with patch.object(
            org, "configuration_result", side_effect=RuntimeError("injected")
        ):
            reject(
                "object_set.configure",
                objects=[
                    {
                        "name": "A",
                        "rename": "New",
                        "parent": "B",
                        "collections": ["Parts"],
                        "role": "changed",
                    }
                ],
            )
        self.assertIn("A", bpy.data.objects)
        self.assertNotIn("New", bpy.data.objects)
        self.assertIsNone(bpy.data.objects["A"].parent)
        self.assertIn("A", bpy.context.scene.collection.objects)
        self.assertNotIn(org.ROLE, bpy.data.objects["A"])

    def test_bound_geometry_metadata_preserves_domain_ownership(self) -> None:
        self.create(part("Surface"))
        call(
            "armature.create",
            name="Rig",
            bones=[
                dict(
                    name="Segment", head=[0, 0, -1], tail=[0, 0, 1], envelope_distance=3
                )
            ],
        )
        call(
            "armature.bind",
            object_name="Surface",
            armature_object="Rig",
            weights=dict(method="envelopes", bones=["Segment"]),
        )
        obj = bpy.data.objects["Surface"]
        owned = {k: obj[k] for k in obj.keys() if k not in {org.ROLE, org.TAGS}}
        self.assertTrue(owned)
        before = call("armature.inspect", object_name="Rig")
        world = obj.matrix_world.copy()
        weights = [[(g.group, g.weight) for g in v.groups] for v in obj.data.vertices]
        call(
            "object_set.configure",
            objects=[dict(name="Surface", role="domain_structure", tags=["prototype"])],
        )
        self.assertEqual(obj[org.ROLE], "domain_structure")
        self.assertEqual(list(obj[org.TAGS]), ["prototype"])
        call(
            "object_set.configure",
            objects=[dict(name="Surface", collections=[None], hide_render=True)],
        )
        self.assertEqual(list(obj.users_collection), [bpy.context.scene.collection])
        self.assertTrue(obj.hide_render)
        for patch_fields in (
            {"rename": "Changed"},
            {"parent": None},
        ):
            reject(
                "object_set.configure", objects=[dict(name="Surface", **patch_fields)]
            )
        with patch.object(
            org, "configuration_result", side_effect=RuntimeError("injected")
        ):
            reject(
                "object_set.configure",
                objects=[dict(name="Surface", role="wrong", tags=[])],
            )
        self.assertEqual(obj[org.ROLE], "domain_structure")
        self.assertEqual(list(obj[org.TAGS]), ["prototype"])
        self.assertEqual({k: obj[k] for k in owned}, owned)
        self.assertEqual(call("armature.inspect", object_name="Rig"), before)
        self.assertEqual(
            [[(g.group, g.weight) for g in v.groups] for v in obj.data.vertices],
            weights,
        )
        self.same_matrix(obj.matrix_world, world)
        call("object_set.configure", objects=[dict(name="Surface", role=None, tags=[])])
        self.assertNotIn(org.ROLE, obj)
        self.assertNotIn(org.TAGS, obj)

    def test_compact_configuration_reports_only_actual_changes(self) -> None:
        self.create(part("A"), part("B"))
        result = call(
            "object_set.configure",
            objects=[
                dict(name="A", rename="Renamed", hide_render=True),
                dict(name="B", hide_render=False),
            ],
        )
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["unchanged_count"], 1)
        self.assertEqual(
            result["changes"],
            [
                {
                    "name": "Renamed",
                    "previous_name": "A",
                    "fields": ["hide_render", "rename"],
                }
            ],
        )
        self.assertNotIn("objects", result)
        no_op = call(
            "object_set.configure", objects=[dict(name="Renamed", hide_render=True)]
        )
        self.assertEqual(no_op["changed_count"], 0)
        self.assertEqual(no_op["changes"], [])
        detail = call("object_set.inspect", names=["Renamed"], fields=["visibility"])
        self.assertTrue(detail["objects"][0]["visibility"]["hide_render"])

    def test_complete_managed_assembly_removal_and_dependency_preflight(self) -> None:
        for z in [0, 2]:
            call(
                "assembly.create",
                name="Rack" + str(z),
                templates=[
                    {
                        "id": "bar",
                        "kind": "loft",
                        "spec": {
                            "name": "Bar",
                            "sides": 8,
                            "subdivisions": 1,
                            "sections": [
                                {"center": [0, 0, 0], "radii": [0.1] * 4},
                                {"center": [0, 1, 0], "radii": [0.1] * 4},
                            ],
                        },
                    }
                ],
                families=[
                    {
                        "id": key,
                        "template": "bar",
                        "count": 50,
                        "path": [[0, 0, z], [100, 0, z]],
                    }
                    for key in ["bars"]
                ],
            )
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        self.assertEqual(len(meshes), 100)
        reject("object_set.remove", names=[meshes[0].name])
        reject("object.delete", name=meshes[0].name)
        self.create(dict(key="Dependent", name="Dependent", kind="empty"))
        dependent = bpy.data.objects["Dependent"]
        constraint = dependent.constraints.new("COPY_LOCATION")
        constraint.target = meshes[0]
        before = set(bpy.data.objects.keys())
        reject("object_set.remove", names=["Rack0", "Rack2"])
        self.assertEqual(set(bpy.data.objects.keys()), before)
        dependent.constraints.remove(constraint)
        result = call("object_set.remove", names=["Rack0", "Rack2", meshes[0].name])
        self.assertEqual(result["requested_count"], 3)
        self.assertEqual(result["expanded_count"], 104)
        self.assertEqual(result["removed_count"], 104)
        self.assertEqual(result["blocked_count"], 0)
        self.assertEqual(set(bpy.data.objects.keys()), {"Dependent"})
        self.assertGreaterEqual(len(bpy.data.meshes), 100)

    def test_specialized_owned_resources_still_require_cleanup(self) -> None:
        self.create(part("Plain"), part("Protected"))
        bpy.data.objects["Protected"]["_tyvrana_growth_dynamics_owner"] = "system"
        reject("object_set.remove", names=["Plain", "Protected"])
        self.assertIn("Plain", bpy.data.objects)
        self.assertIn("Protected", bpy.data.objects)

    def test_safe_deletion_and_data_retention(self) -> None:
        self.create(part("A"), part("B", parent={"key": "A"}, location=[3, 4, 5]))
        mesh = bpy.data.objects["A"].data.name
        world = bpy.data.objects["B"].matrix_world.copy()
        reject("object_set.remove", names=["A"])
        result = call("object_set.remove", names=["A"], children="unparent")
        self.assertEqual(result["deleted"], ["A"])
        self.assertIsNone(bpy.data.objects["B"].parent)
        self.same_matrix(bpy.data.objects["B"].matrix_world, world)
        self.assertIn(mesh, bpy.data.meshes)

    def test_deletion_dependency_guard(self) -> None:
        self.create(part("A"), part("B"))
        constraint = bpy.data.objects["B"].constraints.new("COPY_LOCATION")
        constraint.target = bpy.data.objects["A"]
        reject("object_set.remove", names=["A"])
        self.assertIn("A", bpy.data.objects)

    def test_shared_scene_object_guard(self) -> None:
        self.create(part("A"))
        other = bpy.data.scenes.new("Other")
        other.collection.objects.link(bpy.data.objects["A"])
        reject("object_set.configure", objects=[{"name": "A", "role": "changed"}])
        reject("object_set.remove", names=["A"])

    def test_large_scene_compact_pages(self) -> None:
        for batch in range(3):
            self.create(*[part(f"Part{batch * 64 + i:03}") for i in range(64)])
        result = call("object_set.inspect", fields=["metadata"], limit=5)
        self.assertEqual(len(result["objects"]), 5)
        self.assertEqual(result["page"]["next_offset"], 5)
        self.assertEqual(result["page"]["matched_count"], 192)
        self.assertIsNone(result["objects"][0]["transforms"])
        result = call("object_set.inspect", names=["Part100"], fields=["hierarchy"])
        self.assertEqual(result["page"]["matched_count"], 1)

    def test_all_primitive_geometry(self) -> None:
        self.create(
            *[
                dict(key=k, name=k, kind="primitive", primitive=k)
                for k in ["cube", "plane", "uv_sphere", "cylinder"]
            ]
        )
        self.assertEqual(len(bpy.data.objects["plane"].data.polygons), 1)
        for obj in bpy.context.scene.objects:
            self.assertGreater(len(obj.data.vertices), 3)

    def test_persistence_renames_membership_parent_metadata_sharing(self) -> None:
        self.hierarchy()
        self.create(
            dict(key="Root", name="Root", kind="empty"),
            part(
                "Source",
                parent={"key": "Root"},
                material="Metal",
                role="structure",
                tags=["module"],
            ),
            dict(
                key="Linked",
                name="Linked",
                kind="copy",
                source={"key": "Source"},
                parent={"key": "Root"},
            ),
            collections=["Parts", "Review"],
        )
        call(
            "collection.configure",
            collections=[
                {
                    "name": "Parts",
                    "rename": "Components",
                    "hide_render": True,
                    "hide_viewport": True,
                    "hide_select": True,
                }
            ],
        )
        call(
            "object_set.configure",
            objects=[
                {"name": "Root", "rename": "Root Renamed"},
                {"name": "Source", "rename": "Source Renamed"},
            ],
        )
        path = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "organization.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        source = bpy.data.objects["Source Renamed"]
        linked = bpy.data.objects["Linked"]
        self.assertEqual(source.data, linked.data)
        self.assertEqual(source.parent.name, "Root Renamed")
        self.assertEqual(source[org.ROLE], "structure")
        self.assertEqual(list(source[org.TAGS]), ["module"])
        self.assertEqual(
            {c.name for c in source.users_collection}, {"Components", "Review"}
        )
        c = bpy.data.collections["Components"]
        self.assertTrue(c.hide_render and c.hide_viewport and c.hide_select)

    def test_partial_deletion_reports_exact_progress(self) -> None:
        self.create(
            part("A"),
            part("B"),
            part("C", parent={"key": "A"}),
            part("D", parent={"key": "B"}),
        )

        def injected(items: list[Any]) -> None:
            bpy.data.objects.remove(items[0], do_unlink=True)
            raise RuntimeError("injected")

        with patch.object(org, "remove_objects", injected):
            result = call("object_set.remove", names=["A", "B"], children="unparent")
        self.assertEqual(result["deleted"], ["A"])
        self.assertEqual(result["remaining"], ["B"])
        self.assertIsNotNone(result["error"])
        self.assertIsNone(bpy.data.objects["C"].parent)
        self.assertEqual(bpy.data.objects["D"].parent, bpy.data.objects["B"])

    def test_library_linked_object_and_collection_guards(self) -> None:
        self.hierarchy()
        self.create(part("Linked Source"), collections=["Parts"])
        path = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "library.blend")
        bpy.data.libraries.write(path, {bpy.data.collections["Assembly"]})
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for c in list(bpy.data.collections):
            bpy.data.collections.remove(c)
        with bpy.data.libraries.load(path, link=True) as (source, target):
            target.collections = ["Assembly"]
        bpy.context.scene.collection.children.link(bpy.data.collections["Assembly"])
        reject(
            "collection.configure",
            collections=[{"name": "Assembly", "hide_render": True}],
        )
        reject(
            "object_set.configure",
            objects=[{"name": "Linked Source", "role": "changed"}],
        )
        reject(
            "object_set.create",
            objects=[
                dict(
                    key="Copy",
                    name="Copy",
                    kind="copy",
                    source={"name": "Linked Source"},
                )
            ],
        )
        reject("object_set.remove", names=["Linked Source"])

    def test_sheared_clear_rejected_and_rolled_back(self) -> None:
        self.create(
            part("A", scale=[2, 3, 4]),
            part("B", parent={"key": "A"}, rotation=[0.3, 0.4, 0.5]),
        )
        obj = bpy.data.objects["B"]
        old = obj.matrix_world.copy()
        reject("object_set.configure", objects=[{"name": "B", "parent": None}])
        self.assertEqual(obj.parent.name, "A")
        self.same_matrix(obj.matrix_world, old)
        reject("object_set.remove", names=["A"], children="unparent")
        self.assertEqual(obj.parent.name, "A")
        self.same_matrix(obj.matrix_world, old)

    def test_animated_and_constrained_parenting_rejected(self) -> None:
        self.create(part("A"), part("B"))
        b = bpy.data.objects["B"]
        b.keyframe_insert(data_path="location", frame=1)
        reject("object_set.configure", objects=[{"name": "B", "parent": "A"}])
        b.animation_data_clear()
        b.constraints.new("LIMIT_LOCATION")
        reject("object_set.configure", objects=[{"name": "B", "parent": "A"}])

    def test_collection_configure_rollback_reverses_all_links(self) -> None:
        self.hierarchy()
        with patch.object(
            org, "collection_summary", side_effect=RuntimeError("injected")
        ):
            reject(
                "collection.configure",
                collections=[
                    {"name": "Assembly", "parents": ["Parts"]},
                    {"name": "Parts", "parents": [None], "hide_render": True},
                ],
            )
        self.assertIn("Assembly", bpy.context.scene.collection.children)
        self.assertIn("Parts", bpy.data.collections["Assembly"].children)
        self.assertNotIn("Parts", bpy.context.scene.collection.children)
        self.assertFalse(bpy.data.collections["Parts"].hide_render)

    def test_object_and_utf8_name_collisions_fail_without_suffixes(self) -> None:
        self.create(part("A"))
        reject("object_set.create", objects=[part("B"), part("A")])
        reject("object_set.create", objects=[part("界" * 100)])
        self.assertEqual([o.name for o in bpy.context.scene.objects], ["A"])
        self.create(part("B"))
        reject("object_set.configure", objects=[{"name": "A", "rename": "B"}])

    def test_delta_parenting_guard_preserves_channels(self) -> None:
        self.create(part("A"), part("B"))
        obj = bpy.data.objects["B"]
        obj.delta_location = (2, 3, 4)
        bpy.context.view_layer.update()
        old = obj.matrix_world.copy()
        reject("object_set.configure", objects=[{"name": "B", "parent": "A"}])
        self.same_matrix(obj.matrix_world, old)
        self.assertEqual(tuple(obj.delta_location), (2, 3, 4))


suite = unittest.defaultTestLoader.loadTestsFromTestCase(OrganizationTests)
count = suite.countTestCases()
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print("ORGANIZATION_NATIVE_PASSED", count)
