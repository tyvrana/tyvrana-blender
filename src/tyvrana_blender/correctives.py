"Native relative keys and residual-verified original-space corrective capture."

import json
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

from . import deformation_geometry as geo
from . import deformation_qa, retopo_geometry, rig
from .corrective_models import (
    CapturedCorrection,
    CaptureTargetArguments,
    CaptureTargetResult,
    DeformationCompareArguments,
    DeformationCompareResult,
    DeviationSample,
    RegionCorrection,
    ShapeKeysEditArguments,
    ShapeKeysEditResult,
    ShapeKeysInspectArguments,
    ShapeKeysRemoveArguments,
    ShapeKeysRemoveResult,
    ShapeKeysSummary,
    ShapeKeySummary,
    SparseCorrection,
    SparseDelta,
    TargetDeviation,
)

TOPOLOGY_KEY = "_tyvrana_shape_topology"
CAPTURE_KEY = "_tyvrana_correction_target"
CAPTURE_SOURCE = "_tyvrana_correction_source"


def keys_guard(obj: Any) -> None:
    keys = obj.data.shape_keys
    if keys:
        if not keys.use_relative:
            geo.fail(
                "Relative shape keys required; absolute timed keys are unsupported"
            )
        if (
            len(keys.key_blocks) > 64
            or len(keys.key_blocks) * len(obj.data.vertices) > geo.MAX_KEY_COORDINATES
        ):
            geo.fail("Shape storage exceeds 64 keys/2000000 key coordinates")
        if any(len(k.data) != len(obj.data.vertices) for k in keys.key_blocks):
            geo.fail("Shape key vertex count differs from authored mesh")
    if TOPOLOGY_KEY in obj.data and obj.data[TOPOLOGY_KEY] != geo.topology(obj.data):
        geo.fail(
            "Ordered topology changed since shape authoring; "
            "restore compatible topology"
        )


@contextmanager
def staged(obj: Any) -> Iterator[None]:
    original = obj.data
    active = obj.active_shape_key_index
    candidate = original.copy()
    try:
        obj.data = candidate
        yield
        bpy.context.view_layer.update()
    except BaseException:
        obj.data = original
        obj.active_shape_key_index = active
        bpy.context.view_layer.update()
        if candidate.users == 0:
            bpy.data.meshes.remove(candidate)
        raise
    else:
        obj.active_shape_key_index = (
            min(active, max(0, len(candidate.shape_keys.key_blocks) - 1))
            if candidate.shape_keys
            else 0
        )
        if original.users == 0:
            name = original.name
            bpy.data.meshes.remove(original)
            candidate.name = name


def inspect(args: ShapeKeysInspectArguments) -> ShapeKeysSummary:
    obj = geo.context(args.object_name)
    keys_guard(obj)
    keys = obj.data.shape_keys
    blocks = list(keys.key_blocks) if keys else []
    filtered = [k for k in blocks if args.names is None or k.name in args.names]
    result = []
    for key in filtered[args.offset : args.offset + args.limit]:
        delta = [
            (p.co - q.co).length
            for p, q in zip(key.data, key.relative_key.data, strict=True)
        ]
        path = key.path_from_id("value")
        result.append(
            ShapeKeySummary(
                name=key.name,
                relative_to=key.relative_key.name,
                value=key.value,
                minimum=key.slider_min,
                maximum=key.slider_max,
                mute=key.mute,
                locked=key.lock_shape,
                vertex_group=key.vertex_group,
                affected_vertices=sum(v > 1e-7 for v in delta),
                maximum_delta=max(delta, default=0),
                mean_delta=sum(delta) / len(delta) if delta else 0,
                driven=bool(
                    keys.animation_data
                    and any(d.data_path == path for d in keys.animation_data.drivers)
                ),
            )
        )
    details = []
    total = 0
    if args.detail_key:
        key = keys.key_blocks.get(args.detail_key) if keys else None
        if key is None:
            geo.fail("Requested detail key does not exist")
        for index, (a, b) in enumerate(
            zip(key.data, key.relative_key.data, strict=True)
        ):
            delta = a.co - b.co
            if delta.length <= 1e-7:
                continue
            if args.detail_offset <= total < args.detail_offset + args.detail_limit:
                details.append(SparseDelta(vertex=index, delta=list(delta)))
            total += 1
    return ShapeKeysSummary(
        object_name=obj.name,
        topology_sha256=geo.topology(obj.data),
        vertex_count=len(obj.data.vertices),
        relative=bool(not keys or keys.use_relative),
        basis=keys.reference_key.name if keys else None,
        total=len(filtered),
        keys=result,
        truncated=args.offset + len(result) < len(filtered),
        details=details,
        detail_total=total,
        details_truncated=args.detail_offset + len(details) < total,
    )


def capture_stack(obj: Any) -> None:
    keys_guard(obj)
    if any(m.type != "ARMATURE" for m in obj.modifiers):
        geo.fail(
            "Corrective capture supports no modifiers or one owned linear "
            "Armature; disable other modifiers by removing them from this "
            "capture workflow"
        )
    if obj.modifiers:
        if len(obj.modifiers) != 1:
            geo.fail("Corrective capture supports one Armature only")
        mod = rig.binding_modifier(obj)
        if (
            mod.use_deform_preserve_volume
            or not mod.show_viewport
            or not mod.show_render
        ):
            geo.fail(
                "Corrective capture requires an enabled linear Armature "
                "(preserve_volume=false)"
            )
        rig.armature(mod.object.name, edit=True)


def capture_target(args: CaptureTargetArguments) -> CaptureTargetResult:
    obj = geo.context(args.object_name, edit=True)
    capture_stack(obj)
    geo.native_name(args.name)
    if args.name in bpy.data.objects:
        geo.fail("Correction target name already exists")
    points, signature = geo.evaluated(obj)
    data = obj.data.copy()
    target = None
    try:
        target = bpy.data.objects.new(args.name, data)
        if data.shape_keys:
            target.shape_key_clear()
        data.vertices.foreach_set("co", [c for p in points for c in p])
        data.update()
        # Keep topology/material/UV payload native; target has no primary deformation.
        for group in obj.vertex_groups:
            target.vertex_groups.new(name=group.name).lock_weight = group.lock_weight
        target.matrix_world = obj.matrix_world.copy()
        target[CAPTURE_SOURCE] = obj
        baseline = geo.coordinates(points)
        target[CAPTURE_KEY] = json.dumps(
            dict(
                topology=signature,
                baseline=baseline,
                transform=[list(r) for r in obj.matrix_world],
            )
        )
        bpy.context.scene.collection.objects.link(target)
        return CaptureTargetResult(
            source=obj.name,
            target=target.name,
            vertex_count=len(points),
            topology_sha256=signature,
            baseline_sha256=baseline,
        )
    except BaseException:
        if target is not None:
            bpy.data.objects.remove(target, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
        raise


def captured_deltas(
    obj: Any, definition: CapturedCorrection
) -> tuple[dict[int, Any], Any]:
    capture_stack(obj)
    target = geo.context(definition.target)
    if target.get(CAPTURE_SOURCE) != obj or CAPTURE_KEY not in target:
        geo.fail(
            "Use deformation.capture_target on this source before captured correction"
        )
    try:
        metadata = json.loads(target[CAPTURE_KEY])
    except (ValueError, TypeError):
        geo.fail("Correction target provenance is invalid")
    points, signature = geo.evaluated(obj)
    if metadata.get("topology") != signature or geo.topology(target.data) != signature:
        geo.fail("Correction target ordered topology differs from captured source")
    if metadata.get("baseline") != geo.coordinates(points) or metadata.get(
        "transform"
    ) != [list(r) for r in obj.matrix_world]:
        geo.fail(
            "Source pose/shape/evaluation changed since capture; recapture in "
            "intended state"
        )
    if target.modifiers or target.data.shape_keys:
        geo.fail("Correction target must remain an unmodified authored mesh")
    transform = retopo_geometry.matrix(obj).inverted() @ retopo_geometry.matrix(target)
    chosen = geo.selection(obj, definition.selector)
    graph = retopo_geometry.graph(obj)
    result = {}
    try:
        obj.crazyspace_eval(graph, bpy.context.scene)
        for i in chosen:
            delta = transform @ target.data.vertices[i].co - points[i]
            original = obj.crazyspace_displacement_to_original(
                vertex_index=i, displacement=delta
            )
            if original.length > max(1e-12, delta.length) * 1000:
                geo.fail(
                    "Primary deformation inverse amplifies displacement over 1000 "
                    "times; use a less singular pose"
                )
            # A singular map must fail even if Blender returns a fallback inverse.
            forward = obj.crazyspace_displacement_to_deformed(
                vertex_index=i, displacement=original
            )
            if (forward - delta).length > definition.tolerance:
                geo.fail(
                    "Primary deformation is not invertible within capture tolerance"
                )
            result[i] = original
    finally:
        obj.crazyspace_eval_clear()
    return result, target


def edit(args: ShapeKeysEditArguments) -> ShapeKeysEditResult:
    obj = geo.context(args.object_name, edit=True)
    keys_guard(obj)
    signature = geo.topology(obj.data)
    if args.expected_topology_sha256 and args.expected_topology_sha256 != signature:
        geo.fail("Expected ordered topology differs; inspect the current snapshot")
    capture = None
    target = None
    capture_error = None
    for definition in args.keys:
        if isinstance(definition.correction, CapturedCorrection):
            capture, target = captured_deltas(obj, definition.correction)
    changed = []
    with staged(obj):
        if not obj.data.shape_keys:
            obj.shape_key_add(name="Basis", from_mix=False)
        keys = obj.data.shape_keys
        basis = keys.reference_key
        for definition in args.keys:
            geo.native_name(definition.name)
            if definition.rename:
                geo.native_name(definition.rename)
            key = keys.key_blocks.get(definition.name)
            if definition.create:
                if key is not None:
                    geo.fail("Shape key already exists; edit it explicitly")
                if (len(keys.key_blocks) + 1) * len(
                    obj.data.vertices
                ) > geo.MAX_KEY_COORDINATES or len(keys.key_blocks) >= 64:
                    geo.fail("Shape storage exceeds 64 keys/2000000 coordinates")
                key = obj.shape_key_add(name=definition.name, from_mix=False)
                key.value = 0.0
            elif key is None:
                geo.fail("Shape key does not exist; set create=true")
            # Native key-block insertion can reallocate RNA wrappers.
            basis = keys.reference_key
            key = keys.key_blocks[definition.name]
            if key == basis or key.lock_shape:
                geo.fail("Preserve Basis and locked shape keys")
            if definition.relative_to is not None:
                reference = keys.key_blocks.get(definition.relative_to)
                if reference is None or reference == key:
                    geo.fail("Relative reference must be another existing key")
                seen = {key.name}
                current = reference
                while current != basis:
                    if current.name in seen:
                        geo.fail("Relative shape relationship would create a cycle")
                    seen.add(current.name)
                    current = current.relative_key
                key.relative_key = reference
            minimum = (
                definition.minimum if definition.minimum is not None else key.slider_min
            )
            maximum = (
                definition.maximum if definition.maximum is not None else key.slider_max
            )
            requested_value = (
                definition.value if definition.value is not None else key.value
            )
            if minimum >= maximum or not minimum <= requested_value <= maximum:
                geo.fail(
                    "Shape range must increase and include its requested/current value"
                )
            for name, field in [
                ("slider_min", "minimum"),
                ("slider_max", "maximum"),
                ("mute", "mute"),
                ("vertex_group", "vertex_group"),
            ]:
                value = getattr(definition, field)
                if value is not None:
                    if (
                        field == "vertex_group"
                        and value
                        and value not in obj.vertex_groups
                    ):
                        geo.fail("Shape mask group does not exist")
                    setattr(key, name, value)
            if key.slider_min >= key.slider_max:
                geo.fail("Shape minimum must be below maximum")
            value = definition.value if definition.value is not None else key.value
            if not key.slider_min <= value <= key.slider_max:
                geo.fail("Shape value must lie within its configured range")
            correction = definition.correction
            if correction:
                if isinstance(correction, CapturedCorrection):
                    if (
                        key.relative_key != basis
                        or key.vertex_group
                        or key.value != 0
                        or key.mute
                    ):
                        geo.fail(
                            "Captured correction requires an unmuted Basis-relative "
                            "unmasked "
                            "key at value zero"
                        )
                    deltas = capture
                    operation = "replace"
                elif isinstance(correction, SparseCorrection):
                    deltas = {d.vertex: Vector(d.delta) for d in correction.deltas}
                    operation = correction.operation
                else:
                    deltas = {
                        i: Vector(correction.delta)
                        for i in geo.selection(obj, correction.selector)
                    }
                    operation = correction.operation
                assert deltas is not None
                transform = (
                    retopo_geometry.matrix(obj).to_3x3().inverted()
                    if not isinstance(correction, CapturedCorrection)
                    and correction.space == "world"
                    else None
                )
                for i, delta in deltas.items():
                    if i >= len(key.data):
                        geo.fail("Sparse vertex is outside current authored topology")
                    if transform is not None:
                        delta = transform @ delta
                    if isinstance(correction, RegionCorrection) and correction.falloff:
                        f = correction.falloff
                        p = obj.data.vertices[i].co
                        distance = math.sqrt(
                            sum(
                                ((p[j] - f.center[j]) / f.radii[j]) ** 2
                                for j in range(3)
                            )
                        )
                        t = max(0, 1 - distance)
                        delta = delta * (t * t * (3 - 2 * t))
                    point = (
                        key.relative_key.data[i].co
                        if operation == "replace"
                        else key.data[i].co
                    ) + delta
                    if any(not math.isfinite(c) or abs(c) > 1e6 for c in point):
                        geo.fail(
                            "Corrective coordinates exceed finite 1000000-unit bounds"
                        )
                    key.data[i].co = point
                if isinstance(correction, CapturedCorrection):
                    key.value = 1
                    obj.data.update()
                    bpy.context.view_layer.update()
                    actual, _ = geo.evaluated(obj)
                    assert target is not None
                    capture_error = max(
                        (
                            (obj.matrix_world @ actual[i])
                            - (target.matrix_world @ target.data.vertices[i].co)
                        ).length
                        for i in deltas
                    )
                    if capture_error > correction.tolerance:
                        geo.fail(
                            "Captured correction fails evaluated target tolerance; "
                            "original keys preserved"
                        )
            key.value = value
            if definition.rename:
                if (
                    definition.rename != key.name
                    and definition.rename in keys.key_blocks
                ):
                    geo.fail("Renamed shape key already exists")
                key.name = definition.rename
            changed.append(key.name)
        obj.data[TOPOLOGY_KEY] = signature
        obj.data.update()
    return ShapeKeysEditResult(
        object_name=obj.name,
        topology_sha256=signature,
        changed=changed,
        capture_max_error=capture_error,
    )


def remove(args: ShapeKeysRemoveArguments) -> ShapeKeysRemoveResult:
    obj = geo.context(args.object_name, edit=True)
    keys_guard(obj)
    keys = obj.data.shape_keys
    if (
        not keys
        or len(set(args.names)) != len(args.names)
        or any(n not in keys.key_blocks for n in args.names)
    ):
        geo.fail("Remove unique existing shape keys")
    selected = {keys.key_blocks[n] for n in args.names}
    basis = keys.reference_key
    if any(k.lock_shape for k in selected):
        geo.fail("Preserve locked shape keys")
    if basis in selected and (
        not args.remove_basis or len(selected) != len(keys.key_blocks)
    ):
        geo.fail("Removing Basis requires remove_basis=true and every key")
    if any(k not in selected and k.relative_key in selected for k in keys.key_blocks):
        geo.fail(
            "Other keys depend on this reference; preserve or "
            "remove dependants explicitly"
        )
    with staged(obj):
        if basis in selected:
            obj.shape_key_clear()
        else:
            for name in args.names:
                obj.shape_key_remove(obj.data.shape_keys.key_blocks[name])
        if not obj.data.shape_keys and TOPOLOGY_KEY in obj.data:
            del obj.data[TOPOLOGY_KEY]
    return ShapeKeysRemoveResult(
        object_name=obj.name,
        removed=args.names,
        remaining=len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0,
    )


def compare(args: DeformationCompareArguments) -> DeformationCompareResult:
    start = time.perf_counter()
    results = []
    work = 0
    for pair in args.pairs:
        obj = geo.context(pair.object_name)
        target = geo.context(pair.target)
        a, signature = geo.evaluated(obj)
        b, other = geo.evaluated(target)
        if signature != other:
            geo.fail(
                "Target comparison requires identical ordered topology, "
                "not just vertex count"
            )
        chosen = geo.selection(obj, pair.selector)
        work += len(a) + len(b)
        if work > 1_000_000:
            geo.fail("Comparison exceeds 1000000 evaluated vertex samples")
        distances = {
            i: ((obj.matrix_world @ a[i]) - (target.matrix_world @ b[i])).length
            for i in chosen
        }
        values = list(distances.values())
        results.append(
            TargetDeviation(
                object_name=obj.name,
                target=target.name,
                vertex_count=len(chosen),
                distance=deformation_qa.distribution(values),
                rms=math.sqrt(sum(v * v for v in values) / len(values)),
                worst=[
                    DeviationSample(vertex=i, distance=distances[i])
                    for i in sorted(chosen, key=lambda i: (-distances[i], i))[
                        : args.sample_limit
                    ]
                ],
            )
        )
    return DeformationCompareResult(
        comparisons=results, processing_seconds=time.perf_counter() - start
    )
