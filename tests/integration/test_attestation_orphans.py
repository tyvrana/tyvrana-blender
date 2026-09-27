"""Packaged native persistence with a transitively orphaned material."""

import subprocess
from pathlib import Path

from .conftest import ROOT


def test_attestation_orphan_chain(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--factory-startup",
            "--threads",
            "2",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/attestation_orphan_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=90,
    )
    log = result.stdout + result.stderr
    (tmp_path / "orphan-native.log").write_text(log)
    assert result.returncode == 0, log
    assert "ORPHAN_ATTESTATION_PASSED" in log
