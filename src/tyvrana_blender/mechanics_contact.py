"""Sampled regional contact with solid winding or guarded oriented patch support."""

import math
import time
from collections import Counter
from dataclasses import replace
from typing import Any

from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from .geometry_qa import Budget, Surface, point_segment, proximity
from .geometry_qa_models import GeometryInspectArguments, GeometryQuery
from .layer_geometry import SurfaceCache
from .mechanics_geometry import (
    basis,
    bounded_indices,
    digest,
    evidence,
    fail,
    selected,
    triangle_indices,
)
from .mechanics_models import (
    ContactArguments,
    ContactEnvelope,
    ContactFinding,
    ContactResult,
    ContactState,
    EnvelopeSummary,
)


def winding(point: Any, points: Any, triangles: Any, budget: Budget) -> float:
    angle = 0.0
    for tri in triangles:
        budget.test()
        a, b, c = (points[i] - point for i in tri)
        la, lb, lc = a.length, b.length, c.length
        numerator = a.dot(b.cross(c))
        denominator = la * lb * lc + a.dot(b) * lc + b.dot(c) * la + c.dot(a) * lb
        angle += 2 * math.atan2(numerator, denominator)
    return abs(angle) / (4 * math.pi)


def evaluate(
    query: ContactEnvelope, cache: SurfaceCache, budget: Budget, worst_limit: int
) -> EnvelopeSummary:
    source = cache.get(query.source.object_name)
    target = cache.get(query.target.object_name)
    source_ids = selected(source, query.source)
    target_ids = selected(target, query.target)
    triangles = [
        target.triangles[i] for i in triangle_indices(target, query.target, target_ids)
    ]
    if len(triangles) > 32768:
        fail(
            "Regional contact supports at most 32768 target triangles; "
            "narrow the interface"
        )
    fingerprint = digest(
        [
            basis(source),
            basis(target),
            query.source.model_dump(),
            query.target.model_dump(),
        ]
    )
    fresh = query.expected_sha256 is None or query.expected_sha256 == fingerprint
    issues: list[str] = []
    edges: dict[tuple[int, int], list[int]] = {}
    normals = []
    for tri in triangles:
        a, b, c = (target.points[i] for i in tri)
        n = (b - a).cross(c - a)
        normals.append(n.normalized())
        if n.length < 1e-12:
            issues.append("Degenerate target triangles")
        for i, j in zip(tri, (*tri[1:], tri[0]), strict=True):
            edges.setdefault((min(i, j), max(i, j)), []).append(1 if i < j else -1)
    if not triangles:
        issues.append("Target region has no complete triangles")
    if any(len(v) > 2 or (len(v) == 2 and sum(v) != 0) for v in edges.values()):
        issues.append("Target region is nonmanifold or inconsistently oriented")
    boundary = [e for e, v in edges.items() if len(v) == 1]
    if query.mode == "closed_solid" and boundary:
        issues.append(
            "Closed-solid evidence has an open boundary; select an oriented patch"
            " explicitly"
        )
    if not fresh:
        issues.append("Source fingerprint changed")
    if not issues:
        selected_surface = Surface(
            replace(
                target, triangles=triangles, triangle_faces=list(range(len(triangles)))
            )
        )
        qa = GeometryInspectArguments(
            objects=[
                GeometryQuery(object_name=target.obj.name, self_intersection=True)
            ],
            tolerance=1e-8,
            worst_limit=0,
        )
        _, self_contacts, _, _ = proximity(
            selected_surface, selected_surface, qa, budget
        )
        if self_contacts:
            issues.append("Target patch self-intersects; sidedness is not qualified")
    tree = (
        BVHTree.FromPolygons(target.points, triangles, all_triangles=True)
        if triangles
        else None
    )
    ids = bounded_indices(source_ids, query.sample_limit)
    findings = []
    counts: Counter[str] = Counter()
    gaps: list[float] = []
    separations: list[float] = []
    # Preserve authored normal-side semantics even under a reflected object transform.
    orientation = 1 if target.obj.matrix_world.to_3x3().determinant() > 0 else -1
    orientation *= 1 if query.allowed_side == "positive" else -1
    for index in ids:
        p = source.points[index]
        reason = "; ".join(dict.fromkeys(issues)) if issues else None
        gap = None
        triangle = None
        if reason is None and tree is not None:
            budget.test()
            near, normal, triangle, distance = tree.find_nearest(p)
            if near is None:
                reason = "No interface support"
            elif query.mode == "closed_solid":
                if distance <= 1e-8:
                    gap = 0.0
                else:
                    value = winding(p, target.points, triangles, budget)
                    if min(abs(value), abs(value - 1)) > 0.01:
                        reason = "Ambiguous solid winding; target may self-intersect"
                    else:
                        gap = -distance if value > 0.5 else distance
                separations.append(float(distance))
            else:
                assert triangle is not None
                n = normals[triangle] * orientation
                displacement = p - near
                alignment = (
                    abs(displacement.dot(n)) / distance if distance > 1e-8 else 1.0
                )
                # A rim does not establish an inside/outside side: require a complete
                # supporting neighborhood wider than the measured gap plus margin.
                margin = max(distance, query.boundary_margin)
                boundary_distance = math.inf
                for a, b in boundary:
                    budget.test()
                    boundary_distance = min(
                        boundary_distance,
                        (
                            near
                            - point_segment(near, target.points[a], target.points[b])
                        ).length,
                    )
                if boundary_distance <= margin:
                    reason = (
                        "Nearest support is at or too close to an open region boundary"
                    )
                elif alignment < query.minimum_alignment:
                    reason = "Offset is not aligned with the supporting surface normal"
                else:
                    candidates = tree.find_nearest_range(
                        p, distance + query.boundary_margin
                    )
                    budget.tests += len(candidates)
                    budget.test()
                    if len(candidates) > 64:
                        reason = "Too many competing local supports"
                    elif any(
                        normals[c[2]].dot(normals[triangle]) < query.minimum_alignment
                        for c in candidates
                    ):
                        reason = (
                            "Competing surface orientations; no unique supported side"
                        )
                    else:
                        gap = (
                            math.copysign(distance, displacement.dot(n))
                            if distance > 1e-8
                            else 0.0
                        )
                separations.append(float(distance))
        if reason is not None or gap is None:
            counts["UNCERTAIN"] += 1
        elif gap < query.minimum_gap:
            counts["INVALID_PENETRATION"] += 1
            gaps.append(float(gap))
        elif gap > query.maximum_gap:
            counts["SEPARATED"] += 1
            gaps.append(float(gap))
        else:
            counts["PERMITTED_CONTACT"] += 1
            gaps.append(float(gap))
        findings.append(
            ContactFinding(
                source_vertex=index,
                target_triangle=triangle,
                point=list(p),
                signed_gap=gap,
                reason=reason,
            )
        )
    states: list[ContactState] = [
        "INVALID_PENETRATION",
        "UNCERTAIN",
        "SEPARATED",
        "PERMITTED_CONTACT",
    ]
    classification: ContactState = next(
        (s for s in states if counts[s]),
        "UNCERTAIN",
    )
    reasons = sorted(set(issues + [f.reason for f in findings if f.reason]))[:8]
    if not ids:
        reasons.append("No source support")

    def severity(f: ContactFinding) -> tuple[float, int]:
        if f.signed_gap is not None and f.signed_gap < query.minimum_gap:
            return (f.signed_gap / (1 + abs(f.signed_gap)), f.source_vertex)
        if f.reason:
            return (1, f.source_vertex)
        return (
            2 - abs(f.signed_gap or 0) / (1 + abs(f.signed_gap or 0)),
            f.source_vertex,
        )

    return EnvelopeSummary(
        name=query.name,
        provenance=[evidence(source, query.source), evidence(target, query.target)],
        classification=classification,
        source=query.source.object_name,
        target=query.target.object_name,
        source_sha256=fingerprint,
        fresh=fresh,
        metric="solid_signed_surface_distance"
        if query.mode == "closed_solid"
        else "supported_oriented_patch_distance",
        selected_vertices=len(source_ids),
        sampled_vertices=len(ids),
        permitted_samples=counts["PERMITTED_CONTACT"],
        separated_samples=counts["SEPARATED"],
        penetrating_samples=counts["INVALID_PENETRATION"],
        uncertain_samples=counts["UNCERTAIN"],
        minimum_gap=min(gaps) if gaps else None,
        maximum_gap=max(gaps) if gaps else None,
        minimum_separation=min(separations) if separations else None,
        penetration_max=max(0.0, -min(gaps)) if gaps else None,
        contact_fraction=counts["PERMITTED_CONTACT"] / len(ids) if ids else 0,
        worst=sorted(findings, key=severity)[:worst_limit],
        reasons=reasons,
    )


def inspect(args: ContactArguments) -> ContactResult:
    start = time.perf_counter()
    budget = Budget(args.max_tests)
    with SurfaceCache() as cache:
        results = [evaluate(q, cache, budget, args.worst_limit) for q in args.envelopes]
        vertices = cache.vertices
    return ContactResult(
        envelopes=results,
        evaluated_vertices=vertices,
        tests=budget.tests,
        processing_seconds=time.perf_counter() - start,
        limitations=[
            "Sampled source vertices, not a continuous collision or whole-volume "
            "certificate. Contact fraction is a vertex-count proxy, not contact "
            "area.",
            "Closed-solid signed distance uses winding; requires an embedded "
            "consistently oriented boundary. Self-intersecting solids are "
            "unsupported.",
            "Open patches require an explicitly declared permitted normal side, "
            "interior support and consistent local orientation. Rim, corner and "
            "unsupported projections are UNCERTAIN; no exempted contacts.",
            "Patch penetration is a supported local unilateral distance, not a "
            "global minimum translation or intersection volume. Frames and "
            "geometry remain unchanged.",
        ],
    )
