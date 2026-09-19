"""Native structural assembly, interfaces, repair, rollback and persistence."""

import importlib
import json
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.assembly_fixtures import compact_member, master_spec, varying_member

package = os.environ.get("TYVRANA_TEST_PACKAGE", "bl_ext.user_default.tyvrana_blender")
adapter = importlib.import_module(package + ".blender")
ops = importlib.import_module(package + ".operations")
assembly = importlib.import_module(package + ".assembly")
placement = importlib.import_module(package + ".placement")
cleanup = importlib.import_module(package + ".cleanup")
loft = importlib.import_module(package + ".loft")
bindings = importlib.import_module(package + ".bindings")
construction = importlib.import_module(package + ".construction")
artifacts = importlib.import_module(package + ".artifacts")
render = importlib.import_module(package + ".render")
models = importlib.import_module(package + ".models")
backend = adapter.BlenderBackend()


def execute(operation: str, **args: Any) -> Any:
    return ops.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="assembly-check",
            operation="blender." + operation,
            arguments=args,
        ),
    )


def call(operation: str, **args: Any) -> Any:
    result = execute(operation, **args)
    assert isinstance(result, OperationSuccess), result
    return result.result


def small_spec(count: int = 3) -> dict[str, Any]:
    return {
        "name": "Module",
        "templates": [{"kind": "loft", "id": "segment", "spec": compact_member()}],
        "families": [
            {
                "id": "series",
                "template": "segment",
                "count": count,
                "path": [[1, 0, 0], [1, count, 0.2]],
                "feature_scale": [0.6, 1.3],
                "overrides": [
                    {
                        "index": 0,
                        "shape": {
                            "sections": [
                                {"id": "broad", "radii": [0.25, 0.19, 0.21, 0.16]}
                            ]
                        },
                    }
                ],
            }
        ],
    }


def inventory() -> tuple[int, int, int]:
    return len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.cameras)


def malformed_cube(name: str) -> Any:
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.verts.new((3, 3, 3))
    data = bpy.data.meshes.new(name)
    bm.to_mesh(data)
    bm.free()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


class AssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.meshes):
            if not data.users:
                bpy.data.meshes.remove(data)

    def test_long_features_revision_and_native_shape(self) -> None:
        spec = varying_member()
        first = call("loft.create", components=[spec])["components"][0]
        obj = bpy.data.objects[spec["name"]]
        pointer = obj.data.as_pointer()
        fingerprint = bindings._fingerprint("object", obj)
        self.assertEqual(first["vertex_count"], 1440)
        self.assertIn("process", first["region_ids"])
        stats = assembly.surfaces.native_checks(obj.data)
        self.assertEqual(stats["components"], 1)
        # First cap support rings move inward, and the opposite cap extends outward.
        self.assertGreater(obj.data.vertices[1184 + 3 * 32].co.y, 0.05)
        self.assertGreater(max(v.co.x for v in obj.data.vertices), 0.65)
        result = call(
            "loft.configure",
            components=[
                {
                    "name": spec["name"],
                    "expected_revision": 1,
                    "section_edits": [
                        {
                            "id": "middle",
                            "center": [0.25, 1.45, 0.18],
                            "radii": None,
                            "twist": None,
                        }
                    ],
                }
            ],
        )["components"][0]
        self.assertEqual(result["revision"], 2)
        self.assertEqual(first["component_id"], result["component_id"])
        self.assertEqual(pointer, obj.data.as_pointer())
        self.assertNotEqual(fingerprint, bindings._fingerprint("object", obj))
        self.assertEqual(
            execute(
                "loft.configure",
                components=[
                    {"name": spec["name"], "expected_revision": 1, "smooth": False}
                ],
            ).error.code,
            "loft_revision_conflict",
        )

    def test_family_rule_exception_mirroring_and_stale_edit(self) -> None:
        spec = small_spec(16)
        spec["families"].append({"id": "paired", "mirror_of": "series"})
        first = call("assembly.create", **spec)
        self.assertEqual(first["component_count"], 32)
        left = bpy.data.objects["Module.series.07"]
        right = bpy.data.objects["Module.paired.07"]
        self.assertNotEqual(left[bindings.RESOURCE_KEY], right[bindings.RESOURCE_KEY])
        for a, b in zip(left.data.vertices, right.data.vertices, strict=True):
            world_a, world_b = left.matrix_world @ a.co, right.matrix_world @ b.co
            self.assertLess(
                (world_b - Vector((-world_a.x, world_a.y, world_a.z))).length, 1e-5
            )
        before = {
            o.name: assembly.content(o) for o in bpy.data.objects if o.type == "MESH"
        }
        identity = right[bindings.RESOURCE_KEY]
        result = call(
            "assembly.configure",
            name="Module",
            expected_revision=1,
            members=[
                {
                    "family": "paired",
                    "index": 7,
                    "shape": {"features": [{"id": "lug", "height": 0.12}]},
                }
            ],
        )
        self.assertEqual(result["changed_members"], [right.name])
        self.assertEqual([row["name"] for row in result["components"]], [right.name])
        self.assertEqual(identity, right[bindings.RESOURCE_KEY])
        self.assertEqual(before[left.name], assembly.content(left))
        self.assertNotEqual(before[right.name], assembly.content(right))
        family = {"id": "series", "feature_scale": [0.7, 1.4]}
        result = call(
            "assembly.configure", name="Module", expected_revision=2, families=[family]
        )
        self.assertEqual(result["revision"], 3)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(
            json.loads(right[loft.KEY])["spec"]["features"][0]["height"], 0.12
        )
        right.data.vertices[0].co.x += 0.1
        self.assertFalse(call("assembly.inspect", name="Module")["valid"])
        self.assertEqual(
            execute(
                "assembly.configure",
                name="Module",
                expected_revision=3,
                refresh_placements=True,
            ).error.code,
            "assembly_stale_geometry",
        )

    def test_placement_fit_geometry_landmarks_and_freshness(self) -> None:
        call("loft.create", components=[compact_member("Part")])
        call(
            "landmark.set",
            landmarks=[
                {"name": "Start", "point": [2, 1, 0]},
                {"name": "End", "point": [3, 4, 0.5]},
            ],
        )
        obj = bpy.data.objects["Part"]
        result = call(
            "object_set.place",
            placements=[
                {
                    "name": "Part",
                    "rule": {
                        "kind": "between",
                        "start": {"kind": "landmark", "name": "Start"},
                        "end": {"kind": "landmark", "name": "End"},
                    },
                }
            ],
        )
        self.assertTrue(result["placements"][0]["valid"])
        self.assertAlmostEqual(
            result["placements"][0]["dimensions"][1],
            (Vector((3, 4, 0.5)) - Vector((2, 1, 0))).length,
            places=5,
        )
        identity = loft.metadata(obj)["id"]
        signature = loft.signature(obj.data)
        call("landmark.set", landmarks=[{"name": "End", "point": [3, 5, 0.5]}])
        self.assertFalse(placement.summary(obj).valid)
        self.assertTrue(
            call("object_set.place", refresh=["Part"])["placements"][0]["valid"]
        )
        self.assertEqual(identity, loft.metadata(obj)["id"])
        self.assertEqual(signature, loft.signature(obj.data))
        result = call(
            "landmark.derive",
            landmarks=[
                {
                    "name": "Contact",
                    "source": {
                        "kind": "geometry",
                        "source": {
                            "kind": "geometry",
                            "object": "Part",
                            "position": [0.5, 0, 0.5],
                        },
                    },
                }
            ],
        )
        self.assertEqual(result["solved"], 1)
        self.assertTrue(construction.freshness(bpy.data.objects["Contact"]))
        call(
            "loft.configure",
            components=[
                {
                    "name": "Part",
                    "section_edits": [
                        {"id": "upper", "radii": [0.2, 0.18, 0.17, 0.14]}
                    ],
                }
            ],
        )
        self.assertFalse(construction.freshness(bpy.data.objects["Contact"]))

    def test_create_and_revision_injected_rollback(self) -> None:
        spec = small_spec()
        before = inventory()
        original = assembly.set_geometry_state
        counter = 0

        def inject(*args: Any, **kwargs: Any) -> None:
            nonlocal counter
            counter += 1
            if counter == 2:
                raise RuntimeError("injected assembly commit")
            original(*args, **kwargs)

        with patch.object(assembly, "set_geometry_state", side_effect=inject):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                assembly.create(assembly.AssemblyCreateArguments.model_validate(spec))
        self.assertEqual(before, inventory())
        call("assembly.create", **spec)
        objects = [o for o in bpy.data.objects if o.type == "MESH"]
        before = inventory()
        signatures = {
            o.name: (assembly.content(o), o.data.as_pointer(), dict(o.items()))
            for o in objects
        }
        counter = 0
        family = deepcopy(spec["families"][0])
        family["feature_scale"] = [1, 1.5]
        with patch.object(assembly, "set_geometry_state", side_effect=inject):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                assembly.configure(
                    assembly.AssemblyConfigureArguments(
                        name="Module", expected_revision=1, families=[family]
                    )
                )
        self.assertEqual(before, inventory())
        for obj in objects:
            self.assertEqual(
                signatures[obj.name],
                (assembly.content(obj), obj.data.as_pointer(), dict(obj.items())),
            )

    def test_impossible_geometry_cycles_and_placement_rollback(self) -> None:
        spec = small_spec()
        spec["families"][0]["overrides"] = [
            {"index": 2, "shape": {"features": [{"id": "lug", "height": -10}]}}
        ]
        before = inventory()
        self.assertNotIsInstance(execute("assembly.create", **spec), OperationSuccess)
        self.assertEqual(before, inventory())
        call("assembly.create", **small_spec())
        names = ["Module.series.00", "Module.series.01"]
        matrices = {n: bpy.data.objects[n].matrix_world.copy() for n in names}
        rules = [
            {"name": n, "rule": {"kind": "fit", "dimensions": [0.6, 0.6, 0.6]}}
            for n in names
        ]
        with patch.object(
            placement, "summary", side_effect=RuntimeError("injected placement report")
        ):
            result = execute("object_set.place", placements=rules)
            self.assertNotIsInstance(result, OperationSuccess)
        for name in names:
            self.assertEqual(matrices[name], bpy.data.objects[name].matrix_world)
            self.assertNotIn(placement.KEY, bpy.data.objects[name])
        cyclic = [
            {"name": names[i], "rule": {"kind": "mirror", "source": names[1 - i]}}
            for i in range(2)
        ]
        self.assertNotIsInstance(
            execute("object_set.place", placements=cyclic), OperationSuccess
        )

    def test_cleanup_scope_noop_preview_and_atomicity(self) -> None:
        first, second = malformed_cube("First"), malformed_cube("Second")
        identities = (first.data.as_pointer(), second.data.as_pointer())
        before = inventory()
        preview = call(
            "mesh.cleanup",
            names=[first.name, second.name],
            preview=True,
            require_closed=True,
        )
        self.assertTrue(all(o["changed"] for o in preview["objects"]))
        self.assertEqual(before, inventory())
        self.assertEqual(len(first.data.vertices), 9)
        original = cleanup.commit
        count = 0

        def inject(obj: Any, data: Any) -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("injected cleanup commit")
            original(obj, data)

        with patch.object(cleanup, "commit", side_effect=inject):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                cleanup.cleanup(
                    cleanup.CleanupArguments(
                        names=[first.name, second.name], require_closed=True
                    )
                )
        self.assertEqual(before, inventory())
        self.assertEqual(
            identities, (first.data.as_pointer(), second.data.as_pointer())
        )
        result = call(
            "mesh.cleanup", names=[first.name, second.name], require_closed=True
        )
        self.assertTrue(
            all(
                o["vertices_after"] == 8 and o["self_contacts"] == 0
                for o in result["objects"]
            )
        )
        self.assertFalse(
            any(
                o["changed"]
                for o in call(
                    "mesh.cleanup", names=[first.name, second.name], require_closed=True
                )["objects"]
            )
        )

    def test_master_dimensions_multiview_and_save_reopen(self) -> None:
        spec = master_spec()
        result = call("assembly.create", **spec)
        self.assertEqual(result["component_count"], 41)
        self.assertTrue(result["valid"])
        call(
            "landmark.set",
            landmarks=[
                {"name": "RailStart", "point": [-2.5, -2.6, 0.25]},
                {"name": "RailMiddle", "point": [-2.5, 0, 0.25]},
                {"name": "RailEnd", "point": [-2.5, 2.6, 0.25]},
            ],
        )
        placements = [
            {
                "name": f"StructuralModule.segments.{i:02}",
                "rule": {"kind": "fit", "dimensions": [None, 0.28, None]},
            }
            for i in range(16)
        ]
        for i, (a, b) in enumerate(
            (("RailStart", "RailMiddle"), ("RailMiddle", "RailEnd"))
        ):
            placements.append(
                {
                    "name": f"StructuralModule.membersL.{i:02}",
                    "rule": {
                        "kind": "between",
                        "start": {"kind": "landmark", "name": a},
                        "end": {"kind": "landmark", "name": b},
                    },
                }
            )
        call(
            "object_set.place",
            placements=placements,
            refresh=[f"StructuralModule.membersR.{i:02}" for i in range(2)],
        )
        queries = []
        for i in range(16):
            name = f"StructuralModule.segments.{i:02}"
            queries.append(
                {
                    "kind": "distance",
                    "name": f"segment_{i}",
                    "a": {
                        "kind": "geometry",
                        "object": name,
                        "position": [0.5, 0, 0.5],
                        "project": False,
                    },
                    "b": {
                        "kind": "geometry",
                        "object": name,
                        "position": [0.5, 1, 0.5],
                        "project": False,
                    },
                    "comparison": {"target": 0.28, "tolerance": 0.001},
                }
            )
        for side in ("membersL", "membersR"):
            for i in range(2):
                name = f"StructuralModule.{side}.{i:02}"
                queries.append(
                    {
                        "kind": "distance",
                        "name": f"{side}_{i}",
                        "a": {
                            "kind": "geometry",
                            "object": name,
                            "position": [0.5, 0, 0.5],
                            "project": False,
                        },
                        "b": {
                            "kind": "geometry",
                            "object": name,
                            "position": [0.5, 1, 0.5],
                            "project": False,
                        },
                        "comparison": {"target": 2.6, "tolerance": 0.001},
                    }
                )
        measurements = call("measurement.inspect", queries=queries)
        self.assertEqual(len(measurements["measurements"]), 20)
        self.assertTrue(
            all(m["within_tolerance"] for m in measurements["measurements"])
        )
        unchanged = bpy.data.objects["StructuralModule.membersL.01"]
        fingerprint = bindings._fingerprint("object", unchanged)
        call(
            "assembly.configure",
            name=spec["name"],
            expected_revision=1,
            members=[
                {
                    "family": "segments",
                    "index": 7,
                    "shape": {"features": [{"id": "lug", "height": 0.1}]},
                }
            ],
        )
        self.assertEqual(fingerprint, bindings._fingerprint("object", unchanged))
        self.assertTrue(
            all(
                m["within_tolerance"]
                for m in call("measurement.inspect", queries=queries)["measurements"]
            )
        )
        mesh_names = [o.name for o in bpy.data.objects if o.type == "MESH"]
        for start in range(0, len(mesh_names), 16):
            qa = call(
                "geometry.inspect",
                objects=[
                    {"object_name": n, "self_intersection": True}
                    for n in mesh_names[start : start + 16]
                ],
                worst_limit=0,
            )
            for sample in qa["samples"]:
                self.assertTrue(
                    all(
                        o["degenerate_triangles"] == 0
                        and o["self_contact_triangle_pairs"] == 0
                        for o in sample["objects"]
                    )
                )
        before = {
            n: (
                assembly.content(bpy.data.objects[n]),
                bpy.data.objects[n][bindings.RESOURCE_KEY],
            )
            for n in mesh_names
        }
        counts = inventory()
        directory = Path(os.environ["TYVRANA_TEST_CONTROL"])
        spool = artifacts.ArtifactSpool()
        output, descriptor = render.render_image(
            models.RenderArguments(width=256, height=256, inspection={}), spool
        )
        self.assertEqual(len(output.inspection_tiles), 7)
        self.assertEqual(counts, inventory())
        destination = directory / "assembly-views.png"
        destination.write_bytes(
            artifacts.artifact_path(spool.root, descriptor).read_bytes()
        )
        (directory / "assembly-views.json").write_text(output.model_dump_json(indent=2))
        spool.close()
        path = directory / "assembly.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        after = {
            n: (
                assembly.content(bpy.data.objects[n]),
                bpy.data.objects[n][bindings.RESOURCE_KEY],
            )
            for n in mesh_names
        }
        self.assertEqual(before, after)
        self.assertTrue(call("assembly.inspect", name=spec["name"])["valid"])
        (directory / "assembly-master-summary.json").write_text(
            json.dumps(
                {
                    "components": 41,
                    "dimensions": 20,
                    "geometry": "pass",
                    "views": 7,
                    "save_reopen": True,
                },
                indent=2,
            )
        )

    def test_interface_projection_clearance_and_revision(self) -> None:
        receiver: dict[str, Any] = {
            "name": "Receiver",
            "sections": [
                {"id": "base", "center": [0, 0, 0], "radii": [0.3] * 4},
                {"id": "top", "center": [0, 0.5, 0], "radii": [0.3] * 4},
            ],
            "sides": 32,
            "subdivisions": 4,
            "ends": {"start_depth": 0.09, "end_depth": 0, "rings": 6},
        }
        insert = deepcopy(receiver)
        insert["name"] = "Insert"
        for section in insert["sections"]:
            section["radii"] = [0.16] * 4
        insert["ends"] = {"start_depth": 0, "end_depth": -0.06, "rings": 6}
        call("loft.create", components=[receiver, insert])
        target = {
            "kind": "geometry",
            "object": "Receiver",
            "region": "start_cap",
            "position": [0.5, 0.8, 0.5],
            "offset": 0.035,
        }
        local = {
            "kind": "geometry",
            "object": "Insert",
            "region": "end_cap",
            "position": [0.5, 1, 0.5],
        }
        call(
            "object_set.place",
            placements=[
                {
                    "name": "Insert",
                    "rule": {"kind": "frame", "target": target, "local_anchor": local},
                }
            ],
        )
        derived = call(
            "landmark.derive",
            landmarks=[
                {"name": "Interface", "source": {"kind": "geometry", "source": target}}
            ],
        )
        self.assertEqual(derived["solved"], 1)
        qa = call(
            "geometry.inspect",
            pairs=[{"left": "Receiver", "right": "Insert"}],
            worst_limit=2,
        )
        pair = qa["samples"][0]["pairs"][0]
        self.assertEqual(pair["contact_triangle_pairs"], 0)
        self.assertGreater(pair["minimum_distance"], 0.01)
        self.assertLess(pair["minimum_distance"], 0.04)
        self.assertEqual(pair["left_representatives_inside_right"], 0)
        self.assertEqual(pair["right_representatives_inside_left"], 0)
        call(
            "loft.configure",
            components=[
                {
                    "name": "Receiver",
                    "ends": {"start_depth": 0.1, "end_depth": 0, "rings": 6},
                }
            ],
        )
        self.assertFalse(construction.freshness(bpy.data.objects["Interface"]))
        self.assertFalse(placement.summary(bpy.data.objects["Insert"]).valid)
        call("object_set.place", refresh=["Insert"])
        self.assertTrue(placement.summary(bpy.data.objects["Insert"]).valid)
        before = inventory()
        result = execute(
            "landmark.derive",
            landmarks=[
                {
                    "name": "ValidPoint",
                    "source": {"kind": "geometry", "source": target},
                },
                {
                    "name": "InvalidPoint",
                    "source": {
                        "kind": "geometry",
                        "source": {**target, "region": "missing"},
                    },
                },
            ],
        )
        self.assertNotIsInstance(result, OperationSuccess)
        self.assertEqual(before, inventory())
        directory = Path(os.environ["TYVRANA_TEST_CONTROL"])
        (directory / "interface-clearance.json").write_text(json.dumps(qa, indent=2))

    def test_topology_rebuild_protection_and_mirror_rollback(self) -> None:
        spec = small_spec(2)
        spec["families"].append({"id": "paired", "mirror_of": "series", "count": 2})
        before = inventory()
        with patch.object(
            placement, "place", side_effect=RuntimeError("mirror failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "mirror failure"):
                assembly.create(assembly.AssemblyCreateArguments.model_validate(spec))
        self.assertEqual(before, inventory())
        call("assembly.create", **spec)
        members = [o for o in bpy.data.objects if o.type == "MESH"]
        ids = {o.name: o[bindings.RESOURCE_KEY] for o in members}
        template = deepcopy(spec["templates"][0])
        template["spec"]["sides"] = 28
        args = {"name": "Module", "expected_revision": 1, "templates": [template]}
        self.assertEqual(
            execute("assembly.configure", **args).error.code,
            "assembly_topology_change_required",
        )
        members[0].data.uv_layers.new(name="Protected")
        self.assertNotIsInstance(
            execute("assembly.configure", **args, topology_policy="rebuild"),
            OperationSuccess,
        )
        members[0].data.uv_layers.remove(members[0].data.uv_layers[0])
        snapshot = {o.name: (o.data.as_pointer(), assembly.content(o)) for o in members}
        with patch.object(
            assembly, "record", side_effect=RuntimeError("final record failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "final record failure"):
                assembly.configure(
                    assembly.AssemblyConfigureArguments.model_validate(
                        {**args, "topology_policy": "rebuild"}
                    )
                )
        self.assertEqual(
            snapshot,
            {o.name: (o.data.as_pointer(), assembly.content(o)) for o in members},
        )
        self.assertTrue(
            call("assembly.configure", **args, topology_policy="rebuild")["valid"]
        )
        self.assertEqual(ids, {o.name: o[bindings.RESOURCE_KEY] for o in members})

    def test_cleanup_explicit_holes_islands_duplicates_and_guards(self) -> None:
        obj = malformed_cube("Damaged")
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
        bm.to_mesh(obj.data)
        bm.free()
        before = loft.signature(obj.data)
        self.assertNotIsInstance(
            execute("mesh.cleanup", names=[obj.name], require_closed=True),
            OperationSuccess,
        )
        self.assertEqual(before, loft.signature(obj.data))
        preserved = call("mesh.cleanup", names=[obj.name], preview=True)["objects"][0]
        self.assertEqual(preserved["boundary_edges"], 4)
        fixed = call(
            "mesh.cleanup", names=[obj.name], fill_holes_up_to=4, require_closed=True
        )["objects"][0]
        self.assertEqual(fixed["boundary_edges"], 0)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.create_cube(bm, size=1)  # Duplicate coincident shell.
        a, b, c = [bm.verts.new((x, 5, 0)) for x in (0, 1, 2)]
        bm.faces.new((a, b, c))  # Zero-area fragment.
        other = bmesh.ops.create_cube(bm, size=0.1)
        bmesh.ops.translate(bm, verts=other["verts"], vec=Vector((4, 0, 0)))
        bm.to_mesh(obj.data)
        bm.free()
        cleaned = call(
            "mesh.cleanup",
            names=[obj.name],
            remove_islands_max_faces=6,
            require_closed=True,
        )["objects"][0]
        self.assertEqual(cleaned["vertices_after"], 8)
        self.assertEqual(cleaned["faces_after"], 6)
        call("loft.create", components=[compact_member("Managed")])
        managed = bpy.data.objects["Managed"]
        call(
            "project.bind",
            resources=[{"resource_kind": "object", "name": managed.name}],
        )
        resource_id = managed[bindings.RESOURCE_KEY]
        bm = bmesh.new()
        bm.from_mesh(managed.data)
        bm.verts.new((5, 5, 5))
        bm.to_mesh(managed.data)
        bm.free()
        self.assertEqual(
            execute("mesh.cleanup", names=[managed.name]).error.code,
            "cleanup_construction_protected",
        )
        self.assertTrue(
            call(
                "mesh.cleanup",
                names=[managed.name],
                detach_construction=True,
                triangulate_ngons=True,
            )["objects"][0]["construction_detached"]
        )
        self.assertNotIn(loft.KEY, managed)
        self.assertEqual(resource_id, managed.get(bindings.RESOURCE_KEY))
        self.assertTrue(all(len(p.vertices) <= 4 for p in managed.data.polygons))
        overlap = malformed_cube("Intersecting")
        bm = bmesh.new()
        bm.from_mesh(overlap.data)
        added = bmesh.ops.create_cube(bm, size=1)
        bmesh.ops.translate(bm, verts=added["verts"], vec=Vector((0.2, 0.3, 0.4)))
        bm.to_mesh(overlap.data)
        bm.free()
        before = loft.signature(overlap.data)
        self.assertEqual(
            execute(
                "mesh.cleanup", names=[overlap.name], require_closed=True
            ).error.code,
            "cleanup_postcondition_failed",
        )
        self.assertEqual(before, loft.signature(overlap.data))

    def test_multiview_failure_restores_scene_and_spool(self) -> None:
        call("loft.create", components=[compact_member("Subject")])
        scene = bpy.context.scene
        before = (
            inventory(),
            scene.camera,
            scene.render.engine,
            scene.display.shading.color_type,
            bpy.data.objects["Subject"].hide_render,
        )
        spool = artifacts.ArtifactSpool()
        count = 0

        def checkpoint() -> None:
            nonlocal count
            count += 1
            if count == 3:
                raise RuntimeError("cancelled diagnostic")

        with self.assertRaisesRegex(RuntimeError, "cancelled diagnostic"):
            next(
                render.render_steps(
                    models.RenderArguments(width=128, height=128, inspection={}),
                    spool,
                    checkpoint=checkpoint,
                )
            )
        self.assertEqual(
            before,
            (
                inventory(),
                scene.camera,
                scene.render.engine,
                scene.display.shading.color_type,
                bpy.data.objects["Subject"].hide_render,
            ),
        )
        self.assertFalse(list(spool.root.iterdir()))
        spool.close()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AssemblyTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"ASSEMBLY_NATIVE_PASSED {result.testsRun}", flush=True)
