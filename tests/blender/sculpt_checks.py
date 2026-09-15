"""Native surface picking, Multires preservation and interactive sculpt behavior."""

import importlib
import math
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
sculpt = importlib.import_module("bl_ext.user_default.tyvrana_blender.sculpt")
multires = importlib.import_module("bl_ext.user_default.tyvrana_blender.multires")
raycast = importlib.import_module("bl_ext.user_default.tyvrana_blender.raycast")
models = importlib.import_module("bl_ext.user_default.tyvrana_blender.sculpt_models")


def coordinates(obj: Any) -> list[Any]:
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    try:
        data = evaluated.to_mesh()
        return [v.co.copy() for v in data.vertices]
    finally:
        evaluated.to_mesh_clear()


def authored(obj: Any) -> Any:
    data = obj.data
    return (
        [tuple(v.co) for v in data.vertices],
        [(tuple(e.vertices), e.use_seam) for e in data.edges],
        [(tuple(f.vertices), f.material_index) for f in data.polygons],
        [
            (u.name, u.active_render, [tuple(v.vector) for v in u.uv])
            for u in data.uv_layers
        ],
        [m.name if m else None for m in data.materials],
        [[(g.group, g.weight) for g in v.groups] for v in data.vertices],
    )


class NativeCase(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.meshes):
            if data.users == 0:
                bpy.data.meshes.remove(data)
        bpy.ops.mesh.primitive_cube_add()
        self.obj = bpy.context.object
        self.obj.name = "Surface"
        self.backend = adapter.BlenderBackend()
        bpy.context.scene.render.use_simplify = False

    def response(self, op: str, **args: Any) -> Any:
        if not op.startswith("scene."):
            args = {"object_name": self.obj.name, **args}
        return operations.execute(
            self.backend,
            OperationRequest(
                type="operation.request",
                request_id="sculpt-test",
                operation="blender." + op,
                arguments=args,
            ),
        )

    def call(self, op: str, **args: Any) -> Any:
        response = self.response(op, **args)
        self.assertIsInstance(response, OperationSuccess, str(response))
        return response.result

    def error(self, op: str, code: str, **args: Any) -> Any:
        response = self.response(op, **args)
        self.assertIsInstance(response, OperationFailure, str(response))
        self.assertEqual(response.error.code, code, str(response))
        return response

    def add_multires(self, levels: int = 2) -> Any:
        self.call("multires.create")
        if levels:
            self.call("multires.subdivide", levels=levels)
        return self.obj.modifiers[0]

    def sphere(self, *, bump: float = 0) -> None:
        mod = self.obj.modifiers.new("Base", "SUBSURF")
        mod.levels = 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
        for vertex in self.obj.data.vertices:
            vertex.co.normalize()
            if vertex.co.z > 0:
                vertex.co.z += bump * math.exp(
                    -((vertex.co.x - 0.2) ** 2 + vertex.co.y**2) / 0.025
                )
        self.obj.data.update()

    def camera(self, kind: str = "PERSP") -> Any:
        data = bpy.data.cameras.new("Camera")
        data.type = kind
        data.ortho_scale = 6
        obj = bpy.data.objects.new("Camera", data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (0, 0, 5)
        bpy.context.scene.camera = obj
        bpy.context.view_layer.update()
        return obj


class RaycastTests(NativeCase):
    def test_world_normalization_distance_and_miss(self) -> None:
        result = self.call(
            "scene.raycast", mode="world", origin=[0, 0, 5], direction=[0, 0, -20]
        )
        self.assertTrue(result["hit"])
        self.assertEqual(result["object_name"], self.obj.name)
        self.assertAlmostEqual(result["distance"], 4)
        self.assertAlmostEqual(result["normal_world"][2], 1)
        miss = self.call(
            "scene.raycast", mode="world", origin=[10, 0, 5], direction=[0, 0, -1]
        )
        self.assertFalse(miss["hit"])
        self.assertTrue(
            all(value is None for key, value in miss.items() if key != "hit")
        )

    def test_perspective_center_and_off_object(self) -> None:
        self.camera()
        self.assertTrue(self.call("scene.raycast", mode="camera", u=0.5, v=0.5)["hit"])
        self.assertFalse(self.call("scene.raycast", mode="camera", u=0, v=0)["hit"])

    def test_orthographic_top_left_convention(self) -> None:
        self.camera("ORTHO")
        result = self.call(
            "scene.raycast", mode="camera", u=0.55, v=0.45, width=512, height=512
        )
        self.assertAlmostEqual(result["location_world"][0], 0.3, places=5)
        self.assertAlmostEqual(result["location_world"][1], 0.3, places=5)

    def test_render_dimensions_and_pixel_aspect(self) -> None:
        self.camera("ORTHO")
        scene = bpy.context.scene
        for width, height, px, py in [
            (800, 400, 1, 1),
            (400, 800, 1, 1),
            (800, 400, 1, 2),
        ]:
            with self.subTest(dimensions=(width, height, px, py)):
                scene.render.resolution_x, scene.render.resolution_y = width, height
                scene.render.pixel_aspect_x, scene.render.pixel_aspect_y = px, py
                default = self.call("scene.raycast", mode="camera", u=0.55, v=0.45)
                explicit = self.call(
                    "scene.raycast",
                    mode="camera",
                    u=0.55,
                    v=0.45,
                    width=width,
                    height=height,
                )
                self.assertEqual(default, explicit)
                expected = 0.3 * min(1, height * py / (width * px))
                self.assertAlmostEqual(
                    explicit["location_world"][1], expected, places=5
                )
        scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1

    def test_shift_sensor_and_lens_agree_with_native_projection(self) -> None:
        from bpy_extras import object_utils  # type: ignore[import-not-found]

        camera = self.camera()
        scene = bpy.context.scene
        scene.render.resolution_x, scene.render.resolution_y = 800, 400
        camera.data.shift_x, camera.data.shift_y = 0.15, -0.1
        camera.data.sensor_fit = "VERTICAL"
        camera.data.sensor_height = 30
        camera.data.lens = 60
        bpy.context.view_layer.update()
        projected = object_utils.world_to_camera_view(
            scene, camera, Vector((0.3, 0.2, 1))
        )
        result = self.call(
            "scene.raycast", mode="camera", u=projected.x, v=1 - projected.y
        )
        for value, expected in zip(
            result["location_world"], [0.3, 0.2, 1], strict=True
        ):
            self.assertAlmostEqual(value, expected, places=4)

    def test_transformed_camera_and_object(self) -> None:
        camera = self.camera()
        self.obj.location = (2, 3, 1)
        self.obj.rotation_euler = (0.2, 0.3, 0.4)
        self.obj.scale = (2, 0.75, 1.5)
        bpy.context.view_layer.update()
        point = self.obj.matrix_world @ Vector((0, 0, 1))
        normal = (
            self.obj.matrix_world.to_3x3().inverted().transposed() @ Vector((0, 0, 1))
        ).normalized()
        camera.location = point + normal * 4
        camera.rotation_euler = (-normal).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        result = self.call("scene.raycast", mode="camera", u=0.5, v=0.5)
        self.assertLess((Vector(result["location_world"]) - point).length, 1e-4)
        self.assertLess(
            (Vector(result["location_object"]) - Vector((0, 0, 1))).length, 1e-4
        )
        self.assertLess(
            (Vector(result["normal_object"]) - Vector((0, 0, 1))).length, 1e-4
        )

    def test_active_and_explicit_camera(self) -> None:
        first = self.camera()
        second = self.camera("ORTHO")
        second.location.x = 10
        bpy.context.view_layer.update()
        self.assertFalse(self.call("scene.raycast", mode="camera", u=0.5, v=0.5)["hit"])
        self.assertTrue(
            self.call(
                "scene.raycast", mode="camera", camera_name=first.name, u=0.5, v=0.5
            )["hit"]
        )
        self.assertEqual(bpy.context.scene.camera, second)

    def test_no_camera_and_unsupported_projection(self) -> None:
        bpy.context.scene.camera = None
        self.error("scene.raycast", "no_camera", mode="camera", u=0.5, v=0.5)
        camera = self.camera("PANO")
        self.error(
            "scene.raycast", "unsupported_projection", mode="camera", u=0.5, v=0.5
        )
        self.assertEqual(camera.data.type, "PANO")

    def test_max_distance_and_camera_clipping(self) -> None:
        self.assertFalse(
            self.call(
                "scene.raycast",
                mode="world",
                origin=[0, 0, 5],
                direction=[0, 0, -1],
                max_distance=3,
            )["hit"]
        )
        camera = self.camera()
        camera.data.clip_end = 3
        self.assertFalse(self.call("scene.raycast", mode="camera", u=0.5, v=0.5)["hit"])
        camera.data.clip_end = 100
        camera.data.clip_start = 7
        self.assertFalse(self.call("scene.raycast", mode="camera", u=0.5, v=0.5)["hit"])

    def test_hits_evaluated_multires(self) -> None:
        self.add_multires(3)
        result = self.call(
            "scene.raycast", mode="world", origin=[0, 0, 5], direction=[0, 0, -1]
        )
        self.assertLess(result["location_object"][2], 0.9)
        self.assertGreaterEqual(result["evaluated_face_index"], 6)
        self.assertEqual(len(self.obj.data.polygons), 6)

    def test_temporary_evaluated_data_is_released(self) -> None:
        self.add_multires()
        count = len(bpy.data.meshes)
        for _ in range(5):
            self.call(
                "scene.raycast", mode="world", origin=[0, 0, 5], direction=[0, 0, -1]
            )
        self.assertEqual(count, len(bpy.data.meshes))


class MultiresTests(NativeCase):
    def test_levels_and_authored_topology(self) -> None:
        before = authored(self.obj)
        result = self.call("multires.create")
        self.assertTrue(result["present"])
        self.assertEqual(result["total_levels"], 0)
        self.call("multires.subdivide")
        self.assertEqual(self.call("multires.inspect")["total_levels"], 1)
        self.call("multires.subdivide", levels=2)
        self.assertEqual(self.call("mesh.inspect_evaluated")["face_count"], 384)
        self.call(
            "multires.configure", viewport_level=1, sculpt_level=2, render_level=3
        )
        result = self.call("multires.inspect")
        self.assertEqual(
            [result[k] for k in ["viewport_level", "sculpt_level", "render_level"]],
            [1, 2, 3],
        )
        self.assertEqual(authored(self.obj), before)

    def test_all_subdivision_modes(self) -> None:
        mod = self.add_multires(0)
        for mode in ["catmull_clark", "simple", "linear"]:
            self.call("multires.subdivide", mode=mode)
        self.assertEqual(mod.total_levels, 3)

    def test_invalid_level_is_atomic(self) -> None:
        self.add_multires()
        before = self.call("multires.inspect")
        self.error(
            "multires.configure", "invalid_arguments", viewport_level=0, render_level=3
        )
        self.assertEqual(before, self.call("multires.inspect"))

    def test_total_and_geometry_limits(self) -> None:
        self.add_multires(1)
        self.error("multires.subdivide", "invalid_context", levels=6)
        with patch.object(multires.mesh, "MAX_WORK_ELEMENTS", 500):
            self.error("multires.subdivide", "invalid_context", levels=3)
        self.assertEqual(self.obj.modifiers[0].total_levels, 1)

    def test_stack_ownership_and_generic_guards(self) -> None:
        mod = self.add_multires(1)
        generic = self.call("modifier.inspect")["modifiers"][0]
        self.assertEqual(generic["type"], "multires")
        self.assertFalse(generic["supported"])
        self.error(
            "modifier.configure",
            "modifier_type_unsupported",
            modifier_name=mod.name,
            type="mirror",
        )
        self.error("modifier.remove", "invalid_context", modifier_name=mod.name)
        self.error("modifier.apply", "invalid_context", modifier_name=mod.name)
        self.error("modifier.move", "invalid_context", modifier_name=mod.name, index=0)
        self.error("multires.create", "invalid_context")

    def test_existing_generators_require_explicit_resolution(self) -> None:
        for kind in ["MIRROR", "SUBSURF", "SOLIDIFY", "BOOLEAN", "ARMATURE"]:
            with self.subTest(kind=kind):
                mod = self.obj.modifiers.new("Existing", kind)
                self.error("multires.create", "invalid_context")
                self.assertEqual(list(self.obj.modifiers), [mod])
                self.obj.modifiers.remove(mod)

    def test_shape_keys_protected_and_inspectable(self) -> None:
        self.obj.shape_key_add(name="Basis")
        result = self.call("multires.inspect")
        self.assertIn("shape_keys", [d["code"] for d in result["diagnostics"]])
        self.error("multires.create", "mesh_has_shape_keys")

    def test_nonquad_boundary_diagnostics_are_not_prohibitions(self) -> None:
        self.obj.data.clear_geometry()
        self.obj.data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        result = self.call("multires.create")
        self.assertEqual(result["base_mesh"]["non_quad_face_count"], 1)
        self.assertEqual(result["base_mesh"]["non_manifold_edge_count"], 3)
        self.call("multires.subdivide")

    def test_uv_material_seam_attribute_and_weight_preservation(self) -> None:
        mat = bpy.data.materials.new("Finish")
        self.obj.data.materials.append(mat)
        self.obj.data.edges[0].use_seam = True
        attr = self.obj.data.attributes.new("Production", "FLOAT", "POINT")
        for item in attr.data:
            item.value = 0.37
        group = self.obj.vertex_groups.new(name="Region")
        group.add([0, 1], 0.4, "REPLACE")
        before = authored(self.obj)
        self.add_multires(3)
        self.call(
            "multires.configure", viewport_level=0, sculpt_level=1, render_level=2
        )
        self.call("multires.configure", viewport_level=3, sculpt_level=3)
        self.assertEqual(authored(self.obj), before)
        self.assertTrue(
            all(
                abs(x.value - 0.37) < 1e-6
                for x in self.obj.data.attributes["Production"].data
            )
        )

    def test_shared_displacement_is_isolated_before_subdivision(self) -> None:
        sibling = self.obj.copy()
        sibling.data = self.obj.data
        bpy.context.scene.collection.objects.link(sibling)
        original = self.obj.data
        before = authored(sibling)
        self.add_multires(2)
        self.assertNotEqual(self.obj.data, original)
        self.assertEqual(sibling.data, original)
        self.assertEqual(authored(sibling), before)

    def test_topology_guards_and_coordinate_only_multires_edit(self) -> None:
        modifier = self.add_multires(2)
        before = authored(self.obj)
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "mesh.extrude_faces",
                {"selector": {"domain": "face", "mode": "all"}, "offset": [0, 0, 1]},
            ),
            (
                "mesh.inset_faces",
                {"selector": {"domain": "face", "mode": "all"}, "thickness": 0.1},
            ),
            (
                "mesh.bevel_edges",
                {"selector": {"domain": "edge", "mode": "all"}, "width": 0.1},
            ),
            (
                "mesh.subdivide_edges",
                {"selector": {"domain": "edge", "mode": "all"}, "cuts": 1},
            ),
            ("mesh.delete_elements", {"selector": {"domain": "face", "mode": "all"}}),
            (
                "mesh.merge_vertices",
                {"selector": {"domain": "vertex", "mode": "all"}, "mode": "center"},
            ),
        ]
        for op, args in cases:
            with self.subTest(operation=op):
                self.error(op, "invalid_context", **args)
        self.assertEqual(authored(self.obj), before)
        # Coordinate-only cage edits preserve modifiers and ordered native data.
        # This policy predates the registry; topology edits above remain forbidden.
        evaluated_before = coordinates(self.obj)
        result = self.call(
            "mesh.transform",
            selector={"domain": "vertex", "mode": "all"},
            translation=[0, 0, 1],
        )
        self.assertEqual(result["transformed_vertices"], len(before[0]))
        self.assertEqual(authored(self.obj)[1:], before[1:])
        self.assertEqual(list(self.obj.modifiers), [modifier])
        self.assertEqual(modifier.total_levels, 2)
        evaluated_after = coordinates(self.obj)
        self.assertEqual(len(evaluated_before), len(evaluated_after))
        for old, new in zip(evaluated_before, evaluated_after, strict=True):
            self.assertLess((new - old - Vector((0, 0, 1))).length, 1e-5)

    def test_headless_stroke_rejection(self) -> None:
        if not bpy.app.background:
            self.skipTest("Headless-only check")
        self.error(
            "sculpt.stroke",
            "invalid_context",
            brush="draw",
            samples=[{"location": [0, 0, 1]}],
            radius=0.3,
            strength=0.5,
        )

    def test_main_thread_guards(self) -> None:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for method, args in [
                (
                    "scene_raycast",
                    models.WorldRayArguments(
                        mode="world", origin=[0, 0, 3], direction=[0, 0, -1]
                    ),
                ),
                (
                    "multires_inspect",
                    models.MultiresInspectArguments(object_name=self.obj.name),
                ),
                (
                    "sculpt_inspect",
                    models.SculptInspectArguments(object_name=self.obj.name),
                ),
            ]:
                with self.subTest(method=method), self.assertRaises(RuntimeError):
                    pool.submit(getattr(self.backend, method), args).result()


class SculptTests(NativeCase):
    def stroke(
        self,
        brush: str = "draw",
        *,
        location: list[float] | None = None,
        count: int = 8,
        **kwargs: Any,
    ) -> Any:
        return self.call(
            "sculpt.stroke",
            brush=brush,
            samples=[{"location": location or [0, 0, 1]}] * count,
            radius=0.3,
            strength=0.5,
            **kwargs,
        )

    def test_draw_raises_surface(self) -> None:
        self.sphere()
        before = max(v.co.z for v in self.obj.data.vertices)
        self.assertTrue(self.stroke()["changed"])
        self.assertGreater(max(v.co.z for v in self.obj.data.vertices), before + 0.02)

    def test_inflate_increases_local_radius(self) -> None:
        self.sphere()
        self.stroke("inflate")
        radii = [v.co.length for v in self.obj.data.vertices if v.co.z > 0.98]
        self.assertGreater(sum(radii) / len(radii), 1.02)

    def test_clay_builds_volume(self) -> None:
        self.sphere()
        self.stroke("clay")
        self.assertGreater(max(v.co.z for v in self.obj.data.vertices), 1.01)

    def test_crease_forms_groove(self) -> None:
        self.sphere()
        self.stroke("crease")
        near = [
            v.co.z
            for v in self.obj.data.vertices
            if abs(v.co.x) < 0.06 and abs(v.co.y) < 0.06 and v.co.z > 0.5
        ]
        self.assertLess(sum(near) / len(near), 0.97)

    def test_smooth_reduces_bump_magnitude(self) -> None:
        self.sphere(bump=0.2)
        before = max(v.co.z for v in self.obj.data.vertices)
        self.stroke("smooth", location=[0.2, 0, 1.17], count=40)
        self.assertLess(max(v.co.z for v in self.obj.data.vertices), before - 0.03)

    def test_flatten_reduces_local_height_range(self) -> None:
        data = self.obj.data
        data.clear_geometry()
        vertices = [
            (
                x / 16 - 1,
                y / 16 - 1,
                0.2 * math.exp(-((x / 16 - 1) ** 2 + (y / 16 - 1) ** 2) / 0.035),
            )
            for y in range(33)
            for x in range(33)
        ]
        faces = [
            (y * 33 + x, y * 33 + x + 1, (y + 1) * 33 + x + 1, (y + 1) * 33 + x)
            for y in range(32)
            for x in range(32)
        ]
        data.from_pydata(vertices, [], faces)
        data.update()
        region = [v.index for v in data.vertices if v.co.x**2 + v.co.y**2 < 0.02]
        self.assertGreater(len(region), 3)
        before = [data.vertices[i].co.z for i in region]
        result = self.call(
            "sculpt.stroke",
            brush="flatten",
            samples=[
                {"location": [x / 100, 0, 0.2 * math.exp(-((x / 100) ** 2) / 0.035)]}
                for x in list(range(-12, 13)) + list(range(12, -13, -1))
            ],
            radius=0.3,
            strength=0.5,
        )
        self.assertTrue(result["changed"])
        after = [data.vertices[i].co.z for i in region]
        self.assertLess(max(after) - min(after), (max(before) - min(before)) * 0.85)

    def test_invert_reverses_draw(self) -> None:
        self.sphere()
        self.stroke(invert=True)
        self.assertLess(max(v.co.z for v in self.obj.data.vertices), 0.99)

    def test_zero_pressure_and_zero_strength_are_noops(self) -> None:
        self.sphere()
        for pressure, strength in [(0, 0.5), (1, 0)]:
            before = authored(self.obj)
            result = self.call(
                "sculpt.stroke",
                brush="draw",
                samples=[{"location": [0, 0, 1], "pressure": pressure}],
                radius=0.3,
                strength=strength,
            )
            self.assertFalse(result["changed"])
            self.assertEqual(authored(self.obj), before)

    def test_symmetry_and_restoration(self) -> None:
        for enabled in [False, True]:
            with self.subTest(enabled=enabled):
                self.setUp()
                self.sphere()
                self.obj.data.use_mirror_x = not enabled
                before = coordinates(self.obj)
                self.stroke(location=[0.6, 0, 0.8], symmetry={"x": enabled})
                after = coordinates(self.obj)
                right = max(
                    (a - b).length
                    for a, b in zip(before, after, strict=True)
                    if a.x > 0.4
                )
                left = max(
                    (a - b).length
                    for a, b in zip(before, after, strict=True)
                    if a.x < -0.4
                )
                self.assertGreater(right, 0.01)
                if enabled:
                    self.assertAlmostEqual(left, right, places=4)
                    mirrored = {
                        tuple(round(c, 4) for c in (-v.x, v.y, v.z)) for v in after
                    }
                    self.assertTrue(
                        all(tuple(round(c, 4) for c in v) in mirrored for v in after)
                    )
                else:
                    self.assertLess(left, 1e-6)
                self.assertEqual(self.obj.data.use_mirror_x, not enabled)

    def test_unapplied_and_inherited_scale_rejected(self) -> None:
        for scale in [(2, 2, 2), (1, 2, 1), (-1, 1, 1)]:
            self.obj.scale = scale
            bpy.context.view_layer.update()
            self.error(
                "sculpt.stroke",
                "sculpt_unapplied_scale",
                brush="draw",
                samples=[{"location": [0, 0, 1]}],
                radius=0.3,
                strength=0.5,
            )
        self.obj.scale = (1, 1, 1)
        parent = bpy.data.objects.new("Parent", None)
        bpy.context.scene.collection.objects.link(parent)
        self.obj.parent = parent
        parent.scale = (2, 2, 2)
        bpy.context.view_layer.update()
        self.error(
            "sculpt.stroke",
            "sculpt_unapplied_scale",
            brush="draw",
            samples=[{"location": [0, 0, 1]}],
            radius=0.3,
            strength=0.5,
        )

    def test_off_surface_preflight_and_snapping(self) -> None:
        self.sphere()
        before = authored(self.obj)
        self.error(
            "sculpt.stroke",
            "invalid_arguments",
            brush="draw",
            samples=[{"location": [0, 0, 1]}, {"location": [0, 0, 20]}],
            radius=0.3,
            strength=0.5,
        )
        self.assertEqual(authored(self.obj), before)
        result = self.stroke(location=[0, 0, 1.1], count=1)
        self.assertAlmostEqual(result["max_snap_distance"], 0.1, places=4)
        self.assertAlmostEqual(result["snapped_locations"][0][2], 1, places=4)

    def test_multires_displacement_and_level_persistence(self) -> None:
        self.sphere()
        mod = self.add_multires(1)
        before_base = authored(self.obj)
        self.stroke(location=[0.6, 0, 0.8], count=5)
        self.call("multires.subdivide", levels=2)
        self.stroke(location=[0, 0, 1], count=4)
        detail = coordinates(self.obj)
        self.call("multires.configure", viewport_level=1, sculpt_level=1)
        self.assertLess(len(coordinates(self.obj)), len(detail))
        self.call("multires.configure", viewport_level=3, sculpt_level=3)
        self.assertEqual(coordinates(self.obj), detail)
        self.assertEqual(authored(self.obj), before_base)
        self.assertEqual(mod.total_levels, 3)

    def test_scene_brush_mode_selection_and_view_restored(self) -> None:
        self.sphere()
        other = bpy.data.objects.new("Other", None)
        bpy.context.scene.collection.objects.link(other)
        self.obj.select_set(False)
        other.select_set(True)
        bpy.context.view_layer.objects.active = other
        area, _, rv = sculpt.view_context()
        before = (
            bpy.context.scene,
            bpy.context.view_layer.objects.active,
            [o.name for o in bpy.context.selected_objects],
            bpy.context.tool_settings.sculpt.brush,
            tuple(rv.view_rotation),
            tuple(rv.view_location),
            rv.view_distance,
            rv.view_perspective,
            len(bpy.data.brushes),
            len(bpy.data.scenes),
            len(bpy.data.meshes),
            len(bpy.data.libraries),
        )
        self.stroke(count=1)
        after = (
            bpy.context.scene,
            bpy.context.view_layer.objects.active,
            [o.name for o in bpy.context.selected_objects],
            bpy.context.tool_settings.sculpt.brush,
            tuple(rv.view_rotation),
            tuple(rv.view_location),
            rv.view_distance,
            rv.view_perspective,
            len(bpy.data.brushes),
            len(bpy.data.scenes),
            len(bpy.data.meshes),
            len(bpy.data.libraries),
        )
        self.assertEqual(before, after)
        self.assertEqual(bpy.context.mode, "OBJECT")
        self.assertEqual(area.type, "VIEW_3D")

    def test_existing_sculpt_brush_and_tool_are_restored(self) -> None:
        self.sphere()
        area, region, _ = sculpt.view_context()
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.object.mode_set(mode="SCULPT")
            bpy.ops.brush.asset_activate(
                asset_library_type="ESSENTIALS",
                relative_asset_identifier="brushes/essentials_brushes-mesh_sculpt.blend/Brush/Smooth",
            )
            brush = bpy.context.tool_settings.sculpt.brush
            brush.strength = 0.237
            tool = bpy.context.workspace.tools.from_space_view3d_mode("SCULPT").idname
            self.stroke(count=1)
            self.assertEqual(bpy.context.mode, "SCULPT")
            self.assertEqual(bpy.context.tool_settings.sculpt.brush, brush)
            self.assertAlmostEqual(brush.strength, 0.237, places=6)
            self.assertEqual(
                bpy.context.workspace.tools.from_space_view3d_mode("SCULPT").idname,
                tool,
            )
            bpy.ops.object.mode_set(mode="OBJECT")

    def test_camera_pick_to_native_stroke(self) -> None:
        self.sphere()
        self.add_multires(2)
        self.camera()
        hit = self.call(
            "scene.raycast", mode="camera", u=0.53, v=0.5, width=512, height=512
        )
        self.assertEqual(hit["object_name"], self.obj.name)
        before = authored(self.obj)
        result = self.stroke(location=hit["location_object"])
        self.assertTrue(result["changed"])
        self.assertEqual(authored(self.obj), before)

    def test_radius_is_independent_of_user_view_zoom(self) -> None:
        results = []
        for distance in [2, 200]:
            self.setUp()
            self.sphere()
            _, _, view = sculpt.view_context()
            view.view_distance = distance
            self.stroke(count=4)
            results.append([tuple(v.co) for v in self.obj.data.vertices])
            self.assertEqual(view.view_distance, distance)
        self.assertEqual(results[0], results[1])

    def test_distinct_3d_samples_target_both_regions(self) -> None:
        self.sphere()
        before = coordinates(self.obj)
        self.call(
            "sculpt.stroke",
            brush="draw",
            samples=[{"location": [x, 0, 0.8]} for x in [0.6] * 4 + [-0.6] * 4],
            radius=0.2,
            strength=0.5,
        )
        after = coordinates(self.obj)
        for sign in [-1, 1]:
            self.assertGreater(
                max(
                    (a - b).length
                    for a, b in zip(before, after, strict=True)
                    if a.x * sign > 0.4
                ),
                0.01,
            )

    def test_simplify_and_shared_mesh_rejected(self) -> None:
        self.sphere()
        self.add_multires()
        bpy.context.scene.render.use_simplify = True
        self.error(
            "sculpt.stroke",
            "invalid_context",
            brush="draw",
            samples=[{"location": [0, 0, 1]}],
            radius=0.3,
            strength=0.5,
        )
        bpy.context.scene.render.use_simplify = False
        other = self.obj.copy()
        bpy.context.scene.collection.objects.link(other)
        self.error(
            "sculpt.stroke",
            "invalid_context",
            brush="draw",
            samples=[{"location": [0, 0, 1]}],
            radius=0.3,
            strength=0.5,
        )

    def test_masks_are_honored_without_modification(self) -> None:
        self.sphere()
        mask = self.obj.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        for value in mask.data:
            value.value = 1
        self.assertTrue(self.call("sculpt.inspect")["mask"]["base_mesh"]["present"])
        before = authored(self.obj)
        self.assertFalse(self.stroke()["changed"])
        self.assertEqual(before, authored(self.obj))
        self.assertTrue(all(v.value == 1 for v in mask.data))

    def test_lower_level_form_edit_preserves_higher_detail(self) -> None:
        self.sphere()
        self.add_multires(3)
        self.stroke(count=5)
        high = coordinates(self.obj)
        peak = max(v.z for v in high)
        self.assertGreater(peak, 1.02)
        self.call("multires.configure", viewport_level=1, sculpt_level=1)
        self.stroke(location=[0.8, 0, 0.6], count=3)
        self.call("multires.configure", viewport_level=3, sculpt_level=3)
        final = coordinates(self.obj)
        self.assertEqual(len(final), len(high))
        self.assertGreater(max(v.z for v in final), 1.02)
        self.assertTrue(
            any((a - b).length > 0.005 for a, b in zip(high, final, strict=True))
        )

    def test_failure_reports_possible_mutation_and_restores_state(self) -> None:
        self.sphere()
        scene = bpy.context.scene
        before = authored(self.obj)
        original_surface = sculpt.surface
        calls = 0

        def fail_after_stroke(obj: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("post-stroke inspection failure")
            return original_surface(obj)

        with (
            patch.object(sculpt, "surface", side_effect=fail_after_stroke),
            self.assertLogs(sculpt.logger, level="ERROR"),
        ):
            response = self.error(
                "sculpt.stroke",
                "sculpt_failed",
                brush="draw",
                samples=[{"location": [0, 0, 1]}],
                radius=0.3,
                strength=0.5,
            )
        self.assertTrue(response.error.details["mutation_possible"])
        self.assertNotEqual(authored(self.obj), before)
        self.assertEqual(bpy.context.scene, scene)
        self.assertEqual(bpy.context.mode, "OBJECT")
        self.assertEqual(len(bpy.data.scenes), 1)


def run() -> None:
    runtime = adapter._runtime
    worker = runtime.worker if runtime else None
    adapter.unregister()
    if worker:
        assert worker.process.returncode == 0
        assert not worker.spool.root.exists()
    suite = unittest.TestSuite()
    classes = [SculptTests] if not bpy.app.background else [RaycastTests, MultiresTests]
    for cls in classes:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert not bpy.app.timers.is_registered(adapter.pump)
    if result.wasSuccessful():
        print("BLENDER_SCULPT_TESTS_PASSED", result.testsRun, flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    elif not result.wasSuccessful():
        raise RuntimeError("Native sculpt foundation checks failed")


if __name__ == "__main__":
    if bpy.app.background:
        run()
    else:
        bpy.app.timers.register(run, first_interval=3)
