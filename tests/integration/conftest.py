import asyncio
import os
import signal
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def input_image_fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.raster_fixtures import create_rasters

    directory = tmp_path_factory.mktemp("input-images")
    create_rasters(directory)
    return directory


@pytest.fixture
def profile(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "profile"
    root.mkdir()
    env = {
        **os.environ,
        "BLENDER_USER_RESOURCES": str(root),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "TYVRANA_TEST_CONTROL": str(tmp_path),
        "TYVRANA_TEST_EMPTY": "1",
        "TMPDIR": str(tmp_path),
    }
    archive = ROOT / "dist/tyvrana_blender-0.1.0.zip"
    assert archive.is_file(), "Build the extension before integration tests"
    result = subprocess.run(
        [
            "blender",
            "--factory-startup",
            "--command",
            "extension",
            "install-file",
            "--repo",
            "user_default",
            "--enable",
            str(archive),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "Exception in module" not in result.stdout + result.stderr
    if os.environ.get("TYVRANA_TEST_SOURCE") == "1":
        import shutil

        installed = root / "extensions/user_default/tyvrana_blender"
        shutil.copytree(
            ROOT / "src/tyvrana_blender",
            installed,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        dependencies = list(root.rglob("site-packages/tyvrana_protocol"))
        assert len(dependencies) == 1, dependencies
        shutil.rmtree(dependencies[0])
        shutil.copytree(Path(os.environ["TYVRANA_PROTOCOL_SOURCE"]), dependencies[0])
    return env


@asynccontextmanager
async def running_blender(
    env: dict[str, str], tmp_path: Path, *, ui: bool
) -> AsyncIterator[asyncio.subprocess.Process]:
    command = [
        "blender",
        "--python-exit-code",
        "1",
        "--python",
        str(ROOT / "tests/blender/host.py"),
    ]
    if ui:
        command = ["xvfb-run", "-a", *command]
    else:
        command.insert(1, "--background")
    log_path = tmp_path / "blender.log"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            *command, env=env, stdout=log, stderr=log, start_new_session=True
        )
        try:
            yield process
        finally:
            (tmp_path / "stop").touch()
            try:
                async with asyncio.timeout(10):
                    await process.wait()
            except TimeoutError:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    async with asyncio.timeout(3):
                        await process.wait()
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    async with asyncio.timeout(3):
                        await process.wait()
            assert process.returncode == 0, log_path.read_text()
    assert (tmp_path / "stopped").read_text() == "clean", log_path.read_text()
    assert not (tmp_path / "error").exists(), (tmp_path / "error").read_text()
    assert "Traceback" not in log_path.read_text()
    assert "WARNING" not in log_path.read_text()
    assert not list(tmp_path.glob("tyvrana-blender-artifacts-*"))  # noqa: ASYNC240 - Isolated test directory.
