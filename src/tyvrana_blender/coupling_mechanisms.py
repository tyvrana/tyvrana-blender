"""Closed relationships owned by the canonical coupling catalog and operations."""

import hashlib
import json
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import motion_channels as channels
from . import references
from .mechanics_geometry import digest
from .motion_models import MechanismSolution, MechanismSpec, MechanismSummary

KEY = "_tyvrana_closed_couplings"


def catalog() -> dict[str, Any]:
    try:
        raw = bpy.context.scene.get(KEY, "{}")
        if not isinstance(raw, str) or len(raw) > 262144:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or len(data) > 16:
            raise ValueError
        for name, row in data.items():
            if MechanismSpec.model_validate(row["definition"]).name != name:
                raise ValueError
        return dict(data)
    except (ValueError, TypeError, KeyError) as exc:
        channels.fail("Closed coupling metadata is invalid: " + str(exc)[:120])


def referenced_names(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str) and (
                k in {"object_name", "object", "reference"}
                or k == "name"
                and value.get("kind") == "landmark"
            ):
                result.add(v)
            else:
                result.update(referenced_names(v))
    elif isinstance(value, list):
        for v in value:
            result.update(referenced_names(v))
    return result


def pointer(name: str, owner: str) -> str:
    return KEY + "_" + hashlib.sha256((name + ":" + owner).encode()).hexdigest()[:24]


def definition(name: str) -> MechanismSpec:
    data = catalog()
    if name not in data:
        channels.fail(f'Closed coupling "{name}" is missing')
    row = data[name]
    mapping = {}
    for original in referenced_names(row["definition"]):
        obj = bpy.context.scene.get(pointer(name, original))
        if (
            not isinstance(obj, bpy.types.Object)
            or bpy.context.scene.objects.get(obj.name) != obj
        ):
            channels.fail(
                "Closed coupling owner was removed; remove/reconfigure explicitly"
            )
        mapping[original] = obj.name

    def rename(value: Any) -> Any:
        if isinstance(value, list):
            return [rename(v) for v in value]
        if isinstance(value, dict):
            return {
                k: mapping.get(v, v)
                if isinstance(v, str)
                and (
                    k in {"object_name", "object", "reference"}
                    or k == "name"
                    and value.get("kind") == "landmark"
                )
                else rename(v)
                for k, v in value.items()
            }
        return value

    return MechanismSpec.model_validate(rename(row["definition"]))


def preflight(spec: MechanismSpec) -> None:
    for var in spec.variables:
        target = channels.resolve(var.channel, write=var.role == "solve")
        if var.minimum < target.minimum or var.maximum > target.maximum:
            channels.fail("Mechanism range exceeds the native writable channel range")
        if var.role == "solve" and (target.driver() or target.keyed()):
            channels.fail(
                "Solved channels cannot also be keyed or driven; use input variables"
            )
    for c in spec.closures:
        references.resolve(c.a)
        references.resolve(c.b)


def proposed(
    specs: list[MechanismSpec],
    replace: bool,
    scalar_names: set[str],
    scalar_targets: set[str],
) -> dict[str, Any]:
    data = catalog()
    for spec in specs:
        if spec.name in data and not replace:
            channels.fail("Closed coupling exists; use replace=true")
        preflight(spec)
        data[spec.name] = {"definition": spec.model_dump(mode="json"), "last": None}
    if len(data) > 16 or scalar_names & data.keys():
        channels.fail("Coupling names conflict or closed catalog exceeds 16 mechanisms")
    owned = set(scalar_targets)
    for name in data:
        spec = next((s for s in specs if s.name == name), None) or definition(name)
        for var in spec.variables:
            if var.role == "solve":
                key = var.channel.model_dump_json()
                if key in owned:
                    channels.fail(
                        "A solved native channel can have only one coupling owner"
                    )
                owned.add(key)
    if len(json.dumps(data)) > 262144:
        channels.fail("Closed coupling catalog exceeds 256 KiB")
    return data


def persist(data: dict[str, Any]) -> None:
    expected = {
        pointer(n, owner)
        for n, row in data.items()
        for owner in referenced_names(row["definition"])
    }
    for key in list(bpy.context.scene.keys()):
        if key.startswith(KEY + "_") and key not in expected:
            del bpy.context.scene[key]
    for name, row in data.items():
        for owner in referenced_names(row["definition"]):
            key = pointer(name, owner)
            if key not in bpy.context.scene:
                bpy.context.scene[key] = channels.object_named(owner)
    bpy.context.scene[KEY] = json.dumps(data, separators=(",", ":"))


def state(spec: MechanismSpec) -> tuple[dict[str, float], list[float], float, str]:
    values = {v.name: channels.resolve(v.channel).value() for v in spec.variables}
    points = [
        [list(references.resolve(c.a)), list(references.resolve(c.b))]
        for c in spec.closures
    ]
    residuals = [
        sum((a - b) ** 2 for a, b in zip(p, q, strict=True)) ** 0.5 for p, q in points
    ]
    limit = max(
        [0.0]
        + [
            max(v.minimum - values[v.name], values[v.name] - v.maximum, 0.0)
            for v in spec.variables
        ]
    )
    evaluated = {
        v.name: channels.resolve(v.channel).value(evaluated=True)
        for v in spec.variables
    }
    limit = max(limit, max(abs(values[n] - evaluated[n]) for n in values))
    return (
        values,
        residuals,
        limit,
        digest([list(values.values()), list(evaluated.values()), points]),
    )


def summary(name: str) -> MechanismSummary:
    spec = definition(name)
    values, residuals, limit, fingerprint = state(spec)
    last = catalog()[name].get("last")
    current = bool(last and last["fingerprint"] == fingerprint)
    return MechanismSummary(
        definition=spec,
        active_limits=[
            v.name
            for v in spec.variables
            if min(abs(values[v.name] - v.minimum), abs(values[v.name] - v.maximum))
            <= spec.tolerance
        ],
        values=values,
        closure_residual=max(residuals),
        limit_residual=limit,
        solution_current=current,
        last_solution=MechanismSolution.model_validate(last["solution"])
        if current
        else None,
    )


def save_solution(spec: MechanismSpec, result: MechanismSolution) -> None:
    data = catalog()
    data[spec.name]["last"] = {
        "fingerprint": state(spec)[3],
        "solution": result.model_dump(mode="json"),
    }
    persist(data)
