"""Resolve an image-guided stroke against one current evaluated surface."""

import math
from typing import Any

from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from .errors import OperationError
from .sculpt_models import ImageStrokePath, StrokeSample


def samples(obj: Any, tree: Any, path: ImageStrokePath) -> list[StrokeSample]:
    try:
        camera = Matrix(path.view.camera_world)
        projection = Matrix(path.view.projection_matrix)
        if any(abs(camera[3][i] - (1 if i == 3 else 0)) > 1e-6 for i in range(4)):
            raise ValueError("Camera transform must be affine")
        camera.inverted()
        inverse = projection.inverted()
        local = obj.matrix_world.inverted()
    except (ValueError, OverflowError) as exc:
        raise OperationError(
            "invalid_arguments", "Image stroke framing is singular or out of range"
        ) from exc
    output = []
    for index, sample in enumerate(path.samples):
        ends = []
        for depth in (-1, 1):
            point = inverse @ Vector((2 * sample.u - 1, 1 - 2 * sample.v, depth, 1))
            if not all(math.isfinite(v) for v in point) or abs(point.w) < 1e-30:
                raise OperationError(
                    "invalid_arguments", "Image stroke projection is degenerate"
                )
            ends.append(local @ (camera @ (point.xyz / point.w)))
        direction = ends[1] - ends[0]
        length = math.hypot(*direction)
        if not all(math.isfinite(v) for p in ends for v in p) or not (
            0 < length < 1e30
        ):
            raise OperationError("invalid_arguments", "Image stroke ray is degenerate")
        hit = tree.ray_cast(ends[0], direction / length, length)[0]
        if hit is None:
            raise OperationError(
                "sculpt_image_miss",
                "Image stroke sample misses the target within the captured view",
                {"sample_index": index},
            )
        output.append(StrokeSample(location=list(hit), pressure=sample.pressure))
    return output
