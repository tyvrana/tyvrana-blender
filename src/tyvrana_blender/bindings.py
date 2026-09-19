"""Saved native identities and bounded existence/structure observations."""

import hashlib
import json
import re
from typing import Any, Literal
from uuid import uuid4

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import (
    ResourceInspectionRequest,
    ResourceInspectionResult,
    ResourceObservation,
)

from .binding_models import ProjectBindArguments, ProjectBindResult
from .errors import OperationError

DOCUMENT_KEY = "_tyvrana_document_id"
RESOURCE_KEY = "_tyvrana_resource_id"
SCOPE = (
    "Structural summary only: object type/local transform/parent identity, "
    "data counts/bounds/modifier kinds, material node types/links, collection "
    "membership; managed reference observations/registration and "
    "derived-landmark freshness. Excludes full geometry, shader values, "
    "rig/animation internals "
    "and external edits outside this scope. Caps: 64 modifiers,256 nodes,512 "
    "links,4096 members; total counts are included. Existence is not content "
    "validation."
)


def _marker() -> Any:
    markers = [text for text in bpy.data.texts if DOCUMENT_KEY in text]
    if len(markers) > 1:
        raise OperationError(
            "project_identity_ambiguous", "Multiple saved document identity markers"
        )
    return markers[0] if markers else None


def project_id() -> str | None:
    marker = _marker()
    if marker is None:
        return None
    value = marker.get(DOCUMENT_KEY)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise OperationError(
            "project_identity_invalid", "Saved document identity is invalid"
        )
    return value


def _collection(kind: str) -> Any:
    return {
        "object": bpy.data.objects,
        "material": bpy.data.materials,
        "collection": bpy.data.collections,
    }.get(kind)


def _fingerprint(kind: str, resource: Any) -> str:
    data: dict[str, Any] = {"kind": kind}
    if kind == "object":
        from .construction import fingerprint

        construction = fingerprint(resource)
        if construction is not None:
            data["construction"] = construction
        data.update(
            type=resource.type,
            transform=[float(v) for row in resource.matrix_local for v in row],
            parent=resource.parent.get(RESOURCE_KEY) if resource.parent else None,
            bounds=[list(p) for p in resource.bound_box],
            modifiers=[
                (m.type, m.show_viewport, m.show_render)
                for m in resource.modifiers[:64]
            ],
        )
        data["modifier_count"] = len(resource.modifiers)
        if resource.type == "MESH":
            data["counts"] = [
                len(resource.data.vertices),
                len(resource.data.edges),
                len(resource.data.polygons),
            ]
    elif kind == "material" and resource.node_tree:
        data["counts"] = [len(resource.node_tree.nodes), len(resource.node_tree.links)]
        data["nodes"] = sorted(n.bl_idname for n in resource.node_tree.nodes[:256])
        data["links"] = sorted(
            (
                link.from_node.bl_idname,
                link.from_socket.identifier,
                link.to_node.bl_idname,
                link.to_socket.identifier,
            )
            for link in resource.node_tree.links[:512]
        )
    elif kind == "collection":
        data["object_count"] = len(resource.objects)
        data["objects"] = sorted(
            o.get(RESOURCE_KEY, "unbound:" + o.name) for o in resource.objects[:4096]
        )
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def inspect(arguments: ResourceInspectionRequest) -> ResourceInspectionResult:
    current = project_id()
    if current != arguments.project_id:
        raise OperationError(
            "project_identity_mismatch",
            "Opened document differs from the requested saved identity",
            {"current_project_id": current},
        )
    observations = []
    indexes: dict[str, dict[str, list[Any]]] = {}
    for kind in {r.resource_kind for r in arguments.resources}:
        collection = _collection(kind)
        if collection is not None:
            index: dict[str, list[Any]] = {}
            for resource in collection:
                identifier = resource.get(RESOURCE_KEY)
                if isinstance(identifier, str):
                    index.setdefault(identifier, []).append(resource)
            indexes[kind] = index
    for reference in arguments.resources:
        if reference.resource_kind not in indexes:
            observations.append(
                ResourceObservation(**reference.model_dump(), state="unsupported")
            )
            continue
        matches = indexes[reference.resource_kind].get(reference.resource_id, [])
        state: Literal["present", "missing", "ambiguous"] = (
            "present" if len(matches) == 1 else "ambiguous" if matches else "missing"
        )
        observations.append(
            ResourceObservation(
                **reference.model_dump(),
                state=state,
                name=matches[0].name if len(matches) == 1 else None,
                fingerprint=_fingerprint(reference.resource_kind, matches[0])
                if len(matches) == 1
                else None,
            )
        )
    return ResourceInspectionResult(
        project_id=arguments.project_id, resources=observations, fingerprint_scope=SCOPE
    )


def bind(arguments: ProjectBindArguments) -> ProjectBindResult:
    if bpy.context.mode != "OBJECT":
        raise OperationError(
            "invalid_context", "Identity establishment requires Object Mode"
        )
    marker = _marker()
    current = project_id()
    selected = []
    for target in arguments.resources:
        collection = _collection(target.resource_kind)
        resource = collection.get(target.name)
        if resource is None or resource.library is not None:
            raise OperationError(
                "resource_unavailable",
                "Identity establishment requires an existing local resource",
                {"resource_kind": target.resource_kind, "name": target.name},
            )
        previous = resource.get(RESOURCE_KEY)
        if previous is not None and (
            not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{32}", previous)
        ):
            raise OperationError(
                "resource_identity_invalid", "Existing resource identity is invalid"
            )
        if (
            previous
            and not arguments.renew_resource_ids
            and sum(r.get(RESOURCE_KEY) == previous for r in collection) > 1
        ):
            raise OperationError(
                "resource_identity_ambiguous",
                "Duplicated resource identity; create/rebind a distinct resource "
                "instead of choosing by name",
            )
        selected.append(
            (target, resource, previous, _fingerprint(target.resource_kind, resource))
        )
    created_marker = marker is None
    changed = (
        created_marker
        or arguments.fork_project
        or arguments.renew_resource_ids
        or any(not p for _, _, p, _ in selected)
    )
    assigned = []
    try:
        if marker is None:
            marker = bpy.data.texts.new("Tyvrana Project Identity")
            marker.use_fake_user = True
        identifier = (
            uuid4().hex if created_marker or arguments.fork_project else current
        )
        assert identifier is not None
        marker[DOCUMENT_KEY] = identifier
        observations = []
        for target, resource, previous, fingerprint in selected:
            if previous is None or arguments.renew_resource_ids:
                resource[RESOURCE_KEY] = uuid4().hex
                assigned.append((resource, previous))
            observations.append(
                ResourceObservation(
                    resource_kind=target.resource_kind,
                    resource_id=resource[RESOURCE_KEY],
                    state="present",
                    name=resource.name,
                    fingerprint=fingerprint,
                )
            )
        return ProjectBindResult(
            project_id=identifier,
            filepath=str(bpy.data.filepath) or None,
            resources=observations,
            save_required=changed
            or bool(bpy.data.is_dirty)
            or not bool(bpy.data.is_saved),
        )
    except Exception:
        for resource, previous in assigned:
            if previous is None:
                del resource[RESOURCE_KEY]
            else:
                resource[RESOURCE_KEY] = previous
        if created_marker and marker is not None:
            bpy.data.texts.remove(marker)
        elif marker is not None and current:
            marker[DOCUMENT_KEY] = current
        raise
