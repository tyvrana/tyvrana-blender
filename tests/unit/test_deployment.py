"""Package staging must never rewrite code used by the active Blender process."""

import json
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from tyvrana_blender.deployment import STABLE_FILES, identity, stage, update_directory
from tyvrana_blender.extension_models import ExtensionReloadArguments


def package(root: Path) -> Path:
    root.mkdir()
    (root / "blender_manifest.toml").write_text(
        'id = "tyvrana_blender"\ntype = "add-on"\n'
    )
    for name in STABLE_FILES:
        (root / name).write_text("# stable\n")
    (root / "operations.py").write_text('MARKER = "A"\n')
    return root


def archive(root: Path, output: Path) -> Path:
    with zipfile.ZipFile(output, "w") as stream:
        for path in root.rglob("*"):
            if path.is_file():
                stream.write(path, path.relative_to(root).as_posix())
    return output


def test_staging_is_separate_and_build_identity_is_content_based(
    tmp_path: Path,
) -> None:
    installed = package(tmp_path / "installed")
    candidate = package(tmp_path / "candidate")
    (candidate / "operations.py").write_text('MARKER = "B"\n')
    before = identity(installed)
    result = stage(archive(candidate, tmp_path / "package.zip"), installed)
    assert identity(installed) == before
    assert result["build"] == identity(candidate) != before
    assert identity(update_directory(installed) / "candidate") == result["build"]
    assert (
        json.loads((update_directory(installed) / "staged.json").read_text()) == result
    )
    assert not (update_directory(installed) / "lock").exists()


@pytest.mark.parametrize("change", ["bootstrap", "dependency", "syntax", "manifest"])
def test_reject_invalid_updates_without_changing_install(
    tmp_path: Path, change: str
) -> None:
    installed = package(tmp_path / "installed")
    candidate = package(tmp_path / "candidate")
    if change == "bootstrap":
        (candidate / "lifecycle.py").write_text("# changed bootstrap\n")
    elif change == "dependency":
        (candidate / "wheels").mkdir()
        (candidate / "wheels/new.whl").write_bytes(b"new dependency")
    elif change == "syntax":
        (candidate / "operations.py").write_text("invalid syntax !!!")
    else:
        (candidate / "blender_manifest.toml").write_text(
            'id = "other"\ntype = "add-on"\n'
        )
    before = identity(installed)
    with pytest.raises((ValueError, SyntaxError)):
        stage(archive(candidate, tmp_path / "package.zip"), installed)
    assert identity(installed) == before
    assert not (update_directory(installed) / "lock").exists()


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "bad\\path.py"])
def test_archive_paths_are_bounded(tmp_path: Path, name: str) -> None:
    installed = package(tmp_path / "installed")
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as stream:
        stream.writestr(name, "unsafe")
    with pytest.raises(ValueError, match="archive path"):
        stage(path, installed)
    assert not (tmp_path / "escape.py").exists()


def test_busy_staging_keeps_existing_candidate(tmp_path: Path) -> None:
    installed = package(tmp_path / "installed")
    path = archive(installed, tmp_path / "package.zip")
    stage(path, installed)
    directory = update_directory(installed)
    (directory / "lock").write_text("busy")
    before = identity(directory / "candidate")
    with pytest.raises(ValueError, match="busy"):
        stage(path, installed)
    assert identity(directory / "candidate") == before


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"expected_build": "not-a-digest"},
        {"expected_build": "a" * 64, "module": "os"},
        {"expected_build": "a" * 64, "script": "print(1)"},
    ],
)
def test_reload_does_not_accept_module_names_or_scripts(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ExtensionReloadArguments.model_validate(arguments)


def test_recovery_refuses_live_owner(tmp_path: Path) -> None:
    import os

    from tyvrana_blender.deployment import acquire, recover, release

    installed = package(tmp_path / "installed")
    directory = update_directory(installed)
    acquire(directory)
    assert (directory / "lock").read_text() == str(os.getpid())
    try:
        with pytest.raises(ValueError, match="live process"):
            recover(installed)
    finally:
        release(directory)


def test_recovery_restores_interrupted_directory_exchange(tmp_path: Path) -> None:
    from tyvrana_blender.deployment import recover

    installed = package(tmp_path / "installed")
    before = identity(installed)
    directory = update_directory(installed)
    directory.mkdir()
    installed.replace(directory / "backup")
    (directory / "lock").write_text("999999999")
    assert not installed.exists()
    assert recover(installed)
    assert identity(installed) == before
    assert not (directory / "backup").exists()
    assert not (directory / "lock").exists()
