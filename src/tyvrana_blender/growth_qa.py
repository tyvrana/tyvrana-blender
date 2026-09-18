"""Restoring sampled attachment, orientation and signed clearance diagnostics."""

import math
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

from . import growth, growth_nodes, rig
from .growth_models import (
    GrowthInspectArguments,
    GrowthQA,
    GrowthSampleArguments,
    GrowthSampleResult,
    GrowthSampleRow,
)
from .instances import evaluated_surface
from .layer_geometry import SurfaceCache
from .layer_math import sample_indices
from .motion import restored_state
from .rig_models import ArmaturePoseArguments


def template_points(obj: Any, group: Any, count: int) -> list[Any]:
    graph = growth_nodes.GrowthGraph("Growth template QA")
    clone = None
    evaluated = None
    mesh = None
    try:
        source = graph.info(obj, "Evaluated growth", "ORIGINAL")
        realize = graph.node("GeometryNodeRealizeInstances")
        graph.wire(source.outputs["Geometry"], realize.inputs["Geometry"])
        graph.wire(realize.outputs["Geometry"], graph.output.inputs["Geometry"])
        carrier = bpy.data.meshes.new("Growth template QA")
        clone = bpy.data.objects.new("Growth template QA", carrier)
        clone.matrix_world = obj.matrix_world.copy()
        mod = clone.modifiers.new("Template QA", "NODES")
        mod.node_group = graph.group
        bpy.context.scene.collection.objects.link(clone)
        clone.hide_set(True)
        clone.hide_render = True
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        evaluated = clone.evaluated_get(dg)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
        if mesh is None:
            return []
        if len(mesh.vertices) > 2000000:
            growth.fail("Template QA exceeds2000000 evaluated vertices")
        return [
            obj.matrix_world @ mesh.vertices[i].co
            for i in sample_indices(range(len(mesh.vertices)), count)
        ]
    finally:
        if evaluated is not None and mesh is not None:
            evaluated.to_mesh_clear()
        if clone:
            clone_data = clone.data
            bpy.data.objects.remove(clone, do_unlink=True)
            if not clone_data.users:
                bpy.data.meshes.remove(clone_data)
        bpy.data.node_groups.remove(graph.group)


def inspect_qa(obj: Any, data: Any, args: GrowthInspectArguments) -> GrowthQA:
    chosen = sample_indices(range(len(data.curves)), args.qa_samples)
    errors = []
    dots = []
    flows = []
    reversed_count = 0
    flips = 0
    clearances = []
    positions = []
    surface = obj.data.surface
    matrix = obj.matrix_world
    normal_matrix = surface.matrix_world.to_3x3().inverted().transposed()
    with evaluated_surface(surface, obj.data.surface_uv_map) as sf:
        for i in chosen:
            curve = data.curves[i]
            points = [matrix @ p.position for p in curve.points]
            uv = Vector((*data.attributes["surface_uv_coordinate"].data[i].vector, 0))
            root, normal = sf.sample(uv)
            offset = data.attributes["growth_offset"].data[i].value
            expected = surface.matrix_world @ (root + normal * offset)
            errors.append((points[0] - expected).length)
            tangent = (points[1] - points[0]).normalized()
            n = (normal_matrix @ normal).normalized()
            dots.append(tangent.dot(n))
            reversed_count += int(dots[-1] < 0)
            flow = (
                surface.matrix_world.to_3x3()
                @ data.attributes["growth_flow"].data[i].vector
            ).normalized()
            flows.append(tangent.dot(flow))
            normals = [
                data.attributes["growth_frame_normal"].data[j].vector.copy()
                for j in range(
                    curve.first_point_index,
                    curve.first_point_index + curve.points_length,
                )
            ]
            flips += sum(
                a.dot(b) < -0.5 for a, b in zip(normals[:-1], normals[1:], strict=True)
            )
            segments = [
                (b - a).length for a, b in zip(points[:-1], points[1:], strict=True)
            ]
            length = sum(segments)
            travel = 0.0
            for a, b, size in zip(points[:-1], points[1:], segments, strict=True):
                for step in range(1, 5):
                    fraction = step / 4
                    if travel + size * fraction > length * args.root_exclusion:
                        positions.append(a.lerp(b, fraction))
                travel += size
    template = []
    if args.template_samples:
        _, _, _, group = growth.owned(obj.name)
        template = template_points(obj, group, args.template_samples)
        positions.extend(template)
    with SurfaceCache() as cache:
        targets = [
            cache.get(n) for n in dict.fromkeys([surface.name, *args.clearance_objects])
        ]
        for p in positions:
            distances = []
            for target in targets:
                nearest, normal, _, distance = target.bvh().find_nearest(p)
                if nearest is not None:
                    distances.append(
                        float(distance)
                        * (-1 if (p - nearest).dot(normal) < -1e-7 else 1)
                    )
            if distances:
                clearances.append(min(distances))
    return GrowthQA(
        sampled_roots=len(errors),
        maximum_root_error=max(errors) if errors else None,
        rms_root_error=math.sqrt(sum(e * e for e in errors) / len(errors))
        if errors
        else None,
        tangent_normal_min=min(dots) if dots else None,
        tangent_normal_max=max(dots) if dots else None,
        tangent_flow_mean=sum(flows) / len(flows) if flows else None,
        reversed_guides=reversed_count,
        frame_flips=flips,
        template_samples=len(template),
        clearance_samples=len(clearances),
        below_clearance=sum(d < args.clearance for d in clearances),
        penetrating_samples=sum(d < -1e-6 for d in clearances),
        minimum_signed_clearance=min(clearances, default=None),
    )


def sample(args: GrowthSampleArguments) -> GrowthSampleResult:
    from . import growth_dynamics

    if args.poses and growth_dynamics.KEY in bpy.data.objects.get(args.object_name, {}):
        growth.fail(
            "Cached dynamics require their baked animation frames; "
            "clear for pose sweeps"
        )
    if len(args.frames or args.poses) * args.qa_samples > 4096:
        growth.fail("Sweep exceeds4096 root samples; reduce samples/poses")
    rig.idle()
    rows = []
    inspect_args = GrowthInspectArguments.model_validate(
        args.model_dump(exclude={"armature_object", "poses", "frames"})
    )
    armature = rig.armature(args.armature_object) if args.armature_object else None
    keys = {}
    for pose in args.poses:
        if pose.targets:
            growth.fail("Growth sweeps do not consume deformation comparison targets")
        for value in pose.shape_values:
            obj = bpy.data.objects.get(value.object_name)
            key = (
                obj.data.shape_keys.key_blocks.get(value.key)
                if obj and obj.type == "MESH" and obj.data.shape_keys
                else None
            )
            if key is None:
                growth.fail("Pose references a missing shape key")
            keys[(value.object_name, value.key)] = key
    with restored_state():
        for n in range(len(args.frames or args.poses)):
            if args.frames:
                frame = float(args.frames[n])
                bpy.context.scene.frame_set(
                    math.floor(frame), subframe=frame - math.floor(frame)
                )
                name = str(frame)
            else:
                pose = args.poses[n]
                name = pose.name
                for key in keys.values():
                    key.value = 0
                for value in pose.shape_values:
                    keys[(value.object_name, value.key)].value = value.value
                if armature:
                    rig.pose(
                        ArmaturePoseArguments(
                            object_name=armature.name,
                            reset=True,
                            bones=pose.bones,
                            sample_limit=0,
                        )
                    )
            bpy.context.view_layer.update()
            result = growth.inspect(inspect_args).summary
            rows.append(GrowthSampleRow(sample=name, valid=result.valid, qa=result.qa))
    return GrowthSampleResult(object_name=args.object_name, restored=True, samples=rows)
