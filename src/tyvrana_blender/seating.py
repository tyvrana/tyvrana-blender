"""Projected SE(3) least squares over cached, explicitly scoped mating geometry."""

import math
import time
from dataclasses import replace
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]

from . import mechanics_contact, organization, references
from .errors import OperationError
from .geometry_qa import Budget, inside_count, proximity
from .geometry_qa import Surface as QASurface
from .geometry_qa_models import GeometryInspectArguments, GeometryQuery
from .layer_geometry import SurfaceCache
from .mechanics_geometry import bounded_indices, selected, triangle_indices
from .mechanics_models import ContactEnvelope, MechanicsRegion
from .seating_models import RigidSeating, SeatingResidual, SeatingResult


def fail(message: str) -> Any:
    raise OperationError("placement_invalid", message)


def rotation(vector: Any) -> Any:
    angle = float(np.linalg.norm(vector))
    if angle < 1e-15:
        return np.eye(3)
    a = vector / angle
    skew = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * (skew @ skew)


def point_owner(source: Any) -> str | None:
    if source.kind in {"object", "geometry", "bone"}:
        return str(source.object)
    if source.kind == "landmark":
        obj = organization.object_named(source.name)
        return str(obj.parent.name) if obj.parent else None
    return None


class Region:
    def __init__(self, cache: SurfaceCache, spec: MechanicsRegion, count: int) -> None:
        self.surface = cache.get(spec.object_name)
        self.ids = selected(self.surface, spec)
        self.points = np.asarray(
            [self.surface.points[i] for i in bounded_indices(self.ids, count)]
        )
        self.center = np.mean([self.surface.points[i] for i in self.ids], axis=0)
        self.triangles = [
            self.surface.triangles[i]
            for i in triangle_indices(self.surface, spec, self.ids)
        ]
        if not self.triangles or len(self.triangles) > 8192:
            fail("Seating regions require 1..8192 complete triangles")
        self.tree = BVHTree.FromPolygons(
            self.surface.points, self.triangles, all_triangles=True
        )
        normals = [Vector((0, 0, 0)) for _ in self.surface.points]
        for i, j, k in self.triangles:
            a, b, c = (self.surface.points[q] for q in (i, j, k))
            n = (b - a).cross(c - a)
            for q in (i, j, k):
                normals[q] += n
        for n in normals:
            n.normalize()
        self.normals = np.asarray(
            [normals[i] for i in bounded_indices(self.ids, count)]
        )
        self.orientation = (
            1 if self.surface.obj.matrix_world.to_3x3().determinant() > 0 else -1
        )


def verify_pose(objects: list[Any], original: dict[str, Any], delta: Any) -> None:
    for obj in objects:
        expected = delta @ original[obj.name]
        if (
            max(
                abs(obj.matrix_world[i][j] - expected[i][j])
                for i in range(4)
                for j in range(4)
            )
            > 0.00002
        ):
            fail("Native application did not preserve the rigid transform")


def seat(spec: RigidSeating) -> SeatingResult:
    start = time.perf_counter()
    organization.idle(mutate=spec.apply)
    explicit = {organization.object_named(n) for n in spec.members}
    members = set(explicit)
    if spec.include_descendants:
        for obj in explicit:
            members.update(organization.descendants(obj))
    if len(members) > 256:
        fail("Rigid seating supports at most256 members including descendants")
    if any(c not in members for o in members for c in o.children):
        fail("Include every descendant to preserve rigid assembly membership")
    names = {o.name for o in members}
    ordered = sorted(members, key=lambda o: o.name)
    roots = [o for o in ordered if o.parent not in members]
    original = {o.name: organization.matrix(o).copy() for o in ordered}
    for obj in ordered:
        organization.object_editable(obj)
        organization.simple_transform(obj)
    pivot = np.asarray(original[spec.members[0]].translation, dtype=float)
    bounds = np.asarray([*spec.translation_limit, *spec.rotation_limit], dtype=float)
    free = np.flatnonzero(bounds > 0)
    x = np.asarray(
        [*spec.initial.translation, *spec.initial.rotation_vector], dtype=float
    )
    if spec.initial.mirror_axis is not None:
        sign = np.ones(3)
        sign["XYZ".index(spec.initial.mirror_axis)] = -1
        x[:3] *= sign
        x[3:] *= -sign
    if np.any(np.abs(x) > bounds):
        fail("Initial estimate exceeds translation/rotation-vector bounds")
    budget = Budget(spec.max_tests)
    issues: list[str] = []
    iterations = 0
    evaluations = 0
    status: Any = "NO_CONVERGENCE"

    def membership(source: str | None, target: str | None) -> None:
        if source not in names or target in names:
            fail(
                "Interface sources must move; targets must remain outside the assembly"
            )
        if target:
            obj = organization.object_named(target)
            parent = obj.parent
            while parent is not None:
                if parent.name in names:
                    fail("A target cannot depend on a moving parent")
                parent = parent.parent

    with SurfaceCache() as cache:
        # Transform-dependent modifiers must not deform a dependent member when
        # its object matrix moves. Retain only the selected affected geometry.
        evaluated_members = [
            o.name for o in ordered if o.type == "MESH" and o.modifiers
        ]
        for name in evaluated_members:
            cache.get(name)
        terms: list[tuple[Any, Any, Any]] = []
        guards: list[tuple[Any, Region, Region]] = []
        for term in spec.interfaces:
            if term.kind == "point":
                membership(point_owner(term.source), point_owner(term.target))
                terms.append(
                    (
                        term,
                        np.asarray(references.resolve(term.source)),
                        np.asarray(references.resolve(term.target)),
                    )
                )
            elif term.kind == "frame":
                membership(term.source_object, term.target_object)
                a = organization.matrix(organization.object_named(term.source_object))
                b = organization.matrix(organization.object_named(term.target_object))
                terms.append(
                    (
                        term,
                        (
                            np.asarray(a.translation),
                            np.asarray(a.to_quaternion().to_matrix()),
                        ),
                        (
                            np.asarray(b.translation),
                            np.asarray(b.to_quaternion().to_matrix()),
                        ),
                    )
                )
            else:
                membership(term.source.object_name, term.target.object_name)
                terms.append(
                    (
                        term,
                        Region(cache, term.source, spec.sample_limit),
                        Region(cache, term.target, spec.sample_limit),
                    )
                )
        for guard in spec.guards:
            membership(guard.source.object_name, guard.target.object_name)
            a, b = (
                Region(cache, guard.source, spec.sample_limit),
                Region(cache, guard.target, spec.sample_limit),
            )
            if len(a.ids) > 2048:
                fail("Guard exceeds2048 vertices; narrow the selected region")
            guards.append((guard, a, b))
        # Qualify sidedness and topology once with the canonical contact evaluator.
        for guard, _, _ in guards:
            query = ContactEnvelope(
                name=guard.name,
                source=guard.source,
                target=guard.target,
                mode=guard.mode,
                allowed_side=guard.allowed_side,
                maximum_gap=1000,
                sample_limit=spec.sample_limit,
            )
            evidence = mechanics_contact.evaluate(query, cache, budget, 0)
            if evidence.uncertain_samples:
                fail("Unqualified collision support: " + "; ".join(evidence.reasons))

        def evaluate(at: Any) -> tuple[Any, list[SeatingResidual], list[float], bool]:
            nonlocal evaluations
            evaluations += 1
            if evaluations > spec.max_evaluations:
                raise StopIteration
            rot = rotation(at[3:])

            def move(points: Any) -> Any:
                return (points - pivot) @ rot.T + pivot + at[:3]

            residual: list[float] = []
            summaries = []
            valid = True
            for term, a, b in terms:
                angular = 0.0
                if term.kind == "point":
                    diff = move(a) - b
                    distances = [float(np.linalg.norm(diff))]
                    values = diff.tolist()
                elif term.kind == "frame":
                    diff = move(a[0]) - b[0]
                    error = rot @ a[1] - b[1]
                    angular = float(
                        math.acos(
                            float(
                                np.clip((np.trace(b[1].T @ rot @ a[1]) - 1) / 2, -1, 1)
                            )
                        )
                    )
                    distances = [float(np.linalg.norm(diff))]
                    values = [
                        *diff,
                        *(error.ravel() * term.tolerance / term.angular_tolerance),
                    ]
                else:
                    values = []
                    distances = []
                    angles = []
                    for i, p in enumerate(move(a.points)):
                        budget.test()
                        near, normal, _, distance = b.tree.find_nearest(Vector(p))
                        if near is None:
                            fail("No target surface support")
                        n = np.asarray(normal) * b.orientation
                        delta = p - np.asarray(near) - term.gap * n
                        distances.append(float(np.linalg.norm(delta)))
                        values.extend(delta / math.sqrt(len(a.points)))
                        if term.normals != "none":
                            desired = n * (-1 if term.normals == "opposed" else 1)
                            actual = rot @ a.normals[i] * a.orientation
                            angles.append(
                                math.acos(
                                    float(np.clip(np.dot(actual, desired), -1, 1))
                                )
                            )
                            values.extend(
                                (actual - desired)
                                * term.tolerance
                                / term.angular_tolerance
                                / math.sqrt(len(a.points))
                            )
                    if term.align_centers:
                        center_delta = move(a.center) - b.center
                        # Desired gap follows the mean authored target normal.
                        normal = np.mean(b.normals, axis=0) * b.orientation
                        if np.linalg.norm(normal) > 1e-12:
                            center_delta -= term.gap * normal / np.linalg.norm(normal)
                        distances.append(float(np.linalg.norm(center_delta)))
                        values.extend(center_delta)
                    angular = max(angles, default=0.0)
                distance = max(distances)
                satisfied = distance <= term.tolerance and angular <= getattr(
                    term, "angular_tolerance", math.inf
                )
                valid &= satisfied
                summaries.append(
                    SeatingResidual(
                        name=term.name,
                        maximum_distance=distance,
                        angular_error=angular,
                        satisfied=satisfied,
                    )
                )
                residual.extend(float(v) * math.sqrt(term.weight) for v in values)
            gaps = []
            for guard, a, b in guards:
                bound = guard.minimum_clearance - guard.maximum_penetration
                for p in move(a.points):
                    budget.test()
                    near, normal, _, distance = b.tree.find_nearest(Vector(p))
                    if near is None:
                        fail("No obstacle support")
                    if guard.mode == "closed_solid":
                        inside = (
                            mechanics_contact.winding(
                                Vector(p), b.surface.points, b.triangles, budget
                            )
                            > 0.5
                        )
                        gap = -float(distance) if inside else float(distance)
                    else:
                        direction = b.orientation * (
                            1 if guard.allowed_side == "positive" else -1
                        )
                        gap = math.copysign(
                            float(distance), (Vector(p) - near).dot(normal) * direction
                        )
                    gaps.append(gap)
                    violation = max(0, bound - gap)
                    residual.append(
                        violation
                        * math.sqrt(guard.weight)
                        * (1000 if guard.hard else 1)
                    )
                    if guard.hard and violation > spec.convergence_tolerance:
                        valid = False
            return np.asarray(residual), summaries, gaps, valid

        # Rigid distances give a necessary feasibility bound for explicit point,
        # frame-origin and centroid correspondences, independently of optimization.
        anchors = []
        for term, source, target in terms:
            if term.kind == "point":
                anchors.append((term, source, target))
            elif term.kind == "frame":
                anchors.append((term, source[0], target[0]))
            elif term.align_centers:
                normal = np.mean(target.normals, axis=0) * target.orientation
                goal = target.center.copy()
                if np.linalg.norm(normal) > 1e-12:
                    goal += term.gap * normal / np.linalg.norm(normal)
                anchors.append((term, source.center, goal))
        incompatible = False
        for i, (a, source_a, target_a) in enumerate(anchors):
            for b, source_b, target_b in anchors[i + 1 :]:
                mismatch = abs(
                    float(
                        np.linalg.norm(source_a - source_b)
                        - np.linalg.norm(target_a - target_b)
                    )
                )
                if mismatch > a.tolerance + b.tolerance:
                    incompatible = True
                    issues.append(
                        f"{a.name}/{b.name}: rigid separation exceeds tolerance"
                    )
        last = evaluate(x)
        if incompatible:
            status = "INFEASIBLE"
        damping = 1e-8
        deficient = False
        try:
            for iterations in range(0 if incompatible else spec.iterations + 1):
                r = last[0]
                columns = []
                for index in free:
                    h = max(0.00001, bounds[index] * 0.00001)
                    lo, hi = x.copy(), x.copy()
                    lo[index] = max(-bounds[index], x[index] - h)
                    hi[index] = min(bounds[index], x[index] + h)
                    columns.append(
                        (evaluate(hi)[0] - evaluate(lo)[0]) / (hi[index] - lo[index])
                    )
                jac = np.column_stack(columns) if len(free) else np.zeros((len(r), 0))
                singular = np.linalg.svd(jac, compute_uv=False)
                deficient = bool(
                    len(free)
                    and (
                        len(singular) < len(free)
                        or singular[-1] <= max(singular[0] * 1e-7, 1e-9)
                    )
                )
                if last[3]:
                    status = "POORLY_CONDITIONED" if deficient else "SOLVED"
                    break
                if iterations == spec.iterations or not len(free):
                    status = "INFEASIBLE" if not len(free) else "NO_CONVERGENCE"
                    break
                step = np.linalg.solve(
                    jac.T @ jac + damping * np.eye(len(free)), -jac.T @ r
                )
                scale = max(
                    1.0,
                    float(np.max(np.abs(step) / np.maximum(bounds[free] * 0.25, 1e-6))),
                )
                step /= scale
                improved = False
                for attempt in range(20):
                    candidate = x.copy()
                    candidate[free] = np.clip(
                        x[free] + step * (0.5**attempt), -bounds[free], bounds[free]
                    )
                    trial = evaluate(candidate)
                    if float(trial[0] @ trial[0]) < float(r @ r) - 1e-16:
                        x, last = candidate, trial
                        improved = True
                        damping = max(1e-12, damping * 0.2)
                        break
                if not improved:
                    damping *= 100
                    if damping > 1e8:
                        status = "POORLY_CONDITIONED" if deficient else "INFEASIBLE"
                        break
        except StopIteration:
            issues.append("Evaluation budget exhausted")
            status = "NO_CONVERGENCE"
        rot = rotation(x[3:])
        delta = Matrix(rot.tolist()).to_4x4()
        delta.translation = Vector(pivot + x[:3] - rot @ pivot)
        final_gaps = list(last[2])
        original_evaluated = {name: cache.get(name) for name in evaluated_members}
        # Recheck every guarded source vertex and canonical patch support. No bpy
        # transforms are changed while evaluating or qualifying the candidate.
        if status == "SOLVED":
            for obj in ordered:
                if obj.name in cache.surfaces:
                    surface = cache.surfaces[obj.name]
                    cache.surfaces[obj.name] = replace(
                        surface,
                        points=[delta @ p for p in surface.points],
                        tree=None,
                        normals=None,
                    )
            for guard, a, b in guards:
                query = ContactEnvelope(
                    name=guard.name,
                    source=guard.source,
                    target=guard.target,
                    mode=guard.mode,
                    allowed_side=guard.allowed_side,
                    minimum_gap=-guard.maximum_penetration,
                    maximum_gap=1000,
                    sample_limit=2048,
                )
                evidence = mechanics_contact.evaluate(query, cache, budget, 0)
                if evidence.minimum_gap is not None:
                    final_gaps.append(evidence.minimum_gap)
                violation = (
                    evidence.uncertain_samples
                    or evidence.minimum_gap is None
                    or evidence.minimum_gap
                    < guard.minimum_clearance
                    - guard.maximum_penetration
                    - spec.convergence_tolerance
                )
                if guard.hard and violation:
                    status = "INFEASIBLE"
                    issues.append(f"{guard.name}: complete guard support failed")
                # Positive clearances also require full regional triangle separation,
                # detecting crossings missed by a vertex-only contact sample.
                if guard.hard and guard.maximum_penetration == 0:
                    source = replace(
                        cache.get(guard.source.object_name), triangles=a.triangles
                    )
                    target = replace(b.surface, triangles=b.triangles)
                    qa = GeometryInspectArguments(
                        objects=[GeometryQuery(object_name=target.obj.name)],
                        tolerance=max(1e-8, spec.convergence_tolerance),
                        worst_limit=0,
                    )
                    qa_source, qa_target = QASurface(source), QASurface(target)
                    distance, contacts, _, _ = proximity(
                        qa_source, qa_target, qa, budget
                    )
                    contained = (
                        bool(
                            inside_count(qa_source, qa_target, budget)
                            or inside_count(qa_target, qa_source, budget)
                        )
                        if not contacts
                        else False
                    )
                    if contained and distance is not None:
                        final_gaps.append(-distance.distance)
                    if (
                        contacts
                        or contained
                        or (
                            distance is not None
                            and distance.distance
                            < guard.minimum_clearance - spec.convergence_tolerance
                        )
                    ):
                        status = "INFEASIBLE"
                        issues.append(f"{guard.name}: triangle clearance failed")
        if status != "SOLVED" and not issues:
            issues.append("Interfaces/guards not qualified within the bounded solve")
        applied = False
        backups = {
            o.name: (
                o.location.copy(),
                o.rotation_euler.copy(),
                o.rotation_quaternion.copy(),
                tuple(o.rotation_axis_angle),
                o.scale.copy(),
            )
            for o in roots
        }
        if status == "SOLVED" and spec.apply:
            try:
                for obj in roots:
                    obj.matrix_world = delta @ original[obj.name]
                bpy.context.view_layer.update()
                verify_pose(ordered, original, delta)
                with SurfaceCache() as committed:
                    for name, previous in original_evaluated.items():
                        current = committed.get(name)
                        if current.topology != previous.topology or any(
                            (actual - delta @ prior).length > 0.00002
                            for prior, actual in zip(
                                previous.points, current.points, strict=True
                            )
                        ):
                            fail(
                                "A modifier deformed a member under the rigid transform"
                            )
                applied = True
            except BaseException:
                for obj in roots:
                    location, euler, quaternion, axis_angle, scale = backups[obj.name]
                    obj.location = location
                    obj.rotation_euler = euler
                    obj.rotation_quaternion = quaternion
                    obj.rotation_axis_angle = axis_angle
                    obj.scale = scale
                bpy.context.view_layer.update()
                raise
        active = [
            f"{'translation' if i < 3 else 'rotation'}.{'XYZ'[i % 3]}"
            for i in range(6)
            if abs(abs(x[i]) - bounds[i]) <= spec.convergence_tolerance
        ]
        return SeatingResult(
            status=status,
            applied=applied,
            translation=x[:3].tolist(),
            rotation_vector=x[3:].tolist(),
            pivot=pivot.tolist(),
            world_delta=[list(row) for row in delta],
            iterations=iterations,
            evaluations=min(evaluations, spec.max_evaluations),
            objective=float(last[0] @ last[0]),
            interfaces=last[1],
            worst_clearance=min(final_gaps) if final_gaps else None,
            worst_penetration=max(0, -min(final_gaps)) if final_gaps else None,
            active_bounds=active,
            moved_member_count=len(members) if applied else 0,
            preserved_relative_transforms=True,
            issues=issues[: spec.worst_limit],
            evaluated_vertices=cache.vertices,
            geometry_tests=budget.tests,
            processing_seconds=time.perf_counter() - start,
            limitations=[
                "Local bounded solve; no global feasibility certificate.",
                "Selected regions only. Penetration uses signed vertex contact. "
                "Zero-penetration guards reject triangle contact, including tangency.",
                "Bounds are world-axis components about the first member origin; "
                "zero locks an axis. No scaling or deformation.",
            ],
        )
