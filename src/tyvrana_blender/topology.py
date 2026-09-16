"""Compact cage inspection and guarded native loop refinement."""

import hashlib
import json
import math
import time
from collections import Counter
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import mesh
from .deformation_qa import distribution
from .mesh_models import ElementCounts
from .mesh_selectors import SelectionError, select
from .operations import OperationError
from .topology_models import (
    MeshInsertLoopsArguments,
    PoleSample,
    TopologyInspectArguments,
    TopologySummary,
)


def refinement_guard(obj: Any) -> None:
    from . import modifiers, rig

    modifiers.budget(obj, modifiers.stack(obj), strict=True)
    allowed = {"MIRROR", "SUBSURF", "SHRINKWRAP", "ARMATURE"}
    if any(m.type not in allowed for m in obj.modifiers):
        raise OperationError(
            "invalid_context",
            "Loop refinement supports only Mirror/Subdivision/Shrinkwrap and an "
            "owned Armature binding; preserve other modifier dependencies",
        )
    if any(m.type == "ARMATURE" for m in obj.modifiers):
        rig.binding_summary(obj)
    for other in bpy.data.objects:
        if any(
            m.type in {"MESH_DEFORM", "SURFACE_DEFORM"}
            and getattr(m, "object", getattr(m, "target", None)) == obj
            for m in other.modifiers
        ):
            raise OperationError(
                "invalid_context",
                "Topology is used by an external deformation binding; "
                "remove/rebuild that binding explicitly",
            )


def refine(bm: Any, args: MeshInsertLoopsArguments) -> None:
    from .retopo_finish import quad_ring, refine_ring, reject

    seeds = []
    touched: set[Any] = set()
    growth = 0
    for cut in args.cuts:
        if cut.edge >= len(bm.edges) or cut.from_vertex >= len(bm.verts):
            reject("Cut seed is outside the input snapshot; query current topology")
        edge = bm.edges[cut.edge]
        ring = quad_ring(edge, bm.verts[cut.from_vertex], 4096)
        faces = {f for e in ring for f in e.link_faces}
        if touched & faces:
            reject(
                "Cut strips overlap faces; combine factors for a strip or use "
                "separate operations"
            )
        touched.update(faces)
        seeds.append(
            (
                cut.from_vertex,
                edge.other_vert(bm.verts[cut.from_vertex]).index,
                cut.factors,
            )
        )
        growth += len(ring) * len(cut.factors) * 10
    estimate = mesh.work_size(bm) + growth
    mesh.check_budget(estimate)
    if estimate * sum(len(c.factors) for c in args.cuts) > 20_000_000:
        reject(
            "Refinement exceeds 20000000 element/cut work units; reduce the cut batch"
        )
    for start, end, factors in seeds:
        edge = bm.edges.get((bm.verts[start], bm.verts[end]))
        if edge is None:
            reject("A staged strip changed another seed; use disjoint strips")
        refine_ring(bm, edge.index, start, factors)
    if any(e.calc_length() <= 1e-9 for e in bm.edges) or any(
        f.calc_area() <= 1e-16 for f in bm.faces
    ):
        reject(
            "Refinement produced degenerate geometry; increase spacing or repair "
            "the input"
        )


def inspect(obj: Any, args: TopologyInspectArguments) -> TopologySummary:
    start = time.perf_counter()
    with mesh.snapshot(obj) as bm:
        try:
            chosen = list(select(bm, args.selector, obj))
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        vertices = set(mesh.selected_vertices(chosen, args.selector.domain))
        edges = {
            e
            for v in vertices
            for e in v.link_edges
            if all(w in vertices for w in e.verts)
        }
        faces = {
            f
            for v in vertices
            for f in v.link_faces
            if all(w in vertices for w in f.verts)
        }
        points = {v: obj.matrix_world @ v.co for v in vertices}
        if any(not math.isfinite(c) for point in points.values() for c in point):
            raise OperationError(
                "invalid_context", "World topology coordinates must be finite"
            )
        lengths = {e: (points[e.verts[0]] - points[e.verts[1]]).length for e in edges}
        # Native area in transformed coordinates, with no live data mutation.
        transform = obj.matrix_world
        for v in bm.verts:
            v.co = transform @ v.co
        bm.normal_update()
        if any(not math.isfinite(x) for x in lengths.values()) or any(
            not math.isfinite(f.calc_area()) for f in faces
        ):
            raise OperationError(
                "invalid_context", "World topology metrics exceed numeric capacity"
            )
        poles = sorted(
            (v for v in vertices if len(v.link_edges) != 4),
            key=lambda v: (-len(v.link_edges), v.index),
        )
        signature = hashlib.sha256(
            json.dumps(
                (
                    len(bm.verts),
                    [tuple(v.index for v in e.verts) for e in bm.edges],
                    [tuple(v.index for v in f.verts) for f in bm.faces],
                ),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return TopologySummary(
            object_name=obj.name,
            topology_sha256=signature,
            selected=ElementCounts(
                vertices=len(vertices), edges=len(edges), faces=len(faces)
            ),
            quad_count=sum(len(f.verts) == 4 for f in faces),
            triangle_count=sum(len(f.verts) == 3 for f in faces),
            ngon_count=sum(len(f.verts) > 4 for f in faces),
            boundary_edge_count=sum(e.is_boundary for e in edges),
            non_manifold_edge_count=sum(
                not e.is_manifold and not e.is_boundary for e in edges
            ),
            degenerate_face_count=sum(f.calc_area() <= 1e-16 for f in faces),
            valence={
                str(k): v
                for k, v in sorted(Counter(len(v.link_edges) for v in vertices).items())
            },
            edge_lengths=distribution(list(lengths.values())),
            face_areas=distribution([f.calc_area() for f in faces]),
            quad_aspect=distribution(
                [
                    max(lengths[e] for e in f.edges) ** 2 / f.calc_area()
                    for f in faces
                    if len(f.verts) == 4 and f.calc_area() > 1e-16
                ]
            ),
            pole_count=len(poles),
            poles=[
                PoleSample(
                    vertex=v.index,
                    valence=len(v.link_edges),
                    boundary=v.is_boundary,
                    world=list(points[v]),
                )
                for v in poles[: args.sample_limit]
            ],
            poles_truncated=len(poles) > args.sample_limit,
            inspection_seconds=time.perf_counter() - start,
        )
