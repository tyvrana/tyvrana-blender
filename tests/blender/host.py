"""Bounded real Blender host for isolated background and UI integration tests."""

import importlib
import json
import os
import sys
import time
from pathlib import Path

import bpy  # type: ignore[import-not-found]

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
control = Path(os.environ["TYVRANA_TEST_CONTROL"])
preferences = bpy.context.preferences.addons[adapter.__package__].preferences
if "TYVRANA_TEST_PORT" in os.environ:
    preferences.port = int(os.environ["TYVRANA_TEST_PORT"])
    adapter.restart()
if os.environ.get("TYVRANA_TEST_EMPTY") == "1":
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
if os.environ.get("TYVRANA_TEST_CPU_RENDER") == "1":
    # Keep raster/UV verification independent of software GPU shader compilation.
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
if os.environ.get("TYVRANA_TEST_RENDER") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_scene()
if os.environ.get("TYVRANA_TEST_LIGHTING") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_lighting_scene()
if os.environ.get("TYVRANA_TEST_MATERIAL") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_material_scene()
if os.environ.get("TYVRANA_TEST_SHADER") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_shader_scene()
if os.environ.get("TYVRANA_TEST_MESH") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_mesh_scene()
if "TYVRANA_TEST_MODIFIER" in os.environ:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_modifier_scene(
        os.environ["TYVRANA_TEST_MODIFIER"]
    )
if "TYVRANA_TEST_SCULPT" in os.environ:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.scene").prepare_sculpt_scene(
        os.environ["TYVRANA_TEST_SCULPT"]
    )
if os.environ.get("TYVRANA_TEST_REMESH") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    importlib.import_module("tests.blender.remesh_scene").prepare_scene()
if "TYVRANA_TEST_RETOPO" in os.environ:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    retopo_scene = importlib.import_module("tests.blender.retopo_scene")
    if os.environ["TYVRANA_TEST_RETOPO"] == "tube":
        retopo_scene.prepare_tube_scene()
    else:
        retopo_scene.prepare_scene(patch=os.environ["TYVRANA_TEST_RETOPO"] == "patch")
if "TYVRANA_TEST_FINISH" in os.environ:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    retopo_scene = importlib.import_module("tests.blender.retopo_scene")
    importlib.import_module("tests.blender.retopo_finish_scene").prepare_scene(
        os.environ["TYVRANA_TEST_FINISH"]
    )
if os.environ.get("TYVRANA_TEST_FILE_OPEN") == "1":
    probe = bpy.data.texts.new("AutoRunProbe.py")
    probe.write("import bpy\nbpy.context.scene['file_script_executed'] = True\n")
    probe.use_module = True
deadline = time.monotonic() + 180
if os.environ.get("TYVRANA_TEST_CYCLES_OVERRIDE") == "1":
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.context.scene.cycles.samples = 73


def check() -> float | None:
    try:
        if time.monotonic() >= deadline:
            raise RuntimeError("Integration host exceeded its deadline")
        runtime = adapter._runtime
        if runtime is not None:
            (control / "ready.tmp").write_text(
                json.dumps(
                    {
                        "instance_id": adapter.INSTANCE_ID,
                        "status": runtime.status,
                        "worker_pid": runtime.worker.process.pid,
                        "spooled_artifacts": len(
                            list(runtime.worker.spool.root.iterdir())
                        ),
                    }
                )
            )
            (control / "ready.tmp").replace(control / "ready.json")
        if (control / "stop").exists():
            if os.environ.get("TYVRANA_TEST_SHOW_RENDER") == "1":
                assert any(
                    area.type == "IMAGE_EDITOR"
                    and area.spaces.active.image == bpy.data.images.get("Render Result")
                    for window in bpy.context.window_manager.windows
                    for area in window.screen.areas
                )
            if os.environ.get("TYVRANA_TEST_FILE_OPEN") == "1":
                assert not bpy.context.scene.get("file_script_executed", False)
            if os.environ.get("TYVRANA_TEST_CYCLES_OVERRIDE") == "1":
                assert bpy.context.scene.render.engine == "BLENDER_EEVEE"
                assert bpy.context.scene.cycles.samples == 73
            worker = runtime.worker if runtime is not None else None
            if (
                "TYVRANA_TEST_RETOPO" in os.environ
                or "TYVRANA_TEST_FINISH" in os.environ
            ):
                retopo_scene.clear_display()
            adapter.unregister()
            assert not bpy.app.timers.is_registered(adapter.pump)
            if worker is not None:
                assert worker.process.returncode == 0
            (control / "stopped").write_text("clean")
            if not bpy.app.background:
                bpy.ops.wm.quit_blender()
            return None
    except Exception as exc:
        (control / "error").write_text(str(exc))
        adapter.unregister()
        if not bpy.app.background:
            bpy.ops.wm.quit_blender()
        raise
    return 0.02


if bpy.app.background:
    try:
        while True:
            adapter.pump()
            if check() is None:
                break
            time.sleep(0.02)
    finally:
        adapter.unregister()
else:
    # Blender itself invokes the extension timer; this test timer only observes.
    bpy.app.timers.register(check, first_interval=0.1, persistent=True)
