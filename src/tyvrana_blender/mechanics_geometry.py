"""Shared evidence selection, deterministic samples and complete geometry bases."""

import hashlib
import json
from typing import Any

from . import geometry_points
from .bindings import RESOURCE_KEY
from .errors import OperationError
from .layer_geometry import Surface
from .mechanics_models import GeometryEvidence, MechanicsRegion
from .mesh_selectors import select


def fail(message: str) -> None:
    raise OperationError("mechanics_invalid", message)


def selected(surface: Surface, region: MechanicsRegion) -> list[int]:
    if region.feature is not None:
        if surface.authored != surface.topology:
            fail("Named feature requires unchanged evaluated topology")
        return sorted(geometry_points.region_vertices(surface.obj, region.feature))
    return surface.selected(region.selector)


def bounded_indices(indices: list[int], count: int) -> list[int]:
    if len(indices) <= count:
        return indices
    return (
        [indices[i * (len(indices) - 1) // (count - 1)] for i in range(count)]
        if count > 1
        else [indices[len(indices) // 2]]
    )


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def basis(surface: Surface) -> str:
    return digest(
        [
            surface.obj.name,
            surface.topology,
            [list(p) for p in surface.points],
            surface.triangles,
        ]
    )


def triangle_indices(
    surface: Surface, region: MechanicsRegion, ids: list[int]
) -> list[int]:
    if region.feature is None and region.selector.domain == "face":
        faces = {
            face.index for face in select(surface.bm, region.selector, surface.obj)
        }
        return [i for i, face in enumerate(surface.triangle_faces) if face in faces]
    vertices = set(ids)
    return [
        i for i, tri in enumerate(surface.triangles) if all(v in vertices for v in tri)
    ]


def evidence(surface: Surface, region: MechanicsRegion) -> GeometryEvidence:
    resource = surface.obj.get(RESOURCE_KEY)
    return GeometryEvidence(
        object_name=surface.obj.name,
        resource_id=resource if isinstance(resource, str) else None,
        feature=region.feature,
        selection_sha256=digest(region.model_dump()),
        geometry_sha256=basis(surface),
    )
