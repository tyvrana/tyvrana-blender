"""Transactional native curves with persistent evaluated attachments."""

import hashlib
import json
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]

from . import curve_nodes, organization
from .curve_models import (
    BindingSummary,
    BoneAnchor,
    CurveBinding,
    CurveConfigureArguments,
    CurveCreateArguments,
    CurveInspectArguments,
    CurveInspectResult,
    CurvePoint,
    CurveRemoveArguments,
    CurveRemoveResult,
    CurveResult,
    CurveSample,
    CurveSettings,
    CurveSpec,
    CurveSummary,
    SplineSpec,
    SplineSummary,
    SurfaceAnchor,
)
from .errors import OperationError
from .inspection import page
from .joints import positive_uniform

KEY = curve_nodes.KEY


def fail(message: str) -> None:
    raise OperationError("curve_invalid", message)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def topology(mesh: Any) -> str:
    if len(mesh.vertices) > 250000 or len(mesh.polygons) > 500000:
        fail("Attachment surface exceeds 250000 vertices or 500000 polygons")
    return digest(
        [
            len(mesh.vertices),
            [list(e.vertices) for e in mesh.edges],
            [list(p.vertices) for p in mesh.polygons],
        ]
    )


def dependencies(target: Any, owner: Any, proposed: Any = ()) -> None:
    if target == owner:
        fail("A curve cannot depend on itself")
    users = bpy.data.user_map()
    for dependent, references in proposed:
        for reference in references:
            users.setdefault(reference, set()).add(dependent)
    seen = set()
    pending = [owner]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if len(seen) > 4096:
            fail("Curve dependency closure exceeds 4096 datablocks")
        if current == target:
            fail("Attachment/profile would create a dependency cycle")
        pending.extend(users.get(current, set()) - seen)


def object_curve(name: str, *, edit: bool = False) -> Any:
    obj = organization.object_named(name)
    if obj.type != "CURVE" or KEY not in obj:
        fail(f'"{name}" is not a managed native Curve')
    if (
        not 1 <= len(obj.data.splines) <= 8
        or sum(
            len(s.bezier_points) if s.type == "BEZIER" else len(s.points)
            for s in obj.data.splines
        )
        > 1024
    ):
        fail("Curve exceeds eight splines or 1024 control points")
    positive_uniform(obj.matrix_world)
    if edit:
        organization.object_editable(obj)
        organization.editable(obj.data)
        if (
            obj.data.users != 1
            or obj.animation_data
            or obj.data.animation_data
            or obj.constraints
            or obj.data.shape_keys
        ):
            fail(
                "Curve edits require exclusive local data without animation, "
                "constraints or shape keys"
            )
    return obj


def metadata(obj: Any) -> tuple[dict[str, Any], Any]:
    try:
        meta = json.loads(obj[KEY])
    except (ValueError, TypeError, KeyError):
        fail("Curve ownership metadata is invalid")
    if meta.get("spline_layout") != [
        [s.type, len(points(s))] for s in obj.data.splines
    ]:
        fail(
            "Native curve topology changed outside its typed edit; "
            "preserve it for repair"
        )
    modifier = obj.modifiers.get(curve_nodes.MODIFIER)
    if (
        modifier is None
        or modifier.type != "NODES"
        or len(obj.modifiers) != 1
        or not modifier.show_viewport
        or not modifier.show_render
    ):
        fail(
            "Preserve external modifiers; owned curve evaluation modifier is "
            "missing or changed"
        )
    group = modifier.node_group
    if (
        not group
        or not group.get(KEY)
        or group.get(KEY + "_signature") != curve_nodes.signature(group)
    ):
        fail(
            "Owned curve graph was edited; preserve it instead of silently "
            "replacing external behavior"
        )
    for i, binding in enumerate(meta["bindings"]):
        target = obj.get(KEY + f"_target_{i}")
        if (
            target is None
            or group.nodes[f"Target {i}"].inputs["Object"].default_value != target
        ):
            fail("Attachment target is missing or its native pointer changed")
        if (
            binding["spec"]["target"]["kind"] == "surface"
            and group.nodes[f"Surface {i}"].inputs["Object"].default_value != target
        ):
            fail("Surface attachment pointer was changed")
    if meta["settings"]["profile"]["kind"] == "object":
        profile = obj.get(KEY + "_profile")
        if (
            profile is None
            or group.nodes["Profile"].inputs["Object"].default_value != profile
        ):
            fail("Sweep profile is missing or its native pointer changed")
        meta["settings"]["profile"]["object"] = profile.name
    material = obj.data.materials[0] if obj.data.materials else None
    material_node = group.nodes.get("Material")
    if (
        material_node is not None
        and material_node.inputs["Material"].default_value != material
    ):
        fail("Sweep material assignment was changed outside curve configuration")
    meta["settings"]["material"] = material.name if material else None
    return meta, group


def points(spline: Any) -> list[Any]:
    return list(spline.bezier_points if spline.type == "BEZIER" else spline.points)


def point_value(point: Any, bezier: bool) -> CurvePoint:
    values = dict(co=list(point.co[:3]), radius=point.radius, tilt=point.tilt)
    if bezier:
        mode = point.handle_left_type
        if mode != point.handle_right_type:
            fail(
                "Asymmetric native handle modes require a supported full spline "
                "replacement"
            )
        values["handle_type"] = mode
        if mode in {"FREE", "ALIGNED"}:
            values.update(left=list(point.handle_left), right=list(point.handle_right))
    else:
        values["weight"] = point.co[3]
    return CurvePoint.model_validate(values)


def definitions(obj: Any) -> list[SplineSpec]:
    return [
        SplineSpec(
            type=s.type,
            points=[point_value(p, s.type == "BEZIER") for p in points(s)],
            cyclic=s.use_cyclic_u,
            order=s.order_u,
            endpoint=s.use_endpoint_u,
        )
        for s in obj.data.splines
    ]


def make_data(name: str, splines: list[SplineSpec], settings: CurveSettings) -> Any:
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = settings.resolution
    data.twist_mode = "MINIMUM"
    try:
        for spec in splines:
            s = data.splines.new(spec.type)
            s.resolution_u = settings.resolution
            s.use_cyclic_u = spec.cyclic
            collection = s.bezier_points if spec.type == "BEZIER" else s.points
            collection.add(len(spec.points) - 1)
            for p, value in zip(collection, spec.points, strict=True):
                p.co = value.co if spec.type == "BEZIER" else [*value.co, value.weight]
                p.radius = value.radius
                p.tilt = value.tilt
                if spec.type == "BEZIER":
                    p.handle_left_type = value.handle_type
                    p.handle_right_type = value.handle_type
                    if value.left is not None:
                        p.handle_left = value.left
                    if value.right is not None:
                        p.handle_right = value.right
            if spec.type == "NURBS":
                s.order_u = spec.order
                s.use_endpoint_u = spec.endpoint and not spec.cyclic
        if settings.material is not None:
            data.materials.append(bpy.data.materials[settings.material])
        return data
    except BaseException:
        bpy.data.curves.remove(data)
        raise


def resolve_settings(defaults: CurveSettings, patch: Any) -> CurveSettings:
    return CurveSettings.model_validate(
        {**defaults.model_dump(), **patch.model_dump(exclude_unset=True)}
    )


def validate_splines(
    splines: list[SplineSpec], settings: CurveSettings
) -> list[SplineSpec]:
    resolved = [
        SplineSpec.model_validate(
            {**s.model_dump(), "type": s.type or settings.spline_type}
        )
        for s in splines
    ]
    if sum(len(s.points) for s in resolved) > 1024:
        fail("Curve exceeds 1024 authored points")
    return resolved


def profile_object(settings: CurveSettings, owner: Any = None) -> tuple[Any, int]:
    profile = settings.profile
    if profile.kind == "none":
        return None, 1
    if profile.kind == "circle":
        return None, profile.resolution
    obj = organization.object_named(profile.object)
    if owner is not None:
        dependencies(obj, owner)
    if (
        obj.type != "CURVE"
        or len(obj.data.splines) != 1
        or obj.animation_data
        or obj.constraints
    ):
        fail(
            "A reusable profile must be a single local XY Curve without "
            "animation or constraints"
        )
    if obj.library or obj.data.library:
        fail("Linked profiles are outside the editable curve contract")
    if (
        max(
            abs(v - (1 if i == j else 0))
            for i, row in enumerate(obj.matrix_world)
            for j, v in enumerate(row)
        )
        > 1e-6
    ):
        fail(
            "Profile objects require an identity world transform; author "
            "their cross-section in local XY"
        )
    if obj.modifiers:
        meta, _ = metadata(obj)
        if meta["bindings"] or meta["settings"]["profile"]["kind"] != "none":
            fail("Profile curves cannot be attached or swept themselves")
    for s in obj.data.splines:
        if profile.caps and not s.use_cyclic_u:
            fail("Caps require a cyclic profile")
        if any(
            abs(p.co.z) > 1e-6
            or (
                s.type == "BEZIER"
                and (abs(p.handle_left.z) > 1e-6 or abs(p.handle_right.z) > 1e-6)
            )
            for p in points(s)
        ):
            fail("Profile control points must lie in local XY")
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        count = len(mesh.vertices)
        if not 3 <= count <= 128:
            fail("Profile evaluation must contain 3..128 cross-section vertices")
    finally:
        evaluated.to_mesh_clear()
    return obj, count


def estimate(
    splines: list[SplineSpec], settings: CurveSettings, profile_points: int
) -> int:
    size = (
        sum(
            (len(s.points) if s.type == "POLY" else len(s.points) * settings.resolution)
            for s in splines
        )
        * profile_points
    )
    if size > 200000:
        fail(
            "Curve sweep estimate exceeds 200000 vertices; reduce points, "
            "resolution or profile detail"
        )
    return size


def frame(
    target: Any, spec: CurveBinding, *, bind: bool = False, signature: str | None = None
) -> tuple[Any, dict[str, Any]]:
    if target is None or target.name not in bpy.context.scene.objects:
        fail("Attachment target is missing from the current scene")
    if isinstance(spec.target, BoneAnchor):
        # target here is the native armature, not the owned evaluation helper.
        if target.type != "ARMATURE" or spec.target.bone not in target.pose.bones:
            fail("Attachment bone does not exist")
        ev = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return ev.matrix_world @ ev.pose.bones[spec.target.bone].matrix, {}
    if not isinstance(spec.target, SurfaceAnchor):
        return target.evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        ).matrix_world.copy(), {}
    if target.type != "MESH":
        fail("Surface attachment target must be a Mesh")
    authored = topology(target.data)
    if signature is not None and authored != signature:
        fail("Surface topology changed; replace bindings to rebind deliberately")
    if (
        spec.target.face >= len(target.data.polygons)
        or len(target.data.polygons[spec.target.face].vertices) != 3
    ):
        fail("Surface attachment requires an existing authored triangular polygon")
    indices = list(target.data.polygons[spec.target.face].vertices)
    ev = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        if topology(mesh) != authored:
            fail(
                "Evaluated surface topology differs from authored topology; use "
                "topology-preserving deformation"
            )
        vertices = [mesh.vertices[i].co.copy() for i in indices]
        x = vertices[1] - vertices[0]
        z = x.cross(vertices[2] - vertices[0])
        if x.length < 1e-8 or z.length < 1e-8:
            fail("Surface binding triangle is degenerate")
        x.normalize()
        z.normalize()
        y = z.cross(x)
        center = sum(
            (p * w for p, w in zip(vertices, spec.target.barycentric, strict=True)),
            Vector(),
        )
        matrix = Matrix.Identity(4)
        for i, axis in enumerate([x, y, z]):
            matrix.col[i].xyz = axis
        matrix.translation = center
        return ev.matrix_world @ matrix, dict(topology=authored, vertices=indices)
    finally:
        ev.to_mesh_clear()


def bind_plan(
    obj: Any, splines: list[SplineSpec], bindings: list[CurveBinding]
) -> tuple[list[dict[str, Any]], list[Any]]:
    result = []
    targets = []
    for spec in bindings:
        target = organization.object_named(spec.target.object)
        dependencies(target, obj)
        matrix, extra = frame(target, spec, bind=True)
        if abs(matrix.determinant()) < 1e-10:
            fail("Attachment target frame is singular")
        inverse = matrix.inverted() @ obj.matrix_world
        pivot = Vector(splines[spec.spline].points[spec.point].co)
        shift = Vector(spec.offset) - (inverse @ pivot)
        start = sum(len(s.points) for s in splines[: spec.spline])
        result.append(
            dict(
                spec=spec.model_dump(mode="json"),
                inverse=[list(row) for row in inverse],
                shift=list(shift),
                start=start + (spec.point if spec.follow == "point" else 0),
                end=start + len(splines[spec.spline].points),
                **extra,
            )
        )
        targets.append(target)
    return result, targets


def helper(target: Any, bone: str) -> Any:
    obj = bpy.data.objects.new("Curve bone anchor", None)
    bpy.context.scene.collection.objects.link(obj)
    obj.hide_render = True
    obj.hide_select = True
    obj.empty_display_size = 0.02
    obj[KEY + "_helper"] = True
    c = obj.constraints.new("COPY_TRANSFORMS")
    c.name = "Curve bone frame"
    c.target = target
    c.subtarget = bone
    c.target_space = "WORLD"
    c.owner_space = "WORLD"
    return obj


def native_targets(
    meta: dict[str, Any], targets: list[Any]
) -> tuple[list[Any], list[Any]]:
    actual = []
    helpers = []
    try:
        for binding, target in zip(meta["bindings"], targets, strict=True):
            if binding["spec"]["target"]["kind"] == "bone":
                h = helper(target, binding["spec"]["target"]["bone"])
                helpers.append(h)
                actual.append(h)
            else:
                actual.append(target)
        bpy.context.view_layer.update()
        return actual, helpers
    except BaseException:
        for h in helpers:
            bpy.data.objects.remove(h, do_unlink=True)
        raise


def current_targets(obj: Any, meta: dict[str, Any]) -> list[Any]:
    targets = []
    for i, b in enumerate(meta["bindings"]):
        target = obj.get(KEY + f"_target_{i}")
        if b["spec"]["target"]["kind"] == "bone":
            if (
                target is None
                or not target.get(KEY + "_helper")
                or len(target.constraints) != 1
            ):
                fail("Owned bone anchor was changed or removed")
            c = target.constraints[0]
            if (
                c.type != "COPY_TRANSFORMS"
                or c.target is None
                or c.influence != 1
                or c.mute
                or c.owner_space != "WORLD"
                or c.target_space != "WORLD"
            ):
                fail("Owned bone anchor settings were changed")
            b["spec"]["target"].update(object=c.target.name, bone=c.subtarget)
            targets.append(c.target)
        else:
            if target is None:
                fail("Attachment target is missing")
            b["spec"]["target"]["object"] = target.name
            targets.append(target)
    return targets


def install(
    obj: Any, meta: dict[str, Any], targets: list[Any], profile: Any, group: Any
) -> None:
    meta["spline_layout"] = [[s.type, len(points(s))] for s in obj.data.splines]
    obj[KEY] = json.dumps(meta, separators=(",", ":"))
    for i, target in enumerate(targets):
        obj[KEY + f"_target_{i}"] = target
    if profile is not None:
        obj[KEY + "_profile"] = profile
    modifier = obj.modifiers.new(curve_nodes.MODIFIER, "NODES")
    modifier.node_group = group


def create(args: CurveCreateArguments) -> CurveResult:
    organization.idle(mutate=True)
    plans = []
    budget = 0
    for item in args.curves:
        organization.named_available(item.name, bpy.data.objects)
        settings = resolve_settings(args.defaults, item.settings)
        if (
            settings.material is not None
            and settings.material not in bpy.data.materials
        ):
            fail("Requested material does not exist")
        splines = validate_splines(item.splines, settings)
        world = Matrix.LocRotScale(
            Vector(item.location),
            Euler(item.rotation, "XYZ").to_quaternion(),
            Vector((item.scale,) * 3),
        )
        if item.space == "world":
            values = []
            for s in splines:
                d = s.model_dump()
                d["points"] = [
                    {
                        **p.model_dump(),
                        **{
                            k: list(world.inverted() @ Vector(getattr(p, k)))
                            for k in ["co", "left", "right"]
                            if getattr(p, k) is not None
                        },
                    }
                    for p in s.points
                ]
                values.append(SplineSpec.model_validate(d))
            splines = values
        profile, size = profile_object(settings)
        budget += estimate(splines, settings, size)
        collections = [
            organization.collection_named(n)
            for n in (item.collections or args.collections)
        ]
        plans.append((item, settings, splines, world, profile, collections))
    if budget > 1000000:
        fail("Curve batch sweep estimate exceeds one million vertices")
    objects = []
    groups = []
    helpers = []
    data = []
    try:
        for item, settings, splines, world, profile, collections in plans:
            d = make_data(item.name, splines, settings)
            data.append(d)
            obj = bpy.data.objects.new(item.name, d)
            objects.append(obj)
            for c in collections:
                c.objects.link(obj)
            obj.matrix_world = world
            organization.metadata(obj, item.role or args.role, item.tags or args.tags)
            bpy.context.view_layer.update()
            bindings, targets = bind_plan(obj, splines, item.bindings)
            meta = dict(settings=settings.model_dump(mode="json"), bindings=bindings)
            actual, new_helpers = native_targets(meta, targets)
            helpers.extend(new_helpers)
            group = curve_nodes.build(obj, meta, actual, profile)
            groups.append(group)
            install(obj, meta, actual, profile, group)
        bpy.context.view_layer.update()
        return result(objects, args.sample_limit)
    except BaseException:
        for obj in reversed(objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for g in groups:
            if g.users == 0:
                bpy.data.node_groups.remove(g)
        for h in helpers:
            bpy.data.objects.remove(h, do_unlink=True)
        for d in data:
            if d.users == 0:
                bpy.data.curves.remove(d)
        raise


def result(objects: list[Any], limit: int) -> CurveResult:
    return CurveResult(
        curve_count=len(objects),
        point_count=sum(sum(len(points(s)) for s in o.data.splines) for o in objects),
        curves=[summary(o, CurveInspectArguments()) for o in objects[:limit]],
        truncated=len(objects) > limit,
    )


def protected_helpers(obj: Any, meta: dict[str, Any], group: Any) -> list[Any]:
    if group.users != 1:
        fail("Owned curve evaluation graph has external users")
    helpers = [
        obj.get(KEY + f"_target_{i}")
        for i, b in enumerate(meta["bindings"])
        if b["spec"]["target"]["kind"] == "bone"
    ]
    for helper in helpers:
        if helper is None or not helper.get(KEY + "_helper"):
            fail("Owned bone anchor is missing or changed")
        users = bpy.data.user_map(subset={helper}).get(helper, set())
        if users - {obj, group, bpy.context.scene, *helper.users_collection}:
            fail("Owned bone anchor has external users; detach them first")
        organization.object_editable(helper)
    return helpers


def configure(args: CurveConfigureArguments) -> CurveResult:
    organization.idle(mutate=True)
    plans = []
    budget = 0
    total_points = 0
    total_bindings = 0
    for edit in args.curves:
        obj = object_curve(edit.name, edit=True)
        meta, old_group = metadata(obj)
        protected_helpers(obj, meta, old_group)
        targets = current_targets(obj, meta)
        existing = definitions(obj)
        splines = edit.splines if edit.splines is not None else existing
        if edit.splines is not None and meta["bindings"] and edit.bindings is None:
            fail(
                "Replacing bound spline topology requires explicit bindings to "
                "rebind or [] to detach"
            )
        occupied = set()
        for item in edit.ranges:
            if item.spline >= len(splines) or item.start + len(item.points) > len(
                splines[item.spline].points
            ):
                fail("Point range exceeds an existing spline")
            for i, p in enumerate(item.points, item.start):
                if (item.spline, i) in occupied:
                    fail("Point edit ranges must not overlap")
                occupied.add((item.spline, i))
                if edit.bindings is None and p.co != splines[item.spline].points[i].co:
                    if any(
                        b["spec"]["spline"] == item.spline
                        and (b["spec"]["follow"] == "spline" or b["spec"]["point"] == i)
                        for b in meta["bindings"]
                    ):
                        fail(
                            "Attached positions are target-driven; "
                            "replace bindings to rebind edited geometry"
                        )
                splines[item.spline].points[i] = p
        settings = resolve_settings(
            CurveSettings.model_validate(meta["settings"]), edit.settings
        )
        if (
            settings.material is not None
            and settings.material not in bpy.data.materials
        ):
            fail("Requested material does not exist")
        splines = validate_splines(splines, settings)
        binding_specs = (
            edit.bindings
            if edit.bindings is not None
            else [CurveBinding.model_validate(b["spec"]) for b in meta["bindings"]]
        )
        CurveSpec(name=obj.name, splines=splines, bindings=binding_specs)
        if edit.bindings is None:
            for b, t in zip(meta["bindings"], targets, strict=True):
                frame(
                    t,
                    CurveBinding.model_validate(b["spec"]),
                    signature=b.get("topology"),
                )
            bindings = meta["bindings"]
        else:
            bindings, targets = bind_plan(obj, splines, binding_specs)
        profile, size = profile_object(settings, obj)
        budget += estimate(splines, settings, size)
        total_points += sum(len(s.points) for s in splines)
        total_bindings += len(bindings)
        plans.append(
            (
                obj,
                old_group,
                splines,
                settings,
                dict(settings=settings.model_dump(mode="json"), bindings=bindings),
                targets,
                profile,
            )
        )
    if budget > 1000000 or total_points > 4096 or total_bindings > 128:
        fail("Curve edit batch exceeds its geometry/point/attachment budget")
    proposed = [(p[0], [*p[5], *([p[6]] if p[6] is not None else [])]) for p in plans]
    for owner, references in proposed:
        for reference in references:
            dependencies(reference, owner, proposed)
    staged = []
    saved = []
    try:
        for obj, _old_group, splines, settings, meta, targets, profile in plans:
            d = make_data(obj.data.name, splines, settings)
            entry = dict(
                obj=obj,
                data=d,
                group=None,
                helpers=[],
                meta=meta,
                targets=[],
                profile=profile,
            )
            staged.append(entry)
            actual, helpers = native_targets(meta, targets)
            entry.update(helpers=helpers, targets=actual)
            entry["group"] = curve_nodes.build(obj, meta, actual, profile)
        for entry in staged:
            obj = entry["obj"]
            group = obj.modifiers[curve_nodes.MODIFIER].node_group
            props = {
                k: obj[k] for k in obj.keys() if k == KEY or k.startswith(KEY + "_")
            }
            old_helpers = [
                obj.get(KEY + f"_target_{i}")
                for i, b in enumerate(json.loads(obj[KEY])["bindings"])
                if b["spec"]["target"]["kind"] == "bone"
            ]
            saved.append((obj, obj.data, group, props, old_helpers))
            obj.modifiers.remove(obj.modifiers[curve_nodes.MODIFIER])
            for k in props:
                del obj[k]
            obj.data = entry["data"]
            install(
                obj, entry["meta"], entry["targets"], entry["profile"], entry["group"]
            )
        bpy.context.view_layer.update()
        answer = result([p[0] for p in plans], args.sample_limit)
    except BaseException:
        for obj, d, g, props, _ in reversed(saved):
            if curve_nodes.MODIFIER in obj.modifiers:
                obj.modifiers.remove(obj.modifiers[curve_nodes.MODIFIER])
            for k in list(obj.keys()):
                if k == KEY or k.startswith(KEY + "_"):
                    del obj[k]
            obj.data = d
            for k, v in props.items():
                obj[k] = v
            mod = obj.modifiers.new(curve_nodes.MODIFIER, "NODES")
            mod.node_group = g
        for entry in staged:
            if entry["group"] is not None and entry["group"].users == 0:
                bpy.data.node_groups.remove(entry["group"])
            for h in entry["helpers"]:
                bpy.data.objects.remove(h, do_unlink=True)
            if entry["data"].users == 0:
                bpy.data.curves.remove(entry["data"])
        bpy.context.view_layer.update()
        raise
    for _, d, g, _, helpers in saved:
        if d.users == 0:
            bpy.data.curves.remove(d)
        if g.users == 0:
            bpy.data.node_groups.remove(g)
        for h in helpers:
            bpy.data.objects.remove(h, do_unlink=True)
    return answer


def summary(obj: Any, args: CurveInspectArguments) -> CurveSummary:
    splines = definitions(obj)
    issues = []
    meta, group = metadata(obj)
    targets = current_targets(obj, meta)
    try:
        profile_object(CurveSettings.model_validate(meta["settings"]), obj)
    except OperationError as exc:
        issues.append(str(exc))
    bindings = []
    for b, target in zip(meta["bindings"], targets, strict=True):
        spec = CurveBinding.model_validate(b["spec"])
        point = None
        issue = None
        try:
            matrix, _ = frame(target, spec, signature=b.get("topology"))
            point = list(matrix @ Vector(spec.offset))
        except OperationError as exc:
            issue = str(exc)
            issues.append(issue)
        bindings.append(
            BindingSummary(
                spline=spec.spline,
                point=spec.point,
                follow=spec.follow,
                target=spec.target,
                offset=spec.offset,
                valid=issue is None,
                issue=issue,
                intended_world=point,
                evaluated_world=None,
                error=None,
            )
        )
    rows = []
    minimum = None
    maximum = None
    vertices = None
    faces = None
    length = None
    if not issues:
        sampled = curve_nodes.sample(obj, group, max(2, args.samples))
        control = curve_nodes.sample(obj, group, 2, control=True) if bindings else []
        for i, b in enumerate(bindings):
            offset = sum(len(s.points) for s in splines[: b.spline]) + b.point
            world = obj.matrix_world @ Vector(control[offset]["position"])
            error = float((world - Vector(b.intended_world)).length)
            issue = (
                "Evaluated attachment error exceeds 0.0001 world units"
                if error > 1e-4
                else None
            )
            bindings[i] = b.model_copy(
                update=dict(
                    evaluated_world=list(world),
                    error=error,
                    valid=issue is None,
                    issue=issue,
                )
            )
            if issue:
                issues.append(issue)
        transform = obj.matrix_world if args.space == "world" else Matrix.Identity(4)
        scale = transform.to_scale().x
        for i, spline in enumerate(splines):
            if args.spline is not None and i != args.spline:
                continue
            samples = [v for v in sampled if v["spline_index"] == i]
            if not samples:
                fail("Native curve evaluation produced no samples")
            detail = []
            if args.samples:
                for j, v in enumerate(samples):
                    tangent = (transform.to_3x3() @ Vector(v["tangent"])).normalized()
                    normal = (transform.to_3x3() @ Vector(v["normal"])).normalized()
                    binormal = tangent.cross(normal).normalized()
                    detail.append(
                        CurveSample(
                            factor=j
                            / (len(samples) if spline.cyclic else len(samples) - 1),
                            position=list(transform @ Vector(v["position"])),
                            tangent=list(tangent),
                            normal=list(normal),
                            binormal=list(binormal),
                            radius=v["radius"],
                            tilt=v["tilt"],
                        )
                    )
            assert spline.type is not None
            rows.append(
                SplineSummary(
                    index=i,
                    type=spline.type,
                    point_count=len(spline.points),
                    cyclic=spline.cyclic,
                    length=samples[0]["curve_length"] * scale,
                    start=list(transform @ Vector(samples[0]["position"])),
                    end=list(
                        transform
                        @ Vector(samples[0 if spline.cyclic else -1]["position"])
                    ),
                    radius_range=[
                        min(p.radius for p in spline.points),
                        max(p.radius for p in spline.points),
                    ],
                    tilt_range=[
                        min(p.tilt for p in spline.points),
                        max(p.tilt for p in spline.points),
                    ],
                    points=spline.points[
                        args.point_offset : args.point_offset + args.point_limit
                    ],
                    points_truncated=args.point_offset > 0
                    or args.point_offset + args.point_limit < len(spline.points),
                    samples=detail,
                )
            )
        length = (
            sum(
                next(v["curve_length"] for v in sampled if v["spline_index"] == i)
                for i in range(len(splines))
            )
            * scale
        )
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = ev.to_mesh()
        try:
            vertices = len(mesh.vertices)
            faces = len(mesh.polygons)
            if vertices > 250000:
                fail("Evaluated curve geometry exceeds 250000 vertices")
            coords = [transform @ v.co for v in mesh.vertices]
            if coords:
                minimum = [min(v[i] for v in coords) for i in range(3)]
                maximum = [max(v[i] for v in coords) for i in range(3)]
        finally:
            ev.to_mesh_clear()
    return CurveSummary(
        name=obj.name,
        spline_count=len(splines),
        point_count=sum(len(s.points) for s in splines),
        profile=meta["settings"]["profile"],
        material=meta["settings"]["material"],
        space=args.space,
        length=length,
        minimum=minimum,
        maximum=maximum,
        evaluated_vertices=vertices,
        evaluated_faces=faces,
        valid=not issues,
        issues=issues[:8],
        splines=rows,
        bindings=bindings,
    )


def inspect(args: CurveInspectArguments) -> CurveInspectResult:
    organization.idle()
    objects = [o for o in bpy.context.scene.objects if o.type == "CURVE" and KEY in o]
    if args.collection is not None:
        collection = organization.collection_named(args.collection)
        objects = [o for o in objects if o.name in collection.all_objects]
    selected, info = page(objects, args, lambda o: str(o.name))
    return CurveInspectResult(
        curves=[summary(object_curve(o.name), args) for o in selected], page=info
    )


def evaluation_dependencies(obj: Any) -> list[Any]:
    """Expose only bounded owned graph references to shared geometry preflight."""
    object_curve(obj.name)
    meta, _ = metadata(obj)
    current_targets(obj, meta)
    settings = CurveSettings.model_validate(meta["settings"])
    estimate(
        definitions(obj),
        settings,
        128
        if settings.profile.kind == "object"
        else (settings.profile.resolution if settings.profile.kind == "circle" else 1),
    )
    targets = [obj[KEY + f"_target_{i}"] for i in range(len(meta["bindings"]))]
    if settings.profile.kind == "object":
        targets.append(obj[KEY + "_profile"])
    return targets


def remove(args: CurveRemoveArguments) -> CurveRemoveResult:
    organization.idle(mutate=True)
    plans = []
    for name in args.names:
        obj = object_curve(name, edit=True)
        meta, group = metadata(obj)
        if obj.children:
            fail("Curve has dependent child objects; remove their relationship first")
        users = bpy.data.user_map(subset={obj}).get(obj, set())
        allowed = {bpy.context.scene, *obj.users_collection}
        if users - allowed:
            fail(
                "Curve has external users, attachments or profile dependents; "
                "detach them first"
            )
        helpers = protected_helpers(obj, meta, group)
        plans.append((obj, obj.data, group, helpers))
    removed = []
    for obj, data, group, helpers in plans:
        name = obj.name
        try:
            organization.remove_object(obj)
            removed.append(name)
            if data.users == 0:
                bpy.data.curves.remove(data)
            if group.users == 0:
                bpy.data.node_groups.remove(group)
            for h in helpers:
                bpy.data.objects.remove(h, do_unlink=True)
        except Exception as exc:
            raise OperationError(
                "curve_remove_failed",
                f"Native curve removal failed ({type(exc).__name__}); "
                "inspect partial progress",
                dict(
                    removed=removed,
                    remaining=[n for n in args.names[len(removed) :]],
                    cleanup_may_be_incomplete=name in removed,
                ),
            ) from exc
    return CurveRemoveResult(removed=removed)
