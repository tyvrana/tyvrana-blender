"""Atomic native review output; never replace unrelated or modified files."""

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import OperationError


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(directory: Path) -> dict[str, str]:
    if directory.is_symlink() or not directory.is_dir():
        raise OperationError(
            "review_output_conflict", "Review destination is not a directory"
        )
    paths = list(directory.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in paths) or len(paths) > 26:
        raise OperationError(
            "review_output_conflict", "Review directory contains unrelated files"
        )
    try:
        manifest = directory / "manifest.json"
        if manifest.stat().st_size > 1048576:
            raise ValueError("Manifest exceeds bound")
        data = json.loads(manifest.read_text())
        if data["kind"] != "tyvrana.inspection":
            raise ValueError("Not a review packet")
        expected = {row["filename"]: row["sha256"] for row in data["files"]}
        if set(expected) | {"manifest.json"} != {p.name for p in paths}:
            raise ValueError("Unexpected review contents")
        if any(digest(directory / name) != sha for name, sha in expected.items()):
            raise ValueError("Modified review contents")
        return {**expected, "manifest.json": digest(manifest)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OperationError(
            "review_output_conflict",
            "Destination is not an intact Tyvrana review packet",
        ) from exc


class Packet:
    def __init__(self, directory: Path, *, overwrite: bool, maximum: int) -> None:
        if not directory.is_absolute() or not directory.parent.is_dir():
            raise OperationError(
                "review_output_invalid",
                "Use an absolute directory with an existing parent",
            )
        if directory.is_symlink():
            raise OperationError(
                "review_output_conflict", "Review destination cannot be a symlink"
            )
        if directory.exists() and not overwrite:
            raise OperationError(
                "review_output_conflict", "Review destination already exists"
            )
        self.directory = directory
        self.before = inventory(directory) if directory.exists() else None
        self.stage = Path(
            tempfile.mkdtemp(prefix=".tyvrana-review-", dir=directory.parent)
        )
        self.maximum = maximum
        self.size = 0
        self.files: list[dict[str, Any]] = []

    def add(self, source: Path, filename: str, **metadata: Any) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+\.png", filename):
            raise ValueError("Invalid review filename")
        size = source.stat().st_size
        if self.size + size > self.maximum:
            raise OperationError(
                "artifact_too_large", "Review packet exceeds max_artifact_bytes"
            )
        target = self.stage / filename
        shutil.copyfile(source, target)
        self.files.append(
            {**metadata, "filename": filename, "bytes": size, "sha256": digest(target)}
        )
        self.size += size
        return filename

    def publish(self) -> str:
        manifest = (
            json.dumps({"kind": "tyvrana.inspection", "files": self.files}, indent=2)
            + "\n"
        )
        if self.size + len(manifest.encode()) > self.maximum:
            raise OperationError(
                "artifact_too_large", "Review packet exceeds max_artifact_bytes"
            )
        (self.stage / "manifest.json").write_text(manifest)
        backup = None
        if self.before is not None:
            if inventory(self.directory) != self.before:
                raise OperationError(
                    "review_output_conflict",
                    "Review destination changed during rendering",
                )
            backup = self.stage.with_name(self.stage.name + "-previous")
            self.directory.rename(backup)
        elif self.directory.exists() or self.directory.is_symlink():
            raise OperationError(
                "review_output_conflict", "Review destination appeared during rendering"
            )
        try:
            self.stage.rename(self.directory)
        except OSError:
            if backup is not None:
                backup.rename(self.directory)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return str(self.directory)

    def close(self) -> None:
        if self.stage.exists():
            shutil.rmtree(self.stage)
