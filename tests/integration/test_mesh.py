import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_mesh_operations_safety_and_seam_unwrap(
    profile: dict[str, str], tmp_path: Path
) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/mesh_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "mesh-native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_MESH_TESTS_PASSED 45" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert "Warning:" not in result.stdout + result.stderr
    assert "WARNING" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))
