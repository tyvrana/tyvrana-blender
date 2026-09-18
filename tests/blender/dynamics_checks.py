"""Native rooted XPBD dynamics, cache playback and cancellation recovery."""

import importlib
import sys
import unittest
from pathlib import Path

import bpy  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.blender.growth_checks import GrowthTests, growth, models  # noqa: E402

dynamics = importlib.import_module(str(growth.__package__) + ".growth_dynamics")
dm = importlib.import_module(str(growth.__package__) + ".dynamics_models")


class DynamicsTests(GrowthTests):
    def test_simulation_cache_and_cancellation(self) -> None:
        self.create()
        args = dm.DynamicsBakeArguments(
            object_name="Field", frame_start=1, frame_end=12
        )
        job = dynamics.start(args)
        while dynamics.busy():
            dynamics.tick()
        final = dynamics.status(job.job_id)
        self.assertEqual(final.state, "completed", final.error)
        self.assertGreater(final.result.maximum_displacement, 0.001)
        self.assertLess(final.result.maximum_root_error, 0.001)
        obj, _, _, group = growth.owned("Field")
        bpy.context.scene.frame_set(12)
        with growth.evaluated_path(obj, group) as data:
            tip = list(data.points[2].position)
        self.assertLess(self.inspect().summary.qa.maximum_root_error, 1e-5)
        old = final.result.cache_sha256
        job = dynamics.start(args.model_copy(update={"replace": True}))
        dynamics.tick()
        dynamics.cancel(job.job_id)
        self.assertEqual(
            dynamics.inspect(
                dm.DynamicsObjectArguments(object_name="Field")
            ).cache_sha256,
            old,
        )
        self.assertEqual(bpy.context.scene.frame_current, 12)
        job = dynamics.start(args.model_copy(update={"replace": True}))
        while dynamics.busy():
            dynamics.tick()
        final = dynamics.status(job.job_id)
        self.assertEqual(final.state, "completed", final.error)
        self.assertEqual(final.result.cache_sha256, old)
        obj, _, _, group = growth.owned("Field")
        with growth.evaluated_path(obj, group) as data:
            self.assertEqual(list(data.points[2].position), tip)
        dynamics.invalidate("blender.armature.pose")
        self.assertFalse(
            dynamics.inspect(dm.DynamicsObjectArguments(object_name="Field")).valid
        )
        dynamics.clear(dm.DynamicsObjectArguments(object_name="Field"))
        self.assertFalse(
            dynamics.inspect(dm.DynamicsObjectArguments(object_name="Field")).cached
        )

    def test_moving_surface_collision_and_deformable_templates(self) -> None:

        self.surface.location.z = 1
        self.surface.keyframe_insert("location", frame=1)
        self.surface.location.x = 0.5
        self.surface.keyframe_insert("location", frame=32)
        bpy.context.scene.frame_set(1)
        mesh = bpy.data.meshes.new("Blade")
        mesh.from_pydata(
            [
                [-0.01, 0, 0],
                [0.01, 0, 0],
                [-0.01, 0, 0.5],
                [0.01, 0, 0.5],
                [-0.01, 0, 1],
                [0.01, 0, 1],
            ],
            [],
            [[0, 1, 3, 2], [2, 3, 5, 4]],
        )
        leaf = bpy.data.objects.new("Blade", mesh)
        bpy.context.scene.collection.objects.link(leaf)
        self.spec = self.spec.model_copy(
            update={
                "families": [
                    models.GrowthFamily(
                        name="Fiber",
                        length=2,
                        shape=[[0, 0, 0], [0.5, 0, 0.05], [1, 0, 0.1]],
                        template=models.GrowthTemplate(
                            object_name="Blade", mode="deform"
                        ),
                    )
                ],
                "regions": [
                    models.GrowthRegion(
                        name="Region", family="Fiber", guides=8, children=0
                    )
                ],
            }
        )
        self.create()
        bpy.ops.mesh.primitive_plane_add(size=20)
        floor = bpy.context.object
        floor.name = "Floor"
        args = dm.DynamicsBakeArguments(
            object_name="Field",
            frame_start=1,
            frame_end=32,
            settings=dm.DynamicsSettings(
                surface_collision=False,
                colliders=["Floor"],
                bendiness=1,
                root_bendiness=1,
                linear_damping=1,
                angular_damping=1,
            ),
        )
        job = dynamics.start(args)
        while dynamics.busy():
            dynamics.tick()
        final = dynamics.status(job.job_id)
        self.assertEqual(final.state, "completed", final.error)
        self.assertLess(final.result.maximum_root_error, 0.002)
        bpy.context.scene.frame_set(32)
        obj, _, _, group = growth.owned("Field")
        with growth.evaluated_path(obj, group) as data:
            minimum = min((obj.matrix_world @ p.position).z for p in data.points)
        print("COLLIDER_MIN_Z", minimum)
        self.assertGreater(minimum, -0.005)
        self.assertLess(minimum, 0.1)
        qa = self.inspect(template_samples=32)
        self.assertLess(qa.summary.qa.maximum_root_error, 1e-5)
        from tests.blender.growth_checks import qa

        self.assertEqual(len(qa.template_points(obj, group, 32)), 32)
        old = final.result.cache_sha256
        import tempfile

        path = str(Path(tempfile.mkdtemp(prefix="tyvrana-dynamics-")) / "cache.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        self.assertEqual(
            dynamics.inspect(
                dm.DynamicsObjectArguments(object_name="Field")
            ).cache_sha256,
            old,
        )
        import shutil

        shutil.rmtree(Path(path).parent)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(
            [
                DynamicsTests("test_simulation_cache_and_cancellation"),
                DynamicsTests("test_moving_surface_collision_and_deformable_templates"),
            ]
        )
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("DYNAMICS_NATIVE_PASSED")
