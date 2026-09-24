"""Private entry point for an adapter-owned proof process, never client code."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def run() -> None:
    import bpy  # type: ignore[import-not-found]

    config = Path(os.environ.pop("TYVRANA_PROOF_CONFIG"))
    data = json.loads(config.read_text())
    directory = config.parent
    sys.path[:0] = [str(directory), *data["paths"]]
    import tyvrana_blender
    from tyvrana_blender import deployment

    def report(state: str, error: str | None = None) -> None:
        value = dict(
            lease_id=data["lease"]["lease_id"], state=state, process_id=os.getpid()
        )
        if error:
            value["error"] = dict(code="proof_bootstrap_failed", message=error[:512])
        deployment.atomic_json(directory / "status.json", value)

    enabled = False
    try:
        if (
            deployment.identity(Path(tyvrana_blender.__file__).parent)
            != data["expected_build"]
        ):
            raise ValueError("Proof implementation build differs")
        artifact = Path(data["artifact"]["locator"])
        before = artifact.stat()
        with artifact.open("rb") as stream:
            if (
                hashlib.file_digest(stream, "sha256").hexdigest()
                != data["artifact"]["sha256"]
            ):
                raise ValueError("Trusted artifact SHA256 differs")
        bpy.ops.wm.open_mainfile(
            filepath=str(artifact), load_ui=False, use_scripts=False
        )
        after = artifact.stat()
        if any(
            getattr(before, field) != getattr(after, field)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise ValueError("Trusted artifact changed during proof load")
        tyvrana_blender.register()
        enabled = True
        from tyvrana_blender import blender, proof_hosts

        proof_hosts.configure(data)

        deadline = time.monotonic() + data["ttl_seconds"]
        connected = False
        disconnected = None
        while time.monotonic() < deadline and not (directory / "stop").exists():
            if os.getppid() != data["parent_pid"]:
                break
            os.kill(data["parent_pid"], 0)
            blender.pump()
            live = (
                blender._runtime is not None and blender._runtime.status == "connected"
            )
            if live:
                if not connected:
                    report("ready")
                connected = True
                disconnected = None
            elif connected:
                disconnected = disconnected or time.monotonic()
                if time.monotonic() - disconnected > 3:
                    break
            time.sleep(0.01)
    except Exception as exc:
        report("failed", str(exc))
    finally:
        if enabled:
            tyvrana_blender.unregister()


if __name__ == "__main__":
    run()
