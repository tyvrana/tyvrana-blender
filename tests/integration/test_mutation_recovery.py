"""Packaged background host qualification for failed post-mutation proof."""

import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_mutation_recovery(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/mutation_recovery_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "mutation-recovery-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "MUTATION_RECOVERY_NATIVE_PASSED" in log
