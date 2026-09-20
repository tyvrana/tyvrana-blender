"""Deterministic registered foreground fields; no image or semantic inference."""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np  # type: ignore[import-not-found]

from . import construction, references
from .errors import OperationError
from .form_distance import distance_grid
from .form_models import ReferenceMask
from .image_buffers import decoded


@dataclass
class Mask:
    field: Any
    foreground: Any
    origin: Any
    horizontal: Any
    vertical: Any
    normal: Any
    spacing: tuple[float, float]
    provenance: dict[str, Any]

    def sample(self, points: Any) -> Any:
        q = points - self.origin
        x = q @ self.horizontal / self.spacing[0]
        y = q @ self.vertical / self.spacing[1]
        h, w = self.field.shape
        cx, cy = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
        ix, iy = cx.astype(int), cy.astype(int)
        tx, ty = cx - ix, cy - iy
        jx, jy = np.minimum(ix + 1, w - 1), np.minimum(iy + 1, h - 1)
        value = ((1 - tx) * self.field[iy, ix] + tx * self.field[iy, jx]) * (1 - ty) + (
            (1 - tx) * self.field[jy, ix] + tx * self.field[jy, jx]
        ) * ty
        outside = np.hypot((x - cx) * self.spacing[0], (y - cy) * self.spacing[1])
        return value + outside

    def corners(self) -> Any:
        h, w = self.field.shape
        return np.array(
            [
                self.origin
                + self.horizontal * x * self.spacing[0]
                + self.vertical * y * self.spacing[1]
                for x in (0, w - 1)
                for y in (0, h - 1)
            ]
        )

    def foreground_corners(self) -> Any:
        rows, columns = np.nonzero(self.foreground)
        # One sample of padding includes interpolated pixel-boundary uncertainty.
        return np.array(
            [
                self.origin
                + self.horizontal * x * self.spacing[0]
                + self.vertical * y * self.spacing[1]
                for x in (columns.min() - 1, columns.max() + 1)
                for y in (rows.min() - 1, rows.max() + 1)
            ]
        )


def compile_mask(spec: ReferenceMask, projection: str, resolution: int = 512) -> Mask:
    obj = references.owned(spec.reference, references.REFERENCE)
    with decoded(obj.data):
        return _compile_mask(obj, spec, projection, resolution)


def _compile_mask(
    obj: Any, spec: ReferenceMask, projection: str, resolution: int
) -> Mask:
    record, fresh = construction.current_registration(obj)
    if not fresh:
        raise OperationError(
            "reference_stale",
            "Re-register changed source evidence before form fitting/comparison",
        )
    if record["specification"]["projection"] != projection:
        raise OperationError(
            "reference_projection_invalid",
            f"This constraint requires a registered {projection} source",
        )
    image = obj.data
    w, h = image.size
    if w * h > 4194304 or image.channels != 4:
        raise OperationError(
            "reference_limit", "Mask source requires RGBA pixels within4 megapixels"
        )
    pixels = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    step = max(1, math.ceil(max(w, h) / resolution))
    sampled = pixels.reshape((h, w, 4))[::step, ::step]
    values = (
        sampled[:, :, 3]
        if spec.channel == "alpha"
        else sampled[:, :, :3] @ np.array([0.2126, 0.7152, 0.0722])
    )
    inside = values >= spec.threshold
    if spec.invert:
        inside = ~inside
    if (
        not inside.any()
        or inside.all()
        or inside[0].any()
        or inside[-1].any()
        or inside[:, 0].any()
        or inside[:, -1].any()
    ):
        raise OperationError(
            "reference_mask_invalid",
            (
                "Foreground must be nonempty and surrounded by background; "
                "crop/pad or revise channel/threshold"
            ),
            {
                "reference": spec.reference,
                "channel": spec.channel,
                "foreground_pixels": int(inside.sum()),
                "sampled_pixels": int(inside.size),
                "channel_range": [float(values.min()), float(values.max())],
                "alpha_range": [
                    float(sampled[:, :, 3].min()),
                    float(sampled[:, :, 3].max()),
                ],
            },
        )
    origin = np.array(references.pixel_point(obj, [0.5, 0.5]), dtype=np.float64)
    # Use image-wide spans: subtracting adjacent float32 world points amplifies
    # cancellation when padding or placement puts the pixel origin far away.
    dx = (np.array(references.pixel_point(obj, [w - 0.5, 0.5])) - origin) / (w - 1)
    dy = (np.array(references.pixel_point(obj, [0.5, h - 0.5])) - origin) / (h - 1)
    sx, sy = float(np.linalg.norm(dx)), float(np.linalg.norm(dy))
    horizontal, vertical = dx / sx, dy / sy
    if abs(float(horizontal @ vertical)) > 1e-5:
        raise OperationError(
            "reference_projection_invalid",
            "Mask pixels require perpendicular registered axes",
        )
    boundary = np.zeros(inside.shape, dtype=bool)
    boundary[1:, :] |= inside[1:, :] != inside[:-1, :]
    boundary[:-1, :] |= inside[1:, :] != inside[:-1, :]
    boundary[:, 1:] |= inside[:, 1:] != inside[:, :-1]
    boundary[:, :-1] |= inside[:, 1:] != inside[:, :-1]
    # Pixel-center boundary approximation; half a sample is the uncertainty floor.
    half = min(sx, sy) * step / 2
    field = (
        (distance_grid(boundary, sx * step, sy * step) + half) * np.where(inside, -1, 1)
    ).astype(np.float32)
    return Mask(
        field,
        inside,
        origin,
        horizontal,
        vertical,
        np.cross(horizontal, vertical),
        (sx * step, sy * step),
        {
            "reference_id": record["reference_id"],
            "source_sha256": record["source_sha256"],
            "basis_sha256": record["basis_sha256"],
            "frame_sha256": record["frame_sha256"],
            "sampling_uncertainty": max(sx, sy) * step,
            "sample_stride": step,
            "projection": projection,
        },
    )
