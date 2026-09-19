"""Native dependency inventory; read-only and conservative about unknown caches."""

import hashlib
import time
from collections import Counter
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import actions, files, organization
from .delivery_models import DeliveryDependency, FileAuditArguments, FileAuditResult
from .errors import OperationError


def audit(args: FileAuditArguments) -> FileAuditResult:
    organization.idle()
    started = time.perf_counter()
    document = files.inspect()
    rows: list[DeliveryDependency] = []
    paths: set[str] = set()
    hashed = 0

    def add(**values: Any) -> None:
        if len(rows) >= args.max_resources:
            raise OperationError(
                "delivery_limit", "Dependency inventory exceeds max_resources"
            )
        rows.append(DeliveryDependency(**values))

    def external(
        kind: str, name: str, raw: str, library: Any = None, **values: Any
    ) -> None:
        path = Path(bpy.path.abspath(raw, library=library))
        resolved = str(path)
        paths.add(resolved)
        try:
            size = path.stat().st_size if path.is_file() else None
            status = "present" if size is not None else "missing"
            add(
                kind=kind,
                name=name,
                filepath=resolved,
                status=status,
                byte_size=size,
                **values,
            )
        except OSError as exc:
            add(
                kind=kind,
                name=name,
                filepath=resolved,
                status="unverified",
                detail=str(exc)[:300],
            )

    add(
        kind="document",
        name="Current document",
        filepath=document.filepath,
        status="present"
        if document.is_saved and document.exists and not document.is_dirty
        else "unsaved",
        byte_size=document.byte_size,
    )
    for image in bpy.data.images:
        if image.source == "VIEWER" or not image.users:
            continue
        if image.packed_file or image.packed_files:
            add(
                kind="image",
                name=image.name,
                status="unsaved" if image.is_dirty else "packed",
                detail="Live pixels changed since persistence"
                if image.is_dirty
                else None,
            )
        elif image.source == "GENERATED":
            add(
                kind="image",
                name=image.name,
                status="unsaved",
                detail="Pack or persist generated pixels before delivery",
            )
        elif image.source == "TILED":
            for tile in image.tiles:
                external(
                    "image",
                    f"{image.name}:{tile.number}",
                    image.filepath.replace("<UDIM>", str(tile.number)),
                    image.library,
                )
        elif image.source == "SEQUENCE":
            add(
                kind="image",
                name=image.name,
                status="unverified",
                filepath=image.filepath,
                detail="Sequence coverage requires explicit required_files",
            )
        else:
            external("image", image.name, image.filepath, image.library)
            if image.is_dirty:
                add(
                    kind="image",
                    name=image.name,
                    status="unsaved",
                    detail="Live pixels differ from the external file",
                )
    for library in bpy.data.libraries:
        external("library", library.name, library.filepath, library.parent)
    for raw in sorted(set(bpy.utils.blend_paths(absolute=True, packed=False))):
        if raw not in paths:
            external("external", Path(raw).name, raw)
    for action in bpy.data.actions:
        status, detail = "embedded", None
        if not action.users:
            status, detail = (
                "unsaved",
                "Unused action without a fake user may be omitted on save",
            )
        elif actions.KEY in action:
            try:
                actions.validate(action)
            except OperationError as exc:
                status, detail = "unverified", exc.error.message
        add(kind="action", name=action.name, status=status, detail=detail)
    for name in args.required_actions:
        if name not in bpy.data.actions:
            add(
                kind="action",
                name=name,
                status="missing",
                detail="Required action is absent",
            )
    from . import growth, growth_dynamics
    from .dynamics_models import DynamicsObjectArguments

    for obj in bpy.data.objects:
        if growth_dynamics.KEY in obj and growth.KEY in obj:
            try:
                owned_cache = growth_dynamics.inspect(
                    DynamicsObjectArguments(object_name=obj.name)
                )
                add(
                    kind="cache",
                    name=obj.name,
                    status="embedded"
                    if owned_cache.valid and owned_cache.cached
                    else "unverified",
                    detail="; ".join(owned_cache.issues) or None,
                )
            except OperationError as exc:
                add(
                    kind="cache",
                    name=obj.name,
                    status="unverified",
                    detail=exc.error.message,
                )
        for modifier in obj.modifiers:
            cache = getattr(modifier, "point_cache", None)
            if cache is not None:
                add(
                    kind="cache",
                    name=f"{obj.name}/{modifier.name}",
                    status="embedded"
                    if cache.is_baked and not cache.use_disk_cache
                    else "unverified",
                    detail="External/unbaked cache coverage must be verified explicitly"
                    if not cache.is_baked or cache.use_disk_cache
                    else None,
                )
            if modifier.type == "FLUID" and modifier.domain_settings:
                add(
                    kind="cache",
                    name=f"{obj.name}/{modifier.name}",
                    status="unverified",
                    filepath=bpy.path.abspath(modifier.domain_settings.cache_directory),
                    detail="Fluid cache coverage is not certified; "
                    "declare required cache files",
                )
            if modifier.type == "NODES" and modifier.node_group:
                if any(
                    n.bl_idname == "GeometryNodeSimulationOutput"
                    for n in modifier.node_group.nodes
                ):
                    add(
                        kind="cache",
                        name=f"{obj.name}/{modifier.name}",
                        status="unverified",
                        detail="Native simulation-zone bake coverage is not certified",
                    )
    for required in args.required_files:
        external("required_file", Path(required.filepath).name, required.filepath)
        row = rows[-1]
        if row.status != "present":
            continue
        if (row.byte_size or 0) < required.minimum_bytes:
            rows[-1] = row.model_copy(
                update={
                    "status": "unverified",
                    "detail": "File is smaller than minimum_bytes",
                }
            )
        if required.sha256:
            if hashed + (row.byte_size or 0) > args.max_hash_bytes:
                raise OperationError(
                    "delivery_limit", "Required file checksums exceed max_hash_bytes"
                )
            digest = hashlib.sha256()
            assert row.filepath is not None
            with Path(row.filepath).open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    hashed += len(block)
                    if hashed > args.max_hash_bytes:
                        raise OperationError(
                            "delivery_limit", "Required file grew beyond max_hash_bytes"
                        )
                    digest.update(block)
            if digest.hexdigest() != required.sha256:
                rows[-1] = row.model_copy(
                    update={
                        "status": "unverified",
                        "detail": "SHA-256 differs from the required digest",
                    }
                )
    bad = {"missing", "unsaved", "unverified"}
    issues = sum(row.status in bad for row in rows)
    ordered = sorted(rows, key=lambda r: (r.status not in bad, r.kind, r.name))
    return FileAuditResult(
        document=document,
        ready=issues == 0,
        dependency_count=len(rows),
        counts=dict(Counter(r.status for r in rows)),
        issues=issues,
        dependencies=ordered[: args.limit],
        truncated=len(rows) > args.limit,
        hashed_bytes=hashed,
        processing_seconds=time.perf_counter() - started,
        limitations=[
            "Presence/persistence audit, not visual, motion "
            "or continuous-cache acceptance.",
            "Linked paths are checked; linked files are not recursively opened.",
            "Unknown simulation/sequence coverage is reported as unverified; "
            "a directory alone is not proof.",
            "Required output artifacts are verified only when explicitly listed.",
        ],
    )
