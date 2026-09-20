"""Bounded native volume meshing of finite semantic form inputs."""

from collections.abc import Generator
from typing import Any, cast

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Euler  # type: ignore[import-not-found]

from . import loft, surfaces
from .errors import OperationError
from .form_models import (
    EllipsoidPart,
    FormPart,
    FormSpec,
    LoftPart,
    ReferenceSurfaceFit,
    SectionMaskPart,
    SilhouettePart,
)
from .form_reference import compile_mask


def smooth_min(a: Any, b: Any, radius: float) -> Any:
    if radius == 0:
        return np.minimum(a, b)
    h = np.maximum(radius - np.abs(a - b), 0) / radius
    return np.minimum(a, b) - h * h * radius / 4


def prepare(
    part: FormPart,
    voxel: float,
    band: float,
    support: ReferenceSurfaceFit | None = None,
    whole_form: bool = False,
) -> dict[str, Any]:
    if isinstance(part, EllipsoidPart):
        rotation = np.array(Euler(part.rotation).to_matrix(), dtype=np.float64)
        extent = np.abs(rotation) @ np.array(part.radii)
        return {
            "low": np.array(part.center) - extent,
            "high": np.array(part.center) + extent,
            "rotation": rotation,
            "sources": [],
        }
    if isinstance(part, (SectionMaskPart, SilhouettePart)):
        projection = "plane" if isinstance(part, SectionMaskPart) else "orthographic"
        masks = [compile_mask(m, projection) for m in part.masks]
        complementary: list[dict[str, Any]] = []
        missing_intervals: list[tuple[float, float]] = []
        if isinstance(part, SectionMaskPart):
            # Reuse already-declared fitting evidence in internal section gaps.
            # A component may only borrow from its own matching image stack:
            # all original masks must also be declared as fitting observations.
            # Never extend caps, mix frames, or invent/interpolate new evidence.
            selected = {m.model_dump_json() for m in part.masks}
            available = (
                {m.model_dump_json() for m in support.masks} if support else set()
            )
            if support is not None and selected <= available:
                first = masks[0]
                axial = [float(m.origin @ first.normal) for m in masks]
                if np.any(np.diff(axial) < voxel * 0.5):
                    raise OperationError(
                        "form_sections_invalid",
                        "Order section planes with at least half a voxel spacing",
                    )
                lower, upper = min(axial), max(axial)
                original_positions = sorted(axial)
                other_masks = []
                for candidate in support.masks:
                    if candidate.model_dump_json() in selected:
                        continue
                    mask = compile_mask(candidate, "plane")
                    if abs(float(mask.normal @ first.normal)) < 1e-5:
                        other_masks.append(mask)
                    position = float(mask.origin @ first.normal)
                    offset = mask.origin - first.origin
                    if (
                        mask.field.shape == first.field.shape
                        and np.allclose(mask.spacing, first.spacing, rtol=1e-5)
                        and np.allclose(mask.horizontal, first.horizontal, atol=1e-5)
                        and np.allclose(mask.vertical, first.vertical, atol=1e-5)
                        and abs(float(offset @ first.horizontal)) < voxel * 1e-4
                        and abs(float(offset @ first.vertical)) < voxel * 1e-4
                        and lower < position < upper
                        and min(abs(position - p) for p in axial) >= voxel * 0.5
                    ):
                        masks.append(mask)
                        axial.append(position)
                        j = int(np.searchsorted(original_positions, position))
                        missing_intervals.append(
                            (original_positions[j - 1], original_positions[j])
                        )
                masks.sort(key=lambda m: float(m.origin @ first.normal))
                # A single whole-form stack can leave an inward interpolation
                # pocket even after omitted planes are restored. Only two
                # independent perpendicular stacks may corroborate missing
                # material in those gaps; supplied section planes stay exact.
                if whole_form and missing_intervals:
                    groups: list[list[Any]] = []
                    for mask in other_masks:
                        group = next(
                            (
                                g
                                for g in groups
                                if float(g[0].normal @ mask.normal) > 0.99999
                            ),
                            None,
                        )
                        if group is None:
                            groups.append([mask])
                        else:
                            group.append(mask)
                    for group in groups:
                        direction = group[0].normal
                        group.sort(key=lambda m: float(m.origin @ direction))
                        positions = np.array(
                            [m.origin @ group[0].normal for m in group]
                        )
                        if len(group) >= 2 and np.all(
                            np.diff(positions) >= voxel * 0.5
                        ):
                            if all(
                                abs(float(group[0].normal @ c["masks"][0].normal))
                                < 1e-5
                                for c in complementary
                            ):
                                complementary.append(
                                    {"masks": group, "positions": positions}
                                )
                    if len(complementary) != 2:
                        complementary = []
            normal = masks[0].normal
            if any(float(m.normal @ normal) < 0.99999 for m in masks):
                raise OperationError(
                    "form_sections_invalid",
                    "Section planes must have matching oriented normals",
                )
            positions = np.array([m.origin @ normal for m in masks])
            if np.any(np.diff(positions) < voxel * 0.5):
                raise OperationError(
                    "form_sections_invalid",
                    "Order section planes with at least half a voxel spacing",
                )
            # Bound material rather than unused image background. Extrude each
            # observed foreground rectangle across the axial range: a monotone
            # interpolant cannot become negative where every source is positive.
            # This remains conservative for oblique section frames.
            extents = []
            for mask in masks:
                corners = mask.foreground_corners()
                for position in (positions[0], positions[-1]):
                    offset = (position - corners @ normal) / (mask.normal @ normal)
                    extents.append(corners + offset[:, None] * mask.normal)
            points = np.concatenate(extents)
            low, high = points.min(axis=0), points.max(axis=0)
        else:
            low, high = np.array(part.bounds_min), np.array(part.bounds_max)
            normals = np.array([m.normal for m in masks])
            if np.linalg.matrix_rank(normals) < 2:
                raise OperationError(
                    "form_silhouettes_invalid",
                    "Use at least two distinct view directions",
                )
            positions = None
        return {
            "low": low,
            "high": high,
            "masks": masks,
            "positions": positions,
            "sources": [m.provenance for m in masks],
            "complementary": complementary,
            "missing_intervals": missing_intervals,
        }
    mesh = None
    try:
        if isinstance(part, LoftPart):
            if not part.spec.caps:
                raise OperationError(
                    "form_source_invalid", "Form loft parts must be closed"
                )
            points, faces = loft.geometry(part.spec)
            mesh = bpy.data.meshes.new("FormInput")
            mesh.from_pydata(points, [], faces)
            mesh.update()
        else:
            mesh, _, _ = surfaces.build(part.spec)
        mesh.calc_loop_triangles()
        points = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", points)
        points = points.reshape((-1, 3))
        triangles = np.array(
            [t.vertices[:] for t in mesh.loop_triangles], dtype=np.uint32
        )
        if len(points) > 131072 or len(triangles) > 262144:
            raise OperationError(
                "form_limit", "A form input exceeds131072 vertices/262144 triangles"
            )
        return {
            "low": points.min(axis=0),
            "high": points.max(axis=0),
            "points": points,
            "triangles": triangles,
            "sources": [],
        }
    finally:
        if mesh is not None:
            bpy.data.meshes.remove(mesh)


def analytic(part: FormPart, prepared: dict[str, Any], points: Any) -> Any:
    if isinstance(part, EllipsoidPart):
        q = (points - np.array(part.center)) @ prepared["rotation"]
        radii = np.array(part.radii)
        k0 = np.linalg.norm(q / radii, axis=1)
        k1 = np.linalg.norm(q / (radii * radii), axis=1)
        return np.where(
            k1 > 1e-12, k0 * (k0 - 1) / np.maximum(k1, 1e-12), -min(part.radii)
        )
    masks = prepared["masks"]
    if isinstance(part, SilhouettePart):
        value = np.maximum.reduce([m.sample(points) for m in masks])
        # Explicit modeling bounds are constraints too, not invented reference evidence.
        return np.maximum(
            value,
            np.maximum(
                np.array(part.bounds_min) - points, points - np.array(part.bounds_max)
            ).max(axis=1),
        )
    positions = prepared["positions"]
    axial = points @ masks[0].normal
    index = np.clip(np.searchsorted(positions, axial) - 1, 0, len(masks) - 2)
    value = np.empty(len(points), dtype=np.float32)
    for i in np.unique(index):
        selected = index == i
        t = np.clip(
            (axial[selected] - positions[i]) / (positions[i + 1] - positions[i]), 0, 1
        )
        q = points[selected]
        first, second = masks[i].sample(q), masks[i + 1].sample(q)
        h = positions[i + 1] - positions[i]
        slope = (second - first) / h

        def tangent(left: Any, right: Any, left_h: float, right_h: float) -> Any:
            same = left * right > 0
            w1, w2 = 2 * right_h + left_h, right_h + 2 * left_h
            denominator = np.divide(
                w1, left, out=np.ones_like(left), where=same
            ) + np.divide(w2, right, out=np.ones_like(right), where=same)
            return np.divide(w1 + w2, denominator, out=np.zeros_like(left), where=same)

        left_slope = (
            slope
            if i == 0
            else tangent(
                (first - masks[i - 1].sample(q)) / (positions[i] - positions[i - 1]),
                slope,
                positions[i] - positions[i - 1],
                h,
            )
        )
        right_slope = (
            slope
            if i + 2 == len(masks)
            else tangent(
                slope,
                (masks[i + 2].sample(q) - second)
                / (positions[i + 2] - positions[i + 1]),
                h,
                positions[i + 2] - positions[i + 1],
            )
        )
        value[selected] = (
            (2 * t**3 - 3 * t**2 + 1) * first
            + (t**3 - 2 * t**2 + t) * h * left_slope
            + (-2 * t**3 + 3 * t**2) * second
            + (t**3 - t**2) * h * right_slope
        )
    complementary = prepared.get("complementary", [])
    if complementary:
        corroborated = np.maximum.reduce(
            [analytic(part, stack, points) for stack in complementary]
        )
        local = np.zeros(len(points), dtype=bool)
        for low, high in prepared["missing_intervals"]:
            local |= (axial > low) & (axial < high)
        t = np.clip(
            (axial - positions[index]) / (positions[index + 1] - positions[index]),
            0,
            1,
        )
        # C1 taper vanishes on every supplied/restored plane. Add material only
        # when both independent stacks place it inside their observed volumes.
        weight = np.sin(np.pi * t) ** 2
        value -= np.where(
            local & (corroborated < 0),
            weight * np.maximum(value - corroborated, 0),
            0,
        )
    return np.maximum(value, np.maximum(positions[0] - axial, axial - positions[-1]))


def build(spec: FormSpec) -> tuple[Any, dict[str, Any]]:
    steps = build_steps(spec)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return cast(tuple[Any, dict[str, Any]], done.value)


def build_steps(spec: FormSpec) -> Generator[None, None, tuple[Any, dict[str, Any]]]:
    import openvdb  # type: ignore[import-not-found]

    voxel = spec.voxel_size
    band = max(p.blend for p in spec.parts) + voxel * 4
    prepared = []
    for part in spec.parts:
        prepared.append(
            prepare(
                part, voxel, band, spec.surface_fit, whole_form=len(spec.parts) == 1
            )
        )
        yield None
    additions = [
        v for p, v in zip(spec.parts, prepared, strict=True) if p.operation == "add"
    ]
    margin = sum(p.blend for p in spec.parts if p.operation == "add") / 4 + voxel * 4
    low_float = np.floor(
        (np.minimum.reduce([p["low"] for p in additions]) - margin) / voxel
    )
    high_float = np.ceil(
        (np.maximum.reduce([p["high"] for p in additions]) + margin) / voxel
    )
    if (
        not np.isfinite([low_float, high_float]).all()
        or max(np.max(np.abs(low_float)), np.max(np.abs(high_float))) > 1e9
        or np.max(high_float - low_float + 1) > 1024
    ):
        raise OperationError(
            "form_limit", "Sampling grid exceeds bounded native coordinates/resolution"
        )
    low, high = low_float.astype(int), high_float.astype(int)
    shape = high - low + 1
    count = int(np.prod(shape))
    if min(shape) < 3 or max(shape) > 1024 or count > spec.max_voxels:
        raise OperationError(
            "form_limit",
            f"Form needs {count} samples in {shape.tolist()}; "
            "increase voxel_size or bounded max_voxels",
        )
    field = None
    for part, item in zip(spec.parts, prepared, strict=True):
        values = np.empty(tuple(shape), dtype=np.float32)
        if "points" in item:
            native_shape = np.ceil((item["high"] - item["low"] + 2 * band) / voxel) + 1
            if float(np.prod(native_shape)) > spec.max_voxels * 2:
                raise OperationError(
                    "form_limit",
                    (
                        "Native input distance band exceeds bounded voxel work; revise "
                        "blend/sampling"
                    ),
                )
            source_grid = openvdb.FloatGrid.createLevelSetFromPolygons(
                item["points"],
                triangles=item["triangles"],
                transform=openvdb.createLinearTransform(voxelSize=voxel),
                halfWidth=band / voxel,
            )
            source_grid.copyToArray(values, ijk=tuple(int(v) for v in low))
            del source_grid
        else:
            flat = values.reshape(-1)
            for start in range(0, count, 65536):
                stop = min(start + 65536, count)
                indices = np.array(
                    np.unravel_index(np.arange(start, stop), tuple(shape))
                ).T
                flat[start:stop] = analytic(part, item, (indices + low) * voxel)
                yield None
        if field is None:
            field = values
        elif part.operation == "add":
            field = smooth_min(field, values, part.blend)
        elif part.operation == "subtract":
            field = -smooth_min(-field, values, part.blend)
        else:
            field = -smooth_min(-field, -values, part.blend)
    assert field is not None
    if not np.isfinite(field).all() or not (field < 0).any():
        raise OperationError(
            "form_empty", "Form has no bounded material at requested sampling size"
        )
    if any(
        np.any(np.take(field, i, axis=axis) <= 0) for axis in range(3) for i in (0, -1)
    ):
        raise OperationError(
            "form_clipped", "Form touches sampling boundary; revise bounds or blends"
        )
    # Smooth the scalar surface before extracting topology. The finite binomial
    # kernel removes sampling ripples without a separate brush/vertex workflow.
    # Positive domain padding stays fixed; thin features still need comparison.
    for _ in range(spec.smoothing_passes):
        for axis in range(3):
            center = [slice(None)] * 3
            lower, upper = center.copy(), center.copy()
            center[axis], lower[axis], upper[axis] = (
                slice(1, -1),
                slice(None, -2),
                slice(2, None),
            )
            field[tuple(center)] = (
                0.25 * field[tuple(lower)]
                + 0.5 * field[tuple(center)]
                + 0.25 * field[tuple(upper)]
            )
            yield None
    grid = openvdb.FloatGrid(background=voxel * 3)
    grid.copyFromArray(np.clip(field, -voxel * 3, voxel * 3).astype(np.float32))
    grid.transform = openvdb.createLinearTransform(voxelSize=voxel)
    yield None
    points, triangles, quads = grid.convertToPolygons(adaptivity=spec.adaptivity)
    if not len(points) or len(points) > spec.max_vertices:
        raise OperationError(
            "form_limit",
            f"Form generated {len(points)} vertices; "
            "revise sampling/adaptivity or bounded max_vertices",
        )
    points += low * voxel
    mesh = bpy.data.meshes.new(spec.name + ".Mesh")
    try:
        mesh.from_pydata(points.tolist(), [], triangles.tolist() + quads.tolist())
        # Isosurfaces passing exactly through grid samples can produce coincident
        # vertices/sliver faces. Remove only sub-voxel numerical debris.
        native = bmesh.new()
        try:
            native.from_mesh(mesh)
            bmesh.ops.remove_doubles(
                native, verts=list(native.verts), dist=voxel * 1e-4
            )
            bmesh.ops.dissolve_degenerate(
                native, edges=list(native.edges), dist=voxel * 1e-5
            )
            native.to_mesh(mesh)
        finally:
            native.free()
        mesh.update()
        for face in mesh.polygons:
            face.use_smooth = True
        sources = [source for p in prepared for source in p["sources"]]
        fit = None
        if spec.surface_fit is not None:
            from .form_fitting import fit_steps

            fit, fit_sources = yield from fit_steps(mesh, spec.surface_fit)
            sources.extend(fit_sources)
        sources = list({s["reference_id"]: s for s in sources}.values())
        return mesh, {
            "sampled_voxels": count,
            "sources": sources,
            "surface_fit": fit.model_dump(mode="json") if fit else None,
        }
    except BaseException:
        bpy.data.meshes.remove(mesh)
        raise
