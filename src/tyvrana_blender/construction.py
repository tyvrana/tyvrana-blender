"""Persistent source observations and finite, application-side construction modes."""

import hashlib
import json
import math
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NoReturn, cast
from uuid import uuid4

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from . import references as refs
from .bindings import RESOURCE_KEY
from .construction_math import Equation, solve
from .errors import OperationError
from .inspection import page
from .reference_models import (
    ConstructionPointResult,
    ConstructionReport,
    DerivationProvenance,
    LandmarkDerivation,
    LandmarkDeriveArguments,
    LandmarkSetArguments,
    LandmarkSpec,
    NamedRemoveArguments,
    NamedRemoveResult,
    ObservationEvidence,
    ObservationInspectArguments,
    ObservationResult,
    ObservationSetArguments,
    ObservationSummary,
    ObservationWriteResult,
    ReferenceCalibrateArguments,
    ReferenceInspectArguments,
    ReferenceRegistration,
    RegistrationArguments,
    RegistrationResult,
    RegistrationSummary,
    SolveStatus,
)

STATE = "tyvrana_source_observations"
REGISTRATION = "tyvrana_reference_registration"
DERIVATION = "tyvrana_landmark_derivation"
MAX_OBSERVATIONS = 2048
MAX_BYTES = 2 * 1024 * 1024


def fail(code: str, message: str) -> NoReturn:
    raise OperationError(code, message)


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def load(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(value)
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
        return result
    except (ValueError, TypeError) as exc:
        raise OperationError(
            "construction_state_invalid", "Invalid saved construction data"
        ) from exc


def observation_store() -> dict[str, Any]:
    return load(bpy.context.scene.get(STATE, '{"revision":0,"observations":{}}'))


def observations() -> dict[str, Any]:
    return dict(observation_store()["observations"])


def write_observations(value: dict[str, Any]) -> None:
    text = encoded(
        {"revision": observation_store()["revision"] + 1, "observations": value}
    )
    if len(value) > MAX_OBSERVATIONS or len(text.encode()) > MAX_BYTES:
        fail(
            "construction_limit_exceeded",
            "Document observation limit is 2048 records / 2 MiB",
        )
    bpy.context.scene[STATE] = text


@contextmanager
def transaction(objects: list[Any]) -> Iterator[None]:
    """Restore only domain-owned changes, including newly created datum objects."""
    before = {o.name for o in bpy.data.objects}
    state = bpy.context.scene.get(STATE)
    saved = [
        (
            o,
            o.parent,
            o.matrix_parent_inverse.copy(),
            o.matrix_basis.copy(),
            float(o.empty_display_size),
            {
                k: o.get(k)
                for k in (
                    RESOURCE_KEY,
                    REGISTRATION,
                    DERIVATION,
                    refs.LANDMARK,
                    refs.LABEL,
                    refs.CATEGORY,
                )
            },
        )
        for o in objects
    ]
    try:
        yield
    except BaseException:
        for o in list(bpy.data.objects):
            if o.name not in before:
                bpy.data.objects.remove(o, do_unlink=True)
        for obj, parent, inverse, basis, size, props in saved:
            obj.parent, obj.matrix_parent_inverse, obj.matrix_basis = (
                parent,
                inverse,
                basis,
            )
            obj.empty_display_size = size
            for key, value in props.items():
                if value is None:
                    if key in obj:
                        del obj[key]
                else:
                    obj[key] = value
        if state is None:
            if STATE in bpy.context.scene:
                del bpy.context.scene[STATE]
        else:
            bpy.context.scene[STATE] = state
        bpy.context.view_layer.update()
        raise


def identity(obj: Any) -> str:
    value = obj.get(RESOURCE_KEY)
    if value is None:
        obj[RESOURCE_KEY] = value = uuid4().hex
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        fail("resource_identity_invalid", "Invalid native resource identity")
    if sum(o.get(RESOURCE_KEY) == value for o in bpy.data.objects) != 1:
        fail("resource_identity_ambiguous", "Duplicated native resource identity")
    return str(value)


def by_id(identifier: str) -> Any:
    matches = [
        o for o in bpy.context.scene.objects if o.get(RESOURCE_KEY) == identifier
    ]
    if len(matches) != 1:
        fail("stale_dependency", "Construction resource is missing or ambiguous")
    return matches[0]


def frame(name: str | None) -> Any:
    if name is None:
        return Matrix.Identity(4)
    obj = refs.object_named(name)
    if DERIVATION in obj:
        fail(
            "invalid_construction_frame",
            "Derived landmarks cannot define construction frames",
        )
    value = refs.matrix(obj)
    axes = [value.to_3x3().col[i] for i in range(3)]
    if (
        any(abs(a.length - 1) > 1e-5 for a in axes)
        or any(abs(axes[i].dot(axes[j])) > 1e-5 for i in range(3) for j in range(i))
        or value.to_3x3().determinant() < 0
    ):
        fail(
            "invalid_construction_frame",
            "Construction frame must be rigid, right-handed and unit-scale",
        )
    return value


def frame_basis(name: str | None) -> str:
    return digest([[float(v) for v in row] for row in frame(name)])


def source(obj: Any) -> dict[str, Any]:
    refs.dimensions(obj)
    image = obj.data
    if image.is_dirty or len(image.packed_files) != 1:
        fail(
            "source_image_unavailable",
            "Source observations require one unchanged packed static image;"
            " save/reimport edited image bytes explicitly",
        )
    return {
        "sha256": hashlib.sha256(image.packed_files[0].packed_file.data).hexdigest(),
        "dimensions": list(image.size),
        "aspect": list(image.display_aspect),
    }


def basis(obj: Any, source_state: dict[str, Any] | None = None) -> str:
    return digest(
        {
            "source": source_state if source_state is not None else source(obj),
            "transform": [[float(v) for v in row] for row in refs.matrix(obj)],
            "size": float(obj.empty_display_size),
            "offset": list(obj.empty_image_offset),
            "meters_per_unit": refs.units().meters_per_unit,
        }
    )


def current_registration(
    obj: Any, source_state: dict[str, Any] | None = None
) -> tuple[dict[str, Any], bool]:
    if REGISTRATION not in obj:
        fail(
            "registration_required",
            "Register a calibrated plane or orthographic source before "
            "deriving geometry",
        )
    record = load(obj[REGISTRATION])
    try:
        frame_obj = by_id(record["frame_id"]) if record["frame_id"] else None
        fresh = record["basis_sha256"] == basis(obj, source_state) and record[
            "frame_sha256"
        ] == frame_basis(frame_obj.name if frame_obj else None)
    except OperationError:
        fresh = False
    return record, fresh


def register(arguments: RegistrationArguments) -> RegistrationResult:
    refs.idle(mutate=True)
    targets = [
        refs.owned(s.reference, refs.REFERENCE, edit=True)
        for s in arguments.registrations
    ]
    frames = [refs.object_named(s.frame) for s in arguments.registrations if s.frame]
    if any(f in targets for f in frames):
        fail(
            "invalid_construction_frame",
            "A registered reference cannot be its own construction frame or"
            " another target in the batch",
        )
    for obj, spec in zip(targets, arguments.registrations, strict=True):
        if spec.projection == "perspective":
            fail(
                "unsupported_projection",
                "Perspective reconstruction requires camera calibration; "
                "only declared planes and orthographic projections are "
                "supported",
            )
        if obj.parent or obj.children:
            fail(
                "reference_context_invalid",
                "Registration requires unparented references without children",
            )
        frame(spec.frame)
        source(obj)
        for pixel in (spec.origin_pixel, spec.calibration.a, spec.calibration.b):
            refs.pixel_point(obj, pixel)
    with transaction(targets + frames):
        for obj, spec in zip(targets, arguments.registrations, strict=True):
            previous = load(obj.get(REGISTRATION, "{}"))
            horizontal = axis(spec.horizontal)
            vertical = axis(spec.vertical)
            normal = horizontal.cross(vertical)
            rotation = Matrix((horizontal, vertical, normal)).transposed().to_4x4()
            obj.matrix_world = frame(spec.frame) @ rotation
            bpy.context.view_layer.update()
            refs.calibrate(
                ReferenceCalibrateArguments(
                    name=obj.name,
                    a=spec.calibration.a,
                    b=spec.calibration.b,
                    target_distance=spec.calibration.distance,
                    unit=spec.calibration.unit,
                )
            )
            obj.location += frame(spec.frame) @ Vector(spec.origin) - refs.pixel_point(
                obj, spec.origin_pixel
            )
            bpy.context.view_layer.update()
            identity(obj)
            record = {
                "specification": spec.model_dump(),
                "reference_id": obj[RESOURCE_KEY],
                "revision": previous.get("revision", 0) + 1,
                "source_sha256": source(obj)["sha256"],
                "basis_sha256": basis(obj),
                "frame_id": identity(refs.object_named(spec.frame))
                if spec.frame
                else None,
                "frame_sha256": frame_basis(spec.frame),
            }
            obj[REGISTRATION] = encoded(record)
        return registration_inspect(
            ReferenceInspectArguments(names=[o.name for o in targets])
        )


def axis(name: str) -> Any:
    result = Vector((0.0, 0.0, 0.0))
    result["xyz".index(name[-1])] = -1.0 if name.startswith("-") else 1.0
    return result


def registration_inspect(arguments: ReferenceInspectArguments) -> RegistrationResult:
    refs.idle()
    selected, info = page(
        (
            o
            for o in bpy.context.scene.objects
            if o.get(refs.REFERENCE)
            and REGISTRATION in o
            and (arguments.image is None or o.data.name == arguments.image)
            and (
                arguments.category is None
                or o.get(refs.CATEGORY, "") == arguments.category
            )
            and (
                arguments.collection is None
                or any(c.name == arguments.collection for c in o.users_collection)
            )
        ),
        arguments,
        lambda o: str(o.name),
    )
    result = []
    for obj in selected:
        record, fresh = current_registration(obj)
        spec = ReferenceRegistration.model_validate(record["specification"])
        spec = spec.model_copy(update={"reference": obj.name})
        if fresh and record["frame_id"]:
            spec = spec.model_copy(update={"frame": by_id(record["frame_id"]).name})
        result.append(
            RegistrationSummary(
                **spec.model_dump(),
                reference_id=record["reference_id"],
                frame_id=record["frame_id"],
                revision=record["revision"],
                state="calibrated" if fresh else "stale",
                source_sha256=record["source_sha256"],
                basis_sha256=record["basis_sha256"],
            )
        )
    return RegistrationResult(registrations=result, page=info)


def set_observations(arguments: ObservationSetArguments) -> ObservationWriteResult:
    refs.idle(mutate=True)
    targets = {
        s.reference: refs.owned(s.reference, refs.REFERENCE, edit=True)
        for s in arguments.observations
    }
    sources = {name: source(obj) for name, obj in targets.items()}
    for spec in arguments.observations:
        size = sources[spec.reference]["dimensions"]
        if any(not 0 <= spec.pixel[i] <= size[i] for i in range(2)):
            fail(
                "observation_outside_source",
                "Pixel must lie inside the source image-edge bounds",
            )
        refs.pixel_point(targets[spec.reference], spec.pixel)
    values = observations()
    with transaction(list(targets.values())):
        for spec in arguments.observations:
            obj, image = targets[spec.reference], sources[spec.reference]
            values[spec.id] = {
                **spec.model_dump(),
                "revision": observation_store()["revision"] + 1,
                "reference_id": identity(obj),
                "source_sha256": image["sha256"],
                "source_dimensions": image["dimensions"],
                "source_label": str(obj.get(refs.SOURCE, "")),
            }
        write_observations(values)
    return ObservationWriteResult(
        count=len(arguments.observations), ids=[s.id for s in arguments.observations]
    )


def observation_state(
    record: dict[str, Any], cache: dict[str, Any]
) -> tuple[Any, bool]:
    obj = by_id(record["reference_id"])
    if obj.get(refs.REFERENCE) is not True:
        fail("stale_dependency", "Source is no longer a managed reference")
    if record["reference_id"] not in cache:
        cache[record["reference_id"]] = source(obj)
    image = cache[record["reference_id"]]
    return obj, image["sha256"] == record["source_sha256"] and image[
        "dimensions"
    ] == record["source_dimensions"]


def inspect_observations(arguments: ObservationInspectArguments) -> ObservationResult:
    refs.idle()
    values = observations()
    requested = (
        refs.owned(arguments.reference, refs.REFERENCE) if arguments.reference else None
    )
    rows = [
        r
        for r in values.values()
        if requested is None or r["reference_id"] == requested.get(RESOURCE_KEY)
    ]
    selected, info = page(rows, arguments, lambda r: str(r["id"]))
    result = []
    cache: dict[str, Any] = {}
    for record in selected:
        world = local = None
        try:
            obj, fresh = observation_state(record, cache)
            if fresh and arguments.mapped_points:
                point = refs.pixel_point(obj, record["pixel"])
                world = refs.xyz(point)
                local = refs.xyz(refs.matrix(obj).inverted() @ point)
        except OperationError:
            fresh = False
        result.append(
            ObservationSummary(
                **record, stale=not fresh, world_point=world, reference_local=local
            )
        )
    return ObservationResult(observations=result, page=info)


def remove_observations(arguments: NamedRemoveArguments) -> NamedRemoveResult:
    refs.idle(mutate=True)
    values = observations()
    if any(name not in values for name in arguments.names):
        fail("observation_not_found", "All observation IDs must exist")
    for name in arguments.names:
        del values[name]
    write_observations(values)
    return NamedRemoveResult(removed=arguments.names)


def landmark_basis(obj: Any) -> str:
    return digest(
        {
            "id": obj.get(RESOURCE_KEY),
            "matrix": [[float(v) for v in row] for row in refs.matrix(obj)],
            "derivation": obj.get(DERIVATION),
        }
    )


def freshness(
    obj: Any,
    *,
    depth: int = 0,
    context: tuple[dict[str, Any], dict[str, Any]] | None = None,
) -> bool:
    if DERIVATION not in obj:
        return True
    if depth > 8:
        return False
    try:
        record = DerivationProvenance.model_validate_json(obj[DERIVATION])
        if record.stale:
            return False
        f = by_id(record.frame_id).name if record.frame_id else None
        if (
            frame_basis(f) != record.frame_sha256
            or (obj.matrix_world.translation - Vector(record.world_point)).length > 1e-6
        ):
            return False
        current, cache = context if context is not None else (observations(), {})
        for evidence in record.observations:
            observation = current.get(evidence.id)
            if observation is None or observation["revision"] != evidence.revision:
                return False
            ref, fresh = observation_state(observation, cache)
            registration, registered = current_registration(
                ref, cache[evidence.reference_id]
            )
            if (
                not fresh
                or not registered
                or registration["revision"] != evidence.registration_revision
                or registration["basis_sha256"] != evidence.basis_sha256
            ):
                return False
        if record.reflected_landmark:
            other = refs.owned(record.reflected_landmark, refs.LANDMARK)
            if landmark_basis(other) != record.reflected_basis or not freshness(
                other, depth=depth + 1
            ):
                return False
        if record.specification.source.kind == "geometry":
            from .geometry_points import basis as geometry_basis

            source = refs.object_named(record.specification.source.source.object)
            if (
                source.get(RESOURCE_KEY) != record.geometry_resource_id
                or geometry_basis(source) != record.geometry_basis
            ):
                return False
        return True
    except (OperationError, ValueError, KeyError):
        return False


def derive_one(
    spec: LandmarkDerivation,
    arguments: LandmarkDeriveArguments,
    values: dict[str, Any],
    cache: dict[str, Any],
) -> tuple[ConstructionPointResult, DerivationProvenance | None]:
    construction = frame(arguments.frame)
    inverse = construction.inverted()
    f_id = identity(refs.object_named(arguments.frame)) if arguments.frame else None
    evidence: list[ObservationEvidence] = []
    reflected = reflected_basis = None
    geometry_id = geometry_hash = None
    if spec.source.kind == "geometry":
        from .geometry_points import basis as geometry_basis

        source = refs.object_named(spec.source.source.object)
        geometry_id = identity(source)
        geometry_hash = geometry_basis(source)
        point = inverse @ refs.resolve(spec.source.source)
        result = ConstructionPointResult(
            name=spec.name,
            status="solved",
            rank=3,
            residual=0,
            message="Authored geometry estimate; exact native shape basis, no "
            "physical-center inference",
        )
    elif spec.source.kind == "reflection":
        other = refs.owned(spec.source.landmark, refs.LANDMARK)
        if not refs.landmark_summary(other).valid:
            return ConstructionPointResult(
                name=spec.name,
                status="stale_dependency",
                rank=0,
                message="Reflected landmark is invalid/stale",
            ), None
        point = inverse @ other.matrix_world.translation
        component = "xyz".index(spec.source.axis)
        point[component] = 2 * spec.source.plane - point[component]
        reflected, reflected_basis = other.name, landmark_basis(other)
        result = ConstructionPointResult(
            name=spec.name,
            status="solved",
            rank=3,
            residual=0,
            message="Exact reflection; source uncertainty is not propagated",
        )
    else:
        equations: list[Equation] = []
        spans: list[tuple[str, int, int]] = []
        for identifier in spec.source.observations:
            observation = values.get(identifier)
            if observation is None:
                fail(
                    "observation_not_found",
                    f'Observation "{identifier}" does not exist',
                )
            try:
                obj, fresh = observation_state(observation, cache)
                registration, registered = current_registration(
                    obj, cache[observation["reference_id"]]
                )
            except OperationError as exc:
                status: SolveStatus = (
                    "registration_required"
                    if exc.error.code == "registration_required"
                    else "stale_dependency"
                )
                return ConstructionPointResult(
                    name=spec.name, status=status, rank=0, message=exc.error.message
                ), None
            if not fresh or not registered:
                return ConstructionPointResult(
                    name=spec.name,
                    status="stale_dependency",
                    rank=0,
                    message=(
                        "Source/calibration/frame changed; explicitly update "
                        "registration or observations"
                    ),
                ), None
            if observation["visibility"] != "visible":
                continue
            p = inverse @ refs.pixel_point(obj, observation["pixel"])
            transform = inverse.to_3x3() @ refs.matrix(obj).to_3x3()
            directions = [transform.col[i].normalized() for i in range(3)]
            if abs(directions[0].dot(directions[1])) > 1e-5:
                fail(
                    "invalid_construction_frame",
                    "Sheared reference projections are unsupported",
                )
            start = len(equations)
            count = 3 if registration["specification"]["projection"] == "plane" else 2
            width, height = refs.dimensions(obj)
            for i in range(count):
                normal = directions[i]
                sigma = observation["sigma_pixels"]
                sigma_world = (
                    sigma
                    * (width if i == 0 else height)
                    / obj.data.size[i]
                    * transform.col[i].length
                    if sigma is not None and i < 2
                    else None
                )
                equations.append(
                    Equation(
                        (float(normal[0]), float(normal[1]), float(normal[2])),
                        float(normal.dot(p)),
                        sigma_world,
                    )
                )
            spans.append((identifier, start, len(equations)))
            evidence.append(
                ObservationEvidence(
                    id=identifier,
                    revision=observation["revision"],
                    reference_id=observation["reference_id"],
                    registration_revision=registration["revision"],
                    source_sha256=observation["source_sha256"],
                    basis_sha256=registration["basis_sha256"],
                    pixel=observation["pixel"],
                    sigma_pixels=observation["sigma_pixels"],
                    residual=0,
                )
            )
        for constraint in spec.source.constraints:
            equations.append(
                Equation(
                    cast(tuple[float, float, float], tuple(axis(constraint.axis))),
                    constraint.value,
                )
            )
        answer = solve(equations)
        if answer.point is None:
            return ConstructionPointResult(
                name=spec.name,
                status="underconstrained",
                rank=answer.rank,
                message=(
                    f"Only {answer.rank}/3 independent coordinates constrained; "
                    "add an independent view or explicit axis plane"
                ),
            ), None
        point = Vector(answer.point)
        # Assess the coordinates that native float32 transforms can actually store.
        native_point = inverse @ (construction @ point)
        native_residuals = [
            sum(e.normal[i] * native_point[i] for i in range(3)) - e.value
            for e in equations
        ]
        residuals = {
            identifier: math.sqrt(sum(v * v for v in native_residuals[start:end]))
            for identifier, start, end in spans
        }
        evidence = [
            item.model_copy(update={"residual": residuals[item.id]})
            for item in evidence
        ]
        maximum = max(
            [abs(v) for v in native_residuals] + list(residuals.values()), default=0.0
        )
        worst = max(residuals, key=lambda k: residuals[k]) if residuals else None
        result = ConstructionPointResult(
            name=spec.name,
            status="solved" if maximum <= arguments.tolerance else "inconsistent",
            rank=3,
            residual=maximum,
            worst_observation=worst,
            sigma=answer.sigma,
            message="Conditional uncertainty assumes exact calibration/registration"
            if answer.sigma is not None
            else (
                "Uncertainty unavailable: not all scalar inputs have known "
                "independent errors"
            ),
        )
        if result.status != "solved":
            return result, None
    provenance = DerivationProvenance(
        name=spec.name,
        specification=spec,
        frame=arguments.frame,
        frame_id=f_id,
        frame_sha256=frame_basis(arguments.frame),
        observations=evidence,
        reflected_landmark=reflected,
        reflected_basis=reflected_basis,
        geometry_resource_id=geometry_id,
        geometry_basis=geometry_hash,
        point=refs.xyz(point),
        world_point=refs.xyz(construction @ point),
        tolerance=arguments.tolerance,
        result=result,
    )
    return result, provenance


def report(
    results: list[ConstructionPointResult],
    frame_name: str | None,
    seconds: float = 0,
    limit: int = 8,
) -> ConstructionReport:
    residuals = [r.residual for r in results if r.residual is not None]
    worst = sorted(
        results, key=lambda r: (r.status == "solved", -(r.residual or 0), r.name)
    )
    return ConstructionReport(
        count=len(results),
        solved=sum(r.status == "solved" for r in results),
        underconstrained=sum(r.status == "underconstrained" for r in results),
        inconsistent=sum(r.status == "inconsistent" for r in results),
        stale=sum(r.status == "stale_dependency" for r in results),
        registration_required=sum(r.status == "registration_required" for r in results),
        max_residual=max(residuals) if residuals else None,
        mean_residual=sum(residuals) / len(residuals) if residuals else None,
        worst=worst[:limit],
        worst_truncated=len(worst) > limit,
        frame=frame_name,
        solve_seconds=seconds,
    )


def derive(arguments: LandmarkDeriveArguments) -> ConstructionReport:
    refs.idle(mutate=True)
    if arguments.frame and arguments.frame in {s.name for s in arguments.landmarks}:
        fail("invalid_construction_frame", "A solve cannot redefine its own frame")
    existing = [
        bpy.data.objects[s.name]
        for s in arguments.landmarks
        if s.name in bpy.data.objects
    ]
    objects = existing + (
        [refs.object_named(arguments.frame)] if arguments.frame else []
    )
    objects.extend(
        refs.object_named(s.source.source.object)
        for s in arguments.landmarks
        if s.source.kind == "geometry"
    )
    # Preflight all destinations even when a solve will be underconstrained.
    for obj in existing:
        refs.owned(obj.name, refs.LANDMARK, edit=True)
        if obj.children:
            fail("reference_context_invalid", "Cannot redefine landmarks with children")
    values = observations()
    cache: dict[str, Any] = {}
    started = time.perf_counter()
    with transaction(objects):
        solved = [
            derive_one(spec, arguments, values, cache) for spec in arguments.landmarks
        ]
        accepted = [p for _, p in solved if p is not None]
        if accepted:
            refs.set_landmarks(
                LandmarkSetArguments(
                    landmarks=[
                        LandmarkSpec(
                            name=p.name,
                            point=p.world_point,
                            label=p.specification.label,
                            category=p.specification.category,
                        )
                        for p in accepted
                    ]
                )
            )
            for provenance in accepted:
                obj = refs.owned(provenance.name, refs.LANDMARK)
                identity(obj)
                provenance = provenance.model_copy(
                    update={"world_point": refs.xyz(obj.matrix_world.translation)}
                )
                obj[DERIVATION] = provenance.model_dump_json()
                if not freshness(obj, context=(values, cache)):
                    fail(
                        "stale_dependency",
                        "Native construction basis changed or reflection chain "
                        "exceeds the supported depth",
                    )
        # Failed re-solves retain existing coordinates but must never remain valid.
        for result, solved_provenance in solved:
            if (
                solved_provenance is None
                and result.name in bpy.data.objects
                and DERIVATION in bpy.data.objects[result.name]
            ):
                previous = DerivationProvenance.model_validate_json(
                    bpy.data.objects[result.name][DERIVATION]
                )
                previous = previous.model_copy(update={"stale": True})
                bpy.data.objects[result.name][DERIVATION] = previous.model_dump_json()
        return report(
            [r for r, _ in solved],
            arguments.frame,
            time.perf_counter() - started,
            arguments.worst_limit,
        ).model_copy(
            update={
                "frames": sorted({p.frame_id or "world" for p in accepted}),
                "reference_ids": sorted(
                    {e.reference_id for p in accepted for e in p.observations}
                ),
            }
        )


def fingerprint(obj: Any) -> str | None:
    """Extend the existing semantic binding fingerprint, not its dependency graph."""
    if DERIVATION in obj:
        return digest({"provenance": obj[DERIVATION], "current": freshness(obj)})
    if obj.get(refs.REFERENCE):
        try:
            current = basis(obj)
        except OperationError as exc:
            current = exc.error.code
        records = {
            k: v
            for k, v in observations().items()
            if v["reference_id"] == obj.get(RESOURCE_KEY)
        }
        return digest(
            {
                "basis": current,
                "registration": obj.get(REGISTRATION),
                "observations": records,
            }
        )
    return None
