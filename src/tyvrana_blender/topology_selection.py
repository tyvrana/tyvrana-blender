"""Deterministic graph traversal and native rest-frame conversion."""

from typing import Any

from .mesh_selectors import SelectionError
from .region_models import FrameRegion

MAX_TRAVERSAL = 100_000


def check_count(count: int) -> None:
    if count > MAX_TRAVERSAL:
        raise SelectionError(
            "Selection traversal exceeds 100000 elements; narrow the region"
        )


def element(elements: Any, index: int) -> Any:
    if index >= len(elements):
        raise SelectionError(
            "Seed index is outside the current topology; query a new snapshot"
        )
    return elements[index]


def ring(edge: Any) -> set[Any]:
    found = {edge}
    pending = [edge]
    while pending:
        current = pending.pop()
        if not 1 <= len(current.link_faces) <= 2:
            raise SelectionError("Ring requires manifold or boundary edges")
        for face in current.link_faces:
            if len(face.verts) != 4:
                raise SelectionError(
                    "Ring meets a triangle or ngon; select a different quad strip"
                )
            opposite = next(
                e for e in face.edges if not set(e.verts) & set(current.verts)
            )
            if opposite not in found:
                found.add(opposite)
                check_count(len(found))
                pending.append(opposite)
    return found


def loop(edge: Any, boundary: bool) -> set[Any]:
    if boundary and not edge.is_boundary:
        raise SelectionError("Boundary-loop seed must be a boundary edge")
    if not boundary and not edge.is_manifold:
        raise SelectionError(
            "Loop seed must be manifold; use boundary_loop for a boundary"
        )
    found = {edge}
    pending = [(edge, v) for v in edge.verts]
    while pending:
        previous, vertex = pending.pop()
        if boundary:
            candidates = [
                e for e in vertex.link_edges if e.is_boundary and e != previous
            ]
            if len(candidates) != 1:
                raise SelectionError(
                    "Boundary branches or is open; repair the boundary before traversal"
                )
        else:
            # A pole/boundary is an explicit termination, never a guessed turn.
            if (
                len(vertex.link_edges) != 4
                or not vertex.is_manifold
                or any(len(f.verts) != 4 for f in vertex.link_faces)
            ):
                continue
            candidates = [
                e
                for e in vertex.link_edges
                if e != previous and not set(e.link_faces) & set(previous.link_faces)
            ]
            if len(candidates) != 1:
                raise SelectionError("Loop continuation is ambiguous at this vertex")
        following = candidates[0]
        if following in found:
            continue
        found.add(following)
        check_count(len(found))
        pending.append((following, following.other_vert(vertex)))
    return found


def graph_select(bm: Any, selector: Any) -> list[Any]:
    domain = {"vertex": bm.verts, "edge": bm.edges, "face": bm.faces}[selector.domain]
    if selector.mode == "topology":
        seed = element(bm.edges, selector.seed)
        found = (
            ring(seed)
            if selector.path == "ring"
            else loop(seed, selector.path == "boundary_loop")
        )
    elif selector.mode == "connected":
        seed = element(domain, selector.seed)
        found = {seed}
        pending = [seed]
        while pending:
            current = pending.pop()
            if selector.domain == "vertex":
                adjacent = [e.other_vert(current) for e in current.link_edges]
            elif selector.domain == "edge":
                adjacent = [e for v in current.verts for e in v.link_edges]
            else:
                adjacent = [f for e in current.edges for f in e.link_faces]
            for item in adjacent:
                if item not in found:
                    found.add(item)
                    check_count(len(found))
                    pending.append(item)
    else:
        seed = element(bm.verts, selector.vertex)
        vertices = {seed}
        frontier = {seed}
        for _ in range(selector.steps):
            frontier = {
                e.other_vert(v) for v in frontier for e in v.link_edges
            } - vertices
            vertices.update(frontier)
            check_count(len(vertices))
        found = (
            vertices
            if selector.domain == "vertex"
            else {
                item
                for v in vertices
                for item in (
                    v.link_edges if selector.domain == "edge" else v.link_faces
                )
                if all(w in vertices for w in item.verts)
            }
        )
    check_count(len(found))
    return sorted(found, key=lambda item: item.index)


def region_matrix(region: FrameRegion) -> Any:
    from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]

    from .references import matrix, object_named

    frame = region.frame
    if frame.kind == "world":
        transform = (
            Matrix.Translation(Vector(frame.origin))
            @ Euler(frame.rotation, "XYZ").to_matrix().to_4x4()
        )
    else:
        obj = object_named(frame.object)
        transform = matrix(obj)
        if frame.kind == "bone":
            if obj.type != "ARMATURE" or frame.bone not in obj.data.bones:
                raise SelectionError("Region bone must exist on the named armature")
            transform = transform @ obj.data.bones[frame.bone].matrix_local
    if abs(transform.determinant()) < 1e-12:
        raise SelectionError("Region frame is singular; use an invertible transform")
    return transform.inverted()


def contains(point: Any, region: FrameRegion) -> bool:
    return all(
        a <= value <= b
        for a, value, b in zip(region.min, point, region.max, strict=True)
    )


def region_select(bm: Any, selector: Any, obj: Any) -> list[Any]:
    if obj is None:
        raise SelectionError("Frame-relative selection requires an object context")
    transform = region_matrix(selector.region) @ obj.matrix_world
    elements = {"vertex": bm.verts, "edge": bm.edges, "face": bm.faces}[selector.domain]
    found = []
    for item in elements:
        points = [
            transform @ v.co
            for v in ([item] if selector.domain == "vertex" else item.verts)
        ]
        if selector.inclusion == "center":
            matches = contains(
                sum(points[1:], points[0].copy()) / len(points), selector.region
            )
        else:
            matches = (any if selector.inclusion == "any_vertex" else all)(
                contains(p, selector.region) for p in points
            )
        if matches:
            found.append(item)
            check_count(len(found))
    return found
