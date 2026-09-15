"""Stage this extension's packages without modifying loaded implementation files."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

STABLE_FILES = ("__init__.py", "deployment.py", "lifecycle.py")
MAX_PACKAGE_BYTES = 128 * 1024 * 1024


def update_directory(extension: Path) -> Path:
    return extension.parent / ("." + extension.name + "-update")


def files(directory: Path) -> list[Path]:
    return sorted(
        p
        for p in directory.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def identity(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in files(directory):
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def acquire(directory: Path) -> None:
    directory.mkdir(exist_ok=True)
    try:
        fd = os.open(directory / "lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(
            "An extension staging or activation transaction is busy"
        ) from None
    with os.fdopen(fd, "w") as stream:
        stream.write(str(os.getpid()))


def release(directory: Path) -> None:
    (directory / "lock").unlink(missing_ok=True)


def recover(installed: Path) -> bool:
    """Recover an interrupted transaction only after its owning process exited."""
    directory = update_directory(installed)
    lock = directory / "lock"
    backup = directory / "backup"
    if not lock.exists() and not backup.exists():
        return False
    if lock.exists():
        owner = int(lock.read_text())
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError("Cannot recover a transaction owned by a live process")
    if backup.exists():
        if installed.exists():
            shutil.rmtree(installed)
        backup.replace(installed)
        (directory / "staged.json").unlink(missing_ok=True)
    release(directory)
    atomic_json(directory / "status.json", {"status": "recovered_after_exit"})
    return True


def validate_candidate(candidate: Path, installed: Path) -> str:
    manifest = tomllib.loads((candidate / "blender_manifest.toml").read_text())
    if manifest.get("id") != "tyvrana_blender" or manifest.get("type") != "add-on":
        raise ValueError("The package must be the Tyvrana Blender extension")
    previous_manifest = tomllib.loads((installed / "blender_manifest.toml").read_text())
    for field in ("wheels", "blender_version_min", "blender_version_max", "platforms"):
        if manifest.get(field) != previous_manifest.get(field):
            raise ValueError(
                "Dependency or host requirements changed; use a stopped installation"
            )
    for name in STABLE_FILES:
        if (candidate / name).read_bytes() != (installed / name).read_bytes():
            raise ValueError(
                "Bootstrap changes require a disabled extension installation"
            )
    old_wheels = {p.name: p.read_bytes() for p in (installed / "wheels").glob("*.whl")}
    new_wheels = {p.name: p.read_bytes() for p in (candidate / "wheels").glob("*.whl")}
    if new_wheels != old_wheels:
        raise ValueError("Blender-managed dependency changes cannot be hot reloaded")
    for path in files(candidate):
        if path.is_symlink():
            raise ValueError("Symbolic links are not accepted in update packages")
        if path.suffix == ".py":
            compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
    return identity(candidate)


def stage(archive: Path, installed: Path) -> dict[str, str]:
    """Install an isolated candidate; activation belongs to the typed operation."""
    installed = installed.resolve(strict=True)
    directory = update_directory(installed)
    acquire(directory)
    try:
        if (directory / "backup").exists():
            raise ValueError("An interrupted activation needs recovery before staging")
        with tempfile.TemporaryDirectory(prefix="stage-", dir=directory) as temporary:
            candidate = Path(temporary) / "candidate"
            candidate.mkdir()
            with zipfile.ZipFile(archive) as package:
                entries = package.infolist()
                if (
                    len(entries) > 2048
                    or sum(e.file_size for e in entries) > MAX_PACKAGE_BYTES
                ):
                    raise ValueError("Extension package exceeds its size bound")
                seen: set[str] = set()
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in entry.filename
                        or entry.filename in seen
                        or stat.S_ISLNK(entry.external_attr >> 16)
                    ):
                        raise ValueError("Invalid or duplicate extension archive path")
                    seen.add(entry.filename)
                    if entry.is_dir():
                        continue
                    destination = candidate.joinpath(*path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(package.read(entry))
            build = validate_candidate(candidate, installed)
            previous = directory / "candidate"
            if previous.exists():
                shutil.rmtree(previous)
            candidate.replace(previous)
            result = {"build": build, "status": "staged"}
            atomic_json(directory / "staged.json", result)
            return result
    finally:
        release(directory)
