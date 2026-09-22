"""Isolated native qualification in two independently started Blender processes."""

import json
import shutil
import subprocess
from pathlib import Path

from .conftest import ROOT


def test_attestation_capacity(profile: dict[str, str], tmp_path: Path) -> None:
    installed = (
        Path(profile["BLENDER_USER_RESOURCES"])
        / "extensions/user_default/tyvrana_blender"
    )
    shutil.copytree(
        ROOT / "src/tyvrana_blender",
        installed,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for phase in ("create", "reopen"):
        result = subprocess.run(
            [
                "blender",
                "--background",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "tests/blender/attestation_capacity_checks.py"),
            ],
            env={**profile, "TYVRANA_ATTEST_PHASE": phase},
            capture_output=True,
            text=True,
            timeout=240,
        )
        (tmp_path / f"{phase}.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((tmp_path / "capacity-create.json").read_text())
    assert data["scale_geometry"]["objects"] > 300
    assert data["scale_geometry"]["meshes"] > 150
    assert data["saved"]["work"]["stream_bytes"] > 512 * 1024 * 1024
    assert data["response_bytes"] < 12000
