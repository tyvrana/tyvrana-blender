"""Atomic mixed-family construction using the canonical loft/surface generators."""

import hashlib
import json
import uuid
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Euler, Matrix, Vector  # type: ignore[import-not-found]
from pydantic import ValidationError

from . import loft, organization, placement, surfaces
from .assembly_models import (
    AssemblyComponent,
    AssemblyConfigureArguments,
    AssemblyCreateArguments,
    AssemblyFamily,
    AssemblyInspectArguments,
    AssemblyResult,
    AssemblySpec,
    FamilyMemberOverride,
    ShapeOverride,
)
from .bindings import RESOURCE_KEY
from .errors import OperationError, constraint_error
from .loft_models import LoftRevision, LoftSpec
from .surface_models import SurfaceRevision, SurfaceSpec

KEY = "tyvrana_assembly"
MEMBER = "tyvrana_assembly_member"


def fail(message: str, code: str = "assembly_invalid") -> None:
    raise OperationError(code, message)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def shape(
    spec: LoftSpec | SurfaceSpec, override: ShapeOverride, factor: float
) -> LoftSpec | SurfaceSpec:
    data = spec.model_dump()
    features = {f["id"]: f for f in data["features"]}
    for feature in features.values():
        feature["height"] *= factor
    for feature in override.features:
        if feature.id not in features:
            fail(f"Unknown feature handle: {feature.id}")
        features[feature.id]["height"] = feature.height
    if isinstance(spec, LoftSpec):
        if override.nodes or override.openings or override.thickness is not None:
            fail("Surface edits cannot be applied to a loft family")
        updated = LoftSpec.model_validate(data)
        changes: dict[str, Any] = {
            "name": spec.name,
            "section_edits": override.sections,
        }
        if override.ends is not None:
            changes["ends"] = override.ends
        return loft.revised(updated, LoftRevision.model_validate(changes))
    if override.sections or override.ends is not None:
        fail("Loft edits cannot be applied to a surface family")
    surface_spec = SurfaceSpec.model_validate(data)
    if override.nodes or override.openings or override.thickness is not None:
        return surfaces.revised(
            surface_spec,
            SurfaceRevision(
                name=spec.name,
                expected_revision=1,
                nodes=override.nodes,
                openings=override.openings,
                thickness=override.thickness,
            ),
        )
    return surface_spec


def sample_path(points: list[list[float]], fraction: float) -> tuple[Any, Any]:
    values = [Vector(p) for p in points]
    if len(values) == 1:
        return values[0], Vector((0, 1, 0))
    lengths = [(b - a).length for a, b in zip(values, values[1:], strict=False)]
    if min(lengths) < 1e-6:
        fail("Family path contains coincident consecutive points")
    distance = fraction * sum(lengths)
    for index, length in enumerate(lengths):
        if distance <= length or index == len(lengths) - 1:
            direction = values[index + 1] - values[index]
            return values[index].lerp(
                values[index + 1], min(1, distance / length)
            ), direction
        distance -= length
    raise AssertionError("Unreachable path interval")


def member_name(name: str, family: str, index: int) -> str:
    value = f"{name}.{family}.{index:02}"
    if len(value.encode()) > 63:
        fail("Generated member names exceed63 UTF-8 bytes; shorten assembly/family IDs")
    return value


def reflection(family: AssemblyFamily) -> Any:
    normal = Vector(family.mirror_normal)
    if normal.length < 1e-8:
        fail("Mirror plane requires a nonzero normal")
    normal.normalize()
    result = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            result[i][j] -= 2 * normal[i] * normal[j]
    result.translation = 2 * normal.dot(Vector(family.mirror_point)) * normal
    return result


def expand(spec: AssemblySpec) -> list[dict[str, Any]]:
    templates = {t.id: t for t in spec.templates}
    families = {f.id: f for f in spec.families}
    graph = {f.id: {f.mirror_of} if f.mirror_of else set() for f in spec.families}
    order = organization.ordered(graph)
    output: dict[tuple[str, int], dict[str, Any]] = {}
    for family_id in order:
        family = families[family_id]
        overrides = {o.index: o for o in family.overrides}
        for index in range(family.count):
            t = index / max(1, family.count - 1)
            override = overrides.get(index, FamilyMemberOverride(index=index))
            name = member_name(spec.name, family.id, index)
            mirrored = None
            if family.mirror_of:
                source = output[(family.mirror_of, index)]
                base = source["spec"]
                transform = reflection(family) @ source["matrix"]
                mirrored = source["name"]
                transform = (
                    transform
                    @ Matrix.Translation(Vector(override.offset))
                    @ Euler(override.rotation).to_matrix().to_4x4()
                )
                if override.scale:
                    transform = transform @ Matrix.Diagonal((*override.scale, 1.0))
            else:
                assert family.template is not None
                base = templates[family.template].spec
                location, direction = sample_path(family.path, t)
                location += Vector(override.offset)
                scale = override.scale or [
                    a * (1 - t) + b * t
                    for a, b in zip(family.scale_start, family.scale_end, strict=True)
                ]
                rotation = Euler(
                    [
                        a * (1 - t) + b * t + c
                        for a, b, c in zip(
                            family.rotation_start,
                            family.rotation_end,
                            override.rotation,
                            strict=True,
                        )
                    ]
                ).to_matrix()
                if family.align_path:
                    rotation = placement.frame(direction, Vector(family.up)) @ rotation
                transform = (
                    Matrix.Translation(location)
                    @ rotation.to_4x4()
                    @ Matrix.Diagonal((*scale, 1.0))
                )
            factor = family.feature_scale[0] * (1 - t) + family.feature_scale[1] * t
            try:
                geometry = shape(base, override.shape, factor).model_copy(
                    update={"name": name}
                )
            except ValidationError as exc:
                raise constraint_error("assembly_invalid", exc) from exc
            rule = override.placement
            if (
                mirrored
                and rule is None
                and override.offset == [0, 0, 0]
                and override.rotation == [0, 0, 0]
                and override.scale is None
            ):
                from .placement_models import MirrorPlacement

                rule = MirrorPlacement(
                    kind="mirror",
                    source=mirrored,
                    plane_point=family.mirror_point,
                    plane_normal=family.mirror_normal,
                )
            output[(family.id, index)] = dict(
                name=name,
                family=family.id,
                index=index,
                spec=geometry,
                matrix=transform,
                rule=rule,
                mirrored_from=mirrored,
            )
    # Keep declared family/member order in compact results.
    return [output[(f.id, i)] for f in spec.families for i in range(f.count)]


def build(spec: LoftSpec | SurfaceSpec) -> tuple[Any, Any, Any]:
    if isinstance(spec, SurfaceSpec):
        return surfaces.build(spec)
    points, faces = loft.geometry(spec)
    mesh = bpy.data.meshes.new(spec.name + ".Mesh")
    try:
        mesh.from_pydata(points, [], faces)
        mesh.update()
        for polygon in mesh.polygons:
            polygon.use_smooth = spec.smooth
        # Assemblies qualify every closed member, not only feature-bearing lofts.
        stats = loft.native_checks(mesh, spec) if spec.caps else {}
        return mesh, None, stats
    except BaseException:
        bpy.data.meshes.remove(mesh)
        raise


def set_geometry_state(
    obj: Any, item: dict[str, Any], previous: dict[str, Any] | None = None
) -> None:
    spec = item["spec"]
    if isinstance(spec, SurfaceSpec):
        obj[surfaces.KEY] = surfaces.state(
            spec,
            obj.data,
            item["geometry"],
            item["stats"],
            previous=previous,
            changed=item.get("topology_changed", False),
        )
    else:
        obj[loft.KEY] = json.dumps(
            dict(
                id=previous["id"] if previous else uuid.uuid4().hex,
                revision=previous["revision"] + 1 if previous else 1,
                spec=spec.model_dump(),
                signature=loft.signature(obj.data),
            )
        )


def content(obj: Any) -> str:
    return (
        surfaces.content_hash(obj.data)
        if surfaces.KEY in obj
        else loft.signature(obj.data)
    )


def root_state(root: Any) -> dict[str, Any]:
    try:
        result: dict[str, Any] = json.loads(root[KEY])
        AssemblySpec.model_validate(result["spec"])
        return result
    except (KeyError, ValueError, TypeError) as exc:
        raise OperationError(
            "assembly_state_invalid", "Assembly metadata is missing or damaged"
        ) from exc


def get_members(meta: dict[str, Any]) -> list[Any]:
    result = []
    for member in meta["members"]:
        obj = bpy.data.objects.get(member["name"])
        if obj is None or obj.get(RESOURCE_KEY) != member["resource_id"]:
            fail("Assembly member is missing/renamed or its resource identity changed")
        result.append(obj)
    return result


def record(
    root: Any,
    spec: AssemblySpec,
    items: list[dict[str, Any]],
    objects: list[Any],
    previous: dict[str, Any] | None,
) -> None:
    root[KEY] = json.dumps(
        dict(
            id=previous["id"] if previous else uuid.uuid4().hex,
            revision=previous["revision"] + 1 if previous else 1,
            spec=spec.model_dump(exclude_none=True),
            members=[
                dict(
                    name=o.name,
                    resource_id=o[RESOURCE_KEY],
                    family=i["family"],
                    index=i["index"],
                    signature=content(o),
                    spec_hash=digest(i["spec"].model_dump()),
                    kind="surface" if isinstance(i["spec"], SurfaceSpec) else "loft",
                    mirrored_from=i["mirrored_from"],
                )
                for i, o in zip(items, objects, strict=True)
            ],
        )
    )


def inspect(
    args: AssemblyInspectArguments, changed: list[str] | None = None
) -> AssemblyResult:
    organization.idle()
    root = organization.object_named(args.name)
    meta = root_state(root)
    objects = get_members(meta)
    rows = []
    valid = True
    for record, obj in zip(meta["members"], objects, strict=True):
        managed = surfaces.KEY in obj or loft.KEY in obj
        current = managed and content(obj) == record["signature"]
        if placement.KEY in obj:
            current = current and placement.summary(obj).valid
        valid = valid and current
        if changed is not None and obj.name not in changed:
            continue
        if args.families and record["family"] not in args.families:
            continue
        geometry = (
            json.loads(obj[surfaces.KEY if surfaces.KEY in obj else loft.KEY])
            if managed
            else None
        )
        regions = (
            (
                list(geometry["regions"])
                if surfaces.KEY in obj
                else [s["id"] for s in geometry["spec"]["sections"] if s["id"]]
                + [f["id"] for f in geometry["spec"]["features"]]
                + (["start_cap", "end_cap"] if geometry["spec"]["caps"] else [])
            )
            if geometry is not None
            else []
        )
        points = [obj.matrix_world @ v.co for v in obj.data.vertices]
        low = [min(p[k] for p in points) for k in range(3)]
        high = [max(p[k] for p in points) for k in range(3)]
        rows.append(
            AssemblyComponent(
                name=obj.name,
                member_id=f"{meta['id']}:{record['family']}:{record['index']}",
                resource_id=obj[RESOURCE_KEY],
                family=record["family"],
                index=record["index"],
                kind=record["kind"],
                revision=geometry["revision"] if geometry is not None else 0,
                vertex_count=len(obj.data.vertices),
                face_count=len(obj.data.polygons),
                dimensions=[b - a for a, b in zip(low, high, strict=True)],
                bounds_min=low,
                bounds_max=high,
                region_ids=regions[:8],
                region_count=len(regions),
                valid=current,
                mirrored_from=record["mirrored_from"],
            )
        )
    end = args.offset + args.limit
    return AssemblyResult(
        name=root.name,
        assembly_id=meta["id"],
        revision=meta["revision"],
        component_count=len(objects),
        family_count=len(meta["spec"]["families"]),
        vertex_count=sum(len(o.data.vertices) for o in objects),
        face_count=sum(len(o.data.polygons) for o in objects),
        valid=valid,
        changed_members=changed or [],
        components=rows[args.offset : end],
        next_offset=end if changed is None and args.limit and end < len(rows) else None,
        components_truncated=end < len(rows),
        spec=AssemblySpec.model_validate(meta["spec"]) if args.include_spec else None,
    )


def prepare(items: list[dict[str, Any]], old: dict[str, Any] | None = None) -> None:
    total = 0
    for item in items:
        previous = old.get(item["name"]) if old else None
        item["changed"] = (
            previous is None
            or digest(item["spec"].model_dump()) != previous["spec_hash"]
        )
        if item["changed"]:
            try:
                mesh, geometry, stats = build(item["spec"])
            except OperationError as exc:
                raise OperationError(
                    exc.error.code,
                    f"{item['name']}: {exc.error.message}",
                    {
                        "component": item["name"],
                        "family": item["family"],
                        "index": item["index"],
                        "cause": exc.error.details,
                    },
                ) from exc
            item.update(mesh=mesh, geometry=geometry, stats=stats)
            total += len(mesh.vertices)
        else:
            total += len(bpy.data.objects[item["name"]].data.vertices)
        if total > 131072:
            fail("Assembly exceeds131072 generated vertices", "assembly_limit_exceeded")


def discard(items: list[dict[str, Any]]) -> None:
    for item in items:
        mesh = item.get("mesh")
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def place_members(items: list[dict[str, Any]], objects: list[Any]) -> None:
    from .placement_models import PlacementArguments, PlacementEdit

    before = [(obj, obj.matrix_world.copy(), obj.get(placement.KEY)) for obj in objects]
    for item, obj in zip(items, objects, strict=True):
        obj.matrix_world = item["matrix"]
    bpy.context.view_layer.update()
    edits = []
    for item, obj in zip(items, objects, strict=True):
        rule = item["rule"]
        if rule is None and placement.KEY in obj:
            rule = json.loads(obj[placement.KEY])["rule"]
        if rule is not None:
            edits.append(PlacementEdit(name=obj.name, rule=rule))
    if edits:
        placement.place(PlacementArguments(placements=edits))
    # Intermediate family transforms must not invalidate unchanged constraints.
    for obj, transform, raw in before:
        if raw is not None and placement.KEY in obj:
            if json.loads(raw)["rule"] == json.loads(obj[placement.KEY])[
                "rule"
            ] and placement.same_matrix(transform, obj.matrix_world):
                obj[placement.KEY] = raw


def create(args: AssemblyCreateArguments) -> AssemblyResult:
    organization.idle(mutate=True)
    spec = AssemblySpec.model_validate(
        args.model_dump(exclude={"collections"}, exclude_none=True)
    )
    items = expand(spec)
    collections = [organization.collection_named(n) for n in args.collections]
    for collection in collections:
        organization.collection_editable(collection)
    for name in [
        spec.name,
        *(spec.name + "." + f.id for f in spec.families),
        *(i["name"] for i in items),
    ]:
        organization.named_available(name, bpy.data.objects)
    made = []
    try:
        prepare(items)
        root = bpy.data.objects.new(spec.name, None)
        made.append(root)
        root[RESOURCE_KEY] = uuid.uuid4().hex
        groups = {}
        for family in spec.families:
            group = bpy.data.objects.new(spec.name + "." + family.id, None)
            made.append(group)
            group.parent = root
            group[RESOURCE_KEY] = uuid.uuid4().hex
            groups[family.id] = group
        objects = []
        for item in items:
            obj = bpy.data.objects.new(item["name"], item["mesh"])
            made.append(obj)
            objects.append(obj)
            obj.parent = groups[item["family"]]
            obj[RESOURCE_KEY] = uuid.uuid4().hex
            obj[MEMBER] = root[RESOURCE_KEY]
            set_geometry_state(obj, item)
        for obj in made:
            for collection in collections:
                collection.objects.link(obj)
        place_members(items, objects)
        record(root, spec, items, objects, None)
        return inspect(
            AssemblyInspectArguments(name=root.name), [o.name for o in objects]
        )
    except BaseException:
        for obj in reversed(made):
            bpy.data.objects.remove(obj, do_unlink=True)
        discard(items)
        raise


def revision_spec(old: AssemblySpec, args: AssemblyConfigureArguments) -> AssemblySpec:
    data = old.model_dump(exclude_none=True)
    for field in ("templates", "families"):
        existing = {v["id"]: v for v in data[field]}
        for value in getattr(args, field):
            if value.id not in existing:
                fail(f"Unknown {field} ID: {value.id}")
            if (
                field == "templates"
                and existing[value.id]["kind"] != value.model_dump()["kind"]
            ):
                fail("Revision preserves each template's construction kind")
            if field == "families":
                existing[value.id].update(
                    value.model_dump(exclude_unset=True, exclude_none=True)
                )
            else:
                existing[value.id] = value.model_dump(exclude_none=True)
        data[field] = list(existing.values())
    families = {f["id"]: f for f in data["families"]}
    for member in args.members:
        if member.family not in families:
            fail(f"Unknown family ID: {member.family}")
        family = families[member.family]
        overrides = {o["index"]: o for o in family["overrides"]}
        existing = overrides.get(
            member.index,
            FamilyMemberOverride(index=member.index).model_dump(exclude_none=True),
        )
        values = member.model_dump(exclude_unset=True, exclude={"family"})
        if "shape" in values:
            # Selected semantic arrays patch by ID; omitted exceptions survive.
            shape_values = values.pop("shape")
            for key, value in shape_values.items():
                if isinstance(value, list):
                    merged = {x["id"]: x for x in existing["shape"][key]}
                    for update in value:
                        merged[update["id"]] = {
                            **merged.get(update["id"], {}),
                            **update,
                        }
                    existing["shape"][key] = list(merged.values())
                else:
                    existing["shape"][key] = value
        existing.update(values)
        overrides[member.index] = existing
        family["overrides"] = list(overrides.values())
    try:
        spec = AssemblySpec.model_validate(data)
    except ValidationError as exc:
        raise constraint_error("assembly_invalid", exc) from exc
    if [(f.id, f.count) for f in old.families] != [
        (f.id, f.count) for f in spec.families
    ]:
        fail("Revision preserves family membership and member identities")
    return spec


def configure(args: AssemblyConfigureArguments) -> AssemblyResult:
    organization.idle(mutate=True)
    root = organization.object_named(args.name)
    organization.object_editable(root)
    meta = root_state(root)
    if args.expected_revision != meta["revision"]:
        fail("Expected assembly revision is not current", "assembly_revision_conflict")
    objects = get_members(meta)
    # Construction paths and explicit constraint targets are world coordinates.
    # Refuse transformed organization frames instead of silently relocating them.
    for frame_obj in [root, *root.children]:
        if not placement.same_matrix(frame_obj.matrix_world, Matrix.Identity(4)):
            fail(
                "Assembly revision requires identity organization frames; "
                "use member placement rules"
            )
    for record_value, obj in zip(meta["members"], objects, strict=True):
        organization.object_editable(obj)
        organization.simple_transform(obj)
        if content(obj) != record_value["signature"]:
            fail(
                "Member geometry changed outside its family; preserve edits",
                "assembly_stale_geometry",
            )
        if obj.data.users != 1 or obj.data.shape_keys or obj.data.animation_data:
            fail("Revision requires exclusive, unanimated mesh data")
    spec = revision_spec(AssemblySpec.model_validate(meta["spec"]), args)
    items = expand(spec)
    backups = []
    raw = root[KEY]
    changed = []
    try:
        prepare(items, {r["name"]: r for r in meta["members"]})
        for item, obj in zip(items, objects, strict=True):
            if not item["changed"]:
                continue
            item["topology_changed"] = surfaces.topology_hash(
                item["mesh"]
            ) != surfaces.topology_hash(obj.data)
            if item["topology_changed"]:
                if args.topology_policy != "rebuild":
                    fail(
                        "Revision changes topology; explicitly request "
                        "topology_policy=rebuild",
                        "assembly_topology_change_required",
                    )
                allowed = {
                    "position",
                    ".edge_verts",
                    ".corner_vert",
                    ".corner_edge",
                    ".select_vert",
                    ".select_edge",
                    ".select_poly",
                    "sharp_face",
                    surfaces.PATCH_ATTRIBUTE,
                    surfaces.REGION_ATTRIBUTE,
                }
                if (
                    obj.modifiers
                    or obj.vertex_groups
                    or obj.data.uv_layers
                    or any(a.name not in allowed for a in obj.data.attributes)
                    or any(p.material_index for p in obj.data.polygons)
                ):
                    fail("Topology rebuild would discard downstream data")
        for item, obj in zip(items, objects, strict=True):
            key = surfaces.KEY if surfaces.KEY in obj else loft.KEY
            backup = (
                obj.data.copy()
                if item["changed"] and not item["topology_changed"]
                else None
            )
            backups.append(
                (obj, obj.data, backup, dict(obj.items()), obj.matrix_world.copy())
            )
            if item["changed"]:
                previous = json.loads(obj[key])
                if item["topology_changed"]:
                    for material in obj.data.materials:
                        item["mesh"].materials.append(material)
                    obj.data = item["mesh"]
                else:
                    for a, b in zip(
                        obj.data.vertices, item["mesh"].vertices, strict=True
                    ):
                        a.co = b.co
                    for attr in (surfaces.PATCH_ATTRIBUTE, surfaces.REGION_ATTRIBUTE):
                        if attr in item["mesh"].attributes:
                            for a, b in zip(
                                obj.data.attributes[attr].data,
                                item["mesh"].attributes[attr].data,
                                strict=True,
                            ):
                                a.value = b.value
                    obj.data.update()
                    if (
                        isinstance(item["spec"], LoftSpec)
                        and previous["spec"]["smooth"] != item["spec"].smooth
                    ):
                        for polygon in obj.data.polygons:
                            polygon.use_smooth = item["spec"].smooth
                set_geometry_state(obj, item, previous)
                changed.append(obj.name)
        place_members(items, objects)
        for obj, _, _, _, transform in backups:
            if (
                not placement.same_matrix(transform, obj.matrix_world)
                and obj.name not in changed
            ):
                changed.append(obj.name)
        record(root, spec, items, objects, meta)
        result = inspect(AssemblyInspectArguments(name=root.name), changed)
    except BaseException:
        root[KEY] = raw
        for obj, old, backup, props, transform in reversed(backups):
            obj.data = old
            if backup is not None:
                for a, b in zip(old.vertices, backup.vertices, strict=True):
                    a.co = b.co
                for attr in (surfaces.PATCH_ATTRIBUTE, surfaces.REGION_ATTRIBUTE):
                    if attr in backup.attributes:
                        for a, b in zip(
                            old.attributes[attr].data,
                            backup.attributes[attr].data,
                            strict=True,
                        ):
                            a.value = b.value
                for a, b in zip(old.polygons, backup.polygons, strict=True):
                    a.use_smooth = b.use_smooth
                old.update()
                bpy.data.meshes.remove(backup)
            for key in (loft.KEY, surfaces.KEY, placement.KEY):
                if key in props:
                    obj[key] = props[key]
                elif key in obj:
                    del obj[key]
            obj.matrix_world = transform
        bpy.context.view_layer.update()
        discard(items)
        raise
    for obj, old, backup, _, _ in backups:
        if old != obj.data:
            bpy.data.meshes.remove(old)
        if backup is not None:
            bpy.data.meshes.remove(backup)
    discard(items)
    return result
