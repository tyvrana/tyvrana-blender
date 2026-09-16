"""Native image empties and persistent datum points; no vision inference."""

import math
from typing import Any, cast

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]

from .errors import OperationError
from .inspection import page
from .reference_models import (
    AngleQuery,
    BoundsQuery,
    LandmarkInspectArguments,
    LandmarkInspectResult,
    LandmarkResult,
    LandmarkSetArguments,
    LandmarkSummary,
    MeasurementArguments,
    MeasurementResult,
    MeasurementValue,
    NamedRemoveArguments,
    NamedRemoveResult,
    PointSource,
    ReferenceCalibrateArguments,
    ReferenceCalibrateResult,
    ReferenceConfigureArguments,
    ReferenceCreateArguments,
    ReferenceInspectArguments,
    ReferenceInspectResult,
    ReferenceResult,
    ReferenceSummary,
    UnitsConfigureArguments,
    UnitsSummary,
)

REFERENCE = "tyvrana_reference"
LANDMARK = "tyvrana_landmark"
COLLECTION = "tyvrana_reference_collection"
CATEGORY = "tyvrana_category"
SOURCE = "tyvrana_source_label"
LABEL = "tyvrana_label"
MAX_BOUND_VERTICES = 128_000


def fail(message: str, code: str = "reference_context_invalid") -> None:
    raise OperationError(code, message)


def idle(*, mutate: bool = False) -> None:
    from .blender import data_mutation_context, main_thread

    main_thread()
    if bpy.context.mode != "OBJECT" or bpy.app.is_job_running("RENDER"):
        fail(
            "Reference and measurement operations require Object Mode outside rendering"
        )
    if mutate:
        data_mutation_context()
    bpy.context.view_layer.update()


def xyz(value: Any) -> list[float]:
    result = [float(value[i]) for i in range(3)]
    if any(not math.isfinite(v) or abs(v) > 1e12 for v in result):
        fail("Coordinate exceeds finite measurement bounds")
    return result


def matrix(obj: Any) -> Any:
    value = obj.matrix_world.copy()
    for row in value:
        if any(not math.isfinite(v) or abs(v) > 1e12 for v in row):
            fail("Object transform exceeds finite measurement bounds")
    if abs(value.to_3x3().determinant()) < 1e-12:
        fail("Measurement requires an invertible object transform")
    return value


def object_named(name: str) -> Any:
    obj = bpy.context.scene.objects.get(name)
    if obj is None:
        fail(f'Object "{name}" does not exist in the current scene', "object_not_found")
    return obj


def owned(name: str, key: str, *, edit: bool = False) -> Any:
    obj = object_named(name)
    if obj.type != "EMPTY" or not obj.get(key):
        kind = "reference" if key == REFERENCE else "landmark"
        fail(f'Object "{name}" is not a managed {kind}')
    if edit:
        mutable(obj)
    return obj


def mutable(obj: Any) -> None:
    if (
        obj.library
        or obj.override_library
        or obj.animation_data
        or obj.constraints
        or len(obj.users_scene) != 1
    ):
        fail(
            "Preserve linked/shared-scene objects, overrides, animation and constraints"
        )


def image_valid(image: Any) -> bool:
    return bool(
        image is not None
        and image.source in {"FILE", "GENERATED"}
        and all(0 < n <= 4096 for n in image.size)
        and (image.source == "GENERATED" or image.packed_files)
        and all(math.isfinite(v) and v > 0 for v in image.display_aspect)
        # Reopened images are lazy. Acquire only bounded packed/generated buffers;
        # has_data alone would incorrectly reject a valid persisted reference.
        and len(image.pixels) >= 4
        and image.has_data
    )


def dimensions(obj: Any) -> tuple[float, float]:
    image = obj.data
    if obj.empty_display_type != "IMAGE" or not image_valid(image):
        fail(
            "Reference requires a loaded, packed or generated static image (up to "
            "4096px)"
        )
    width, height = (image.size[i] * image.display_aspect[i] for i in range(2))
    longest = max(width, height)
    return (
        obj.empty_display_size * width / longest,
        obj.empty_display_size * height / longest,
    )


def pixel_point(obj: Any, pixel: list[float]) -> Any:
    width, height = dimensions(obj)
    x, y = pixel
    if not 0 <= x <= obj.data.size[0] or not 0 <= y <= obj.data.size[1]:
        fail(
            "Reference point must be inside image-edge coordinates [0,width] x "
            "[0,height]"
        )
    local = Vector(
        (
            width * (x / obj.data.size[0] + obj.empty_image_offset[0]),
            height * (y / obj.data.size[1] + obj.empty_image_offset[1]),
            0,
        )
    )
    return matrix(obj) @ local


def reference_summary(obj: Any) -> ReferenceSummary:
    image = obj.data if obj.empty_display_type == "IMAGE" else None
    valid = image_valid(image)
    corners: list[list[float]] = []
    size = [0.0, 0.0, 0.0]
    if valid and image is not None:
        width, height = dimensions(obj)
        size = [width, height, 0.0]
        corners = [
            xyz(pixel_point(obj, [x, y]))
            for x, y in (
                (0, 0),
                (image.size[0], 0),
                tuple(image.size),
                (0, image.size[1]),
            )
        ]
    collections = sorted(c.name for c in obj.users_collection)
    return ReferenceSummary(
        name=obj.name,
        image=image.name if image else None,
        image_size=list(image.size) if image else [],
        packed=bool(image and image.packed_files),
        valid=valid,
        local_dimensions=size,
        world_corners=corners,
        location=xyz(obj.location),
        rotation=xyz(obj.rotation_euler),
        scale=xyz(obj.scale),
        size=float(obj.empty_display_size),
        opacity=float(obj.color[3]),
        depth=obj.empty_image_depth.lower(),
        side="both"
        if obj.empty_image_side == "DOUBLE_SIDED"
        else obj.empty_image_side.lower(),
        orthographic=bool(obj.show_empty_image_orthographic),
        perspective=bool(obj.show_empty_image_perspective),
        axis_aligned_only=bool(obj.show_empty_image_only_axis_aligned),
        hidden=bool(obj.hide_viewport),
        category=str(obj.get(CATEGORY, "")),
        source_label=str(obj.get(SOURCE, "")),
        collections=collections[:16],
        collection_count=len(collections),
        collections_truncated=len(collections) > 16,
    )


PROPERTIES = {
    "size": "empty_display_size",
    "depth": "empty_image_depth",
    "side": "empty_image_side",
    "orthographic": "show_empty_image_orthographic",
    "perspective": "show_empty_image_perspective",
    "axis_aligned_only": "show_empty_image_only_axis_aligned",
    "hidden": "hide_viewport",
}


def apply_properties(obj: Any, values: dict[str, Any]) -> None:
    for key, attr in PROPERTIES.items():
        if key in values:
            value = values[key]
            if key in {"depth", "side"}:
                value = "DOUBLE_SIDED" if value == "both" else value.upper()
            setattr(obj, attr, value)
    if "opacity" in values:
        obj.use_empty_image_alpha = True
        obj.color[3] = values["opacity"]
    for key, prop in (("category", CATEGORY), ("source_label", SOURCE)):
        if key in values:
            obj[prop] = values[key]


def clean_collections(collections: list[Any]) -> None:
    for collection in collections:
        if (
            collection.get(COLLECTION)
            and not collection.objects
            and not collection.children
        ):
            bpy.data.collections.remove(collection)


def create(arguments: ReferenceCreateArguments) -> ReferenceResult:
    idle(mutate=True)
    scene_collections = {c for c in bpy.context.scene.collection.children_recursive}
    for spec in arguments.references:
        if bpy.data.objects.get(spec.name):
            fail(f'Object name "{spec.name}" already exists')
        if not image_valid(bpy.data.images.get(spec.image)):
            fail(f'Image "{spec.image}" must be a packed or generated static image')
        collection = bpy.data.collections.get(spec.collection)
        if collection and (
            collection not in scene_collections
            or collection.library
            or collection.override_library
        ):
            fail("Reference collection must be editable and in the current scene")
    created: list[Any] = []
    new_collections: list[Any] = []
    try:
        for spec in arguments.references:
            collection = bpy.data.collections.get(spec.collection)
            if collection is None:
                collection = bpy.data.collections.new(spec.collection)
                new_collections.append(collection)
                collection[COLLECTION] = True
                if collection.name != spec.collection:
                    fail("Blender cannot store the collection name exactly")
                bpy.context.scene.collection.children.link(collection)
            obj = bpy.data.objects.new(spec.name, None)
            created.append(obj)
            if obj.name != spec.name:
                fail("Blender cannot store the reference name exactly")
            obj.empty_display_type = "IMAGE"
            obj.data = bpy.data.images[spec.image]
            obj.empty_image_offset = (-0.5, -0.5)
            obj[REFERENCE] = True
            obj.location, obj.rotation_euler = spec.location, spec.rotation
            apply_properties(obj, spec.model_dump())
            collection.objects.link(obj)
        bpy.context.view_layer.update()
        return ReferenceResult(references=[reference_summary(o) for o in created])
    except BaseException:
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        clean_collections(new_collections)
        raise


def configure(arguments: ReferenceConfigureArguments) -> ReferenceResult:
    idle(mutate=True)
    objects = [owned(s.name, REFERENCE, edit=True) for s in arguments.references]
    snapshots = [reference_summary(o).model_dump() for o in objects]
    alphas = [o.use_empty_image_alpha for o in objects]
    try:
        for obj, spec in zip(objects, arguments.references, strict=True):
            apply_properties(obj, spec.model_dump(exclude_unset=True))
        bpy.context.view_layer.update()
        return ReferenceResult(references=[reference_summary(o) for o in objects])
    except BaseException:
        for obj, snapshot, alpha in zip(objects, snapshots, alphas, strict=True):
            apply_properties(obj, snapshot)
            obj.use_empty_image_alpha = alpha
        bpy.context.view_layer.update()
        raise


def inspect(arguments: ReferenceInspectArguments) -> ReferenceInspectResult:
    idle()
    objects = (
        o
        for o in bpy.context.scene.objects
        if o.type == "EMPTY"
        and o.get(REFERENCE)
        and (arguments.image is None or o.data and o.data.name == arguments.image)
        and (
            arguments.collection is None
            or any(c.name == arguments.collection for c in o.users_collection)
        )
        and (arguments.category is None or o.get(CATEGORY, "") == arguments.category)
    )
    selected, info = page(objects, arguments, lambda o: str(o.name))
    return ReferenceInspectResult(
        page=info, references=[reference_summary(o) for o in selected]
    )


def remove(arguments: NamedRemoveArguments, key: str) -> NamedRemoveResult:
    idle(mutate=True)
    objects = [owned(name, key, edit=True) for name in arguments.names]
    for obj in objects:
        if obj.children:
            fail(
                "Remove or reattach dependent children before removing a "
                "reference/landmark"
            )
        # Preserve constraint/driver/other ID users, beyond normal collections/scenes.
        users = bpy.data.user_map(subset={obj}).get(obj, set())
        if any(
            not isinstance(user, (bpy.types.Collection, bpy.types.Scene))
            for user in users
        ):
            fail(
                "Reference/landmark has external datablock users; remove "
                "dependencies first"
            )
    collections = list({c for o in objects for c in o.users_collection})
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    clean_collections(collections)
    return NamedRemoveResult(removed=arguments.names)


def landmark_summary(obj: Any) -> LandmarkSummary:
    attachment = str(obj[LANDMARK])
    parent = obj.parent
    valid = (
        obj.parent_type == "OBJECT"
        and not any(obj.delta_location)
        and (
            attachment == "world"
            and parent is None
            or attachment == "object"
            and parent is not None
            and parent.name in bpy.context.scene.objects
        )
    )
    return LandmarkSummary(
        name=obj.name,
        point=xyz(obj.location),
        world_point=xyz(obj.matrix_world.translation),
        object=parent.name if parent else None,
        label=str(obj.get(LABEL, "")),
        category=str(obj.get(CATEGORY, "")),
        attachment=cast(Any, attachment),
        valid=valid,
    )


def set_landmarks(arguments: LandmarkSetArguments) -> LandmarkResult:
    idle(mutate=True)
    names = {s.name for s in arguments.landmarks}
    existing: dict[str, Any] = {}
    parents: dict[str, Any] = {}
    for spec in arguments.landmarks:
        obj = bpy.data.objects.get(spec.name)
        if obj:
            existing[spec.name] = owned(spec.name, LANDMARK, edit=True)
            if obj.children:
                fail("Cannot redefine landmarks with dependent children")
            if obj.parent_type != "OBJECT" or any(obj.delta_location):
                fail("Preserve unsupported bone/vertex parenting and delta translation")
        if spec.object:
            parent = object_named(spec.object)
            if parent.name in names or parent.get(LANDMARK):
                fail("Landmark attachment cannot target another landmark")
            matrix(parent)
            xyz(parent.matrix_world @ Vector(spec.point))
            parents[spec.name] = parent
    snapshots = {
        name: (
            o.parent,
            o.matrix_parent_inverse.copy(),
            o.matrix_basis.copy(),
            dict(o.items()),
        )
        for name, o in existing.items()
    }
    created: list[Any] = []
    try:
        for spec in arguments.landmarks:
            obj = existing.get(spec.name)
            if obj is None:
                obj = bpy.data.objects.new(spec.name, None)
                created.append(obj)
                if obj.name != spec.name:
                    fail("Blender cannot store the landmark name exactly")
                bpy.context.scene.collection.objects.link(obj)
                obj.empty_display_type = "PLAIN_AXES"
                obj.empty_display_size = 0.05
            obj[LANDMARK] = "object" if spec.object else "world"
            obj[LABEL], obj[CATEGORY] = spec.label, spec.category
            obj.parent = parents.get(spec.name)
            obj.parent_type = "OBJECT"
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = Matrix.Translation(Vector(spec.point))
        bpy.context.view_layer.update()
        return LandmarkResult(
            landmarks=[
                landmark_summary(object_named(s.name)) for s in arguments.landmarks
            ]
        )
    except BaseException:
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        for name, (parent, inverse, basis, props) in snapshots.items():
            obj = existing[name]
            obj.parent, obj.matrix_parent_inverse, obj.matrix_basis = (
                parent,
                inverse,
                basis,
            )
            for key in (LANDMARK, LABEL, CATEGORY):
                if key in props:
                    obj[key] = props[key]
                elif key in obj:
                    del obj[key]
        bpy.context.view_layer.update()
        raise


def inspect_landmarks(arguments: LandmarkInspectArguments) -> LandmarkInspectResult:
    idle()
    objects = (
        o
        for o in bpy.context.scene.objects
        if o.type == "EMPTY"
        and o.get(LANDMARK)
        and (arguments.object is None or o.parent and o.parent.name == arguments.object)
        and (arguments.category is None or o.get(CATEGORY, "") == arguments.category)
        and (arguments.attachment is None or o.get(LANDMARK) == arguments.attachment)
    )
    selected, info = page(objects, arguments, lambda o: str(o.name))
    return LandmarkInspectResult(
        page=info, landmarks=[landmark_summary(o) for o in selected]
    )


def resolve(source: PointSource) -> Any:
    if source.kind == "bone":
        from .joints import bone_point

        return bone_point(source)
    if source.kind == "world":
        return Vector(source.point)
    if source.kind == "object":
        return matrix(object_named(source.object)) @ Vector(source.point)
    if source.kind == "reference_pixel":
        return pixel_point(owned(source.reference, REFERENCE), source.pixel)
    obj = owned(source.name, LANDMARK)
    if not landmark_summary(obj).valid:
        fail("Landmark attachment is missing; redefine the landmark explicitly")
    return obj.matrix_world.translation.copy()


def units() -> UnitsSummary:
    settings = bpy.context.scene.unit_settings
    return UnitsSummary(
        system=settings.system.lower(), meters_per_unit=float(settings.scale_length)
    )


def configure_units(arguments: UnitsConfigureArguments) -> UnitsSummary:
    idle(mutate=True)
    settings = bpy.context.scene.unit_settings
    before = settings.system, settings.scale_length
    try:
        settings.system = arguments.system.upper()
        settings.scale_length = arguments.meters_per_unit
        return units()
    except BaseException:
        settings.system, settings.scale_length = before
        raise


def measure(arguments: MeasurementArguments) -> MeasurementResult:
    idle()
    frame = (
        matrix(object_named(arguments.frame)).inverted()
        if arguments.frame
        else Matrix.Identity(4)
    )
    unit = units()
    factor = unit.meters_per_unit if arguments.unit == "meters" else 1.0
    measurements: list[MeasurementValue] = []
    # Bound aggregate native work, not just each query or result.
    meshes = {
        q.object: object_named(q.object)
        for q in arguments.queries
        if isinstance(q, BoundsQuery)
    }
    if any(o.type != "MESH" or not o.data.vertices for o in meshes.values()):
        fail("Bounds require a nonempty authored mesh", "measurement_context_invalid")
    if sum(len(o.data.vertices) for o in meshes.values()) > MAX_BOUND_VERTICES:
        fail(
            "Authored bounds exceed the total 128000-vertex work budget",
            "measurement_limit_exceeded",
        )
    bounds_cache: dict[str, tuple[list[float], list[float]]] = {}
    for q in arguments.queries:
        if isinstance(q, BoundsQuery):
            if q.object not in bounds_cache:
                obj = meshes[q.object]
                transform = frame @ matrix(obj)
                points = [xyz(transform @ v.co) for v in obj.data.vertices]
                lo = [min(p[i] for p in points) * factor for i in range(3)]
                hi = [max(p[i] for p in points) * factor for i in range(3)]
                bounds_cache[q.object] = lo, hi
            lo, hi = bounds_cache[q.object]
            measurements.append(
                MeasurementValue(
                    name=q.name,
                    kind=q.kind,
                    unit=arguments.unit,
                    minimum=xyz(lo),
                    maximum=xyz(hi),
                    dimensions=xyz([hi[i] - lo[i] for i in range(3)]),
                )
            )
            continue
        a, b = frame @ resolve(q.a), frame @ resolve(q.b)
        xyz(a)
        xyz(b)
        if isinstance(q, AngleQuery):
            vertex = frame @ resolve(q.vertex)
            xyz(vertex)
            va, vb = a - vertex, b - vertex
            if va.length < 1e-12 or vb.length < 1e-12:
                fail("Angle needs two nonzero rays", "measurement_degenerate")
            value = math.degrees(
                math.acos(max(-1.0, min(1.0, va.normalized().dot(vb.normalized()))))
            )
            value_unit = "degrees"
        else:
            value = float((b - a).length) * factor
            value_unit = arguments.unit
        if not math.isfinite(value):
            fail("Measurement exceeds finite numeric range")
        comparison = q.comparison
        deviation = value - comparison.target if comparison else None
        measurements.append(
            MeasurementValue(
                name=q.name,
                kind=q.kind,
                value=value,
                unit=cast(Any, value_unit),
                target=comparison.target if comparison else None,
                tolerance=comparison.tolerance if comparison else None,
                deviation=deviation,
                within_tolerance=abs(cast(float, deviation)) <= comparison.tolerance
                if comparison
                else None,
            )
        )
    return MeasurementResult(
        measurements=measurements, frame=arguments.frame, units=unit
    )


def calibrate(arguments: ReferenceCalibrateArguments) -> ReferenceCalibrateResult:
    idle(mutate=True)
    obj = owned(arguments.name, REFERENCE, edit=True)
    if obj.parent or obj.children:
        fail("Calibration requires an unparented reference without attached children")
    a, b = pixel_point(obj, arguments.a), pixel_point(obj, arguments.b)
    factor_unit = units().meters_per_unit if arguments.unit == "meters" else 1.0
    before = float((b - a).length) * factor_unit
    if before < 1e-12:
        fail("Calibration source distance is too small", "measurement_degenerate")
    factor = arguments.target_distance / before
    size = float(obj.empty_display_size) * factor
    if not 0.0001 <= size <= 1000:
        fail("Calibrated display size must remain in 0.0001..1000 Blender units")
    old_size, old_location = obj.empty_display_size, obj.location.copy()
    try:
        obj.empty_display_size = size
        obj.location += a - pixel_point(obj, arguments.a)
        bpy.context.view_layer.update()
        anchor = pixel_point(obj, arguments.a)
        after = float((pixel_point(obj, arguments.b) - anchor).length) * factor_unit
        if not math.isclose(
            after, arguments.target_distance, rel_tol=1e-5, abs_tol=1e-7
        ):
            fail("Native precision cannot satisfy the calibration target")
        if (anchor - a).length > max(1e-6, a.length * 1e-6):
            fail("Native precision cannot preserve the calibration anchor")
        return ReferenceCalibrateResult(
            name=obj.name,
            before_distance=before,
            after_distance=after,
            target_distance=arguments.target_distance,
            unit=arguments.unit,
            factor=factor,
            size=float(obj.empty_display_size),
            anchor_world=xyz(anchor),
        )
    except BaseException:
        obj.empty_display_size, obj.location = old_size, old_location
        bpy.context.view_layer.update()
        raise
