"""Evaluate deterministic poses with frozen rest regions and restored state."""

import time
from typing import Any

import bpy  # type: ignore[import-not-found]

from . import deformation_qa, modifiers, retopo_geometry, rig
from .deformation_sweep_models import (
    MAX_SWEEP_VERTEX_SAMPLES,
    DeformationSweepArguments,
    DeformationSweepResult,
    PoseEvaluation,
    PoseMeshEvaluation,
    RegionEvaluation,
)
from .mesh_selectors import SelectionError
from .operations import OperationError
from .rig_models import ArmaturePoseArguments
from .topology_selection import contains, region_matrix


def subset(snapshot: Any, chosen: set[int]) -> Any:
    points, edges, triangles, weights = snapshot
    return (
        points,
        [e for e in edges if all(i in chosen for i in e)],
        [t for t in triangles if all(i in chosen for i in t)],
        [row if i in chosen else {} for i, row in enumerate(weights)],
    )


def execute(args: DeformationSweepArguments) -> DeformationSweepResult:
    start = time.perf_counter()
    armature = rig.armature(args.armature_object, edit=True)
    objects = [modifiers.object_mesh(n) for n in args.objects]
    if any(rig.binding_modifier(obj).object != armature for obj in objects):
        rig.fail("Every sweep mesh requires an owned binding to the named armature")
    if any(
        n not in armature.data.bones or not armature.data.bones[n].use_deform
        for n in args.bone_names
    ):
        rig.fail("Region bones must exist and deform")
    requested = {b.name for pose in args.poses for b in pose.bones}
    if len(requested) > 128 or any(n not in armature.pose.bones for n in requested):
        rig.fail("Sweep pose references a missing bone")
    graph = retopo_geometry.graph(armature)
    position = armature.data.pose_position
    saved = [
        (
            p,
            p.rotation_mode,
            p.location.copy(),
            p.rotation_euler.copy(),
            p.rotation_quaternion.copy(),
            tuple(p.rotation_axis_angle),
            p.scale.copy(),
        )
        for p in armature.pose.bones
    ]
    results = []
    samples = 0
    try:
        armature.data.pose_position = "REST"
        bpy.context.view_layer.update()
        rest = []
        for obj in objects:
            row = rig.snapshot(obj, graph, args.bone_names)
            samples += len(row[0]) * len(args.poses)
            if samples > MAX_SWEEP_VERTEX_SAMPLES:
                rig.fail(
                    "Sweep exceeds 1000000 evaluated vertex/pose samples; reduce "
                    "objects, subdivision, or poses"
                )
            rest.append(row)
        try:
            frames = [region_matrix(region) for region in args.regions]
        except SelectionError as exc:
            raise OperationError("invalid_arguments", str(exc)) from exc
        chosen = [
            [
                {i for i, p in enumerate(row[0]) if contains(frame @ p, region)}
                for frame, region in zip(frames, args.regions, strict=True)
            ]
            for row in rest
        ]
        regional_rest = [
            [subset(row, selected) for selected in masks]
            for row, masks in zip(rest, chosen, strict=True)
        ]
        armature.data.pose_position = "POSE"
        for definition in args.poses:
            rig.pose(
                ArmaturePoseArguments(
                    object_name=armature.name,
                    reset=True,
                    bones=definition.bones,
                    sample_limit=0,
                )
            )
            posed = [rig.snapshot(obj, graph, args.bone_names) for obj in objects]
            meshes = []
            for index, (obj, a, b) in enumerate(zip(objects, rest, posed, strict=True)):
                if len(a[0]) != len(b[0]) or a[1:3] != b[1:3]:
                    rig.fail(
                        "Evaluated topology changed across poses; use "
                        "topology-stable modifiers"
                    )
                meshes.append(
                    PoseMeshEvaluation(
                        object_name=obj.name,
                        vertex_count=len(a[0]),
                        qa=deformation_qa.compare(a, b, args.sample_limit),
                        regions=[
                            RegionEvaluation(
                                name=region.name,
                                vertex_count=len(mask),
                                qa=deformation_qa.compare(
                                    selected, b, args.sample_limit
                                ),
                            )
                            for region, mask, selected in zip(
                                args.regions,
                                chosen[index],
                                regional_rest[index],
                                strict=True,
                            )
                        ],
                    )
                )
            evaluated = armature.evaluated_get(graph)
            rotations = {}
            for name in sorted(requested):
                p = evaluated.pose.bones[name]
                basis = evaluated.convert_space(
                    pose_bone=p, matrix=p.matrix, from_space="POSE", to_space="LOCAL"
                )
                rotations[name] = list(basis.to_euler("XYZ"))
            results.append(
                PoseEvaluation(
                    name=definition.name, meshes=meshes, evaluated_rotations=rotations
                )
            )
    finally:
        for p, mode, location, euler, quaternion, axis, scale in saved:
            p.rotation_mode = mode
            p.location = location
            p.rotation_euler = euler
            p.rotation_quaternion = quaternion
            p.rotation_axis_angle = axis
            p.scale = scale
        armature.data.pose_position = position
        bpy.context.view_layer.update()
    return DeformationSweepResult(
        armature_object=armature.name,
        poses=results,
        restored=True,
        inspection_seconds=time.perf_counter() - start,
        evaluated_vertex_samples=samples,
        limitations=[
            "Frame boxes select evaluated rest vertices; edges and triangles need "
            "every endpoint inside. Empty regions return empty distributions.",
            "Edge/area ratios and absolute corner-angle changes are diagnostics, "
            "not an artistic acceptance classifier. Collapse means area ratio "
            "below 0.01; normal rotation is not evidence of inversion.",
            "Closed consistently wound region volume is a proxy; open regions "
            "return null. Self-intersection, tissue physics and correctives are "
            "not evaluated.",
            "Weights, topology, joint frames/limits and pose jointly affect "
            "strain; compare controlled variants. Authored selectors and evaluated "
            "regions use different vertex domains.",
        ],
    )
