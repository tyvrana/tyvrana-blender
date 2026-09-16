"""Bounded native topology finishing with explicit geometric orientation."""

from dataclasses import dataclass, field
from typing import Any

import bmesh  # type: ignore[import-not-found]

from . import mesh
from . import retopo as retopo
from . import retopo_geometry as geometry
from .errors import OperationError
from .retopo_models import (
    MAX_COLLAPSE_EDGES,
    MAX_FINISH_EDGES,
    RetopoCollapseArguments,
    RetopoFillArguments,
    RetopoFinishArguments,
    RetopoInsertArguments,
    RetopoRotateArguments,
    RetopoSlideArguments,
    RetopoStitchArguments,
    RetopoSubdivideArguments,
)

FINISH_TYPES = (
    RetopoInsertArguments,
    RetopoSlideArguments,
    RetopoSubdivideArguments,
    RetopoCollapseArguments,
    RetopoRotateArguments,
    RetopoStitchArguments,
    RetopoFillArguments,
)


@dataclass
class Edit:
    selected_count: int
    vertices: list[Any] = field(default_factory=list)
    faces: list[Any] = field(default_factory=list)
    flow_edges: list[Any] = field(default_factory=list)
    rebind_vertices: bool = False
    input_edges_before: list[int] = field(default_factory=list)


def reject(message: str) -> None:
    raise OperationError("retopo_flow_invalid", message)


def one_edge(bm: Any, selector: Any) -> Any:
    edges = retopo.selection(bm, selector, 1)
    if len(edges) != 1:
        reject("Select exactly one edge")
    return edges[0]


def quad_ring(
    edge: Any, start: Any | None = None, maximum: int = MAX_FINISH_EDGES
) -> dict[Any, Any]:
    """Map each transverse edge to its oriented endpoint through opposite quad edges."""
    start = start if start is not None else min(edge.verts, key=lambda v: v.index)
    if start not in edge.verts:
        reject("from_vertex must be an endpoint of the selected edge")
    oriented = {edge: start}
    pending = [edge]
    faces: set[Any] = set()
    while pending:
        current = pending.pop()
        if not 1 <= len(current.link_faces) <= 2:
            reject("A ring requires boundary or manifold edges")
        for face in current.link_faces:
            if len(face.verts) != 4:
                reject("A ring cannot continue through a triangle or ngon")
            opposite = next(
                e for e in face.edges if not set(e.verts) & set(current.verts)
            )
            side = next(
                e for e in face.edges if e != current and oriented[current] in e.verts
            )
            endpoint = side.other_vert(oriented[current])
            if opposite in oriented:
                if oriented[opposite] != endpoint:
                    reject("The ring reverses orientation or revisits invalid topology")
            else:
                oriented[opposite] = endpoint
                pending.append(opposite)
                if len(oriented) > maximum:
                    reject(f"Ring exceeds {maximum} edges")
            faces.add(face)
    if len({v for e in oriented for v in e.verts}) != 2 * len(oriented):
        reject("Ring edges must have disjoint endpoints")
    ends = sum(e.is_boundary for e in oriented)
    if ends not in {0, 2} or len(faces) != len(oriented) - (ends // 2):
        reject("Require a closed or clean boundary-to-boundary quad ring")
    return oriented


def vertex(bm: Any, index: int) -> Any:
    if index >= len(bm.verts):
        reject("Vertex index is outside the current authored snapshot")
    return bm.verts[index]


def insert(bm: Any, args: RetopoInsertArguments) -> Edit:
    edge = one_edge(bm, args.edge)
    start = vertex(bm, args.from_vertex) if args.from_vertex is not None else None
    ring = quad_ring(edge, start)
    input_edges = sorted(e.index for e in ring)
    count = len(bm.verts)
    flow_pairs = refine_ring(
        bm,
        edge.index,
        ring[edge].index,
        args.factors or [args.factor],
        MAX_FINISH_EDGES,
    )
    new = list(bm.verts)[count:]
    flow = [bm.edges.get((bm.verts[a], bm.verts[b])) for a, b in flow_pairs]
    return Edit(
        len(ring),
        new,
        flow_edges=flow,
        rebind_vertices=True,
        input_edges_before=input_edges,
    )


def refine_ring(
    bm: Any,
    edge_index: int,
    start_index: int,
    factors: list[float],
    maximum: int = 4096,
) -> list[tuple[int, int]]:
    """Cut descending fractions; native interpolation uses each actual cut position."""
    end_index = bm.edges[edge_index].other_vert(bm.verts[start_index]).index
    previous = 1.0
    flow_pairs: list[tuple[int, int]] = []
    for factor in reversed(factors):
        start = bm.verts[start_index]
        edge = bm.edges.get((start, bm.verts[end_index]))
        if edge is None:
            reject("Refinement rail disappeared during staged subdivision")
        oriented = quad_ring(edge, start, maximum)
        count = len(bm.verts)
        ratio = factor / previous
        bmesh.ops.subdivide_edges(
            bm,
            edges=list(oriented),
            cuts=1,
            edge_percents={
                e: ratio if e.verts[0] == v else 1 - ratio for e, v in oriented.items()
            },
            use_grid_fill=True,
            use_only_quads=True,
        )
        mesh.refresh(bm)
        if len(bm.verts) - count != len(oriented):
            reject("Native refinement did not produce one vertex per rail")
        flow_pairs.extend(
            (e.verts[0].index, e.verts[1].index)
            for e in bm.edges
            if all(v.index >= count for v in e.verts)
        )
        neighbors = [
            e.other_vert(bm.verts[start_index])
            for e in bm.verts[start_index].link_edges
            if e.other_vert(bm.verts[start_index]).index >= count
        ]
        if len(neighbors) != 1:
            reject("Native refinement produced an ambiguous rail")
        end_index = neighbors[0].index
        previous = factor
    return flow_pairs


def subdivide(bm: Any, args: RetopoSubdivideArguments) -> Edit:
    edges = retopo.selection(bm, args.selector, MAX_FINISH_EDGES)
    chosen = set(edges)
    for face in {f for e in edges for f in e.link_faces}:
        selected = [e for e in face.edges if e in chosen]
        if len(face.verts) != 4 or not (
            len(selected) == 4
            or (
                len(selected) == 2
                and not set(selected[0].verts) & set(selected[1].verts)
            )
        ):
            reject("Each touched quad needs four or two opposite selected edges")
    if any(not e.link_faces for e in edges):
        reject("Subdivision requires surface edges")
    input_edges = [e.index for e in edges]
    count = len(bm.verts)
    bmesh.ops.subdivide_edges(
        bm,
        edges=edges,
        cuts=args.cuts,
        use_grid_fill=True,
        use_only_quads=True,
    )
    mesh.refresh(bm)
    return Edit(
        len(edges),
        list(bm.verts)[count:],
        rebind_vertices=True,
        input_edges_before=input_edges,
    )


def slide(bm: Any, args: RetopoSlideArguments) -> Edit:
    selected = retopo.selection(bm, args.selector, MAX_FINISH_EDGES)
    toward = vertex(bm, args.toward_vertex)
    if args.selector.domain == "vertex":
        if len(selected) != 1:
            reject("Vertex slide requires exactly one vertex and its explicit neighbor")
        current = selected[0]
        if not any(e.other_vert(current) == toward for e in current.link_edges):
            reject("toward_vertex must be connected to the selected vertex")
        rails = {current: toward}
    else:
        edges = selected
        components = geometry.boundary_components(edges)
        if len(components) != 1 or components[0][2] == "branched":
            reject("Slide requires one complete nonbranching loop or chain")
        vertices = set(components[0][1])
        if toward in vertices or any(
            not e.is_manifold or any(len(f.verts) != 4 for f in e.link_faces)
            for e in edges
        ):
            reject("Loop slide requires two adjacent quad rails")
        for v in vertices:
            degree = sum(e in edges for e in v.link_edges)
            if (degree == 1 and (not v.is_boundary or len(v.link_edges) != 3)) or (
                degree == 2 and (v.is_boundary or len(v.link_edges) != 4)
            ):
                reject(
                    "Select a full loop ending at clean boundaries; do not cross poles"
                )
        anchors = [
            v for v in vertices if any(e.other_vert(v) == toward for e in v.link_edges)
        ]
        if len(anchors) != 1:
            reject("toward_vertex must identify exactly one unselected rail neighbor")
        rails = {anchors[0]: toward}
        pending = [anchors[0]]
        while pending:
            v = pending.pop()
            for e in v.link_edges:
                if e not in edges:
                    continue
                w = e.other_vert(v)
                faces = [f for f in e.link_faces if rails[v] in f.verts]
                if len(faces) != 1:
                    reject("The selected loop has ambiguous side correspondence")
                neighbors = [
                    x.other_vert(w) for x in faces[0].edges if w in x.verts and x != e
                ]
                if len(neighbors) != 1 or neighbors[0] in vertices:
                    reject("Invalid slide rail")
                if w in rails and rails[w] != neighbors[0]:
                    reject("Slide side reverses around the loop")
                if w not in rails:
                    rails[w] = neighbors[0]
                    pending.append(w)
        if set(rails) != vertices:
            reject("Slide loop is disconnected")
    if args.factor == 0:
        return Edit(len(selected))
    proposed = {v: v.co.lerp(w.co, args.factor) for v, w in rails.items()}
    for v, co in proposed.items():
        v.co = co
    return Edit(len(selected), list(rails))


def protect_mirror(target: Any, vertices: list[Any]) -> None:
    for mod in target.modifiers:
        if mod.type == "MIRROR":
            axis = list(mod.use_axis).index(True)
            if any(abs(v.co[axis]) <= 1e-6 for v in vertices):
                reject("Collapse/stitch at the Mirror seam is not supported")


def collapse(bm: Any, target: Any, args: RetopoCollapseArguments) -> Edit:
    edge = one_edge(bm, args.selector)
    edges = list(quad_ring(edge)) if args.mode == "ring" else [edge]
    if len(edges) > MAX_COLLAPSE_EDGES:
        reject("Collapse exceeds 64 edges")
    vertices = {v for e in edges for v in e.verts}
    if any(not e.is_manifold and not e.is_boundary for e in edges):
        reject("Collapse requires manifold surface topology")
    if not args.allow_boundary and any(v.is_boundary for v in vertices):
        reject("Boundary vertices are protected; allow_boundary is required")
    protect_mirror(target, list(vertices))
    if any(len(f.verts) not in {3, 4} for v in vertices for f in v.link_faces):
        reject("Collapse only supports triangle/quad neighborhoods")
    neighbors = {w for v in vertices for f in v.link_faces for w in f.verts}
    input_edges = [e.index for e in edges]
    bmesh.ops.collapse(bm, edges=edges, uvs=False)
    survivors = [v for v in vertices if v.is_valid]
    faces = list({f for v in neighbors if v.is_valid for f in v.link_faces})
    if args.mode == "ring" and any(len(f.verts) != 4 for f in faces):
        reject("Ring collapse must preserve a quad neighborhood")
    return Edit(len(edges), survivors, faces, input_edges_before=input_edges)


def rotate(bm: Any, args: RetopoRotateArguments) -> Edit:
    edge = one_edge(bm, args.edge)
    faces = list(edge.link_faces)
    if not edge.is_manifold or not edge.is_contiguous or len(faces) != 2:
        reject("Rotate requires one consistently wound internal edge")
    if len(faces[0].verts) != len(faces[1].verts) or len(faces[0].verts) not in {3, 4}:
        reject("Rotate supports two triangles or two quads")
    vertices = {v for f in faces for v in f.verts}
    if len(vertices) != sum(len(f.verts) for f in faces) - 2:
        reject("Adjacent faces must share only the selected edge")
    input_edges = [edge.index]
    result = bmesh.ops.rotate_edges(
        bm, edges=[edge], use_ccw=args.direction == "counterclockwise"
    )
    if len(result["edges"]) != 1:
        reject("Native edge rotation could not create a valid replacement diagonal")
    new_edge = result["edges"][0]
    return Edit(
        1,
        faces=list(new_edge.link_faces),
        flow_edges=[new_edge],
        input_edges_before=input_edges,
    )


def open_chain(bm: Any, selector: Any, start: int) -> tuple[list[Any], list[Any]]:
    edges = retopo.selection(bm, selector, MAX_FINISH_EDGES)
    vertices = retopo.boundary(edges, closed=False)
    degree = {v: sum(e in edges for e in v.link_edges) for v in vertices}
    if sum(d == 1 for d in degree.values()) != 2:
        reject("Stitch requires open chains, not closed loops")
    if vertices[0].index != start:
        vertices.reverse()
    if vertices[0].index != start:
        reject("Explicit chain start must be an endpoint")
    return edges, vertices


def stitch(bm: Any, target: Any, args: RetopoStitchArguments) -> Edit:
    edges_a, a = open_chain(bm, args.chain_a, args.start_a)
    edges_b, b = open_chain(bm, args.chain_b, args.start_b)
    if len(a) != len(b) or set(a) & set(b):
        reject("Stitch requires disjoint equal-count open chains")
    transform = geometry.matrix(target)
    if any(
        (transform @ x.co - transform @ y.co).length > args.max_weld_distance
        for x, y in zip(a, b, strict=True)
    ):
        reject("Paired seam vertices exceed max_weld_distance")
    protect_mirror(target, a + b)
    # Winding must be opposite along the paired seam, independent of selector order.
    for i in range(len(a) - 1):
        signs = []
        for chain in (a, b):
            x, y = chain[i : i + 2]
            e = next(e for e in x.link_edges if y in e.verts)
            signs.append(
                any(
                    loop.vert == x and loop.link_loop_next.vert == y
                    for loop in e.link_loops
                )
            )
        if signs[0] == signs[1]:
            reject("Stitched boundaries must have compatible opposite face winding")
    input_edges = [e.index for e in edges_a + edges_b]
    for x, y in zip(a, b, strict=True):
        x.co = x.co.lerp(y.co, 0.5)
    bmesh.ops.weld_verts(
        bm, targetmap=dict(zip(b, a, strict=True)), average_vert_data=True
    )
    return Edit(len(edges_a) + len(edges_b), a, input_edges_before=input_edges)


def fill(bm: Any, args: RetopoFillArguments) -> Edit:
    edges = retopo.selection(bm, args.boundary, MAX_FINISH_EDGES)
    vertices = retopo.boundary(edges, closed=True)
    if any(sum(e.is_boundary for e in v.link_edges) != 2 for v in vertices):
        reject("Grid fill requires a complete unbranched boundary component")
    if len(edges) % 2 or args.span >= len(edges) // 2:
        reject("Grid fill needs an even boundary and span smaller than half its length")
    if retopo.crossing_boundaries(edges, []):
        reject("Grid fill boundary intersects itself")
    vertices = retopo.oriented_loop(vertices)
    corners = [i for i, v in enumerate(vertices) if v.index == args.corner_vertex]
    if not corners:
        reject("corner_vertex must lie on the selected boundary")
    n = corners[0]
    vertices = vertices[n:] + vertices[:n]
    ordered = [
        next(e for e in v.link_edges if vertices[(i + 1) % len(vertices)] in e.verts)
        for i, v in enumerate(vertices)
    ]
    half = len(edges) // 2
    rails = ordered[: args.span] + ordered[half : half + args.span]
    count = len(bm.verts)
    input_edges = [e.index for e in edges]
    template = edges[0].link_faces[0]
    result = bmesh.ops.grid_fill(
        bm,
        edges=rails,
        mat_nr=template.material_index,
        use_smooth=template.smooth,
        use_interp_simple=True,
    )
    mesh.refresh(bm)
    faces = result["faces"]
    if len(faces) != args.span * (half - args.span):
        reject("Grid fill did not produce the requested rectangular quad layout")
    return Edit(
        len(edges), list(bm.verts)[count:], faces, input_edges_before=input_edges
    )


def apply(bm: Any, target: Any, args: RetopoFinishArguments) -> Edit:
    if isinstance(args, RetopoInsertArguments):
        return insert(bm, args)
    if isinstance(args, RetopoSlideArguments):
        return slide(bm, args)
    if isinstance(args, RetopoSubdivideArguments):
        return subdivide(bm, args)
    if isinstance(args, RetopoCollapseArguments):
        return collapse(bm, target, args)
    if isinstance(args, RetopoRotateArguments):
        return rotate(bm, args)
    if isinstance(args, RetopoStitchArguments):
        return stitch(bm, target, args)
    return fill(bm, args)
