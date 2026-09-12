"""Bounded real Blender host for isolated background and UI integration tests."""

import importlib
import json
import os
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
deadline = time.monotonic() + 45


def check() -> float | None:
    try:
        if time.monotonic() >= deadline:
            raise RuntimeError("Integration host exceeded its deadline")
        runtime = adapter._runtime
        if runtime is not None:
            (control / "ready.json").write_text(
                json.dumps(
                    {
                        "instance_id": adapter.INSTANCE_ID,
                        "status": runtime.status,
                        "worker_pid": runtime.worker.process.pid,
                    }
                )
            )
        if (control / "stop").exists():
            worker = runtime.worker if runtime is not None else None
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
    bpy.app.timers.register(check, first_interval=0.1)
