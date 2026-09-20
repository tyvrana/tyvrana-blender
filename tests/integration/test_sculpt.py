import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT


@pytest.mark.parametrize("ui,count", [(False, 25), (True, 23)])
def test_native_sculpt_foundation(
    profile: dict[str, str], tmp_path: Path, ui: bool, count: int
) -> None:
    command = [
        "blender",
        "--python-exit-code",
        "1",
        "--python",
        str(ROOT / "tests/blender/sculpt_checks.py"),
    ]
    if ui:
        command = ["xvfb-run", "-a", *command]
    else:
        command.insert(1, "--background")
    result = subprocess.run(
        command, env=profile, capture_output=True, text=True, timeout=240
    )
    log = result.stdout + result.stderr
    (tmp_path / "sculpt-native.log").write_text(log)
    assert result.returncode == 0, log
    assert f"BLENDER_SCULPT_TESTS_PASSED {count}" in log, log
    assert "Traceback" not in log, log
    assert "Warning:" not in log
    assert "WARNING" not in log
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))
