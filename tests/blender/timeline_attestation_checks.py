"""Timeline publication under Blender's real notifier loop, without a desktop."""

import importlib
import json
import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import addon_utils  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender."
addon_utils.enable(PACKAGE.rstrip("."), default_set=False)
lifecycle = importlib.import_module(PACKAGE + "lifecycle")
assert lifecycle._backend._runtime is None
lifecycle.disable()
timeline = importlib.import_module(PACKAGE + "timeline")
models = importlib.import_module(PACKAGE + "motion_models")
attestation = importlib.import_module(PACKAGE + "attestation_jobs")
scene = bpy.context.scene
obj = bpy.data.objects["Cube"]
obj.rotation_euler.z = 0
obj.keyframe_insert("rotation_euler", frame=1)
obj.rotation_euler.z = 1.2
obj.keyframe_insert("rotation_euler", frame=61)
scene.frame_set(1)
preview = (scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end)
cases = ["frame_and_range", "frame_only", "external_frame", "external_geometry"]
results: list[dict[str, Any]] = []
index = -1
steps: Generator[dict[str, Any], None, Any] | None = None
inject = False
deadline = time.monotonic() + 90


def finish() -> None:
    Path(os.environ["TYVRANA_TEST_CONTROL"], "timeline-attestation.json").write_text(
        json.dumps(results, indent=2)
    )
    bpy.ops.wm.quit_blender()


def advance() -> float | None:
    global index, steps, inject
    try:
        if time.monotonic() > deadline:
            raise RuntimeError("Timeline fixture exceeded deadline")
        if steps is None:
            index += 1
            if index == len(cases):
                finish()
                return None
            args = {"frame": 1 if cases[index] == "frame_only" else 61}
            if cases[index] == "frame_and_range":
                args.update(frame_start=1, frame_end=111)
            timeline.configure(models.TimelineArguments(**args))
            assert scene.frame_current == args["frame"]
            assert abs(obj.rotation_euler.z - (0 if args["frame"] == 1 else 1.2)) < 1e-6
            assert preview == (
                scene.use_preview_range,
                scene.frame_preview_start,
                scene.frame_preview_end,
            )
            steps = attestation.guarded_steps()
            # Keep a real guard open across a native notifier-loop iteration.
            # No synthetic handler invocation or replacement hash is involved.
            next(steps)
            inject = cases[index].startswith("external_")
            return 0.02
        if inject:
            inject = False
            if cases[index] == "external_frame":
                scene.frame_set(62)
            else:
                obj.location.x += 0.25
                bpy.context.view_layer.update()
        next(steps)
        return 0.02
    except StopIteration as done:
        results.append({"case": cases[index], "result": done.value.root})
        steps = None
        return 0.1
    except Exception as exc:
        error = getattr(exc, "error", None)
        results.append(
            {
                "case": cases[index],
                "error": error.model_dump(mode="json") if error else str(exc),
            }
        )
        if steps is not None:
            steps.close()
        steps = None
        if error is None:
            finish()
            return None
        return 0.1


bpy.app.timers.register(advance, first_interval=1)
