"""Native notifier causality and post-operation attestation isolation."""

import json
import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT


@pytest.mark.interactive
def test_timeline_attestation_event_loop(
    profile: dict[str, str], tmp_path: Path
) -> None:
    result = subprocess.run(
        [
            "xvfb-run",
            "-a",
            "blender",
            "--factory-startup",
            "--threads",
            "2",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/timeline_attestation_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log = result.stdout + result.stderr
    (tmp_path / "timeline-native.log").write_text(log)
    assert result.returncode == 0, log
    rows = json.loads((tmp_path / "timeline-attestation.json").read_text())
    assert len(rows) == 4, rows
    for row in rows[:2]:
        assert row.get("result", {}).get("status") == "complete", row
    for row, notification in zip(rows[2:], ["frame", "dependency_graph"], strict=True):
        assert row["error"]["code"] == "application_changed", row
        assert notification in row["error"]["details"]["change_notifications"], row
