"""Read-only bounded reference/image previews without file or color-state changes."""

from contextlib import ExitStack
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from tyvrana_protocol import ArtifactDescriptor

from .artifacts import ArtifactSpool
from .errors import OperationError
from .image_buffers import decoded
from .image_models import ImagePreviewArguments, ImagePreviewResult, ImagePreviewTile
from .raster import MAX_IMAGE_PIXELS, png_rgb


def preview(
    arguments: ImagePreviewArguments, spool: ArtifactSpool
) -> tuple[ImagePreviewResult, ArtifactDescriptor]:
    images = [bpy.data.images.get(name) for name in arguments.names]
    with ExitStack() as buffers:
        for source in images:
            if source is not None:
                buffers.enter_context(decoded(source))
        return _preview(arguments, spool, images)


def _preview(
    arguments: ImagePreviewArguments, spool: ArtifactSpool, images: list[Any]
) -> tuple[ImagePreviewResult, ArtifactDescriptor]:
    if any(
        image is None
        or not all(image.size)
        or image.channels != 4
        or image.size[0] * image.size[1] > MAX_IMAGE_PIXELS
        or image.source not in {"FILE", "GENERATED"}
        for image in images
    ):
        raise OperationError(
            "image_invalid", "Preview requires bounded loaded RGBA images"
        )
    if sum(image.size[0] * image.size[1] for image in images) > 2 * MAX_IMAGE_PIXELS:
        raise OperationError("image_limit", "Preview sources exceed64 megapixels")
    columns = min(arguments.columns, len(images))
    rows = (len(images) + columns - 1) // columns
    size = arguments.tile_size
    canvas = np.full((rows * size, columns * size, 3), 32, dtype=np.uint8)
    tiles = []
    for index, image in enumerate(images):
        space = image.colorspace_settings.name
        if space not in {"sRGB", "Non-Color", "Linear Rec.709"}:
            raise OperationError(
                "image_color_unsupported",
                "Preview supports sRGB, linear Rec.709 and data images",
            )
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape((height, width, 4))
        scale = min(1.0, size / max(width, height))
        tw, th = max(1, round(width * scale)), max(1, round(height * scale))
        # Bilinear bounded resampling; Blender remains the source decoder.
        x = np.clip((np.arange(tw) + 0.5) / scale - 0.5, 0, width - 1)
        y = np.clip((np.arange(th) + 0.5) / scale - 0.5, 0, height - 1)
        ix, iy = x.astype(int), y.astype(int)
        tx, ty = (x - ix)[None, :, None], (y - iy)[:, None, None]
        jx, jy = np.minimum(ix + 1, width - 1), np.minimum(iy + 1, height - 1)
        sample = ((1 - tx) * pixels[iy[:, None], ix] + tx * pixels[iy[:, None], jx]) * (
            1 - ty
        ) + ((1 - tx) * pixels[jy[:, None], ix] + tx * pixels[jy[:, None], jx]) * ty
        rgb = np.clip(sample[:, :, :3], 0, 1)
        alpha = np.clip(sample[:, :, 3:4], 0, 1)
        if image.alpha_mode == "PREMUL":
            rgb = np.divide(rgb, alpha, out=np.zeros_like(rgb), where=alpha > 1e-8)
        if space == "Linear Rec.709" or (image.is_float and space == "sRGB"):
            rgb = np.where(
                rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055
            )
        rgb = np.clip(rgb * alpha + 0.125 * (1 - alpha), 0, 1)
        row, column = index // columns, index % columns
        top, left = row * size + (size - th) // 2, column * size + (size - tw) // 2
        canvas[top : top + th, left : left + tw] = np.round(rgb[::-1] * 255).astype(
            np.uint8
        )
        tiles.append(
            ImagePreviewTile(
                name=image.name,
                width=width,
                height=height,
                color_space=space,
                row=row,
                column=column,
            )
        )
    with spool.reserve() as (identifier, path):
        path.write_bytes(png_rgb(columns * size, rows * size, canvas.tobytes()))
        descriptor = spool.describe(identifier)
    return ImagePreviewResult(
        width=columns * size, height=rows * size, tiles=tiles
    ), descriptor
