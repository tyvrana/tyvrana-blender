"""Exercise native script reload on an isolated, unsaved Blender project."""

import importlib
import sys

import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender"


def main() -> None:
    bpy.context.preferences.addons[PACKAGE].preferences.port = 1
    bpy.data.objects["Cube"].location = (1.25, -2.5, 3.75)
    image = bpy.data.images.new("Unsaved image", width=8, height=8, float_buffer=True)
    image.pixels[0] = 0.625
    assert image.pixels[0] == 0.625
    before = [
        (obj.name, obj.as_pointer(), tuple(obj.location)) for obj in bpy.data.objects
    ]
    image_pointer = image.as_pointer()
    for _ in range(2):
        old = importlib.import_module(PACKAGE + ".blender")
        old.pump()
        worker = old._runtime.worker
        spool = worker.spool.root
        model = importlib.import_module(PACKAGE + ".retopo_models")
        old_class = model.RetopoExtrudeArguments
        bpy.utils.load_scripts(reload_scripts=True)
        new = importlib.import_module(PACKAGE + ".blender")
        new_model = importlib.import_module(PACKAGE + ".retopo_models")
        assert new is not old
        assert new_model is not model
        assert new_model.RetopoExtrudeArguments is not old_class
        assert sys.modules[PACKAGE].blender is new
        assert not old._enabled and old._runtime is None
        assert not bpy.app.timers.is_registered(old.pump)
        assert worker.process.poll() is not None
        assert not spool.exists()
        assert new._enabled and bpy.app.timers.is_registered(new.pump)
        for handlers, callback in new._handlers:
            assert list(handlers).count(callback) == 1
        assert before == [
            (obj.name, obj.as_pointer(), tuple(obj.location))
            for obj in bpy.data.objects
        ]
        assert bpy.data.images["Unsaved image"].as_pointer() == image_pointer
        assert image.pixels[0] == 0.625
        new.pump()
        assert new._runtime.worker.process.poll() is None
        assert new._runtime.worker.process.pid != worker.process.pid
    importlib.import_module(PACKAGE).unregister()
    print("BLENDER_RELOAD_TESTS_PASSED")


def run() -> None:
    try:
        main()
    finally:
        bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    bpy.app.timers.register(run, first_interval=1)
