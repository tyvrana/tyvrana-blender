"""Isolated host: assert native state and owned-resource invariants across reloads."""

import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender"
control = Path(os.environ["TYVRANA_TEST_CONTROL"])
lifecycle = importlib.import_module(PACKAGE + ".lifecycle")
preferences = bpy.context.preferences.addons[PACKAGE].preferences
preferences.host = "127.0.0.1"
preferences.port = int(os.environ["TYVRANA_TEST_PORT"])
lifecycle._backend.restart()

# Fixture creation is confined to this disposable profile/project.
from mathutils import Vector  # type: ignore[import-not-found]  # noqa: E402

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.edit.undo_steps = 41
cube = bpy.data.objects["Cube"]
cube.location = (1.25, -2.5, 3.75)
cube.modifiers.new("Preserved subdivision", "SUBSURF").levels = 1
material = bpy.data.materials.new("Preserved material")
cube.data.materials.append(material)
bpy.context.scene.camera.location = (8, -9, 8)
bpy.context.scene.camera.rotation_euler = (
    (Vector(cube.location) - bpy.context.scene.camera.location)
    .to_track_quat("-Z", "Y")
    .to_euler()
)
bpy.context.scene.frame_set(19)
image = bpy.data.images.new("Unsaved float image", width=8, height=8, float_buffer=True)
image.pixels[0] = 0.625
cube["unsaved_property"] = "preserve"


def unrelated_timer() -> float:
    return 1.0


def unrelated_handler(*args: object) -> None:
    pass


bpy.app.handlers.persistent(unrelated_handler)
bpy.app.timers.register(unrelated_timer, persistent=True)
bpy.app.handlers.load_post.append(unrelated_handler)


def state() -> dict[str, object]:
    return {
        "objects": [
            (
                o.name,
                o.as_pointer(),
                tuple(o.location),
                tuple(o.rotation_euler),
                tuple(o.scale),
                o.select_get(),
            )
            for o in bpy.data.objects
        ],
        "vertices": [tuple(v.co) for v in cube.data.vertices],
        "polygons": [tuple(p.vertices) for p in cube.data.polygons],
        "modifiers": [(m.name, m.type, m.levels) for m in cube.modifiers],
        "materials": [(m.name, m.as_pointer()) for m in bpy.data.materials],
        "camera": bpy.context.scene.camera.as_pointer(),
        "frame": bpy.context.scene.frame_current,
        "active": bpy.context.view_layer.objects.active.as_pointer(),
        "preferences": (preferences.host, preferences.port),
        "unrelated_preferences": (
            bpy.context.preferences.view.show_splash,
            bpy.context.preferences.edit.undo_steps,
        ),
        "addons": sorted(a.module for a in bpy.context.preferences.addons),
        "image": (image.as_pointer(), image.pixels[0]),
        "custom": cube["unsaved_property"],
    }


baseline = state()
workers: dict[int, Any] = {}
spools: set[Path] = set()
seen_modules: set[int] = set()
deadline = time.monotonic() + 150


def check() -> float | None:
    global preferences
    if time.monotonic() > deadline:
        raise RuntimeError("Lifecycle host deadline exceeded")
    backend = lifecycle._backend
    assert backend is not None
    preferences = bpy.context.preferences.addons[PACKAGE].preferences
    assert state() == baseline, "Native project/preferences changed across reload"
    assert list(bpy.app.handlers.load_post).count(unrelated_handler) == 1
    assert bpy.app.timers.is_registered(unrelated_timer)
    assert len(backend._registered_classes) == 2
    assert bpy.app.timers.is_registered(backend.pump)
    for handlers, callback in backend._handlers:
        assert list(handlers).count(callback) == 1
        assert (
            len(
                [
                    f
                    for f in handlers
                    if getattr(f, "__module__", "").startswith(PACKAGE + ".")
                ]
            )
            == 1
        )
    runtime = backend._runtime
    if runtime is not None:
        worker = runtime.worker
        workers[worker.process.pid] = worker
        spools.add(worker.spool.root)
        seen_modules.add(id(backend))
        for pid, old in workers.items():
            if pid != worker.process.pid:
                assert old.process.poll() is not None
                assert not old.spool.root.exists()
        output = {
            **lifecycle.inspect(),
            "host_pid": os.getpid(),
            "module_count_seen": len(seen_modules),
            "state_unchanged": True,
        }
        (control / "ready.tmp").write_text(json.dumps(output))
        (control / "ready.tmp").replace(control / "ready.json")
    if (control / "stop").exists():
        importlib.import_module(PACKAGE).unregister()
        assert not bpy.app.timers.is_registered(backend.pump)
        assert not bpy.app.timers.is_registered(lifecycle._poll)
        for handlers, callback in backend._handlers:
            assert callback not in handlers
        assert not backend._registered_classes
        for worker in workers.values():
            assert worker.process.poll() is not None
        assert not any(path.exists() for path in spools)
        assert bpy.app.timers.is_registered(unrelated_timer)
        assert unrelated_handler in bpy.app.handlers.load_post
        bpy.app.timers.unregister(unrelated_timer)
        bpy.app.handlers.load_post.remove(unrelated_handler)
        (control / "stopped").write_text("clean")
        if not bpy.app.background:
            bpy.ops.wm.quit_blender()
        return None
    return 0.02


def checked() -> float | None:
    try:
        return check()
    except Exception as exc:
        (control / "error").write_text(str(exc))
        importlib.import_module(PACKAGE).unregister()
        if not bpy.app.background:
            bpy.ops.wm.quit_blender()
        raise


if bpy.app.background:
    while True:
        lifecycle._backend.pump()
        if bpy.app.timers.is_registered(lifecycle._poll):
            if lifecycle._poll() is None:
                bpy.app.timers.unregister(lifecycle._poll)
        if checked() is None:
            break
        time.sleep(0.02)
else:
    bpy.app.timers.register(checked, first_interval=0.1, persistent=True)
