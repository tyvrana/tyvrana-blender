"""Bounded projected damped least squares on native typed scalar channels."""

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np  # type: ignore[import-not-found]

from . import (
    coupling_mechanisms as mechanisms,
)
from . import (
    couplings,
    references,
)
from . import (
    motion_channels as channels,
)
from .motion_models import (
    ClosureResidual,
    MechanismSolution,
    MechanismSpec,
    SolveStatus,
)


@dataclass
class EvaluationBudget:
    maximum: int
    used: int = 0

    def take(self) -> None:
        if self.used >= self.maximum:
            channels.fail("Closed coupling evaluation budget exhausted; state restored")
        self.used += 1


@dataclass
class Continuation:
    values: list[float]
    jacobian: Any


def solve(
    spec: MechanismSpec, budget: EvaluationBudget, previous: Continuation | None = None
) -> tuple[MechanismSolution, Continuation | None]:
    mechanisms.preflight(spec)
    free = [v for v in spec.variables if v.role == "solve"]
    targets = [channels.resolve(v.channel, write=True) for v in free]
    initial = [t.value() for t in targets]
    lo = np.asarray([v.minimum for v in free])
    hi = np.asarray([v.maximum for v in free])
    scale = hi - lo
    original = np.asarray(initial)
    x = np.clip(np.asarray(previous.values if previous else initial), lo, hi)
    start = budget.used
    status: SolveStatus = "NO_CONVERGENCE"
    reasons: list[str] = []
    rank = 0
    condition = None
    iteration = 0
    jac: Any = None
    committed = False

    def evaluate(at: Any) -> Any:
        budget.take()
        for target, value in zip(targets, at, strict=True):
            couplings.set_value(target, float(value))
        channels.refresh()
        return np.asarray(
            [
                float(a - b)
                for c in spec.closures
                for a, b in zip(
                    references.resolve(c.a), references.resolve(c.b), strict=True
                )
            ]
        )

    def derivative(at: Any) -> Any:
        columns = []
        for i in range(len(free)):
            # Symmetric interior or one-sided bounded finite differences. Native
            # transforms are binary32; microscopic perturbations lose information.
            a = at.copy()
            b = at.copy()
            h = scale[i] * 0.0001
            a[i] = max(lo[i], at[i] - h)
            b[i] = min(hi[i], at[i] + h)
            columns.append((evaluate(b) - evaluate(a)) / ((b[i] - a[i]) / scale[i]))
        evaluate(at)
        return np.column_stack(columns)

    try:
        r = evaluate(x)
        input_violation = max(
            [0.0]
            + [
                max(
                    v.minimum - channels.resolve(v.channel).value(),
                    channels.resolve(v.channel).value() - v.maximum,
                    0.0,
                )
                for v in spec.variables
                if v.role == "input"
            ]
        )
        if input_violation > spec.tolerance:
            status = "INFEASIBLE"
            reasons.append("Requested input lies outside its declared joint range")
        elif len(r) < len(free):
            status = "UNDERCONSTRAINED"
            reasons.append("Fewer closure equations than solved variables")
        else:
            damping = 1e-8
            for iteration in range(spec.iterations + 1):
                jac = derivative(x)
                u, singular, vt = np.linalg.svd(jac, full_matrices=False)
                rank = int(np.sum(singular > max(float(singular[0]) * 1e-6, 1e-9)))
                condition = (
                    float(singular[0] / singular[-1]) if singular[-1] > 1e-12 else None
                )
                if (
                    rank < len(free)
                    or condition is None
                    or condition > spec.maximum_condition
                ):
                    status = "SINGULAR"
                    reasons.append(
                        "Closure Jacobian is rank deficient or poorly conditioned"
                    )
                    break
                if max(np.linalg.norm(r.reshape(-1, 3), axis=1)) <= spec.tolerance:
                    status = "SOLVED"
                    break
                if iteration == spec.iterations:
                    break
                step = -(vt.T @ ((singular / (singular**2 + damping)) * (u.T @ r)))
                largest = float(np.max(np.abs(step)))
                if largest > 0.2:
                    step *= 0.2 / largest
                improved = False
                for attempt in range(8):
                    candidate = np.clip(x + scale * step * (0.5**attempt), lo, hi)
                    trial = evaluate(candidate)
                    if float(trial @ trial) < float(r @ r) - 1e-16:
                        x, r = candidate, trial
                        improved = True
                        damping = max(1e-12, damping * 0.2)
                        break
                if not improved:
                    damping *= 100
                    if damping > 1:
                        break
            r = evaluate(x)
        values, residuals, limit, _ = mechanisms.state(spec)
        active = [
            v.name
            for v in free
            if min(abs(values[v.name] - v.minimum), abs(values[v.name] - v.maximum))
            <= spec.tolerance
        ]
        if status == "NO_CONVERGENCE":
            status = "LIMIT_BLOCKED" if active else "NO_CONVERGENCE"
            reasons.append(
                "Closure remains outside tolerance within bounded iterations/limits; "
                "no global infeasibility proof"
            )
        continuity: Literal["seed", "continuous", "rejected"] = (
            "seed" if previous is None else "continuous"
        )
        if status == "SOLVED" and previous is not None:
            change = np.abs((x - np.asarray(previous.values)) / scale)
            orientation = float(np.linalg.det(previous.jacobian.T @ jac))
            if (
                any(d > v.continuity_limit for d, v in zip(change, free, strict=True))
                or orientation <= 0
            ):
                status = "BRANCH_DISCONTINUITY"
                continuity = "rejected"
                reasons.append(
                    "Previous branch tangent orientation or bounded channel "
                    "continuity was lost"
                )
        if status == "SOLVED" and limit > spec.tolerance:
            status = "LIMIT_BLOCKED"
            reasons.append(
                "Solved channels violate ranges or are altered by native constraints"
            )
        # Check the evaluated geometry after constraints, not only requested RNA.
        if status == "SOLVED" and max(residuals) > spec.tolerance:
            status = "NO_CONVERGENCE"
            reasons.append("Native closure deteriorated after application")
        result = MechanismSolution(
            name=spec.name,
            status=status,
            values=values,
            closure_residual=max(residuals),
            constraint_residuals=[
                ClosureResidual(name=c.name, residual=e)
                for c, e in zip(spec.closures, residuals, strict=True)
            ],
            limit_residual=max(limit, input_violation),
            condition=condition,
            rank=rank,
            iterations=iteration,
            evaluations=budget.used - start,
            active_limits=active,
            continuity=continuity,
            reasons=reasons,
        )
        committed = status == "SOLVED"
        return result, Continuation(
            [float(v) for v in x], jac.copy()
        ) if committed else previous
    finally:
        if not committed:
            for t, value in zip(targets, original, strict=True):
                couplings.set_value(t, float(value))
            channels.refresh()
