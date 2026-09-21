"""Bounded frame QA reusing existing deformation, joint, volume and layer math."""

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import (
    correctives,
    couplings,
    deformation_qa,
    joints,
    layer_geometry,
    layers,
    mechanics_contact,
    modifiers,
    motion_scope,
    references,
    retopo_geometry,
    rig,
    volumes,
)
from . import motion_channels as channels
from .corrective_models import DeformationCompareArguments
from .geometry_qa import Budget
from .mechanics_models import ContactState, EnvelopeAggregate, EnvelopeSummary
from .motion_models import (
    MotionFrame,
    MotionMetric,
    MotionSampleArguments,
    MotionSampleResult,
    MotionViolation,
)
from .reference_models import MeasurementArguments

MAX_VERTEX_SAMPLES = 2_000_000
MAX_METRICS = 1024


@contextmanager
def restored_state(scope: list[Any] | None = None) -> Iterator[None]:
    scene = bpy.context.scene
    objects = list(scene.objects) if scope is None else scope
    if scope is None and len(objects) > 256:
        channels.fail("Unscoped restoration exceeds256 scene objects")
    frame, subframe = scene.frame_current, scene.frame_subframe
    transforms = []
    scalar: list[tuple[Any, str, Any, bool]] = []
    positions = []
    for obj in objects:
        owners = [obj, *list(obj.pose.bones)] if obj.type == "ARMATURE" else [obj]
        for owner in owners:
            transforms.append(
                (
                    owner,
                    owner.rotation_mode,
                    owner.location.copy(),
                    owner.rotation_euler.copy(),
                    owner.rotation_quaternion.copy(),
                    tuple(owner.rotation_axis_angle),
                    owner.scale.copy(),
                )
            )
        if obj.type == "ARMATURE":
            positions.append((obj.data, obj.data.pose_position))
        if obj.type == "MESH" and obj.data.shape_keys:
            scalar.extend(
                (key, "value", key.value, False)
                for key in obj.data.shape_keys.key_blocks
            )
        for name in channels.control_catalog(obj):
            prop = channels.CONTROL_PREFIX + name
            scalar.append((obj, prop, obj[prop], True))
        for owner in owners:
            scalar.extend(
                (c, "influence", c.influence, False) for c in owner.constraints
            )
    if len(transforms) + len(scalar) > 8192:
        channels.fail("Motion restoration exceeds8192 native channels/resources")
    try:
        yield
    finally:
        scene.frame_set(frame, subframe=subframe)
        for owner, mode, loc, euler, quat, axis, scale in transforms:
            owner.rotation_mode = mode
            owner.location = loc
            owner.rotation_euler = euler
            owner.rotation_quaternion = quat
            owner.rotation_axis_angle = axis
            owner.scale = scale
        for owner, prop, value, custom in scalar:
            if custom:
                owner[prop] = value
            else:
                setattr(owner, prop, value)
        for owner, position in positions:
            owner.pose_position = position
        bpy.context.view_layer.update()
        if scene.frame_current != frame or abs(scene.frame_subframe - subframe) > 1e-6:
            channels.fail("Native frame restoration failed")


def aggregate(
    rows: list[MotionFrame], args: MotionSampleArguments
) -> tuple[list[MotionMetric], list[MotionViolation], int]:
    names = sorted({name for row in rows for name in row.values})
    if len(names) > MAX_METRICS:
        channels.fail("Motion exceeds1024 aggregate metrics; reduce diagnostic scope")
    thresholds = {t.metric: t for t in args.thresholds}
    unknown = set(thresholds) - set(names)
    if unknown:
        channels.fail("Threshold metric is absent: " + ", ".join(sorted(unknown)[:4]))
    metrics = []
    violations = []
    count = 0
    for name in names:
        samples = [(row.frame, row.values.get(name)) for row in rows]
        values = [
            (frame, value)
            for frame, value in samples
            if value is not None and math.isfinite(value)
        ]
        ordered = sorted(v for _, v in values)
        threshold = thresholds.get(name)
        bad = []
        for frame, value in samples:
            reason = None
            if value is None:
                if threshold:
                    reason = "diagnostic unavailable"
            elif name.endswith(".valid") and value == 0:
                reason = "native relationship invalid"
            elif name.endswith(".limit_violation") and value > 1e-5:
                reason = "evaluated joint outside limit"
            elif threshold and (
                (threshold.minimum is not None and value < threshold.minimum)
                or (threshold.maximum is not None and value > threshold.maximum)
            ):
                reason = "outside requested threshold"
            if reason:
                bad.append(
                    MotionViolation(
                        frame=frame, metric=name, value=value, reason=reason
                    )
                )
        count += len(bad)
        violations.extend(bad)
        lo = min(values, key=lambda p: p[1]) if values else None
        hi = max(values, key=lambda p: p[1]) if values else None
        metrics.append(
            MotionMetric(
                name=name,
                count=len(values),
                minimum=lo[1] if lo else None,
                maximum=hi[1] if hi else None,
                mean=sum(ordered) / len(ordered) if ordered else None,
                p05=deformation_qa.percentile(ordered, 0.05) if ordered else None,
                p50=deformation_qa.percentile(ordered, 0.5) if ordered else None,
                p95=deformation_qa.percentile(ordered, 0.95) if ordered else None,
                minimum_frame=lo[0] if lo else None,
                maximum_frame=hi[0] if hi else None,
                violation_count=len(bad),
            )
        )
    return metrics, violations[: args.violation_limit], count


def sample(args: MotionSampleArguments) -> MotionSampleResult:
    rig.idle()
    start = time.perf_counter()
    frames = args.samples()
    reference = args.reference_frame if args.reference_frame is not None else frames[0]
    resolved = [(c.name, channels.resolve(c.channel)) for c in args.channels]
    measurement_args = (
        MeasurementArguments.model_validate(
            {"queries": [q.model_dump() for q in args.measurements]}
        )
        if args.measurements
        else None
    )
    catalog = couplings.records() if args.couplings else {}
    if any(n not in catalog for n in args.couplings):
        channels.fail(
            "Requested coupling is missing; inspect named relationships first"
        )
    armature: Any = rig.armature(args.armature_object) if args.armature_object else None
    if args.bones and any(n not in armature.pose.bones for n in args.bones):
        channels.fail("Motion QA references a missing bone")
    objects = [modifiers.object_mesh(n) for n in args.objects]
    estimated = sum(len(o.data.vertices) for o in objects) * (len(frames) + 1)
    if estimated > MAX_VERTEX_SAMPLES:
        channels.fail("Motion exceeds2000000 vertex samples; reduce frames/geometry")
    layer_catalog = layers.records() if args.layers else {}
    samples = 0
    rows = []
    scope = motion_scope.resolve(args, catalog, layer_catalog)
    restored_objects = motion_scope.restoration_objects(scope)
    contact_rows: dict[str, list[tuple[int, EnvelopeSummary]]] = {
        c.name: [] for c in args.contacts
    }
    contact_budget = Budget(args.contact_max_tests)
    with restored_state(restored_objects):
        scene = bpy.context.scene
        scene.frame_set(reference)
        bpy.context.view_layer.update()
        rest = []
        for obj in objects:
            snapshot = rig.snapshot(obj, retopo_geometry.graph(obj))
            samples += len(snapshot[0])
            rest.append(snapshot)
            if samples * (len(frames) + 1) > MAX_VERTEX_SAMPLES:
                channels.fail("Evaluated mesh/frame work exceeds2000000 vertex samples")
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            values: dict[str, float | None] = {}
            if measurement_args:
                for row in references.measure(measurement_args).measurements:
                    prefix = "measurement." + row.name + "."
                    values[prefix + "value"] = row.value
                    if row.deviation is not None:
                        values[prefix + "error"] = abs(row.deviation)
                        values[prefix + "valid"] = float(bool(row.within_tolerance))
            for name, target in resolved:
                values["channel." + name] = target.value(evaluated=True)
            for name in args.couplings:
                summary = couplings.summary(catalog[name], catalog)
                for metric in [
                    "source_value",
                    "mapped_value",
                    "target_value",
                    "evaluated_target_value",
                    "mapping_error",
                    "constrained_error",
                    "saturated",
                    "valid",
                ]:
                    value = getattr(summary, metric)
                    values["coupling." + name + "." + metric] = (
                        float(value) if value is not None else None
                    )
            if armature:
                graph = bpy.context.evaluated_depsgraph_get()
                evaluated = armature.evaluated_get(graph)
                for name in args.bones:
                    p = armature.pose.bones[name]
                    e = evaluated.pose.bones[name]
                    joint_result = joints.evaluation(armature, evaluated, name)
                    limits = joints.model_limits(p)
                    values["joint." + name + ".valid"] = float(
                        not joints.structure_issues(armature, armature.data.bones[name])
                    )
                    for index, axis in enumerate("xyz"):
                        prefix = "joint." + name + "." + axis
                        requested = joint_result.requested_rotation[index]
                        actual = joint_result.evaluated_rotation[index]
                        values[prefix + ".requested"] = requested
                        values[prefix + ".evaluated"] = actual
                        values[prefix + ".constraint_delta"] = abs(actual - requested)
                        span = getattr(limits, axis) if limits else None
                        if span:
                            values[prefix + ".limit_violation"] = max(
                                0, span.minimum - actual, actual - span.maximum
                            )
                            values[prefix + ".requested_excess"] = max(
                                0, span.minimum - requested, requested - span.maximum
                            )
                            values[prefix + ".at_limit"] = float(
                                min(
                                    abs(actual - span.minimum),
                                    abs(actual - span.maximum),
                                )
                                < 1e-5
                            )
                        values["joint." + name + ".head_" + axis] = float(
                            (evaluated.matrix_world @ e.head)[index]
                        )
                        values["joint." + name + ".tail_" + axis] = float(
                            (evaluated.matrix_world @ e.tail)[index]
                        )
                    values["joint." + name + ".reach"] = float(
                        (
                            evaluated.matrix_world @ e.tail
                            - evaluated.matrix_world.translation
                        ).length
                    )
            for obj, baseline in zip(objects, rest, strict=True):
                current = rig.snapshot(obj, retopo_geometry.graph(obj))
                samples += len(current[0])
                if current[1:3] != baseline[1:3] or len(current[0]) != len(baseline[0]):
                    channels.fail(
                        "Evaluated topology changed across frames; use stable topology"
                    )
                qa = deformation_qa.compare(baseline, current, 0)
                prefix = "mesh." + obj.name + "."
                values[prefix + "volume_ratio"] = qa.volume_ratio
                values[prefix + "collapsed_triangles"] = float(
                    qa.collapsed_triangle_count
                )
                for metric in [
                    "edge_ratios",
                    "triangle_area_ratios",
                    "triangle_angle_change_radians",
                ]:
                    distribution = getattr(qa, metric)
                    for statistic in ["minimum", "maximum", "p95", "p99"]:
                        values[prefix + metric + "." + statistic] = (
                            getattr(distribution, statistic) if distribution else None
                        )
            if args.targets:
                samples += sum(
                    len(modifiers.object_mesh(p.object_name).data.vertices)
                    + len(modifiers.object_mesh(p.target).data.vertices)
                    for p in args.targets
                )
                for target_result in correctives.compare(
                    DeformationCompareArguments(pairs=args.targets, sample_limit=0)
                ).comparisons:
                    prefix = (
                        "target."
                        + target_result.object_name
                        + "."
                        + target_result.target
                        + "."
                    )
                    values[prefix + "rms"] = target_result.rms
                    values[prefix + "maximum"] = target_result.distance.maximum
            if samples > MAX_VERTEX_SAMPLES:
                channels.fail("Motion exceeds2000000 evaluated vertex samples")
            with layer_geometry.SurfaceCache(
                max_work=MAX_VERTEX_SAMPLES - samples
            ) as cache:
                for query in args.volumes:
                    volume_result = volumes.summary(query, cache)
                    prefix = "volume." + query.object_name + "."
                    values[prefix + "valid"] = float(volume_result.valid)
                    values[prefix + "volume"] = volume_result.evaluated.volume
                    values[prefix + "ratio"] = volume_result.volume_ratio
                    if volume_result.path:
                        values[prefix + "path_length"] = volume_result.path.length
                        values[prefix + "path_ratio"] = volume_result.path.length_ratio
                        values[prefix + "attachment_error"] = (
                            volume_result.path.attachment_error_max
                        )
                if args.layers:
                    for index, layer_query in enumerate(args.layers.queries):
                        layer_result = layers.inspect_one(
                            layer_query, args.layers, cache, layer_catalog
                        )
                        prefix = "layer." + str(index) + "."
                        values[prefix + "valid"] = float(layer_result.valid)
                        for metric in [
                            "separation",
                            "oriented_normal_gap",
                            "normal_ray_distance",
                            "tangential_movement",
                            "normal_change",
                        ]:
                            distribution = getattr(layer_result, metric)
                            for statistic in ["minimum", "maximum", "p95"]:
                                values[prefix + metric + "." + statistic] = (
                                    getattr(distribution, statistic)
                                    if distribution
                                    else None
                                )
                        for metric in [
                            "contact_samples",
                            "below_minimum_samples",
                            "negative_side_samples",
                            "negative_side_depth_max",
                            "normal_ray_misses",
                        ]:
                            value = getattr(layer_result, metric)
                            values[prefix + metric] = (
                                float(value) if value is not None else None
                            )
                samples += cache.vertices
            if args.contacts:
                with layer_geometry.SurfaceCache(
                    max_work=MAX_VERTEX_SAMPLES - samples
                ) as cache:
                    for envelope in args.contacts:
                        contact = mechanics_contact.evaluate(
                            envelope, cache, contact_budget, 0
                        )
                        contact_rows[envelope.name].append((frame, contact))
                        prefix = "contact." + envelope.name + "."
                        values[prefix + "minimum_gap"] = contact.minimum_gap
                        values[prefix + "maximum_gap"] = contact.maximum_gap
                        values[prefix + "penetration_max"] = contact.penetration_max
                        values[prefix + "valid"] = float(
                            contact.classification == "PERMITTED_CONTACT"
                        )
                        values[prefix + "uncertain_samples"] = float(
                            contact.uncertain_samples
                        )
                    samples += cache.vertices
                if samples > MAX_VERTEX_SAMPLES:
                    channels.fail("Motion exceeds2000000 evaluated vertex samples")
            if len(values) > MAX_METRICS:
                channels.fail("Motion exceeds1024 scalar metrics; reduce diagnostics")
            rows.append(MotionFrame(frame=frame, values=values))
        if len(rows[0].values) * len(args.detail_frames) > 2048:
            channels.fail("Motion detail exceeds2048 scalars; reduce detail_frames")
        metrics, violations, count = aggregate(rows, args)
    contact_aggregates = []
    priorities: dict[ContactState, int] = {
        "PERMITTED_CONTACT": 0,
        "SEPARATED": 1,
        "UNCERTAIN": 2,
        "INVALID_PENETRATION": 3,
    }
    for name, evidence in contact_rows.items():
        worst_frame, worst = max(
            evidence,
            key=lambda item: (
                priorities[item[1].classification],
                item[1].penetration_max or 0,
                item[1].maximum_gap or 0,
            ),
        )
        minima = [r.minimum_gap for _, r in evidence if r.minimum_gap is not None]
        maxima = [r.maximum_gap for _, r in evidence if r.maximum_gap is not None]
        depths = [
            r.penetration_max for _, r in evidence if r.penetration_max is not None
        ]
        contact_aggregates.append(
            EnvelopeAggregate(
                name=name,
                counts={
                    state: sum(r.classification == state for _, r in evidence)
                    for state in priorities
                },
                worst_classification=worst.classification,
                worst_frame=worst_frame,
                minimum_gap=min(minima) if minima else None,
                maximum_gap=max(maxima) if maxima else None,
                penetration_max=max(depths) if depths else None,
                uncertain_samples=sum(
                    r.classification == "UNCERTAIN" for _, r in evidence
                ),
                failed_samples=sum(
                    r.classification in {"INVALID_PENETRATION", "SEPARATED"}
                    for _, r in evidence
                ),
            )
        )
    result = MotionSampleResult(
        scoped_object_count=len(scope),
        restoration_object_count=len(restored_objects),
        contacts=contact_aggregates,
        sampled_frames=frames,
        reference_frame=reference,
        metrics=metrics,
        details=[r for r in rows if r.frame in args.detail_frames],
        violations=violations,
        violation_count=count,
        restored=True,
        evaluated_vertex_samples=samples,
        processing_seconds=time.perf_counter() - start,
        limitations=[
            (
                "Uniform/explicit integer samples do not guarantee continuous "
                "extrema or collision safety; no adaptive optimization or "
                "simulation cache control."
            ),
            (
                "Deformation ratios compare against evaluated reference_frame, "
                "not an implicit rest pose. Shape/target/volume/layer diagnostics"
                " retain their documented proxy limits."
            ),
            (
                "Aggregate percentiles describe the sampled per-frame scalar "
                "values, not pooled mesh elements. Joint angles use principal "
                "LOCAL XYZ radians; endpoint paths are world coordinates."
            ),
            (
                "Active actions are evaluated in place, never reassigned. "
                "Original frame/subframe, transforms, key/control values and "
                "constraint influences restore on success or failure."
            ),
        ],
    )

    if len(result.model_dump_json().encode()) > 393216:
        channels.fail("Motion result exceeds384KiB; reduce diagnostics/detail frames")
    return result
