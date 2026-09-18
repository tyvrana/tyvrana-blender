"""Owned disposable Blender entry point; input is a private typed scene snapshot."""

import importlib
import json
import shutil
import sys
import zipfile
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
    output = importlib.import_module(package + ".render_output")
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
                report["render_seconds"] = report.get("render_seconds", 0) + seconds

        frames = args.frames or [scene.frame_current]
        results = []
        files = []
        total_bytes = 0
        for index, frame in enumerate(frames):
            scene.frame_set(frame)
            result, descriptor = renderer.render_image(
                args.model_copy(update={"frames": None}), spool, observe=observe
            )
            filename = f"frame-{frame:07d}" + (
                ".png" if args.format == "png" else ".exr"
            )
            destination = directory / (
                filename if args.frames else directory.name + output.suffix(args)
            )
            total_bytes += descriptor.byte_size
            if total_bytes > args.budget.max_artifact_bytes:
                raise errors.OperationError(
                    "artifact_too_large",
                    "Sequence exceeds max_artifact_bytes; reduce frames or output size",
                )
            shutil.copyfile(
                artifacts.artifact_path(spool.root, descriptor), destination
            )
            spool.release((descriptor,))
            files.append(destination)
            results.append(
                dict(
                    frame=frame,
                    filename=filename,
                    byte_size=descriptor.byte_size,
                    sha256=descriptor.sha256,
                )
            )
            report["output"] = result.model_dump(mode="json")
            print(
                "TYVRANA_RENDER_EVENT "
                + json.dumps(dict(event="frame_complete", completed_frames=index + 1)),
                flush=True,
            )
        if args.frames:
            with zipfile.ZipFile(
                directory / (directory.name + ".zip"),
                "w",
                compression=zipfile.ZIP_STORED,
            ) as archive:
                for path in files:
                    archive.write(path, path.name)
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        dict(output=report["output"], frames=results), sort_keys=True
                    ),
                )
            for path in files:
                path.unlink()
        report["completed_frames"] = len(frames)
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
