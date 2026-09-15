import subprocess
from pathlib import Path

from .conftest import ROOT


def test_native_uv_operations_and_context(
    profile: dict[str, str], tmp_path: Path
) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/uv_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "uv-native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_UV_TESTS_PASSED 17" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))


def test_native_input_images(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/input_image_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (tmp_path / "image-native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_INPUT_IMAGE_TESTS_PASSED 8" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))


def test_native_joint_uv_layout(profile: dict[str, str], tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "blender",
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(ROOT / "tests/blender/uv_layout_checks.py"),
        ],
        env=profile,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "uv-layout-native.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLENDER_UV_LAYOUT_TESTS_PASSED 5" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))
