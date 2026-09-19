"""Selection-independent collection and coherent object-set transactions."""

import math
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]

from .bindings import RESOURCE_KEY
from .errors import OperationError
from .inspection import page
from .organization_models import (
    CollectionConfigureArguments,
    CollectionCreateArguments,
    CollectionInspectArguments,
    CollectionInspectResult,
    CollectionRemoveArguments,
    CollectionResult,
    CollectionSummary,
    CopyMember,
    CreatedObject,
    DataInfo,
    EmptyMember,
    ExistingRef,
    HierarchyInfo,
    LocalRef,
    Member,
    MembershipInfo,
    MetadataInfo,
    ObjectRef,
    ObjectSetConfigureArguments,
    ObjectSetCreateArguments,
    ObjectSetCreateResult,
    ObjectSetInspectArguments,
    ObjectSetInspectResult,
    ObjectSetRemoveArguments,
    ObjectSetResult,
    OrganizationRemoveResult,
    PrimitiveMember,
    SetObjectSummary,
    TransformInfo,
    VisibilityInfo,
)

ROLE = "tyvrana_organization_role"
TAGS = "tyvrana_organization_tags"
MAX_COLLECTIONS = 4096
MAX_OBJECTS = 100_000
MAX_VERTICES = 250_000


def fail(message: str) -> None:
    raise OperationError("organization_invalid", message)


def idle(*, mutate: bool = False) -> None:
    from .blender import data_mutation_context, main_thread

    main_thread()
    if bpy.context.mode != "OBJECT" or bpy.app.is_job_running("RENDER"):
        fail("Organization operations require Object Mode outside rendering")
    if mutate:
        data_mutation_context()
    if (
        len(bpy.data.collections) > MAX_COLLECTIONS
        or len(bpy.data.objects) > MAX_OBJECTS
    ):
        fail("Organization scope exceeds 4096 collections or 100000 objects")
    bpy.context.view_layer.update()


def descendants(root: Any) -> set[Any]:
    seen: set[Any] = set()
    pending = [root]
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        pending.extend(item.children)
    return seen


def collections() -> set[Any]:
    return descendants(bpy.context.scene.collection)


def collection_named(name: str | None) -> Any:
    if name is None:
        return bpy.context.scene.collection
    item = bpy.data.collections.get(name)
    if item is None or item not in collections():
        fail(f'Collection "{name}" does not exist in the current scene')
    return item


def object_named(name: str) -> Any:
    obj = bpy.context.scene.objects.get(name)
    if obj is None:
        fail(f'Object "{name}" does not exist in the current scene')
    return obj


def editable(item: Any) -> None:
    if not item.is_editable or item.library or item.override_library:
        fail(f'"{item.name}" must be local, editable and not a library override')


def object_editable(obj: Any) -> None:
    editable(obj)
    if any(scene != bpy.context.scene for scene in obj.users_scene):
        fail(f'Object "{obj.name}" is shared with another scene')
    if any(c not in collections() for c in obj.users_collection):
        fail(f'Object "{obj.name}" belongs to a collection outside the current scene')


def parents(item: Any) -> list[Any]:
    return [c for c in collections() if item.name in c.children]


def collection_editable(item: Any) -> None:
    editable(item)
    scene = bpy.context.scene
    if any(
        other != scene and item in descendants(other.collection)
        for other in bpy.data.scenes
    ):
        fail(f'Collection "{item.name}" is shared with another scene')
    users = bpy.data.user_map(subset={item}).get(item, set())
    allowed = collections() | {scene}
    if users - allowed:
        fail(f'Collection "{item.name}" has external users or collection instances')


def collection_label(item: Any) -> str | None:
    return None if item == bpy.context.scene.collection else str(item.name)


def named_available(name: str, store: Any, current: Any = None) -> None:
    if len(name.encode("utf-8")) > 255 or "\x00" in name:
        fail("Datablock names must fit 255 UTF-8 bytes without NUL")
    found = store.get(name)
    if found is not None and found != current:
        fail(f'Name "{name}" already exists; automatic suffixing is not allowed')


def ordered(graph: dict[Any, set[Any]]) -> list[Any]:
    """Stable dependency order, bounded by the caller's graph size."""
    result: list[Any] = []
    remaining = dict(graph)
    while remaining:
        ready = [
            key for key, deps in remaining.items() if not (deps & remaining.keys())
        ]
        if not ready:
            fail("Relationship dependency cycle detected")
        result.extend(ready)
        for key in ready:
            del remaining[key]
    return result


def collection_summary(item: Any) -> CollectionSummary:
    p = sorted((collection_label(x) for x in parents(item)), key=lambda x: x or "")
    children = sorted(str(x.name) for x in item.children)
    return CollectionSummary(
        name=str(item.name),
        parents=p[:16],
        parent_count=len(p),
        parents_truncated=len(p) > 16,
        children=children[:16],
        child_count=len(children),
        children_truncated=len(children) > 16,
        object_count=len(item.objects),
        recursive_object_count=len(item.all_objects),
        hide_viewport=bool(item.hide_viewport),
        hide_render=bool(item.hide_render),
        hide_select=bool(item.hide_select),
        editable=bool(
            item.is_editable and not item.library and not item.override_library
        ),
    )


def collection_inspect(args: CollectionInspectArguments) -> CollectionInspectResult:
    idle()
    root = collection_named(args.root)
    scope = descendants(root) if args.recursive else set(root.children)
    if args.root is None:
        scope.discard(root)
    else:
        scope.add(root)
    selected, info = page(scope, args, lambda c: str(c.name))
    return CollectionInspectResult(
        collections=[collection_summary(c) for c in selected], page=info
    )


def collection_create(args: CollectionCreateArguments) -> CollectionResult:
    idle(mutate=True)
    specs = {s.name: s for s in args.collections}
    graph: dict[str, set[str]] = {}
    for s in args.collections:
        named_available(s.name, bpy.data.collections)
        graph[s.name] = {p for p in s.parents if p in specs}
        for name in s.parents:
            if name not in specs:
                collection_editable(collection_named(name))
    order = ordered(graph)
    created: dict[str, Any] = {}
    try:
        for name in order:
            c = bpy.data.collections.new(name)
            created[name] = c
            s = specs[name]
            for field in ("hide_viewport", "hide_render", "hide_select"):
                setattr(c, field, getattr(s, field))
        for name in order:
            for p in specs[name].parents:
                parent = created[p] if p in created else collection_named(p)
                parent.children.link(created[name])
        bpy.context.view_layer.update()
        return CollectionResult(
            collections=[collection_summary(created[s.name]) for s in args.collections]
        )
    except Exception:
        for c in reversed(list(created.values())):
            bpy.data.collections.remove(c)
        raise


def collection_configure(args: CollectionConfigureArguments) -> CollectionResult:
    idle(mutate=True)
    items = {p.name: collection_named(p.name) for p in args.collections}
    snapshots = {
        c: (str(c.name), parents(c), c.hide_viewport, c.hide_render, c.hide_select)
        for c in items.values()
    }
    graph = {c: set(parents(c)) for c in collections()}
    desired: dict[Any, list[Any]] = {}
    new_names: list[str] = []
    for p in args.collections:
        c = items[p.name]
        collection_editable(c)
        if p.rename is not None:
            named_available(p.rename, bpy.data.collections, c)
        new_names.append(p.rename or p.name)
        if p.parents is not None:
            targets = [collection_named(n) for n in p.parents]
            for target in targets:
                collection_editable(target)
            desired[c] = targets
            graph[c] = set(targets)
    if len(set(new_names)) != len(new_names):
        fail("Renamed collections must have unique names")
    order = ordered(graph)
    try:
        for c in desired:
            for parent in snapshots[c][1]:
                parent.children.unlink(c)
        for c in order:
            for parent in desired.get(c, []):
                parent.children.link(c)
        for p in args.collections:
            c = items[p.name]
            for field in ("hide_viewport", "hide_render", "hide_select"):
                if field in p.model_fields_set:
                    setattr(c, field, getattr(p, field))
            if p.rename is not None:
                c.name = p.rename
        bpy.context.view_layer.update()
        return CollectionResult(
            collections=[collection_summary(items[p.name]) for p in args.collections]
        )
    except Exception:
        # Detached collections need global pointer-based parent lookup during rollback.
        all_parents = list(bpy.data.collections) + [
            s.collection for s in bpy.data.scenes
        ]
        for c in desired:
            for parent in all_parents:
                if c.name in parent.children:
                    parent.children.unlink(c)
        for c, (name, old_parents, hv, hr, hs) in snapshots.items():
            c.name = name
            c.hide_viewport, c.hide_render, c.hide_select = hv, hr, hs
            if c in desired:
                for parent in old_parents:
                    parent.children.link(c)
        bpy.context.view_layer.update()
        raise


def collection_remove(args: CollectionRemoveArguments) -> OrganizationRemoveResult:
    idle(mutate=True)
    c = collection_named(args.name)
    collection_editable(c)
    if args.mode == "empty" and (c.objects or c.children):
        fail("Collection is not empty; explicitly rehome its members and children")
    target = collection_named(args.target)
    collection_editable(target)
    if target in descendants(c):
        fail("Cannot rehome a collection into itself or its descendants")
    for obj in c.objects:
        object_editable(obj)
    for child in c.children:
        collection_editable(child)
    added_objects: list[Any] = []
    added_children: list[Any] = []
    try:
        for obj in c.objects:
            if obj.name not in target.objects:
                target.objects.link(obj)
                added_objects.append(obj)
        for child in c.children:
            if child.name not in target.children:
                target.children.link(child)
                added_children.append(child)
        bpy.data.collections.remove(c)
    except Exception as exc:
        for obj in added_objects:
            target.objects.unlink(obj)
        for child in added_children:
            target.children.unlink(child)
        return OrganizationRemoveResult(
            deleted=[],
            remaining=[args.name],
            error=f"Native collection deletion failed ({type(exc).__name__})",
        )
    bpy.context.view_layer.update()
    return OrganizationRemoveResult(deleted=[args.name], remaining=[])


def simple_transform(obj: Any) -> None:
    if (
        tuple(obj.delta_location) != (0, 0, 0)
        or tuple(obj.delta_rotation_euler) != (0, 0, 0)
        or tuple(obj.delta_rotation_quaternion) != (1, 0, 0, 0)
        or tuple(obj.delta_scale) != (1, 1, 1)
    ):
        fail(f'Object "{obj.name}" requires identity delta transforms')
    if (
        obj.parent_type != "OBJECT"
        or obj.constraints
        or obj.animation_data
        or obj.rigid_body
    ):
        fail(
            f'Object "{obj.name}" requires plain, unanimated object '
            f"parenting without constraints or rigid body"
        )


def matrix(obj: Any) -> Any:
    value = obj.matrix_world.copy()
    if (
        any(not math.isfinite(v) or abs(v) > 1e12 for row in value for v in row)
        or abs(value.to_3x3().determinant()) < 1e-12
    ):
        fail(f'Object "{obj.name}" requires a finite invertible world transform')
    return value


def copyable(obj: Any) -> None:
    object_editable(obj)
    simple_transform(obj)
    if (
        obj.type not in {"MESH", "EMPTY", "LIGHT", "CAMERA"}
        or obj.modifiers
        or obj.instance_type != "NONE"
    ):
        fail(
            f'Object "{obj.name}" is not a plain mesh, empty, light or '
            f"camera copy source"
        )
    if obj.type == "EMPTY" and obj.data is not None:
        fail("Image empties must use the typed reference operations")
    if any(str(k).startswith("tyvrana_") and k not in {ROLE, TAGS} for k in obj.keys()):
        fail(
            f'Object "{obj.name}" has reserved adapter ownership; use '
            f"its domain operations"
        )
    if obj.data is not None:
        editable(obj.data)
        if obj.data.animation_data or getattr(obj.data, "shape_keys", None):
            fail("Copy sources must not contain animation or shape keys")
    if len(obj.material_slots) > 16:
        fail("Copy sources are limited to 16 material slots")


def reference(ref: ObjectRef, created: dict[str, Any]) -> Any:
    return created[ref.key] if isinstance(ref, LocalRef) else object_named(ref.name)


def metadata(obj: Any, role: str | None, tags: list[str]) -> None:
    if role is None:
        if ROLE in obj:
            del obj[ROLE]
    else:
        obj[ROLE] = role
    if tags:
        obj[TAGS] = tags
    elif TAGS in obj:
        del obj[TAGS]


def mesh_new(spec: PrimitiveMember, resources: list[Any]) -> Any:
    mesh = bpy.data.meshes.new(spec.name)
    resources.append(mesh)
    bm = bmesh.new()
    try:
        if spec.primitive == "cube":
            bmesh.ops.create_cube(bm, size=2)
        elif spec.primitive == "plane":
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1)
        elif spec.primitive == "uv_sphere":
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1)
        else:
            bmesh.ops.create_cone(
                bm,
                cap_ends=True,
                cap_tris=False,
                segments=32,
                radius1=1,
                radius2=1,
                depth=2,
            )
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    if spec.material is not None:
        mesh.materials.append(bpy.data.materials[spec.material])
    return mesh


def apply_member(obj: Any, spec: Member, parent: Any) -> None:
    obj.parent = parent
    obj.parent_type = "OBJECT"
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.delta_location = (0, 0, 0)
    obj.delta_rotation_euler = (0, 0, 0)
    obj.delta_rotation_quaternion = (1, 0, 0, 0)
    obj.delta_scale = (1, 1, 1)
    obj.rotation_mode = "XYZ"
    obj.location = spec.location
    obj.rotation_euler = spec.rotation
    obj.scale = spec.scale
    metadata(obj, spec.role, spec.tags)


def object_set_create(args: ObjectSetCreateArguments) -> ObjectSetCreateResult:
    idle(mutate=True)
    specs = {s.key: s for s in args.objects}
    graph: dict[str, set[str]] = {}
    targets: dict[str, list[Any]] = {}
    for s in args.objects:
        named_available(s.name, bpy.data.objects)
        targets[s.key] = [
            collection_named(n) for n in (s.collections or args.collections)
        ]
        for c in targets[s.key]:
            collection_editable(c)
        refs = [s.parent]
        if isinstance(s, CopyMember):
            refs.append(s.source)
            if isinstance(s.source, ExistingRef):
                copyable(object_named(s.source.name))
        if isinstance(s, PrimitiveMember) and s.material is not None:
            mat = bpy.data.materials.get(s.material)
            if mat is None:
                fail(f'Material "{s.material}" does not exist')
        graph[s.key] = {r.key for r in refs if isinstance(r, LocalRef)}
        if isinstance(s.parent, ExistingRef):
            parent = object_named(s.parent.name)
            object_editable(parent)
            matrix(parent)
    order = ordered(graph)
    # Budget all independent copies, including chains of request-local copies.
    costs: dict[str, int] = {}
    total = 0
    for key in order:
        spec = specs[key]
        if isinstance(spec, CopyMember):
            if isinstance(spec.source, LocalRef):
                cost = costs[spec.source.key]
            else:
                src = object_named(spec.source.name)
                cost = len(src.data.vertices) if src.type == "MESH" else 0
            costs[key] = cost
            total += cost if spec.data == "independent" else 0
        else:
            costs[key] = 512 if isinstance(spec, PrimitiveMember) else 0
            total += costs[key]
    if total > MAX_VERTICES:
        fail("Batch allocation exceeds the 250000 mesh vertex budget")
    created: dict[str, Any] = {}
    resources: list[Any] = []
    try:
        for key in order:
            spec = specs[key]
            if isinstance(spec, CopyMember):
                source = reference(spec.source, created)
                # Request-local sources were constructed by this operation.
                obj = source.copy()
                created[key] = obj
                obj.name = spec.name
                if obj.data is not None and spec.data == "independent":
                    copied_data = source.data.copy()
                    resources.append(copied_data)
                    obj.data = copied_data
                if spec.materials == "independent":
                    copies: dict[Any, Any] = {}
                    for i, slot in enumerate(obj.material_slots):
                        material = slot.material
                        if material is not None:
                            if material not in copies:
                                copies[material] = material.copy()
                                resources.append(copies[material])
                            slot.link = "OBJECT"
                            slot.material = copies[material]
                            # Keep independent mesh slots independent as well.
                            if obj.type == "MESH":
                                obj.data.materials[i] = copies[material]
            else:
                data = (
                    mesh_new(spec, resources)
                    if isinstance(spec, PrimitiveMember)
                    else None
                )
                obj = bpy.data.objects.new(spec.name, data)
                created[key] = obj
                if isinstance(spec, EmptyMember):
                    obj.empty_display_type = "PLAIN_AXES"
                    obj.empty_display_size = spec.display_size
            apply_member(
                obj, spec, reference(spec.parent, created) if spec.parent else None
            )
            if obj.name != spec.name:
                fail(f'Blender could not preserve requested name "{spec.name}"')
        # Publish only after all native construction has succeeded.
        for key in order:
            for c in targets[key]:
                c.objects.link(created[key])
        bpy.context.view_layer.update()
        for obj in created.values():
            matrix(obj)
        return ObjectSetCreateResult(
            objects=[
                CreatedObject(
                    key=s.key,
                    name=str(created[s.key].name),
                    type=str(created[s.key].type),
                    parent=str(created[s.key].parent.name)
                    if created[s.key].parent
                    else None,
                )
                for s in args.objects
            ]
        )
    except Exception:
        for obj in reversed(list(created.values())):
            bpy.data.objects.remove(obj, do_unlink=True)
        # Only resources allocated by this attempt; never purge unrelated orphans.
        if resources:
            bpy.data.batch_remove(ids=set(resources))
        bpy.context.view_layer.update()
        raise


def metadata_info(obj: Any) -> MetadataInfo:
    role = obj.get(ROLE)
    raw = obj.get(TAGS, [])
    valid = role is None or isinstance(role, str) and 0 < len(role) <= 48
    tags: list[str] = []
    try:
        valid = valid and not isinstance(raw, str) and len(raw) <= 16
        tags = [str(v)[:48] for v in list(raw)[:16]]
        valid = valid and all(isinstance(v, str) and len(v) <= 48 for v in raw)
    except TypeError:
        valid = False
    return MetadataInfo(
        role=role[:48] if isinstance(role, str) else None, tags=tags, valid=valid
    )


def object_summary(obj: Any, fields: list[str]) -> SetObjectSummary:
    data: dict[str, Any] = {"name": str(obj.name), "type": str(obj.type)}
    if "visibility" in fields:
        data["visibility"] = VisibilityInfo(
            hide_viewport=obj.hide_viewport,
            hide_render=obj.hide_render,
            hide_select=obj.hide_select,
            visible_in_view_layer=obj.visible_get(),
        )
    if "hierarchy" in fields:
        children = sorted(str(c.name) for c in obj.children)
        data["hierarchy"] = HierarchyInfo(
            parent=str(obj.parent.name) if obj.parent else None,
            parent_type=str(obj.parent_type),
            children=children[:16],
            child_count=len(children),
            children_truncated=len(children) > 16,
        )
    if "memberships" in fields:
        names = sorted(
            (collection_label(c) for c in obj.users_collection), key=lambda n: n or ""
        )
        data["memberships"] = MembershipInfo(
            collections=names[:16], count=len(names), truncated=len(names) > 16
        )
    if "transforms" in fields:
        world = obj.matrix_world
        if any(not math.isfinite(v) for row in world for v in row):
            fail(f'Object "{obj.name}" has a non-finite transform')
        data["transforms"] = TransformInfo(
            world_matrix=[list(row) for row in world],
            location=list(obj.location),
            rotation=list(obj.rotation_euler),
            scale=list(obj.scale),
        )
    if "metadata" in fields:
        data["metadata"] = metadata_info(obj)
    if "data" in fields:
        materials = [
            str(s.material.name) if s.material else None
            for s in obj.material_slots[:16]
        ]
        data["data"] = DataInfo(
            name=str(obj.data.name) if obj.data else None,
            users=int(obj.data.users) if obj.data else 0,
            library_linked=bool(obj.data and obj.data.library),
            materials=materials,
            material_count=len(obj.material_slots),
            materials_truncated=len(obj.material_slots) > 16,
        )
    return SetObjectSummary(**data)


def object_set_inspect(args: ObjectSetInspectArguments) -> ObjectSetInspectResult:
    idle()
    c = collection_named(args.collection)
    scope = c.all_objects if args.recursive else c.objects
    filtered = [
        obj
        for obj in scope
        if (args.role is None or obj.get(ROLE) == args.role)
        and set(args.tags) <= set(metadata_info(obj).tags)
    ]
    selected, info = page(filtered, args, lambda o: str(o.name))
    return ObjectSetInspectResult(
        objects=[object_summary(o, list(args.fields)) for o in selected], page=info
    )


def set_parent(obj: Any, parent: Any, world: Any) -> None:
    obj.parent = parent
    obj.parent_type = "OBJECT"
    # Keep the exact world affine matrix even under rotated nonuniform scale.
    obj.matrix_parent_inverse = (
        parent.matrix_world.inverted() @ world @ obj.matrix_basis.inverted()
        if parent
        else Matrix.Identity(4)
    )
    if parent is None:
        obj.matrix_world = world


def object_set_configure(args: ObjectSetConfigureArguments) -> ObjectSetResult:
    idle(mutate=True)
    items = {p.name: object_named(p.name) for p in args.objects}
    snapshots: dict[Any, Any] = {}
    target_parents: dict[Any, Any] = {}
    targets: dict[Any, list[Any]] = {}
    names: list[str] = []
    graph = {
        obj: {obj.parent} if obj.parent else set() for obj in bpy.context.scene.objects
    }
    for p in args.objects:
        obj = items[p.name]
        object_editable(obj)
        metadata_only = p.model_fields_set <= {
            "name",
            "role",
            "tags",
            "collections",
            "hide_viewport",
            "hide_render",
            "hide_select",
        }
        if not metadata_only and any(
            str(k).startswith(("tyvrana_", "_tyvrana_"))
            and k not in {ROLE, TAGS, RESOURCE_KEY}
            for k in obj.keys()
        ):
            fail(
                f'Object "{obj.name}" is owned; only membership, visibility, role/tag '
                f"patches are allowed here; use its typed operations for other changes"
            )
        if p.rename is not None:
            named_available(p.rename, bpy.data.objects, obj)
        names.append(p.rename or p.name)
        if "parent" in p.model_fields_set:
            simple_transform(obj)
            matrix(obj)
            if abs(obj.matrix_basis.to_3x3().determinant()) < 1e-12:
                fail("Cannot preserve an object with a singular basis")
            parent = object_named(p.parent) if p.parent else None
            if parent:
                object_editable(parent)
                matrix(parent)
            target_parents[obj] = parent
            graph[obj] = {parent} if parent else set()
        if p.collections is not None:
            targets[obj] = [collection_named(n) for n in p.collections]
            for c in targets[obj]:
                collection_editable(c)
        snapshots[obj] = (
            str(obj.name),
            obj.parent,
            obj.matrix_parent_inverse.copy(),
            obj.matrix_basis.copy(),
            obj.matrix_world.copy(),
            list(obj.users_collection),
            obj.get(ROLE),
            list(obj.get(TAGS, [])),
            (obj.hide_viewport, obj.hide_render, obj.hide_select),
        )
    if len(set(names)) != len(names):
        fail("Renamed objects must have unique names")
    order = ordered(graph)
    try:
        # Detach first so a valid reversal never forms a transient cycle.
        for obj in target_parents:
            obj.parent = None
        bpy.context.view_layer.update()
        for obj in order:
            if obj in target_parents:
                set_parent(obj, target_parents[obj], snapshots[obj][4])
                bpy.context.view_layer.update()
        for p in args.objects:
            obj = items[p.name]
            if obj in targets:
                for c in targets[obj]:
                    if obj.name not in c.objects:
                        c.objects.link(obj)
                for c in list(obj.users_collection):
                    if c not in targets[obj]:
                        c.objects.unlink(obj)
            if p.rename is not None:
                obj.name = p.rename
            for attr in ("hide_viewport", "hide_render", "hide_select"):
                if getattr(p, attr) is not None:
                    setattr(obj, attr, getattr(p, attr))
            if "role" in p.model_fields_set or "tags" in p.model_fields_set:
                metadata(
                    obj,
                    p.role if "role" in p.model_fields_set else snapshots[obj][6],
                    p.tags if p.tags is not None else snapshots[obj][7],
                )
        bpy.context.view_layer.update()
        for obj in target_parents:
            error = max(
                abs(obj.matrix_world[i][j] - snapshots[obj][4][i][j])
                for i in range(4)
                for j in range(4)
            )
            if error > 1e-5 * max(
                1, max(abs(v) for row in snapshots[obj][4] for v in row)
            ):
                fail(
                    "World transform preservation is not representable "
                    "at native precision"
                )
        return ObjectSetResult(
            objects=[
                object_summary(
                    items[p.name],
                    ["hierarchy", "memberships", "metadata", "visibility"],
                )
                for p in args.objects
            ]
        )
    except Exception:
        for obj in target_parents:
            obj.parent = None
        for obj, (
            name,
            parent,
            inverse,
            basis,
            _world,
            members,
            role,
            tags,
            visibility,
        ) in snapshots.items():
            obj.name = name
            obj.parent = parent
            obj.matrix_parent_inverse = inverse
            obj.matrix_basis = basis
            for c in members:
                if obj.name not in c.objects:
                    c.objects.link(obj)
            for c in list(obj.users_collection):
                if c not in members:
                    c.objects.unlink(obj)
            metadata(obj, role, tags)
            obj.hide_viewport, obj.hide_render, obj.hide_select = visibility
        bpy.context.view_layer.update()
        raise


def remove_object(obj: Any) -> None:
    bpy.data.objects.remove(obj, do_unlink=True)


def object_set_remove(args: ObjectSetRemoveArguments) -> OrganizationRemoveResult:
    idle(mutate=True)
    items = [object_named(n) for n in args.names]
    deleting = set(items)
    children = {c for obj in items for c in obj.children if c not in deleting}
    if children and args.children == "reject":
        fail("Objects have surviving children; explicitly request unparenting")
    for obj in items:
        object_editable(obj)
        if any(
            str(k).startswith("tyvrana_") and k not in {ROLE, TAGS} for k in obj.keys()
        ):
            fail("Domain-owned objects require their typed removal operation")
    for child in children:
        object_editable(child)
        simple_transform(child)
        matrix(child)
    users = bpy.data.user_map(subset=deleting)
    allowed = deleting | collections() | {bpy.context.scene}
    for obj in items:
        external = users.get(obj, set()) - allowed
        # A child is safe only when the parent pointer is its sole reference.
        for child in external & children:
            if (
                child.constraints
                or child.modifiers
                or child.animation_data
                or child.keys()
            ):
                fail("Surviving children have additional references or metadata")
        if external - children:
            fail(f'Object "{obj.name}" has surviving external dependencies')
    snapshots = {
        c: (
            c.parent,
            c.matrix_parent_inverse.copy(),
            c.matrix_basis.copy(),
            c.matrix_world.copy(),
        )
        for c in children
    }
    try:
        for child, (_, _, _, world) in snapshots.items():
            set_parent(child, None, world)
        bpy.context.view_layer.update()
        for child, (_, _, _, world) in snapshots.items():
            if max(
                abs(child.matrix_world[i][j] - world[i][j])
                for i in range(4)
                for j in range(4)
            ) > 1e-5 * max(1, max(abs(v) for row in world for v in row)):
                fail(
                    "Unparenting would lose affine shear; retain the "
                    "parent or change its transform first"
                )
    except Exception:
        for child, (parent, inverse, basis, _) in snapshots.items():
            child.parent = parent
            child.matrix_parent_inverse = inverse
            child.matrix_basis = basis
        bpy.context.view_layer.update()
        raise
    parent_names = {c: str(values[0].name) for c, values in snapshots.items()}
    deleted: list[str] = []
    for obj, name in zip(items, args.names, strict=True):
        try:
            remove_object(obj)
            deleted.append(name)
        except Exception as exc:
            # Restore unparented children whose original parents still exist.
            for child, (parent, inverse, basis, _) in snapshots.items():
                if parent_names[child] not in deleted:
                    child.parent = parent
                    child.matrix_parent_inverse = inverse
                    child.matrix_basis = basis
            return OrganizationRemoveResult(
                deleted=deleted,
                remaining=args.names[len(deleted) :],
                error=f"Native object deletion failed ({type(exc).__name__})",
            )
    bpy.context.view_layer.update()
    return OrganizationRemoveResult(deleted=deleted, remaining=[])
