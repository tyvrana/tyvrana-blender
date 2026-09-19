"""Root-pinned scalar lift, broad triangle clearance and ordered side probes."""

import time
from typing import Any

from mathutils import Vector  # type: ignore[import-not-found]

from . import geometry_instances as instances
from . import geometry_qa as qa
from . import growth, layer_geometry
from .geometry_elements import Element, Elements
from .geometry_qa_models import GeometryInspectArguments, InstanceQuery
from .growth_layers_models import LayerConflict, LayerCorrectArguments


class Solver:
    def __init__(self, args: LayerCorrectArguments) -> None:
        self.args = args
        self.started = time.perf_counter()
        self.budget = qa.Budget(args.max_triangle_tests)
        self.rays = 0
        self.maximum = 0.0
        self.lifts: dict[str, int] = {}
        self.query = GeometryInspectArguments(
            instances=[InstanceQuery(object_name=args.object_name)],
            tolerance=args.clearance,
            worst_limit=1,
        )

    def bounded(self) -> None:
        if time.perf_counter() - self.started > self.args.max_seconds:
            growth.fail("Layer correction time budget exceeded; cache unchanged")
        if self.rays > self.args.max_triangle_tests:
            growth.fail("Layer side-probe budget exceeded; cache unchanged")

    def frame(
        self, system: Elements, matrix: Any, frame: float
    ) -> tuple[list[Any], list[LayerConflict]]:
        args = self.args
        direction = (matrix.to_3x3() @ Vector(args.direction)).normalized()
        inverse = matrix.inverted().to_3x3()
        results: dict[str, list[Any]] = {}
        accepted: list[tuple[str, qa.Surface, bool]] = []
        with layer_geometry.SurfaceCache() as surfaces:
            for name in args.colliders:
                accepted.append(
                    ("object:" + name, qa.Surface(surfaces.get(name)), True)
                )
            for element in sorted(
                system.elements, key=lambda e: (e.layer, e.order, e.root_id)
            ):
                self.bounded()
                original = element.points()
                weights = []
                for p in element.prototype.points:
                    t = min(
                        1,
                        max(0, (p.z - args.pin_end) / (args.full_lift - args.pin_end)),
                    )
                    weights.append(t * t * (3 - 2 * t))
                conflict = None
                for step in range(self.lifts.get(element.identity, 0), args.steps + 1):
                    self.bounded()
                    amount = args.maximum_lift * step / args.steps
                    element._points = [
                        p + direction * (amount * w)
                        for p, w in zip(original, weights, strict=True)
                    ]
                    candidate = instances.surface(element)
                    conflict = self.conflict(
                        element, candidate, accepted, direction, frame
                    )
                    if conflict is None:
                        self.lifts[element.identity] = step
                        self.maximum = max(self.maximum, amount * max(weights))
                        results[element.identity] = [
                            inverse @ (p - q)
                            for p, q in zip(element.points(), original, strict=True)
                        ]
                        accepted.append((element.identity, candidate, False))
                        break
                if conflict is not None:
                    return [], [conflict]
        # Native family Join Geometry order; root order inside each family is stable.
        return [
            p
            for e in sorted(system.elements, key=lambda e: e.family_index)
            for p in results[e.identity]
        ], []

    def conflict(
        self,
        element: Element,
        candidate: qa.Surface,
        accepted: list[tuple[str, qa.Surface, bool]],
        direction: Any,
        frame: float,
    ) -> LayerConflict | None:
        args = self.args
        if any(n.length < 1e-12 for n in candidate.normals):
            return LayerConflict(
                frame=frame,
                element=element.identity,
                obstacle=element.identity,
                reason="Correction contains a degenerate triangle",
            )
        _, self_contacts, _, _ = qa.proximity(
            candidate, candidate, self.query, self.budget
        )
        if self_contacts:
            return LayerConflict(
                frame=frame,
                element=element.identity,
                obstacle=element.identity,
                reason="Nonadjacent triangles within one template violate clearance",
            )
        for identity, target, body in accepted:
            self.bounded()
            distance = None
            reason = None
            if qa.bounds_distance(candidate.root, target.root) <= args.clearance**2:
                nearest, contacts, _, _ = qa.proximity(
                    candidate, target, self.query, self.budget
                )
                distance = nearest.distance if nearest else None
                if contacts:
                    reason = "Broad triangle clearance below requested margin"
                elif (
                    body
                    and target.closed
                    and qa.inside_count(candidate, target, self.budget)
                ):
                    reason = "Template is inside a closed collider"
            if reason is None and (body or element.overlap):
                # Probe the upper surface along the chosen correction direction.
                # Exact triangle clearance above independently guards edge crossings.
                probes = [
                    *candidate.points,
                    *((a + b + c) / 3 for a, b, c in candidate.triangles),
                ]
                tree = target.source.bvh()
                for point in probes:
                    self.rays += 1
                    self.bounded()
                    top = point + direction * (args.maximum_lift + args.clearance * 2)
                    hit, _, _, _ = tree.ray_cast(
                        top, -direction, args.maximum_lift * 2 + args.clearance * 4
                    )
                    if (
                        hit is not None
                        and (point - hit).dot(direction) < args.clearance
                    ):
                        reason = "Requested upper-side overlap is not satisfied"
                        break
            if reason:
                return LayerConflict(
                    frame=frame,
                    element=element.identity,
                    obstacle=identity,
                    distance=distance,
                    reason=reason,
                )
        return None
