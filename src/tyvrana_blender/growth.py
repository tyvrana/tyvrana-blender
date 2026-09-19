"""Transactional native Hair Curves with persistent semantic roots and shared output."""

import array
import bisect
import hashlib
import json
import math
import random
from contextlib import contextmanager
from typing import Any, Never
from uuid import uuid4

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from . import curves, growth_fields, growth_nodes, mesh_selectors
from .errors import OperationError
from .growth_domain_models import GrowthFieldSample, GrowthRow, GrowthRowSummary
from .growth_models import (
    GrowthConfigureArguments,
    GrowthCreateArguments,
    GrowthDelta,
    GrowthGuide,
    GrowthInspectArguments,
    GrowthInspectResult,
    GrowthQA,
    GrowthRemoveArguments,
    GrowthRemoveResult,
    GrowthSummary,
)
from .instances import Surface

KEY = growth_nodes.KEY


def fail(message: str, code: str = "growth_invalid") -> Never:
    raise OperationError(code, message)


def put(data: Any, name: str, kind: str, domain: str, values: list[Any]) -> None:
    a = data.attributes.new(name, kind, domain)
    prop = "vector" if kind in {"FLOAT_VECTOR", "FLOAT2"} else "value"
    a.data.foreach_set(
        prop, [x for v in values for x in v] if prop == "vector" else values
    )


def uv_signature(surface: Any, name: str) -> str:
    layer = surface.data.uv_layers.get(name)
    if layer is None:
        fail("Surface UV map is missing; create a non-overlapping UV map")
    buf = array.array("f", [0]) * (len(layer.uv) * 2)
    layer.uv.foreach_get("vector", buf)
    return hashlib.sha256(buf.tobytes()).hexdigest()


def rest_signature(surface: Any) -> str:
    buf = array.array("f", [0]) * (len(surface.data.vertices) * 3)
    surface.data.vertices.foreach_get("co", buf)
    return hashlib.sha256(buf.tobytes()).hexdigest()


def data_signature(data: Any) -> str:
    h = hashlib.sha256()
    for a in sorted(data.attributes, key=lambda x: x.name):
        if a.name.startswith(".") and a.name != ".curve_type":
            continue
        components = (
            3 if a.data_type == "FLOAT_VECTOR" else 2 if a.data_type == "FLOAT2" else 1
        )
        if a.data_type not in {
            "FLOAT_VECTOR",
            "FLOAT2",
            "INT",
            "FLOAT",
            "BOOLEAN",
            "INT8",
        }:
            continue
        buf = array.array(
            "f" if a.data_type in {"FLOAT_VECTOR", "FLOAT2", "FLOAT"} else "i", [0]
        ) * (len(a.data) * components)
        a.data.foreach_get("vector" if components > 1 else "value", buf)
        h.update(a.name.encode())
        h.update(buf.tobytes())
    if hasattr(data, "curve_offset_data"):
        buf = array.array("i", [0]) * len(data.curve_offset_data)
        data.curve_offset_data.foreach_get("value", buf)
        h.update(buf.tobytes())
    return h.hexdigest()


def owned(name: str) -> tuple[Any, dict[str, Any], Any, Any]:
    obj = bpy.context.scene.objects.get(name)
    if obj is None or obj.type != "CURVES" or KEY not in obj:
        fail("Expected an owned rooted-growth Hair Curves object")
    if (
        obj.library
        or obj.override_library
        or obj.data.library
        or obj.data.users != 1
        or obj.mode != "OBJECT"
    ):
        fail("Growth requires local single-user owned data in Object Mode")
    try:
        meta = json.loads(obj[KEY])
        mod = obj.modifiers.get(growth_nodes.MODIFIER)
        group = mod.node_group if mod else None
        roots = (
            group.nodes["Root Carrier"].inputs["Object"].default_value
            if group
            else None
        )
    except (ValueError, KeyError, TypeError):
        fail("Growth ownership metadata is damaged")
    if (
        len(obj.modifiers) != 1
        or len(obj.users_scene) != 1
        or obj.constraints
        or obj.animation_data
        or mod is None
        or not mod.show_viewport
        or not mod.show_render
        or group is None
        or roots is None
        or group.get(KEY) != meta["id"]
        or roots.get(KEY) != meta["id"]
        or roots.data.get(KEY) != meta["id"]
        or obj.data.get(KEY) != meta["id"]
    ):
        fail(
            "Owned growth data/graph/root carrier changed; preserve the "
            "system or remove it explicitly"
        )
    if group.get(KEY + "_signature") != growth_nodes.signature(group):
        fail("Owned Geometry Nodes graph was modified; refusing to clobber changes")
    if (
        roots.data.users != 1
        or roots.library
        or roots.override_library
        or len(roots.modifiers)
        or roots.constraints
        or roots.animation_data
        or roots.parent != obj.data.surface
        or roots.name not in bpy.context.scene.objects
        or any(
            abs(roots.matrix_local[r][c] - (1 if r == c else 0)) > 1e-6
            for r in range(4)
            for c in range(4)
        )
    ):
        fail("Root carrier ownership is shared or modified")
    if (
        data_signature(obj.data) != meta["guides_hash"]
        or data_signature(roots.data) != meta["roots_hash"]
    ):
        fail(
            "Native growth attributes changed outside the owned recipe; use "
            "batched guide edits"
        )
    return obj, meta, roots, group


def validity(obj: Any, meta: dict[str, Any]) -> list[str]:
    surface = obj.data.surface
    if surface is None or surface.name not in bpy.context.scene.objects:
        return [
            "Surface relationship is missing; restore it or create a newly bound system"
        ]
    if (
        surface.name != meta["spec"]["surface"]
        or obj.data.surface_uv_map != meta["spec"]["uv_map"]
    ):
        return ["Native surface binding changed; restore the binding before rebinding"]
    if not surface.add_rest_position_attribute:
        return [
            "Native surface rest-position generation is disabled; "
            "explicitly rebind to restore it"
        ]
    try:
        if (
            curves.topology(surface.data) != meta["topology"]
            or uv_signature(surface, meta["spec"]["uv_map"]) != meta["uv_hash"]
            or rest_signature(surface) != meta["rest_hash"]
        ):
            return [
                (
                    "Surface rest geometry/topology/UV changed; "
                    "growth.configure rebind=true "
                    "explicitly rebuilds roots/rest data"
                )
            ]
    except OperationError as e:
        return [str(e)]
    if obj.parent != surface or any(
        abs(obj.matrix_local[r][c] - (1 if r == c else 0)) > 1e-6
        for r in range(4)
        for c in range(4)
    ):
        return [
            (
                "Growth object must retain its identity local transform under the"
                " surface parent"
            )
        ]
    return []


def resources(
    spec: GrowthCreateArguments, owner: Any = None, counts: dict[int, int] | None = None
) -> tuple[Any, list[Any], list[Any], int, int]:
    surface = bpy.context.scene.objects.get(spec.surface)
    if surface is None or surface.type != "MESH":
        fail("Growth surface must be a mesh in the current scene")
    if (
        surface.library
        or surface.override_library
        or surface.data.library
        or surface.mode != "OBJECT"
    ):
        fail("Growth surface must be local editable Object Mode data")
    if abs(surface.matrix_world.determinant()) < 1e-12:
        fail("Surface transform is singular")
    uv_signature(surface, spec.uv_map)
    curves.topology(surface.data)
    if owner:
        curves.dependencies(surface, owner)
    for mod in surface.modifiers:
        if mod.show_viewport != mod.show_render or (
            mod.type == "SUBSURF" and mod.levels != mod.render_levels
        ):
            fail("Surface viewport and render modifier evaluation must agree")
    materials = []
    prototypes = []
    vertices = 0
    instances = 0
    for fi, family in enumerate(spec.families):
        material = bpy.data.materials.get(family.material) if family.material else None
        if family.material and material is None:
            fail("Family material is missing")
        materials.append(material)
        proto = None
        if family.template:
            proto = bpy.context.scene.objects.get(family.template.object_name)
            if proto is None or proto.type != "MESH" or proto == surface:
                fail("Template must be an independent mesh")
            if (
                proto.library
                or proto.override_library
                or proto.data.library
                or proto.modifiers
                or proto.data.shape_keys
            ):
                fail(
                    "Template must be a local static mesh without modifiers/shape keys"
                )
            if (
                len(proto.data.vertices) > 4096
                or not len(proto.data.vertices)
                or any(v.co.z < -1e-6 or v.co.z > 1.000001 for v in proto.data.vertices)
            ):
                fail("Template requires1..4096 vertices and local Z in[0,1]")
            if owner:
                curves.dependencies(proto, owner)
            count = sum(
                r.children or r.guides for r in spec.regions if r.family == family.name
            )
            count += sum(
                (row.count or 0) * (2 if row.mirror else 1)
                for region in spec.regions
                for row in region.rows
                if (row.family or region.family) == family.name
            )
            if counts is not None:
                count = counts.get(fi, 0)
            vertices += len(proto.data.vertices) * count
            if family.template.mode == "instances":
                instances += count
        prototypes.append(proto)
    if vertices > 2000000:
        fail(
            (
                "Template output exceeds2000000 equivalent vertices; simplify "
                "templates or reduce counts"
            ),
            "growth_limit",
        )
    return surface, materials, prototypes, vertices, instances


def region_faces(surface: Any, spec: GrowthCreateArguments) -> dict[str, set[int]]:
    bm = bmesh.new()
    try:
        bm.from_mesh(surface.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        result = {
            r.name: {f.index for f in mesh_selectors.select(bm, r.selector, surface)}
            for r in spec.regions
        }
        if any(not v for v in result.values()):
            fail("A surface region is empty; repair its face selector")
        return result
    except mesh_selectors.SelectionError as e:
        fail(str(e))
    finally:
        bm.free()


def old_guides(obj: Any, meta: dict[str, Any]) -> dict[int, dict[str, Any]]:
    data = obj.data
    spec = GrowthCreateArguments.model_validate(meta["spec"])
    out = {}
    for i, c in enumerate(data.curves):

        def attr(name: str, index: int = i) -> Any:
            return data.attributes[name].data[index].value

        out[attr("growth_root_id")] = {
            "points": [list(p.position) for p in c.points],
            "edited": bool(attr("growth_edited")),
            "family": spec.families[attr("growth_family")].name,
            "region": spec.regions[attr("growth_region")].name,
        }
    return out


def plan(
    spec: GrowthCreateArguments,
    surface: Any,
    slots: dict[str, int],
    previous: dict[int, dict[str, Any]],
    edits: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sf = Surface(surface, surface.data, spec.uv_map)
    regions = region_faces(surface, spec)
    families = {f.name: f for f in spec.families}
    family_index = {f.name: i for i, f in enumerate(spec.families)}
    guides: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    changed = {e.root_id: e for e in edits}
    for ri, region in enumerate(spec.regions):
        family = families[region.family]
        triangles = [
            i
            for i, t in enumerate(sf.triangles)
            if t.polygon_index in regions[region.name] and t.area > 1e-12
        ]
        if not triangles:
            fail("Region contains no nondegenerate UV-bearing triangles")
        totals = []
        total = 0.0
        for i in triangles:
            total += sf.triangles[i].area
            totals.append(total)
        if region.field:
            for control in region.field.controls:
                growth_fields.location(
                    sf, Vector((*control.uv, 0)), regions[region.name]
                )
        placements: list[
            tuple[str, int, Any, GrowthRow | None, bool, int, int, random.Random]
        ] = []
        for kind, count in [("guide", region.guides), ("child", region.children)]:
            for n in range(count):
                rng = random.Random(
                    f"{spec.seed}:{slots[growth_fields.region_key(region.name)]}:{kind}:{n}"
                )
                ti = triangles[
                    min(len(triangles) - 1, bisect.bisect(totals, rng.random() * total))
                ]
                tri = sf.triangles[ti]
                u, v = math.sqrt(rng.random()), rng.random()
                weights = [1 - u, u * (1 - v), u * v]
                root = sum(
                    (
                        sf.positions[j] * w
                        for j, w in zip(tri.vertices, weights, strict=True)
                    ),
                    Vector(),
                )
                uv = sum(
                    (sf.uvs[j] * w for j, w in zip(tri.loops, weights, strict=True)),
                    Vector(),
                )
                resolved, _ = sf.sample(uv)
                if (resolved - root).length > 1e-5:
                    fail("Root UV is ambiguous; use non-overlapping UVs")
                identifier = (
                    slots[growth_fields.region_key(region.name)] * 100000
                    + (20001 if kind == "child" else 1)
                    + n
                )
                placements.append(
                    (
                        kind,
                        n,
                        uv,
                        None,
                        False,
                        identifier,
                        slots[growth_fields.region_key(region.name)],
                        rng,
                    )
                )
        for authored_row in region.rows:
            for mirrored in [False, True] if authored_row.mirror else [False]:
                slot = slots[
                    growth_fields.slot_key(region.name, authored_row.name, mirrored)
                ]
                for n, uv in enumerate(
                    growth_fields.row_uvs(
                        sf, regions[region.name], authored_row, mirrored
                    )
                ):
                    identifier = slot * 100000 + 1 + n
                    rng = random.Random(f"{spec.seed}:{slot}:guide:{n}")
                    placements.append(
                        ("guide", n, uv, authored_row, mirrored, identifier, slot, rng)
                    )
        new_guides = sum(item[0] == "guide" for item in placements)
        new_roots = region.children or new_guides
        if len(guides) + new_guides > 10000 or len(roots) + new_roots > 50000:
            fail("System exceeds10000 guides or50000 evaluated curves", "growth_limit")
        for kind, n, uv, row, mirrored, identifier, slot, rng in placements:
            root, normal, ti, x, length_scale = growth_fields.frame(
                sf, uv, regions[region.name], region, row, mirrored
            )
            y = normal.cross(x)
            f = families[row.family] if row and row.family else family
            old = previous.get(identifier)
            edit = changed.pop(identifier, None)
            if old and old["family"] in families and old["edited"]:
                f = families[old["family"]]
            if edit and edit.family:
                if edit.family not in families:
                    fail("Guide edit references missing family")
                f = families[edit.family]
            points = [
                list(
                    root
                    + (x * p[0] + y * p[1] + normal * p[2]) * f.length * length_scale
                )
                for p in f.shape
            ]
            edited = bool(edit and edit.family)
            if old and old["edited"]:
                delta = root - Vector(old["points"][0])
                points = [list(Vector(p) + delta) for p in old["points"]]
                edited = True
            if edit:
                if edit.points is not None:
                    points = edit.points
                    edited = True
                if edit.length_scale is not None:
                    points = [
                        list(root + (Vector(p) - root) * edit.length_scale)
                        for p in points
                    ]
                    edited = True
                if (Vector(points[0]) - root).length > 1e-5:
                    fail(
                        "Edited guide first point must equal its attached "
                        "surface-local "
                        "root"
                    )
                if any(
                    (Vector(a) - Vector(b)).length < 1e-7
                    for a, b in zip(points[:-1], points[1:], strict=True)
                ):
                    fail("Guide has coincident points")
            variation = 1 + rng.uniform(-1, 1) * f.length_variation
            record: dict[str, Any] = dict(
                id=identifier,
                region=ri,
                family=family_index[f.name],
                root=list(root),
                normal=list(normal),
                flow=list(x),
                uv=list(uv)[:2],
                triangle=ti,
                group=slot,
                row=region.rows.index(row) if row else -1,
                sequence=n if row else -1,
                mirrored=mirrored,
                order=row.order if row else 0,
                overlap=int(bool(row and row.overlap == "over_previous")),
                offset=f.offset,
                radius=f.radius,
                tip=f.tip_radius,
                roll=f.roll,
                layer=row.layer if row and row.layer is not None else f.layer,
                variation=variation,
                points=points,
                edited=edited,
            )
            if kind == "guide":
                guides.append(record)
                if not region.children:
                    roots.append(record.copy())
            else:
                roots.append(record)
    if changed:
        fail("Guide edit references an unknown stable root ID")
    if len(guides) > 10000 or len(roots) > 50000:
        fail("System exceeds10000 guides or50000 evaluated curves", "growth_limit")
    if sum(len(r["points"]) for r in guides) > 320000:
        fail("Guide point budget exceeded")
    maxima: dict[int, int] = {}
    for record in guides:
        maxima[record["group"]] = max(
            maxima.get(record["group"], 0), len(record["points"])
        )
    if sum(maxima[r["group"]] for r in roots) > 800000:
        fail("Interpolated guide point budget exceeds 800000", "growth_limit")
    counts: dict[int, int] = {}
    for record in roots:
        counts[record["family"]] = counts.get(record["family"], 0) + 1
    resources(spec, counts=counts)
    return guides, roots


def attributes(data: Any, rows: list[dict[str, Any]], domain: str) -> None:
    for key, kind in [
        ("id", "INT"),
        ("region", "INT"),
        ("row", "INT"),
        ("sequence", "INT"),
        ("mirrored", "BOOLEAN"),
        ("order", "INT"),
        ("overlap", "INT"),
        ("family", "INT"),
        ("root", "FLOAT_VECTOR"),
        ("normal", "FLOAT_VECTOR"),
        ("flow", "FLOAT_VECTOR"),
        ("triangle", "INT"),
        ("offset", "FLOAT"),
        ("radius", "FLOAT"),
        ("tip", "FLOAT"),
        ("roll", "FLOAT"),
        ("layer", "INT"),
        ("variation", "FLOAT"),
    ]:
        put(
            data,
            "growth_" + ("root_id" if key == "id" else key),
            kind,
            domain,
            [r[key] for r in rows],
        )
    put(data, "surface_uv_coordinate", "FLOAT2", domain, [r["uv"] for r in rows])


def stage(
    spec: GrowthCreateArguments,
    surface: Any,
    guides: list[dict[str, Any]],
    roots: list[dict[str, Any]],
    materials: list[Any],
    prototypes: list[Any],
    owner_id: str,
) -> tuple[Any, Any, Any]:
    old_rest = surface.add_rest_position_attribute
    surface.add_rest_position_attribute = True
    surface.update_tag()
    data = None
    carrier = None
    mesh = None
    group = None
    try:
        data = bpy.data.hair_curves.new(spec.name)
        data.add_curves([len(r["points"]) for r in guides])
        data.set_types(type="POLY")
        data.position_data.foreach_set(
            "vector", [x for r in guides for p in r["points"] for x in p]
        )
        data.surface = surface
        data.surface_uv_map = spec.uv_map
        data[KEY] = owner_id
        attributes(data, guides, "CURVE")
        put(data, "guide_up", "FLOAT_VECTOR", "CURVE", [r["normal"] for r in guides])
        put(data, "guide_group", "INT", "CURVE", [r["group"] for r in guides])
        put(data, "growth_edited", "BOOLEAN", "CURVE", [r["edited"] for r in guides])
        put(
            data,
            "growth_pin",
            "FLOAT",
            "POINT",
            [1.0 if i == 0 else 0.0 for r in guides for i in range(len(r["points"]))],
        )
        mesh = bpy.data.meshes.new(spec.name + " Roots")
        mesh.from_pydata([r["root"] for r in roots], [], [])
        mesh[KEY] = owner_id
        attributes(mesh, roots, "POINT")
        put(mesh, "growth_group", "INT", "POINT", [r["group"] for r in roots])
        carrier = bpy.data.objects.new(spec.name + " Roots", mesh)
        carrier[KEY] = owner_id
        carrier.hide_render = True
        carrier.parent = surface
        bpy.context.scene.collection.objects.link(carrier)
        carrier.hide_set(True)
        group = growth_nodes.build(spec, carrier, materials, prototypes, owner_id)
        return data, carrier, group
    except BaseException:
        surface.add_rest_position_attribute = old_rest
        surface.update_tag()
        if group:
            bpy.data.node_groups.remove(group)
        if carrier:
            bpy.data.objects.remove(carrier, do_unlink=True)
        if mesh and not mesh.users:
            bpy.data.meshes.remove(mesh)
        if data and not data.users:
            bpy.data.hair_curves.remove(data)
        raise


def dispose(data: Any, carrier: Any, group: Any) -> None:
    mesh = carrier.data
    bpy.data.objects.remove(carrier, do_unlink=True)
    if not mesh.users:
        bpy.data.meshes.remove(mesh)
    if not data.users:
        bpy.data.hair_curves.remove(data)
    if not group.users:
        bpy.data.node_groups.remove(group)


def metadata(
    spec: GrowthCreateArguments,
    surface: Any,
    data: Any,
    carrier: Any,
    slots: dict[str, int],
    owner_id: str,
) -> str:
    return json.dumps(
        dict(
            id=owner_id,
            spec=spec.model_dump(mode="json"),
            slots=slots,
            topology=curves.topology(surface.data),
            uv_hash=uv_signature(surface, spec.uv_map),
            rest_hash=rest_signature(surface),
            guides_hash=data_signature(data),
            roots_hash=data_signature(carrier.data),
        ),
        separators=(",", ":"),
    )


def create(args: GrowthCreateArguments) -> GrowthDelta:
    if bpy.data.objects.get(args.name):
        fail("Choose an unused growth object name")
    surface, mats, protos, _, _ = resources(args)
    slots = {
        key: i
        for i, key in enumerate(
            [growth_fields.region_key(r.name) for r in args.regions]
            + growth_fields.keys(args.regions)
        )
    }
    guides, roots = plan(args, surface, slots, {}, [])
    owner_id = uuid4().hex
    old_rest = surface.add_rest_position_attribute
    data, carrier, group = stage(args, surface, guides, roots, mats, protos, owner_id)
    obj = None
    try:
        obj = bpy.data.objects.new(args.name, data)
        obj.parent = surface
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = Matrix.Identity(4)
        mod = obj.modifiers.new(growth_nodes.MODIFIER, "NODES")
        mod.node_group = group
        obj[KEY] = metadata(args, surface, data, carrier, slots, owner_id)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        return GrowthDelta(
            summary=summary(
                obj,
                json.loads(obj[KEY]),
                carrier,
                group,
                GrowthInspectArguments(object_name=obj.name, qa_samples=0),
            ),
            created_guides=len(guides),
            families_changed=len(args.families),
        )
    except BaseException:
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
        dispose(data, carrier, group)
        surface.add_rest_position_attribute = old_rest
        surface.update_tag()
        raise


def configure(args: GrowthConfigureArguments) -> GrowthDelta:
    from . import growth_dynamics, growth_layers

    if growth_layers.KEY in bpy.data.objects.get(args.object_name, {}):
        fail(
            "Clear layer correction through growth.layers.clear before growth revision"
        )
    if growth_dynamics.KEY in bpy.data.objects.get(args.object_name, {}):
        fail("Clear dynamics through growth.dynamics.clear before growth revision")
    obj, meta, carrier, group = owned(args.object_name)
    if group.users != 1:
        fail("Owned growth graph has external users; remove those references first")
    warnings = validity(obj, meta)
    if warnings and not args.rebind:
        fail(warnings[0], "growth_attachment_invalid")
    old = GrowthCreateArguments.model_validate(meta["spec"])
    values = old.model_dump()
    values["name"] = obj.name
    if obj.data.surface:
        values["surface"] = obj.data.surface.name
    for name in ["families", "regions", "neighbors", "seed"]:
        value = getattr(args, name)
        if value is not None:
            values[name] = (
                [x.model_dump() for x in value] if isinstance(value, list) else value
            )
    spec = GrowthCreateArguments.model_validate(values)
    old_regions = {r.name: r for r in old.regions}
    rebound = (
        args.rebind
        or spec.seed != old.seed
        or any(
            r.name not in old_regions or r.selector != old_regions[r.name].selector
            for r in spec.regions
        )
    )
    if rebound and not args.rebind:
        fail("Changed seed/region selection requires explicit rebind=true")
    slots = dict(meta["slots"])
    next_slot = max(slots.values(), default=-1) + 1
    for key in [
        growth_fields.region_key(r.name) for r in spec.regions
    ] + growth_fields.keys(spec.regions):
        if rebound or key not in slots:
            slots[key] = next_slot
            next_slot += 1
    if next_slot > 21000:
        fail("Stable root identity range exhausted; create a new system")
    surface, mats, protos, _, _ = resources(spec, obj)
    previous = {} if rebound else old_guides(obj, meta)
    guides, roots = plan(spec, surface, slots, previous, args.guides)
    old_rest = surface.add_rest_position_attribute
    data, new_carrier, new_group = stage(
        spec, surface, guides, roots, mats, protos, meta["id"]
    )
    old_data = obj.data
    old_meta = obj[KEY]
    mod = obj.modifiers[0]
    try:
        obj.data = data
        mod.node_group = new_group
        obj[KEY] = metadata(spec, surface, data, new_carrier, slots, meta["id"])
        bpy.context.view_layer.update()
        result = summary(
            obj,
            json.loads(obj[KEY]),
            new_carrier,
            new_group,
            GrowthInspectArguments(object_name=obj.name, qa_samples=0),
        )
    except BaseException:
        obj.data = old_data
        mod.node_group = group
        obj[KEY] = old_meta
        dispose(data, new_carrier, new_group)
        surface.add_rest_position_attribute = old_rest
        surface.update_tag()
        bpy.context.view_layer.update()
        raise
    old_count = len(old_data.curves)
    dispose(old_data, carrier, group)
    return GrowthDelta(
        summary=result,
        created_guides=max(0, len(guides) - old_count),
        removed_guides=max(0, old_count - len(guides)),
        updated_guides=len(args.guides) if args.guides else min(len(guides), old_count),
        roots_rebound=rebound,
        families_changed=len(args.families or []),
    )


@contextmanager
def evaluated_path(obj: Any, group: Any, *, evaluated_points: bool = False) -> Any:
    graph = growth_nodes.GrowthGraph("Growth QA")
    clone = None
    try:
        n = graph.node("GeometryNodeGroup")
        n.node_tree = group
        graph.wire(graph.input.outputs["Geometry"], n.inputs["Geometry"])
        graph.wire(n.outputs["Path"], graph.output.inputs["Geometry"])
        clone = obj.copy()
        clone.data = obj.data.copy()
        for m in list(clone.modifiers):
            clone.modifiers.remove(m)
        m = clone.modifiers.new("Growth QA", "NODES")
        m.node_group = graph.group
        bpy.context.scene.collection.objects.link(clone)
        clone.hide_set(True)
        clone.hide_render = True
        bpy.context.view_layer.update()
        if evaluated_points:
            # Surface deformation needs the native Hair Curves self-object context.
            # Sample its evaluated Path from a separate temporary mesh carrier.
            with evaluated_path_vertices(clone) as data:
                yield data
        else:
            evaluated = clone.evaluated_get(bpy.context.evaluated_depsgraph_get())
            yield evaluated.data
    finally:
        if clone:
            clone_data = clone.data
            bpy.data.objects.remove(clone, do_unlink=True)
            if not clone_data.users:
                bpy.data.hair_curves.remove(clone_data)
        bpy.data.node_groups.remove(graph.group)


@contextmanager
def evaluated_path_vertices(source: Any) -> Any:
    graph = growth_nodes.GrowthGraph("Evaluated curve samples")
    carrier = clone = evaluated = None
    try:
        path = graph.info(source, "Native evaluated paths", "ORIGINAL").outputs[
            "Geometry"
        ]
        path = graph.put(
            path,
            "growth_evaluated_length",
            graph.node("GeometryNodeSplineLength").outputs["Length"],
            "FLOAT",
            "CURVE",
        )
        points = graph.node("GeometryNodeCurveToPoints")
        points.mode = "EVALUATED"
        graph.wire(path, points.inputs["Curve"])
        path = points.outputs["Points"]
        for name, socket in [("normal", "Normal"), ("tangent", "Tangent")]:
            path = graph.put(
                path,
                "growth_frame_" + name,
                points.outputs[socket],
                "FLOAT_VECTOR",
                "POINT",
            )
        vertices = graph.node("GeometryNodePointsToVertices")
        graph.wire(path, vertices.inputs["Points"])
        graph.wire(vertices.outputs["Mesh"], graph.output.inputs["Geometry"])
        carrier = bpy.data.meshes.new("Evaluated curve samples")
        clone = bpy.data.objects.new("Evaluated curve samples", carrier)
        clone.matrix_world = source.matrix_world.copy()
        modifier = clone.modifiers.new("Evaluated curve samples", "NODES")
        modifier.node_group = graph.group
        bpy.context.scene.collection.objects.link(clone)
        clone.hide_set(True)
        clone.hide_render = True
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = clone.evaluated_get(depsgraph)
        yield evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()
        if clone:
            bpy.data.objects.remove(clone, do_unlink=True)
        if carrier and not carrier.users:
            bpy.data.meshes.remove(carrier)
        bpy.data.node_groups.remove(graph.group)


def summary(
    obj: Any,
    meta: dict[str, Any],
    carrier: Any,
    group: Any,
    args: GrowthInspectArguments,
) -> GrowthSummary:
    from .growth_qa import inspect_qa

    spec = GrowthCreateArguments.model_validate(meta["spec"])
    warnings = validity(obj, meta)
    from . import growth_dynamics, growth_layers
    from .dynamics_models import DynamicsObjectArguments
    from .growth_layers_models import LayerObjectArguments

    if growth_layers.KEY in obj:
        warnings.extend(
            growth_layers.inspect(LayerObjectArguments(object_name=obj.name)).issues
        )
    if growth_dynamics.KEY in obj:
        warnings.extend(
            growth_dynamics.inspect(
                DynamicsObjectArguments(object_name=obj.name)
            ).issues
        )
    count = len(carrier.data.vertices)
    counts: dict[int, int] = {}
    for item in carrier.data.attributes["growth_family"].data:
        counts[item.value] = counts.get(item.value, 0) + 1
    vertices = instances = 0
    try:
        _, _, _, vertices, instances = resources(spec, obj, counts)
    except OperationError as e:
        warnings.append(str(e))
    points = 0
    qa = GrowthQA()
    if not warnings:
        with evaluated_path(obj, group) as data:
            if not isinstance(data, bpy.types.Curves) or len(data.curves) != count:
                fail(
                    "Native evaluated curve count differs from owned roots",
                    "growth_evaluation_failed",
                )
            points = len(data.points)
            if len(data.points) > 800000:
                fail("Evaluated growth exceeds800000 points")
            if args.qa_samples or args.template_samples:
                qa = inspect_qa(obj, data, args)
    else:
        points = sum(
            counts.get(i, 0) * len(f.shape) for i, f in enumerate(spec.families)
        )
    return GrowthSummary(
        object_name=obj.name,
        system_id=meta["id"],
        surface=obj.data.surface.name if obj.data.surface else spec.surface,
        uv_map=spec.uv_map,
        guides=len(obj.data.curves),
        generated_curves=sum(r.children for r in spec.regions),
        evaluated_curves=count,
        evaluated_points=points,
        regions=[r.name for r in spec.regions],
        families=[f.name for f in spec.families],
        templates=[f.template.object_name for f in spec.families if f.template],
        node_count=len(group.nodes),
        valid=not warnings,
        invalid_roots=count if warnings else 0,
        warnings=warnings,
        equivalent_template_vertices=vertices,
        instance_count=instances,
        qa=qa,
    )


def inspect(args: GrowthInspectArguments) -> GrowthInspectResult:
    obj, meta, carrier, group = owned(args.object_name)
    s = summary(obj, meta, carrier, group, args)
    spec = GrowthCreateArguments.model_validate(meta["spec"])
    rows = []
    end = min(len(obj.data.curves), args.guide_offset + args.guide_limit)
    for i in range(args.guide_offset, end):

        def a(n: str, index: int = i) -> Any:
            return obj.data.attributes[n].data[index]

        rows.append(
            GrowthGuide(
                root_id=a("growth_root_id").value,
                region=spec.regions[a("growth_region").value].name,
                family=spec.families[a("growth_family").value].name,
                uv=list(a("surface_uv_coordinate").vector),
                points=[list(p.position) for p in obj.data.curves[i].points]
                if args.include_points
                else None,
                row=(
                    spec.regions[a("growth_region").value]
                    .rows[a("growth_row").value]
                    .name
                )
                if a("growth_row").value >= 0
                else None,
                sequence=a("growth_sequence").value
                if a("growth_row").value >= 0
                else None,
                mirrored=a("growth_mirrored").value,
            )
        )
    return GrowthInspectResult(
        summary=s,
        guides=rows,
        next_guide_offset=end
        if args.guide_limit and end < len(obj.data.curves)
        else None,
        recipe=spec if args.include_recipe else None,
        rows=row_summaries(obj, spec) if args.include_rows else [],
        field_samples=field_samples(obj, spec, args),
    )


def row_summaries(obj: Any, spec: GrowthCreateArguments) -> list[GrowthRowSummary]:
    buckets: dict[tuple[int, int, bool], list[Any]] = {}
    attrs = obj.data.attributes
    for i, curve in enumerate(obj.data.curves):
        ri = attrs["growth_row"].data[i].value
        if ri < 0:
            continue
        key = (
            attrs["growth_region"].data[i].value,
            ri,
            bool(attrs["growth_mirrored"].data[i].value),
        )
        buckets.setdefault(key, []).append(curve.points[0].position.copy())
    result = []
    for (region_index, row_index, mirrored), points in buckets.items():
        region = spec.regions[region_index]
        row = region.rows[row_index]
        family = next(
            f for f in spec.families if f.name == (row.family or region.family)
        )
        spacings = [
            (b - a).length for a, b in zip(points[:-1], points[1:], strict=True)
        ]
        result.append(
            GrowthRowSummary(
                region=region.name,
                row=row.name,
                mirrored=mirrored,
                count=len(points),
                layer=row.layer if row.layer is not None else family.layer,
                order=row.order,
                overlap=row.overlap,
                minimum_spacing=min(spacings, default=None),
                maximum_spacing=max(spacings, default=None),
            )
        )
    return result


def field_samples(
    obj: Any, spec: GrowthCreateArguments, args: GrowthInspectArguments
) -> list[GrowthFieldSample]:
    if not args.field_samples:
        return []
    sf = Surface(obj.data.surface, obj.data.surface.data, spec.uv_map)
    faces = region_faces(obj.data.surface, spec)
    regions = {r.name: r for r in spec.regions}
    result = []
    for query in args.field_samples:
        if query.region not in regions:
            fail("Field sample references an unknown region")
        p, n, ti, flow, scale = growth_fields.frame(
            sf, Vector((*query.uv, 0)), faces[query.region], regions[query.region]
        )
        result.append(
            GrowthFieldSample(
                region=query.region,
                uv=query.uv,
                position=list(p),
                normal=list(n),
                direction=list(flow),
                length_scale=scale,
                face=sf.triangles[ti].polygon_index,
            )
        )
    return result


def remove(args: GrowthRemoveArguments) -> GrowthRemoveResult:
    obj, _, carrier, group = owned(args.object_name)
    from . import growth_dynamics, growth_layers

    if growth_layers.KEY in obj:
        fail("Clear layer correction through growth.layers.clear before removal")
    if growth_dynamics.KEY in obj:
        fail("Clear dynamics through growth.dynamics.clear before removal")
    # Owned resources must not silently delete external references or shared data.
    users = bpy.data.user_map()
    allowed = {obj, carrier, group, bpy.context.scene, *bpy.data.collections}
    for target in [obj, carrier, group, obj.data, carrier.data]:
        if any(u not in allowed for u in users.get(target, set())):
            fail("Growth resources have external users; remove those references first")
    data = obj.data
    name = obj.name
    bpy.data.objects.remove(obj, do_unlink=True)
    dispose(data, carrier, group)
    return GrowthRemoveResult(object_name=name, removed=True)


def evaluation_dependencies(obj: Any) -> list[Any]:
    _, meta, carrier, group = owned(obj.name)
    warnings = validity(obj, meta)
    if warnings:
        fail(warnings[0], "growth_attachment_invalid")
    spec = GrowthCreateArguments.model_validate(meta["spec"])
    surface, _, prototypes, _, _ = resources(spec, obj)
    return [surface, carrier, *(p for p in prototypes if p is not None)]
