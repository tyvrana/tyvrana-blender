"""Owned generic two-segment control networks with measured snapping error."""

import json
import math
import time
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]

from . import organization
from . import rig_constraints as native
from .constraint_models import (
    IK,
    ConstraintRef,
    ConstraintsConfigureArguments,
    ConstraintSpec,
    ConstraintsRemoveArguments,
    RigEndpoint,
)
from .control_rig_models import (
    ControlRigConfigureArguments,
    ControlRigInspectArguments,
    ControlRigResult,
    ControlRigSwitchArguments,
)

KEY = "_tyvrana_control_rigs"


def names(spec: ControlRigConfigureArguments) -> list[ConstraintRef]:
    return [
        ConstraintRef(
            owner=RigEndpoint(object_name=spec.object_name, bone=bone),
            name=f"{spec.name}_{kind}_{i}",
        )
        for i, bone in enumerate(spec.deform)
        for kind in ("FK", "IK")
    ] + [
        ConstraintRef(
            owner=RigEndpoint(object_name=spec.object_name, bone=spec.ik[-1]),
            name=spec.name + "_Solve",
        )
    ]


def data(obj: Any) -> dict[str, Any]:
    raw = obj.get(KEY, "{}")
    if not isinstance(raw, str) or len(raw) > 131072:
        native.fail("Control-rig metadata is invalid")
    result: dict[str, Any] = json.loads(raw)
    if len(result) > 16:
        native.fail("At most 16 control networks per armature")
    return result


def read(
    args: ControlRigInspectArguments,
) -> tuple[Any, dict[str, Any], ControlRigConfigureArguments]:
    obj = organization.object_named(args.object_name)
    catalog = data(obj)
    if args.name not in catalog:
        native.fail("Named control network is missing")
    spec = ControlRigConfigureArguments.model_validate_json(
        json.dumps(catalog[args.name]["definition"])
    )
    spec = spec.model_copy(update={"object_name": obj.name})
    # Constraint pointer ownership is canonical for renamed external targets.
    _, owner = native.endpoint(RigEndpoint(object_name=obj.name, bone=spec.ik[-1]))
    constraint = native.definition(
        owner, RigEndpoint(object_name=obj.name, bone=spec.ik[-1]), spec.name + "_Solve"
    )
    if not isinstance(constraint.settings, IK) or constraint.settings.pole is None:
        native.fail("Control network IK definition is invalid")
    spec = spec.model_copy(
        update={"target": constraint.settings.target, "pole": constraint.settings.pole}
    )
    return obj, catalog, spec


def configure(spec: ControlRigConfigureArguments) -> ControlRigResult:
    organization.idle(mutate=True)
    obj = organization.object_named(spec.object_name)
    organization.object_editable(obj)
    if obj.type != "ARMATURE":
        native.fail("Control networks require an armature")
    catalog = data(obj)
    if spec.name in catalog or len(catalog) >= 16:
        native.fail("Use an unused control name; at most 16 networks per armature")
    for chain in (spec.fk, spec.ik, spec.deform):
        if any(n not in obj.pose.bones for n in chain):
            native.fail("Control-chain bone is missing")
        if (
            obj.data.bones[chain[1]].parent != obj.data.bones[chain[0]]
            or not obj.data.bones[chain[1]].use_connect
        ):
            native.fail("Each two-segment chain requires connected native hierarchy")
        if any(obj.pose.bones[n].constraints for n in chain):
            native.fail(
                "Declare unconstrained chains; network owns their control relationships"
            )
        for i, n in enumerate(chain):
            a, b = obj.data.bones[n], obj.data.bones[spec.fk[i]]
            if (
                native.matrix_error(a.matrix_local, b.matrix_local) > 0.00001
                or abs(a.length - b.length) > 0.00001
            ):
                native.fail("FK, IK and deform rest frames/lengths must match")
    for ref in (spec.target, spec.pole):
        target, owner = native.endpoint(ref, edit=True)
        if owner.constraints or target.animation_data:
            native.fail(
                "Construct networks with independent unanimated target/pole controls"
            )
    constraints = []
    for i, bone in enumerate(spec.deform):
        for kind, chain, influence in [("FK", spec.fk, 1), ("IK", spec.ik, 0)]:
            constraints.append(
                ConstraintSpec.model_validate(
                    dict(
                        name=f"{spec.name}_{kind}_{i}",
                        owner=dict(object_name=obj.name, bone=bone),
                        influence=influence,
                        settings=dict(
                            kind="copy_transforms",
                            target=dict(object_name=obj.name, bone=chain[i]),
                            owner_space="POSE",
                            target_space="POSE",
                        ),
                    )
                )
            )
    constraints.append(
        ConstraintSpec.model_validate(
            dict(
                name=spec.name + "_Solve",
                owner=dict(object_name=obj.name, bone=spec.ik[-1]),
                settings=dict(
                    kind="ik",
                    target=spec.target.model_dump(),
                    pole=spec.pole.model_dump(),
                    pole_angle=spec.pole_angle,
                    chain_length=2,
                ),
            )
        )
    )
    native.configure(ConstraintsConfigureArguments(constraints=constraints))
    try:
        catalog[spec.name] = dict(definition=spec.model_dump(mode="json"), mode="FK")
        obj[KEY] = json.dumps(catalog)
        return inspect(ControlRigInspectArguments(object_name=obj.name, name=spec.name))
    except BaseException:
        native.remove(ConstraintsRemoveArguments(constraints=names(spec)))
        catalog.pop(spec.name, None)
        obj[KEY] = json.dumps(catalog)
        raise


def inspect(args: ControlRigInspectArguments) -> ControlRigResult:
    start = time.perf_counter()
    obj, catalog, spec = read(args)
    mode = catalog[args.name]["mode"]
    issues = []
    for chain in (spec.ik, spec.deform):
        for i, n in enumerate(chain):
            a, b = obj.data.bones[n], obj.data.bones[spec.fk[i]]
            if (
                native.matrix_error(a.matrix_local, b.matrix_local) > 0.00001
                or abs(a.length - b.length) > 0.00001
            ):
                issues.append(
                    "Rest frames differ; revise all three chains consistently"
                )
    for ref in names(spec):
        row = native.summary(ref.owner, ref.name)
        issues.extend(row.issues)
    chain = spec.fk if mode == "FK" else spec.ik
    error = max(
        native.matrix_error(
            native.world(RigEndpoint(object_name=obj.name, bone=source)),
            native.world(RigEndpoint(object_name=obj.name, bone=target)),
        )
        for source, target in zip(chain, spec.deform, strict=True)
    )
    if error > 0.001:
        issues.append("Deform output differs from the selected control chain")
    return ControlRigResult(
        name=args.name,
        object_name=obj.name,
        mode=mode,
        definition=spec,
        valid=not issues,
        issues=issues[:8],
        maximum_output_error=error,
        processing_seconds=time.perf_counter() - start,
    )


def switch(args: ControlRigSwitchArguments) -> ControlRigResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    obj, catalog, spec = read(args)
    organization.object_editable(obj)
    state = inspect(args)
    if not state.valid:
        native.fail("Control network integrity failed; inspect before switching")
    refs = [RigEndpoint(object_name=obj.name, bone=n) for n in spec.fk] + [
        spec.target,
        spec.pole,
    ]
    saved = []
    for ref in refs:
        owner_obj, owner = native.endpoint(ref, edit=True)
        if (
            owner_obj.animation_data
            or owner.constraints
            or any(owner.lock_location)
            or any(owner.lock_rotation)
            or any(owner.lock_scale)
        ):
            native.fail(
                "Match unlocked, unconstrained, unanimated controls; "
                "author keys afterward"
            )
        saved.append((owner, owner.matrix_basis.copy()))
    influences = [
        obj.pose.bones[n].constraints[f"{spec.name}_IK_{i}"].influence
        for i, n in enumerate(spec.deform)
    ]
    match_error = 0.0
    try:
        if args.match and state.mode != args.mode:
            if args.mode == "FK":
                matrices = [
                    native.world(RigEndpoint(object_name=obj.name, bone=n))
                    for n in spec.ik
                ]
                for n, m in zip(spec.fk, matrices, strict=True):
                    native.set_world(RigEndpoint(object_name=obj.name, bone=n), m)
                match_error = max(
                    native.matrix_error(
                        native.world(RigEndpoint(object_name=obj.name, bone=n)), m
                    )
                    for n, m in zip(spec.fk, matrices, strict=True)
                )
            else:
                matrices = [
                    native.world(RigEndpoint(object_name=obj.name, bone=n))
                    for n in spec.fk
                ]
                evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                tip = obj.matrix_world @ evaluated.pose.bones[spec.fk[-1]].tail
                goal = matrices[-1].copy()
                goal.translation = tip
                native.set_world(spec.target, goal)
                base = matrices[0].translation
                axis = tip - base
                if axis.length < 1e-6:
                    native.fail("Collapsed chain cannot define a pole plane")
                direction = axis.normalized()
                cross = matrices[0].to_3x3().col[0]
                cross = (cross - direction * cross.dot(direction)).normalized()
                if cross.length < 0.5:
                    cross = direction.orthogonal().normalized()
                distance = sum(obj.data.bones[n].length for n in spec.ik)
                pole_basis = native.world(spec.pole)

                def place(vector: Any) -> float:
                    m = pole_basis.copy()
                    m.translation = base + axis * 0.5 + vector * distance
                    native.set_world(spec.pole, m)
                    return max(
                        native.matrix_error(
                            native.world(RigEndpoint(object_name=obj.name, bone=n)), m
                        )
                        for n, m in zip(spec.ik, matrices, strict=True)
                    )

                # Rotation-difference pole matching, with bounded local refinement.
                place(cross)
                first = native.world(RigEndpoint(object_name=obj.name, bone=spec.ik[0]))
                angle = (
                    first.to_quaternion()
                    .rotation_difference(matrices[0].to_quaternion())
                    .angle
                )
                candidates = [
                    (place(Matrix.Rotation(a, 3, direction) @ cross), a)
                    for a in (0, angle, -angle, math.pi)
                ]
                _, best = min(candidates)
                span = 0.25
                for _ in range(14):
                    candidates = [
                        (place(Matrix.Rotation(a, 3, direction) @ cross), a)
                        for a in (best - span, best, best + span)
                    ]
                    _, best = min(candidates)
                    span *= 0.5
                match_error = place(Matrix.Rotation(best, 3, direction) @ cross)
            if match_error > args.tolerance:
                native.fail(
                    f"FK/IK match error {match_error:.6g} exceeds tolerance; "
                    "revise chain/controls"
                )
        for i, n in enumerate(spec.deform):
            obj.pose.bones[n].constraints[f"{spec.name}_IK_{i}"].influence = (
                1 if args.mode == "IK" else 0
            )
        bpy.context.view_layer.update()
        catalog[args.name]["mode"] = args.mode
        obj[KEY] = json.dumps(catalog)
        result = inspect(args)
        if not result.valid:
            native.fail("Switched network failed output validation")
        return result.model_copy(
            update={
                "maximum_match_error": match_error,
                "processing_seconds": time.perf_counter() - start,
            }
        )
    except BaseException:
        for owner, matrix in saved:
            owner.matrix_basis = matrix
        for i, n in enumerate(spec.deform):
            obj.pose.bones[n].constraints[f"{spec.name}_IK_{i}"].influence = influences[
                i
            ]
        catalog[args.name]["mode"] = state.mode
        obj[KEY] = json.dumps(catalog)
        bpy.context.view_layer.update()
        raise


def remove(args: ControlRigInspectArguments) -> ControlRigResult:
    result = inspect(args)
    obj, catalog, spec = read(args)
    native.remove(ConstraintsRemoveArguments(constraints=names(spec)))
    del catalog[args.name]
    obj[KEY] = json.dumps(catalog)
    return result.model_copy(
        update={
            "valid": False,
            "issues": ["Control network removed; native bones and controls retained"],
        }
    )
