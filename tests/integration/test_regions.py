import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT


@pytest.mark.parametrize("ui,count", [(False, 6), (True, 14)])
def test_native_sculpt_regions(
    profile: dict[str, str], tmp_path: Path, ui: bool, count: int
) -> None:
    command = [
        "blender",
        "--python-exit-code",
        "1",
        "--python",
        str(ROOT / "tests/blender/regional_checks.py"),
    ]
    if ui:
        command = ["xvfb-run", "-a", *command]
    else:
        command.insert(1, "--background")
    result = subprocess.run(
        command, env=profile, capture_output=True, text=True, timeout=240
    )
    log = result.stdout + result.stderr
    (tmp_path / "regional-native.log").write_text(log)
    assert result.returncode == 0, log
    assert f"BLENDER_REGIONAL_TESTS_PASSED {count}" in log, log
    assert "Traceback" not in log, log
    assert "Warning:" not in log
    assert "WARNING" not in log
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))
