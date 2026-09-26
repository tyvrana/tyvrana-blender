"""Atomic constructive form ownership and bounded semantic regeneration."""

import json
import time
import uuid
from collections.abc import Callable, Generator
from typing import Any, cast

import bpy  # type: ignore[import-not-found]

from . import construction, organization, surfaces
from .bindings import RESOURCE_KEY
from .errors import OperationError
from .form_geometry import build_steps
from .form_models import (
    FormConfigureArguments,
    FormCreateArguments,
    FormEdit,
    FormInspectArguments,
    FormResult,
    FormSpec,
    FormSummary,
)

KEY = "tyvrana_form"


def metadata(obj: Any) -> dict[str, Any]:
    try:
        raw = obj[KEY]
        if not isinstance(raw, str) or len(raw) > 524288 or obj.type != "MESH":
            raise ValueError
        meta: dict[str, Any] = json.loads(raw)
        FormSpec.model_validate(meta["spec"])
        return meta
    except (KeyError, TypeError, ValueError) as exc:
        raise OperationError(
            "form_state_invalid", "Owned form metadata is missing or damaged"
        ) from exc


def state(
    spec: FormSpec,
    mesh: Any,
    stats: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> str:
    encoded = json.dumps(
        {
            "id": previous["id"] if previous else uuid.uuid4().hex,
            "revision": previous["revision"] + 1 if previous else 1,
            "spec": spec.model_dump(),
            "signature": surfaces.content_hash(mesh),
            **stats,
        }
    )
    if len(encoded) > 524288:
        raise OperationError("form_limit", "Owned form state exceeds512 KiB")
    return encoded


def summary(obj: Any, detail: bool = False) -> FormSummary:
    meta = metadata(obj)
    spec = FormSpec.model_validate(meta["spec"])
    issues = []
    if surfaces.content_hash(obj.data) != meta["signature"]:
        issues.append("Generated mesh changed; semantic regeneration is unavailable")
    for source in meta["sources"]:
        try:
            reference = construction.by_id(source["reference_id"])
            record, fresh = construction.current_registration(reference)
            if not fresh or any(
                record[k] != source[k]
                for k in ("source_sha256", "basis_sha256", "frame_sha256")
            ):
                raise ValueError
        except (OperationError, ValueError):
            issues.append(
                "Reference evidence changed; inspect and explicitly regenerate"
            )
            break
    points = [v.co for v in obj.data.vertices]
    return FormSummary(
        name=obj.name,
        component_id=meta["id"],
        revision=meta["revision"],
        vertex_count=len(points),
        face_count=len(obj.data.polygons),
        voxel_size=spec.voxel_size,
        sampled_voxels=meta["sampled_voxels"],
        bounds_min=[min(p[i] for p in points) for i in range(3)],
        bounds_max=[max(p[i] for p in points) for i in range(3)],
        parts=[p.id for p in spec.parts],
        source_count=len(meta["sources"]),
        valid=not issues,
        issues=issues,
        surface_fit=meta.get("surface_fit"),
        spec=spec.model_copy(update={"name": obj.name}) if detail else None,
    )


def revised(spec: FormSpec, edit: FormEdit) -> FormSpec:
    data = spec.model_dump()
    parts = {p["id"]: p for p in data["parts"]}
    for key in edit.remove_parts:
        if key not in parts:
            raise OperationError("form_part_unknown", f"Unknown form part: {key}")
        del parts[key]
    for part in edit.parts:
        parts[part.id] = part.model_dump()
    data["parts"] = list(parts.values())
    for key in (
        "voxel_size",
        "adaptivity",
        "smoothing_passes",
        "max_voxels",
        "max_vertices",
    ):
        if getattr(edit, key) is not None:
            data[key] = getattr(edit, key)
    if "surface_fit" in edit.model_fields_set:
        data["surface_fit"] = (
            edit.surface_fit.model_dump() if edit.surface_fit else None
        )
    return FormSpec.model_validate(data)


def create(arguments: FormCreateArguments) -> FormResult:
    steps = create_steps(arguments)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return cast(FormResult, done.value)


def create_steps(
    arguments: FormCreateArguments,
    before_publish: Callable[[], Generator[int, None, None]] | None = None,
) -> Generator[int, None, FormResult]:
    started = time.perf_counter()
    organization.idle(mutate=True)
    collections = [organization.collection_named(n) for n in arguments.collections]
    for collection in collections:
        organization.collection_editable(collection)
    for spec in arguments.forms:
        organization.named_available(spec.name, bpy.data.objects)
    prepared: list[tuple[FormSpec, Any, dict[str, Any]]] = []
    objects: list[Any] = []
    yield 0
    try:
        for spec in arguments.forms:
            steps = build_steps(spec)
            try:
                while True:
                    try:
                        next(steps)
                    except StopIteration as done:
                        mesh, stats = done.value
                        break
                    yield len(prepared)
            finally:
                steps.close()
            prepared.append((spec, mesh, stats))
            if sum(len(m.vertices) for _, m, _ in prepared) > 1048576:
                raise OperationError(
                    "form_limit", "Form batch exceeds1048576 output vertices"
                )
        if before_publish is not None:
            yield from before_publish()
        for spec, mesh, stats in prepared:
            obj = bpy.data.objects.new(spec.name, mesh)
            objects.append(obj)
            obj[KEY] = state(spec, mesh, stats)
            obj[RESOURCE_KEY] = uuid.uuid4().hex
            for collection in collections:
                collection.objects.link(obj)
        bpy.context.view_layer.update()
        return FormResult(
            forms=[summary(o) for o in objects],
            processing_seconds=time.perf_counter() - started,
        )
    except BaseException:
        for obj in reversed(objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for _, mesh, _ in prepared:
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
        raise


def configure(arguments: FormConfigureArguments) -> FormResult:
    steps = configure_steps(arguments)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return cast(FormResult, done.value)


def configure_steps(
    arguments: FormConfigureArguments,
    before_publish: Callable[[], Generator[int, None, None]] | None = None,
) -> Generator[int, None, FormResult]:
    started = time.perf_counter()
    organization.idle(mutate=True)
    pending = []
    for edit in arguments.forms:
        obj = organization.object_named(edit.name)
        organization.object_editable(obj)
        organization.editable(obj.data)
        meta = metadata(obj)
        if meta["revision"] != edit.expected_revision:
            raise OperationError(
                "form_revision_conflict", "Inspect current form revision before editing"
            )
        if surfaces.content_hash(obj.data) != meta["signature"]:
            raise OperationError(
                "form_geometry_changed",
                "Generated mesh was edited; preserve downstream work with mesh tools",
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
        }
        if (
            obj.data.users != 1
            or obj.data.shape_keys
            or obj.data.animation_data
            or obj.modifiers
            or obj.vertex_groups
            or obj.data.uv_layers
            or any(p.material_index for p in obj.data.polygons)
            or any(a.name not in allowed for a in obj.data.attributes)
        ):
            raise OperationError(
                "form_downstream_data",
                (
                    "Form regeneration changes connectivity; preserve shared data, "
                    "modifiers, groups, UVs and custom attributes"
                ),
            )
        spec = revised(FormSpec.model_validate(meta["spec"]), edit)
        pending.append((obj, spec, meta))
    FormCreateArguments(forms=[spec for _, spec, _ in pending])  # Combined work bound.
    prepared: list[tuple[Any, Any, FormSpec, dict[str, Any], dict[str, Any]]] = []
    committed: list[tuple[Any, Any, str]] = []
    yield 0
    try:
        for obj, spec, meta in pending:
            steps = build_steps(spec)
            try:
                while True:
                    try:
                        next(steps)
                    except StopIteration as done:
                        mesh, stats = done.value
                        break
                    yield len(prepared)
            finally:
                steps.close()
            prepared.append((obj, mesh, spec, stats, meta))
            if sum(len(m.vertices) for _, m, *_ in prepared) > 1048576:
                raise OperationError(
                    "form_limit", "Form batch exceeds1048576 output vertices"
                )
        if before_publish is not None:
            yield from before_publish()
        for obj, mesh, spec, stats, meta in prepared:
            committed.append((obj, obj.data, str(obj[KEY])))
            for material in obj.data.materials:
                mesh.materials.append(material)
            obj.data = mesh
            obj[KEY] = state(spec, mesh, stats, meta)
        bpy.context.view_layer.update()
        result = FormResult(
            forms=[summary(o) for o, *_ in prepared],
            processing_seconds=time.perf_counter() - started,
        )
    except BaseException:
        for obj, old, raw in reversed(committed):
            obj.data, obj[KEY] = old, raw
        for _, mesh, *_ in prepared:
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
        raise
    for _, old, _ in committed:
        bpy.data.meshes.remove(old)
    return result


def inspect(arguments: FormInspectArguments) -> FormResult:
    organization.idle()
    started = time.perf_counter()
    result = [
        summary(organization.object_named(n), arguments.include_spec)
        for n in arguments.names
    ]
    if (
        arguments.include_spec
        and sum(len(r.model_dump_json()) for r in result) > 524288
    ):
        raise OperationError(
            "form_limit", "Detailed form inspection exceeds512 KiB; select fewer forms"
        )
    return FormResult(forms=result, processing_seconds=time.perf_counter() - started)
