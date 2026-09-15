"""Stable, extension-owned activation controller; no arbitrary module execution."""

import importlib
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from . import deployment

logger = logging.getLogger(__name__)
_root = Path(__file__).parent
_backend: ModuleType | None = None
_build = ""
_generation = 0
_phase = "idle"
_error: str | None = None
_reload_id: str | None = None
_request_id: str | None = None
_next_build: str | None = None
_acknowledged = False
_deadline = 0.0
_previous_build = ""
_config: tuple[str, int] | None = None


def _bpy() -> Any:
    return importlib.import_module("bpy")


def _clear_implementation() -> None:
    prefix = __package__ + "."
    keep = {prefix + "lifecycle", prefix + "deployment"}
    for name in tuple(sys.modules):
        if name.startswith(prefix) and name not in keep:
            sys.modules.pop(name, None)
    package = sys.modules[__package__]
    for name, value in tuple(vars(package).items()):
        if (
            isinstance(value, ModuleType)
            and value.__name__.startswith(prefix)
            and value.__name__ not in keep
        ):
            delattr(package, name)
    importlib.invalidate_caches()


def _load() -> None:
    global _backend
    _clear_implementation()
    _backend = importlib.import_module(__package__ + ".blender")
    _backend.register()
    if _config is not None:
        addon = _bpy().context.preferences.addons.get(__package__)
        if addon is not None:
            addon.preferences.host, addon.preferences.port = _config
    _backend.restart()


def enable() -> None:
    global _backend, _build
    if _backend is not None and _backend._enabled:
        return
    deployment.recover(_root)
    _build = deployment.identity(_root)
    _clear_implementation()
    _backend = importlib.import_module(__package__ + ".blender")
    # Blender restricts context while enabling; the first timer starts networking.
    _backend.register()


def disable() -> None:
    global _backend, _request_id
    bpy = _bpy()
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    if _backend is not None:
        _backend.unregister()
        _backend = None
    if _request_id is not None:
        deployment.release(deployment.update_directory(_root))
        _request_id = None
    _clear_implementation()


def inspect() -> dict[str, Any]:
    directory = deployment.update_directory(_root)
    staged = None
    if (directory / "staged.json").is_file() and (directory / "candidate").is_dir():
        staged = json.loads((directory / "staged.json").read_text())["build"]
    runtime = _backend._runtime if _backend is not None else None
    return {
        "build": _build,
        "implementation_build": _backend.IMPLEMENTATION_BUILD
        if _backend is not None
        else None,
        "generation": _generation,
        "status": _phase,
        "reload_id": _reload_id,
        "staged_build": staged,
        "error": _error,
        "adapter_id": _backend.INSTANCE_ID if _backend is not None else None,
        "connection_state": runtime.status if runtime is not None else "stopped",
        "operation_count": len(
            importlib.import_module(__package__ + ".operations").OPERATIONS
        )
        if _backend is not None
        else 0,
        "worker_pid": runtime.worker.process.pid if runtime is not None else None,
        "runtime_timer_count": int(
            _backend is not None and _bpy().app.timers.is_registered(_backend.pump)
        ),
        "lifecycle_timer_count": int(_bpy().app.timers.is_registered(_poll)),
        "registered_class_count": len(_backend._registered_classes)
        if _backend is not None
        else 0,
        "artifact_count": len(list(runtime.worker.spool.root.iterdir()))
        if runtime is not None
        else 0,
        "handler_count": sum(
            list(handlers).count(callback) for handlers, callback in _backend._handlers
        )
        if _backend is not None
        else 0,
    }


def _preflight(candidate: Path) -> None:
    # Validate pure operation/model imports in Blender's bundled interpreter,
    # with its exact wheel paths. Never import staged bpy handlers in this process.
    script = (
        "import importlib,sys; sys.path[:0]=sys.argv[2:]; "
        "m=importlib.import_module(sys.argv[1]+'.operations'); "
        "assert {'blender.extension.reload','blender.extension.inspect'} "
        "<= set(m.OPERATIONS)"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            candidate.name,
            str(candidate.parent),
            *[p for p in sys.path if p and Path(p).is_absolute()],
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode:
        reason = completed.stderr.strip().splitlines()[-1:]
        raise ValueError("Staged operation imports failed: " + " ".join(reason))


def request_reload(expected_build: str, request_id: str) -> dict[str, Any]:
    global _phase, _error, _reload_id, _request_id, _next_build
    global _acknowledged, _deadline, _previous_build, _config
    if _request_id is not None:
        raise ValueError("An extension reload is already pending")
    directory = deployment.update_directory(_root)
    deployment.acquire(directory)
    try:
        if deployment.identity(_root) != _build:
            raise ValueError("Installed files changed outside staged activation")
        candidate = directory / "candidate"
        build = deployment.validate_candidate(candidate, _root)
        if build != expected_build:
            raise ValueError("The staged package does not match expected_build")
        _preflight(candidate)
        if (directory / "backup").exists():
            raise ValueError("An interrupted activation backup needs recovery")
        assert _backend is not None
        config = _backend.preferences_config()
        _config = config.host, config.port
        _previous_build = _build
        _next_build = build
        _reload_id = uuid4().hex
        _request_id = request_id
        _acknowledged = False
        _phase, _error = "scheduled", None
        _deadline = time.monotonic() + 10
        _bpy().app.timers.register(_poll, first_interval=0.05, persistent=True)
        return {
            "reload_id": _reload_id,
            "status": "scheduled",
            "previous_build": _build,
            "new_build": build,
            "previous_adapter_id": _backend.INSTANCE_ID,
        }
    except BaseException:
        _request_id = None
        deployment.release(directory)
        raise


def response_sent(request_id: str) -> None:
    global _acknowledged
    if request_id == _request_id:
        _acknowledged = True


def _record() -> None:
    deployment.atomic_json(
        deployment.update_directory(_root) / "status.json", inspect()
    )


def _finish() -> None:
    global _request_id
    _request_id = None
    try:
        _record()
    finally:
        deployment.release(deployment.update_directory(_root))


def _rollback(exc: Exception) -> None:
    global _build, _phase, _error, _backend
    directory = deployment.update_directory(_root)
    _error = f"{type(exc).__name__}: {exc}"
    try:
        if _backend is not None:
            _backend.unregister()
        _clear_implementation()
        backup = directory / "backup"
        if backup.exists():
            if _root.exists():
                shutil.rmtree(_root)
            backup.replace(_root)
        _build = _previous_build
        _load()
        _phase = "rolled_back"
    except Exception as rollback_error:
        if _backend is not None:
            _backend.unregister()
        _backend = None
        _clear_implementation()
        _phase = "failed"
        _error += (
            f"; rollback failed: {type(rollback_error).__name__}: {rollback_error}"
        )
        logger.exception("Extension activation and rollback failed; adapter disabled")
    _finish()


def _poll() -> float | None:
    global _phase, _build, _generation, _deadline, _error
    directory = deployment.update_directory(_root)
    if _phase == "scheduled":
        if not _acknowledged:
            if time.monotonic() < _deadline:
                return 0.05
            _phase, _error = (
                "failed",
                "Reload response was not acknowledged; active code retained",
            )
            _finish()
            return None
        try:
            assert _backend is not None and _next_build is not None
            if (
                deployment.validate_candidate(directory / "candidate", _root)
                != _next_build
            ):
                raise ValueError("Staged package changed before activation")
            _backend.unregister()
            _clear_implementation()
            _root.replace(directory / "backup")
            (directory / "candidate").replace(_root)
            _load()
            _build = _next_build
            _phase = "reconnecting"
            _deadline = time.monotonic() + 20
            _record()
        except Exception as exc:
            _rollback(exc)
            return None
    if _phase == "reconnecting":
        runtime = _backend._runtime if _backend is not None else None
        if runtime is not None and runtime.status == "connected":
            _phase = "completed"
            _generation += 1
            try:
                retired = directory / "retired"
                if retired.exists():
                    shutil.rmtree(retired)
                (directory / "backup").replace(retired)
                (directory / "staged.json").unlink(missing_ok=True)
                shutil.rmtree(retired)
            except OSError as exc:
                _error = f"Activated, but retired package cleanup failed: {exc}"
                logger.error("%s", _error)
            _finish()
            return None
        if time.monotonic() >= _deadline:
            _rollback(RuntimeError("New adapter did not reconnect before the deadline"))
            return None
    return 0.05
