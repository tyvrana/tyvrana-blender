"""Selection predicates over current authored topology, independent of UI state."""

import math
from collections.abc import Iterator
from typing import Any

from .mesh_models import MeshElementSelector


class SelectionError(Exception):
    pass


def select(bm: Any, selector: MeshElementSelector, obj: Any = None) -> Iterator[Any]:
    if selector.mode in {"topology", "connected", "neighborhood"}:
        from .topology_selection import graph_select

        yield from graph_select(bm, selector)
        return
    if selector.mode == "region":
        from .topology_selection import region_select

        yield from region_select(bm, selector, obj)
        return
    elements = getattr(
        bm, {"vertex": "verts", "edge": "edges", "face": "faces"}[selector.domain]
    )
    if selector.mode == "indices":
        if any(index >= len(elements) for index in selector.indices):
            raise SelectionError("Element index is outside the current topology")
        yield from (elements[index] for index in selector.indices)
        return
    direction = (
        tuple(
            component / math.hypot(*selector.direction)
            for component in selector.direction
        )
        if selector.mode == "normal"
        else None
    )
    for element in elements:
        match selector.mode:
            case "valence":
                matches = (
                    selector.minimum <= len(element.link_edges) <= selector.maximum
                    and (not selector.extraordinary or len(element.link_edges) != 4)
                    and (selector.include_boundary or not element.is_boundary)
                )
            case "all":
                matches = True
            case "box":
                point = (
                    element.co
                    if selector.domain == "vertex"
                    else element.calc_center_median()
                )
                matches = all(
                    selector.min[i] <= point[i] <= selector.max[i] for i in range(3)
                )
            case "normal":
                assert direction is not None
                # Degenerate faces do not have a meaningful normal direction.
                matches = (
                    math.hypot(*element.normal) > 0
                    and sum(element.normal[i] * direction[i] for i in range(3))
                    >= selector.min_dot
                )
            case "boundary":
                matches = bool(element.is_boundary)
            case "seam":
                matches = bool(element.seam) == selector.value
        if matches:
            yield element
