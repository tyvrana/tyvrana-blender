"""Run retopology safety and geometry checks inside the packaged native extension."""

import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT


@pytest.mark.parametrize("ui", [False, True])
def test_native_retopology(profile: dict[str, str], tmp_path: Path, ui: bool) -> None:
    command = [
        "blender",
        "--python-exit-code",
        "1",
        "--python",
        str(ROOT / "tests/blender/retopo_checks.py"),
    ]
    if ui:
        command = ["xvfb-run", "-a", *command]
    else:
        command.insert(1, "--background")
    result = subprocess.run(
        command, env=profile, capture_output=True, text=True, timeout=180
    )
    log = result.stdout + result.stderr
    (tmp_path / "retopo-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "BLENDER_RETOPO_TESTS_PASSED 30" in log, log
    assert "Traceback" not in log, log
    assert "Warning:" not in log, log
    assert "WARNING" not in log, log
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))
