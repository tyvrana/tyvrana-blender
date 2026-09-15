import os
import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT


def test_real_blender_background_operations_and_lifecycle(
    profile: dict[str, str], tmp_path: Path
) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_TESTS_PASSED 97" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert "WARNING" not in result.stdout + result.stderr


def test_blender_exit_handler_reaps_an_active_worker(profile: dict[str, str]) -> None:
    script = (
        "import importlib; "
        "m=importlib.import_module('bl_ext.user_default.tyvrana_blender.blender'); "
        "m.pump(); m.pump(); assert m._enabled; "
        "print('WORKER_PID',m._runtime.worker.process.pid)"
    )
    result = subprocess.run(
        ["blender", "--background", "--python-exit-code", "1", "--python-expr", script],
        env=profile,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    line = next(
        line for line in result.stdout.splitlines() if line.startswith("WORKER_PID")
    )
    with pytest.raises(ProcessLookupError):
        os.kill(int(line.split()[1]), 0)


@pytest.mark.interactive
def test_native_script_reload_preserves_unsaved_project(
    profile: dict[str, str],
) -> None:
    result = subprocess.run(
        [
            "xvfb-run",
            "-a",
            "blender",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/reload_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=60,
    )
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    assert "BLENDER_RELOAD_TESTS_PASSED" in log
    assert "Traceback" not in log
    assert "WARNING" not in log
