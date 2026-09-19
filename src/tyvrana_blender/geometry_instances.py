"""Bounds first, shared prototypes, candidate-only exact triangle comparisons."""

import heapq
import math
from itertools import count
from typing import Any

from . import geometry_elements as elements
from . import geometry_qa as qa
from . import layer_geometry
from .geometry_qa_models import (
    ElementContact,
    GeometryInspectArguments,
    InstanceQuery,
    InstanceSummary,
)


class InstanceBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.vertices = 0

    def use(self, amount: int) -> None:
        self.vertices += amount
        if self.vertices > self.maximum:
            qa.fail(
                "Instance candidate vertex budget exceeded; reduce queries/samples "
                "or raise max_instance_vertices"
            )


def tree(
    bounds: list[tuple[tuple[float, ...], tuple[float, ...]]], indices: list[int]
) -> qa.Node:
    lo = tuple(min(bounds[i][0][k] for i in indices) for k in range(3))
    hi = tuple(max(bounds[i][1][k] for i in indices) for k in range(3))
    if len(indices) <= 4:
        return qa.Node(lo, hi, indices)
    axis = max(range(3), key=lambda k: hi[k] - lo[k])
    indices.sort(key=lambda i: bounds[i][0][axis] + bounds[i][1][axis])
    mid = len(indices) // 2
    return qa.Node(
        lo, hi, [], (tree(bounds, indices[:mid]), tree(bounds, indices[mid:]))
    )


def surface(element: elements.Element) -> qa.Surface:
    proto = element.prototype
    return qa.Surface(
        layer_geometry.Surface(
            obj=None,
            topology="",
            authored="",
            curve=None,
            points=element.points(),
            triangles=proto.triangles,
            triangle_faces=proto.faces,
            bm=None,
        )
    )


def inspect(
    query: InstanceQuery,
    surfaces: dict[str, qa.Surface],
    args: GeometryInspectArguments,
    budget: qa.Budget,
    vertices: InstanceBudget,
) -> InstanceSummary:
    system = elements.collect(query.object_name, args.max_instances)
    bounds = [e.bounds() for e in system.elements]
    root = tree(bounds, list(range(len(bounds))))
    materialized: dict[int, qa.Surface] = {}
    before = vertices.vertices

    def get(i: int) -> qa.Surface:
        if i not in materialized:
            vertices.use(len(system.elements[i].prototype.points))
            materialized[i] = surface(system.elements[i])
        return materialized[i]

    minimum = math.inf
    closest = None
    contacts: list[ElementContact] = []
    contained: set[str] = set()
    contact_pairs = triangle_pairs = candidates = 0

    def compare(i: int, j: int | str) -> None:
        nonlocal minimum, closest, contact_pairs, triangle_pairs, candidates
        candidates += 1
        left = system.elements[i]
        right = system.elements[j] if isinstance(j, int) else None
        a = get(i)
        b = get(j) if isinstance(j, int) else surfaces[j]
        nearest, found, _, details = qa.proximity(a, b, args, budget)
        right_id = right.identity if right else f"object:{j}"

        def row(contact: Any) -> ElementContact:
            return ElementContact(
                left_id=left.identity,
                right_id=right_id,
                left_prototype=left.prototype.name,
                right_prototype=right.prototype.name if right else str(j),
                left_layer=left.layer,
                right_layer=right.layer if right else None,
                surface=contact,
            )

        if nearest and nearest.distance < minimum:
            minimum = nearest.distance
            closest = row(nearest)
        if found:
            contact_pairs += 1
            triangle_pairs += found
            contacts.extend(row(detail) for detail in details)
            contacts.sort(
                key=lambda r: (
                    r.surface.distance,
                    r.left_id,
                    r.right_id,
                    r.surface.left_face,
                    r.surface.right_face,
                )
            )
            del contacts[args.worst_limit :]
        if (
            right is None
            and args.containment
            and not found
            and qa.inside_count(a, b, budget)
        ):
            contained.add(left.identity)

    if query.self_intersection and len(system.elements) > 1:
        serial = count()
        queue = [(0.0, next(serial), root, root)]
        while queue:
            lower, _, a, b = heapq.heappop(queue)
            budget.nodes += 1
            if budget.nodes > 8 * budget.maximum + 1024 or len(queue) > 250000:
                qa.fail("Instance bounds traversal budget exceeded")
            if lower > max(minimum**2, args.tolerance**2):
                continue
            if a.children or b.children:
                if a is b:
                    assert a.children is not None
                    x, y = a.children
                    children = [(x, x), (x, y), (y, y)]
                elif a.children:
                    children = [(x, b) for x in a.children]
                else:
                    assert b.children is not None
                    children = [(a, y) for y in b.children]
                for x, y in children:
                    heapq.heappush(
                        queue, (qa.bounds_distance(x, y), next(serial), x, y)
                    )
                continue
            for i in a.indices:
                for j in b.indices:
                    if a is b and i >= j:
                        continue
                    lo, hi = bounds[i]
                    other_lo, other_hi = bounds[j]
                    lower = sum(
                        max(0, lo[k] - other_hi[k], other_lo[k] - hi[k]) ** 2
                        for k in range(3)
                    )
                    if lower <= max(minimum**2, args.tolerance**2):
                        compare(i, j)
    for name in query.obstacles:
        obstacle = surfaces[name]
        ordered = sorted(
            (qa.bounds_distance(qa.Node(lo, hi, []), obstacle.root), i)
            for i, (lo, hi) in enumerate(bounds)
        )
        for lower, i in ordered:
            if lower > max(minimum**2, args.tolerance**2):
                break
            compare(i, name)
    return InstanceSummary(
        object_name=query.object_name,
        instance_count=len(system.elements),
        prototype_count=len(system.prototypes),
        equivalent_vertices=system.equivalent_vertices,
        transformed_vertices=vertices.vertices - before,
        tested_instances=len(materialized),
        candidate_pairs=candidates,
        contact_element_pairs=contact_pairs,
        contact_triangle_pairs=triangle_pairs,
        minimum_distance=None if math.isinf(minimum) else minimum,
        closest=closest,
        contacts=contacts,
        contained_instances=len(contained),
        contained_ids=sorted(contained)[: args.worst_limit],
        degenerate_tested_triangles=sum(
            n.length <= args.tolerance**2
            for s in materialized.values()
            for n in s.normals
        ),
        path_points=system.evaluated_path_points,
    )
