"""Owned disposable Blender entry point; input is a private typed scene snapshot."""

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]


def main() -> None:
    directory = Path(sys.argv[sys.argv.index("--") + 1])
    config = json.loads((directory / "config.json").read_text())
    sys.path[:0] = config["paths"]
    package = config["package"]
    models = importlib.import_module(package + ".models")
    renderer = importlib.import_module(package + ".render")
    artifacts = importlib.import_module(package + ".artifacts")
    errors = importlib.import_module(package + ".errors")
    report: dict[str, Any] = {}
    spool = artifacts.ArtifactSpool()
    try:
        bpy.ops.wm.open_mainfile(
            filepath=str(directory / "scene.blend"), load_ui=False, use_scripts=False
        )
        scene = bpy.data.scenes[config["scene"]]
        bpy.context.window.scene = scene
        bpy.context.window.view_layer = scene.view_layers[config["layer"]]
        for buffer in config.get("buffers", []):
            image = bpy.data.images.get(buffer["name"])
            if image is None:
                continue  # The image belongs only to a different scene.
            values = np.fromfile(directory / buffer["filename"], dtype=np.float32)
            if len(values) != buffer["count"] or len(image.pixels) != len(values):
                raise errors.OperationError(
                    "render_snapshot_invalid", "Live image buffer dimensions changed"
                )
            image.pixels.foreach_set(values)
            image.update()
        if config["gpu"] is not None:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            prefs.compute_device_type = config["gpu"]["type"]
            prefs.get_devices()
            enabled = False
            for device in prefs.devices:
                device.use = config["gpu"]["devices"].get(device.id, False)
                enabled |= device.use and device.type != "CPU"
            if not enabled:
                raise errors.OperationError(
                    "render_device_unavailable",
                    "No configured GPU device is available to the render subprocess",
                )
        args = models.RenderArguments.model_validate(config["arguments"]).model_copy(
            update={"show_result": False}
        )

        def observe(event: str, seconds: float) -> None:
            if event == "running":
                print(
                    "TYVRANA_RENDER_EVENT " + json.dumps({"event": event}), flush=True
                )
            else:
                report["render_seconds"] = seconds

        _, descriptor = renderer.render_image(args, spool, observe=observe)
        shutil.copyfile(
            spool.root / (descriptor.artifact_id + ".png"),
            directory / (directory.name + ".png"),
        )
    except Exception as exc:
        if isinstance(exc, errors.OperationError):
            report["error"] = {
                "code": exc.error.code,
                "message": exc.error.message[:500],
            }
        else:
            import traceback

            traceback.print_exc()
            report["error"] = {
                "code": "render_failed",
                "message": "Native render failed; see application log",
            }
    finally:
        spool.close()
    (directory / "completed.json").write_text(json.dumps(report))


if __name__ == "__main__":
    main()
