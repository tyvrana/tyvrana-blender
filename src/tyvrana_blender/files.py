"""Native .blend saving with explicit destination and overwrite semantics."""

from pathlib import Path

import bpy  # type: ignore[import-not-found]

from .file_models import FileSaveArguments, FileState
from .operations import OperationError


def inspect() -> FileState:
    filepath = str(bpy.data.filepath) or None
    size = None
    if filepath:
        try:
            size = Path(filepath).stat().st_size
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise OperationError("file_access_failed", str(exc)) from exc
    return FileState(
        filepath=filepath,
        is_saved=bool(bpy.data.is_saved),
        is_dirty=bool(bpy.data.is_dirty),
        exists=size is not None,
        byte_size=size,
    )


def save(arguments: FileSaveArguments) -> FileState:
    requested = arguments.filepath or str(bpy.data.filepath)
    if not requested:
        raise OperationError("file_path_required", "The first save needs filepath")
    if bpy.context.mode != "OBJECT":
        raise OperationError("invalid_context", "Save requires Object Mode")
    destination = Path(requested)
    try:
        if destination.is_symlink():
            raise OperationError("file_destination_invalid", "Do not save to a symlink")
        destination = destination.resolve(strict=False)
        if not destination.parent.is_dir():
            raise OperationError(
                "file_destination_invalid", "Destination parent must already exist"
            )
        if destination.exists():
            if not destination.is_file():
                raise OperationError(
                    "file_destination_invalid", "Destination must be a regular file"
                )
            if not arguments.overwrite:
                raise OperationError(
                    "file_exists", "Existing files require overwrite: true"
                )
    except OSError as exc:
        raise OperationError("file_access_failed", str(exc)) from exc
    try:
        outcome = bpy.ops.wm.save_as_mainfile(
            filepath=str(destination),
            check_existing=False,
            relative_remap=True,
            copy=False,
            show_save_modified_images_dialog=False,
        )
        if "FINISHED" not in outcome:
            raise RuntimeError("Native save did not finish")
        result = inspect()
        if result.filepath != str(destination) or not result.byte_size:
            raise RuntimeError("Native save did not produce the requested file")
        return result
    except Exception as exc:
        raise OperationError(
            "file_save_failed",
            str(exc),
            {"possible_partial_write": True},
        ) from exc
