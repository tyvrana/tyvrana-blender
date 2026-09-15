"""Atomic data-image persistence with native encoding and binary verification."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from .artifacts import ArtifactSpool
from .bake import image_pixels
from .bake_models import ImageSaveArguments, ImageSaveResult
from .operations import OperationError


def save(
    arguments: ImageSaveArguments, spool: ArtifactSpool
) -> tuple[ImageSaveResult, ArtifactDescriptor]:
    image = bpy.data.images.get(arguments.name)
    if (
        image is None
        or not all(image.size)
        or image.channels != 4
        or not image.colorspace_settings.is_data
    ):
        raise OperationError(
            "image_invalid", "Data export requires a loaded non-color RGBA image"
        )
    destination = Path(bpy.path.abspath(arguments.filepath))
    if (
        not destination.is_absolute()
        or destination.suffix.lower() != ".png"
        or destination.is_symlink()
    ):
        raise OperationError(
            "file_destination_invalid",
            "Use an absolute or Blender-relative PNG path, without symlinks",
        )
    destination = destination.resolve()
    if not destination.parent.is_dir() or (
        destination.exists() and not destination.is_file()
    ):
        raise OperationError(
            "file_destination_invalid",
            "Destination parent must exist and destination must be a file",
        )
    if destination.exists() and not arguments.overwrite:
        raise OperationError("file_exists", "Existing maps require overwrite: true")
    reference = (
        image_pixels(image)
        .reshape(-1, 4)[:: max(1, image.size[0] * image.size[1] // 65536)]
        .copy()
    )
    scene = bpy.data.scenes.new("Data image encoding temporary")
    probe: Any = None
    path: Path | None = None
    try:
        fmt = scene.render.image_settings
        fmt.file_format = "PNG"
        fmt.color_mode = "RGBA"
        fmt.color_depth = str(arguments.bit_depth)
        fmt.color_management = "OVERRIDE"
        fmt.linear_colorspace_settings.name = "Non-Color"
        fd, temporary = tempfile.mkstemp(
            prefix=".tyvrana-image-", suffix=".png", dir=destination.parent
        )
        os.close(fd)
        path = Path(temporary)
        image.save_render(str(path), scene=scene)
        encoded = path.read_bytes()
        if encoded[:8] != b"\x89PNG\r\n\x1a\n" or encoded[24] != arguments.bit_depth:
            raise OperationError(
                "image_export_failed",
                "Encoder did not produce the requested PNG bit depth",
            )
        probe = bpy.data.images.load(str(path), check_existing=False)
        probe.colorspace_settings.name = "Non-Color"
        values = image_pixels(probe).reshape(-1, 4)[
            :: max(1, image.size[0] * image.size[1] // 65536)
        ]
        error = float(np.max(np.abs(values - reference)))
        if error > 2 / (2**arguments.bit_depth - 1):
            raise OperationError(
                "image_export_failed",
                "Data-image roundtrip exceeds quantization tolerance",
            )
        with spool.reserve() as (artifact_id, artifact_path):
            artifact_path.write_bytes(encoded)
            descriptor = spool.describe(artifact_id).model_copy(
                update={"name": destination.name}
            )
            # Stage both encoding and binary transport before replacing the destination.
            if not arguments.overwrite:
                os.link(path, destination)
                path.unlink()
            else:
                os.replace(path, destination)
            path = None
            if arguments.pack:
                image.pack(data=encoded, data_len=len(encoded))
            image.filepath_raw = (
                bpy.path.relpath(str(destination))
                if bpy.data.is_saved
                else str(destination)
            )
            image.source = "FILE"
            image.file_format = "PNG"
            image.use_fake_user = True
            return ImageSaveResult(
                image=image.name,
                filepath=str(destination),
                byte_size=len(encoded),
                bit_depth=arguments.bit_depth,
                packed=bool(image.packed_file),
                sha256=hashlib.sha256(encoded).hexdigest(),
                maximum_roundtrip_error=error,
            ), descriptor
    finally:
        if probe is not None:
            bpy.data.images.remove(probe)
        bpy.data.scenes.remove(scene)
        if path is not None:
            path.unlink(missing_ok=True)
