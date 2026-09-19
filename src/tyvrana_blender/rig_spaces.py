"""Keyed fixed-target Child Of branches with measured world-pose compensation."""

import hashlib
import json
import time

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]

from . import organization, rig_keying
from . import rig_constraints as native
from .constraint_models import ChildOf, ConstraintsResult, SpaceSwitchArguments
from .motion_models import ConstraintChannel

KEY = "_tyvrana_keyed_space"


def switch(args: SpaceSwitchArguments) -> ConstraintsResult:
    start = time.perf_counter()
    organization.idle(mutate=True)
    rig_keying.guard(args.owner, args.keying, constrained=True)
    _, owner = native.endpoint(args.owner, edit=True)
    catalog = json.loads(owner.get(KEY, "null"))
    if catalog is None:
        branches = [args.constraint]
    elif (
        isinstance(catalog, dict)
        and catalog.get("constraint") == args.constraint
        and isinstance(catalog.get("branches"), list)
    ):
        branches = list(catalog["branches"])
    else:
        native.fail(
            "Keyed-space declaration is invalid or belongs to another constraint"
        )
    if not 1 <= len(branches) <= 16 or set(branches) != {
        c.name for c in owner.constraints
    }:
        native.fail(
            "Keyed spaces require an intact, exclusive stack of at most 16 branches"
        )
    definitions = [native.definition(owner, args.owner, n) for n in branches]
    if not all(isinstance(s.settings, ChildOf) for s in definitions):
        native.fail("Keyed spaces require owned Child Of constraints")
    old_influences = {n: owner.constraints[n].influence for n in branches}
    if sum(abs(v - 1) < 1e-6 for v in old_influences.values()) != 1 or any(
        min(abs(v), abs(v - 1)) > 1e-6 for v in old_influences.values()
    ):
        native.fail("Keyed spaces require exactly one full-influence branch")
    before = native.world(args.owner)
    basis = rig_keying.snapshot(owner)
    metadata = {
        k: owner[k] for k in owner.keys() if k.startswith(native.KEY) or k == KEY
    }
    destination = next(
        (
            s.name
            for s in definitions
            if isinstance(s.settings, ChildOf) and s.settings.target == args.target
        ),
        None,
    )
    created = None
    try:
        if destination is None:
            if len(branches) == 16:
                native.fail("Keyed space exceeds 16 fixed target branches")
            suffix = hashlib.sha256(args.constraint.encode()).hexdigest()[:8]
            destination = f"Space_{suffix}_{len(branches)}"
            if destination in owner.constraints:
                native.fail("Generated space branch name is occupied")
            new = definitions[0].model_copy(
                update=dict(
                    name=destination,
                    influence=0,
                    settings=ChildOf(
                        kind="child_of", target=args.target, maintain_transform=False
                    ),
                )
            )
            native.graph_guard([new])
            created = destination
            c = native.apply(owner, new)
            c.inverse_matrix = Matrix.Identity(4)
            c.set_inverse_pending = False
            native.persist(owner, new, c)
            branches.append(destination)
        keys = rig_keying.Keys(
            args.keying,
            rig_keying.transforms(args.owner)
            + [
                ConstraintChannel(
                    object_name=args.owner.object_name,
                    bone=args.owner.bone,
                    constraint=n,
                )
                for n in branches
            ],
        )
        for name in branches:
            owner.constraints[name].influence = float(name == destination)
        bpy.context.view_layer.update()
        if args.maintain_transform:
            # With one full Child Of, evaluated world = effective_parent @ basis.
            effective = native.world(args.owner) @ owner.matrix_basis.inverted()
            owner.matrix_basis = effective.inverted() @ before
            bpy.context.view_layer.update()
        owner[KEY] = json.dumps(dict(constraint=args.constraint, branches=branches))
        with keys.commit():
            if (
                args.maintain_transform
                and native.matrix_error(native.world(args.owner), before)
                > args.tolerance
            ):
                native.fail(
                    "Keyed space pose is not representable; "
                    "action and constraints restored"
                )
            summaries = [native.summary(args.owner, name) for name in branches]
            if any(not s.valid for s in summaries):
                native.fail("Keyed space failed native validation; restored")
            return ConstraintsResult(
                constraints=summaries, processing_seconds=time.perf_counter() - start
            )
    except BaseException:
        if created and created in owner.constraints:
            owner.constraints.remove(owner.constraints[created])
        for name, influence in old_influences.items():
            owner.constraints[name].influence = influence
        for key in list(owner.keys()):
            if key.startswith(native.KEY) or key == KEY:
                del owner[key]
        for key, value in metadata.items():
            owner[key] = value
        rig_keying.restore(owner, basis)
        bpy.context.view_layer.update()
        raise
