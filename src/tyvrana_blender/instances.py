"""Surface-bound mesh instances evaluated by a small owned Geometry Nodes graph."""

import hashlib
import json
import math
import random
import time
from contextlib import contextmanager
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix, Vector  # type: ignore[import-not-found]
from mathutils.bvhtree import BVHTree  # type: ignore[import-not-found]
from mathutils.geometry import barycentric_transform  # type: ignore[import-not-found]

from . import mesh
from .instance_models import (
    InstanceSample,
    MeshCreateArguments,
    SurfaceDistribution,
    SurfaceInstancesSummary,
)
from .mesh_models import MeshSummary
from .operations import OperationError

KEY = "tyvrana_surface_instances"
MAX_SURFACE_TRIANGLES = 250_000
MAX_EQUIVALENT_VERTICES = 4_000_000
ATTRS = {
    "root": "tyvrana_root_uv",
    "direction": "tyvrana_direction_uv",
    "scale": "tyvrana_instance_scale",
}


def fail(message: str) -> None:
    raise OperationError("invalid_context", message)


def object_mesh(name: str) -> Any:
    obj = bpy.context.scene.objects.get(name)
    if obj is None or obj.type != "MESH":
        fail(f'Mesh object "{name}" does not exist in the current scene')
    if obj.mode != "OBJECT":
        fail("Surface instancing requires Object Mode")
    return obj


def create_mesh(args: MeshCreateArguments) -> MeshSummary:
    if bpy.data.objects.get(args.name) is not None:
        fail("Choose an unused object name")
    materials = []
    for name in args.materials:
        material = bpy.data.materials.get(name)
        if material is None:
            fail(f'Material "{name}" does not exist')
        materials.append(material)
    data = bpy.data.meshes.new(args.name)
    obj = None
    try:
        data.from_pydata(args.vertices, [], args.faces)
        if data.validate(clean_customdata=False):
            fail("Native validation changed invalid input geometry")
        if any(p.area <= 1e-16 for p in data.polygons):
            fail("Faces must have positive area")
        for material in materials:
            data.materials.append(material)
        for i, face in enumerate(data.polygons):
            face.use_smooth = args.smooth
            if args.material_indices is not None:
                face.material_index = args.material_indices[i]
        if args.corner_uvs is not None:
            uv = data.uv_layers.new(name=args.uv_map)
            for item, value in zip(uv.uv, args.corner_uvs, strict=True):
                item.vector = value
        obj = bpy.data.objects.new(args.name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.hide_render = args.hidden
        obj.hide_set(args.hidden)
        return mesh.inspect(obj)
    except BaseException:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
        raise


class Surface:
    def __init__(self, obj: Any, data: Any, uv_map: str) -> None:
        self.obj = obj
        self.data = data
        layer = data.uv_layers.get(uv_map)
        if layer is None:
            fail("Surface UV map is missing")
        data.calc_loop_triangles()
        if not 0 < len(data.loop_triangles) <= MAX_SURFACE_TRIANGLES:
            fail("Surface exceeds bounded triangle capacity or is empty")
        self.triangles = list(data.loop_triangles)
        self.positions = [v.co.copy() for v in data.vertices]
        self.uvs = [Vector((*item.vector, 0.0)) for item in layer.uv]
        self.normals = [n.vector.copy() for n in data.corner_normals]
        self.bvh = BVHTree.FromPolygons(
            self.positions, [t.vertices for t in self.triangles], all_triangles=True
        )
        self.uv_bvh = BVHTree.FromPolygons(
            self.uvs, [t.loops for t in self.triangles], all_triangles=True
        )

    def project(self, point: Any, distance: float) -> tuple[Any, Any]:
        hit, _, index, separation = self.bvh.find_nearest(point, distance)
        if hit is None or index is None:
            fail("Guide or direction sample exceeds max_projection_distance")
        tri = self.triangles[index]
        uv = barycentric_transform(
            hit,
            *(self.positions[i] for i in tri.vertices),
            *(self.uvs[i] for i in tri.loops),
        )
        resolved, _ = self.sample(uv)
        if (resolved - hit).length > max(1e-5, separation * 1e-4):
            fail("UV binding is ambiguous or does not resolve to the projected surface")
        return hit, uv

    def sample(self, uv: Any) -> tuple[Any, Any]:
        # Blender stores UVs and BVH projections in single precision. A valid
        # interior point can round more than 1e-7 from its nearest projection.
        # Keep disconnected-overlap validation below; this is a UV tolerance,
        # not permission to bind to a different surface location.
        found = self.uv_bvh.find_nearest_range(uv, 1e-6)
        if not found:
            fail("A stored UV binding no longer resolves on the surface")
        values = []
        for point, _, index, _ in found:
            tri = self.triangles[index]
            coords = [self.uvs[i] for i in tri.loops]
            p = barycentric_transform(
                point, *coords, *(self.positions[i] for i in tri.vertices)
            )
            n = barycentric_transform(
                point, *coords, *(self.normals[i] for i in tri.loops)
            ).normalized()
            values.append((p, n))
        if any((p - values[0][0]).length > 1e-5 for p, _ in values[1:]):
            fail("UV binding overlaps disconnected surface locations")
        return values[0]  # Shared triangle edges have the same surface position.


@contextmanager
def evaluated_surface(obj: Any, uv_map: str) -> Any:
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh(
        preserve_all_data_layers=True, depsgraph=bpy.context.evaluated_depsgraph_get()
    )
    try:
        yield Surface(obj, data, uv_map)
    finally:
        evaluated.to_mesh_clear()


def polyline(points: list[list[float]], t: float) -> Any:
    vertices = [Vector(p) for p in points]
    lengths = [(b - a).length for a, b in zip(vertices[:-1], vertices[1:], strict=True)]
    remaining = t * sum(lengths)
    for i, length in enumerate(lengths):
        if length > 0 and (remaining <= length or i == len(lengths) - 1):
            return vertices[i].lerp(vertices[i + 1], remaining / length)
        remaining -= length
    return vertices[-1]


def guide_point(spec: SurfaceDistribution, u: float, t: float) -> Any:
    across = min(len(spec.guides) - 1.000001, max(0, u) * (len(spec.guides) - 1))
    i = int(across)
    return polyline(spec.guides[i], t).lerp(polyline(spec.guides[i + 1], t), across - i)


def validate_inputs(spec: SurfaceDistribution, existing: Any = None) -> tuple[Any, Any]:
    source = object_mesh(spec.surface_object)
    prototype = object_mesh(spec.prototype_object)
    if source == existing or prototype == existing or KEY in source or KEY in prototype:
        fail("Nested distributions and self references are unsupported")
    if any(abs(abs(v) - 1) > 1e-5 for v in source.matrix_world.to_scale()) or (
        source.matrix_world.determinant() <= 0
    ):
        fail("Apply nonunit/mirrored surface scale before binding instances")
    if source.data.library or source.override_library or prototype.override_library:
        fail("Linked or overridden surface state is unsupported")
    if any(m.type == "NODES" for m in prototype.modifiers):
        fail("Prototype Geometry Nodes may contain nested instances")
    for obj in (source, prototype):
        for mod in obj.modifiers:
            if mod.show_viewport != mod.show_render or (
                mod.type == "SUBSURF" and mod.levels != mod.render_levels
            ):
                fail("Surface/prototype viewport and render evaluation must agree")
    return source, prototype


def make_points(
    surface: Surface, spec: SurfaceDistribution
) -> tuple[Any, dict[str, Any]]:
    rng = random.Random(spec.seed)
    roots, directions, scales, positions = [], [], [], []
    for row in range(spec.rows):
        for col in range(spec.columns):
            t = (row + 0.5 + rng.uniform(-1, 1) * spec.spacing_variation) / spec.rows
            u = (
                col
                + 0.5
                + (row % 2 - 0.5) * spec.stagger
                + rng.uniform(-1, 1) * spec.spacing_variation
            ) / spec.columns
            hit, root_uv = surface.project(
                guide_point(spec, u, t), spec.max_projection_distance
            )
            _, normal = surface.sample(root_uv)
            tangent = guide_point(spec, u, min(1.0, t + 0.001)) - guide_point(
                spec, u, max(0.0, t - 0.001)
            )
            tangent -= normal * tangent.dot(normal)
            if tangent.length < 1e-8:
                fail("Guide direction is degenerate at the surface")
            tangent.normalize()
            angle = rng.uniform(-1, 1) * spec.direction_variation
            tangent = tangent * math.cos(angle) + normal.cross(tangent) * math.sin(
                angle
            )
            _, direction_uv = surface.project(
                hit + tangent * spec.direction_distance,
                spec.max_projection_distance + spec.direction_distance,
            )
            tip, _ = surface.sample(direction_uv)
            if (tip - hit).length < spec.direction_distance * 0.05:
                fail("Direction binding collapsed; adjust guide or direction distance")
            roots.append(root_uv)
            directions.append(direction_uv)
            end = spec.scale_end or spec.scale
            scales.append(
                [
                    (a * (1 - t) + b * t) * (1 + rng.uniform(-1, 1) * variation)
                    for a, b, variation in zip(
                        spec.scale, end, spec.scale_variation, strict=True
                    )
                ]
            )
            positions.append(hit)
    # Area of the projected guide region, not the sum of overlapping instance vanes.
    grid = [
        [
            surface.project(
                guide_point(spec, u / 8, t / 8), spec.max_projection_distance
            )[0]
            for u in range(9)
        ]
        for t in range(9)
    ]
    area = 0.0
    for row in range(8):
        for col in range(8):
            a, b = grid[row][col : col + 2]
            d, c = grid[row + 1][col : col + 2]
            area += ((b - a).cross(c - a).length + (c - a).cross(d - a).length) / 2
    spacing = []
    for i, p in enumerate(positions):
        if i % spec.columns:
            spacing.append((p - positions[i - 1]).length)
        if i >= spec.columns:
            spacing.append((p - positions[i - spec.columns]).length)
    data = bpy.data.meshes.new("Surface instance bindings")
    try:
        data.from_pydata(positions, [], [])
        for key, values in [
            ("root", roots),
            ("direction", directions),
            ("scale", scales),
        ]:
            attr = data.attributes.new(ATTRS[key], "FLOAT_VECTOR", "POINT")
            attr.data.foreach_set("vector", [v for item in values for v in item])
        ids = data.attributes.new("id", "INT", "POINT")
        ids.data.foreach_set("value", list(range(len(roots))))
        layers = data.attributes.new("tyvrana_layer", "INT", "POINT")
        layers.data.foreach_set("value", [spec.layer] * len(roots))
        metadata = {
            "distribution": spec.model_dump(mode="json"),
            "area": area,
            "spacing_min": min(spacing),
            "spacing_mean": sum(spacing) / len(spacing),
        }
        return data, metadata
    except BaseException:
        bpy.data.meshes.remove(data)
        raise


def graph(source: Any, prototype: Any, spec: SurfaceDistribution) -> Any:
    group = bpy.data.node_groups.new("Surface Instances", "GeometryNodeTree")
    try:
        group.interface.new_socket(
            name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
        group.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        nodes, links = group.nodes, group.links

        def node(kind: str, name: str) -> Any:
            n = nodes.new(kind)
            n.name = name
            n.label = name
            return n

        def wire(a: Any, output: str, b: Any, input_name: str) -> None:
            links.new(a.outputs[output], b.inputs[input_name])

        def attr(name: str) -> Any:
            n = node("GeometryNodeInputNamedAttribute", name)
            n.data_type = "FLOAT_VECTOR"
            n.inputs["Name"].default_value = name
            return n

        original = node("NodeGroupInput", "Bindings")
        output = node("NodeGroupOutput", "Output")
        target = node("GeometryNodeObjectInfo", "Surface")
        target.transform_space = "RELATIVE"
        target.inputs["Object"].default_value = source
        proto = node("GeometryNodeObjectInfo", "Prototype")
        proto.transform_space = "ORIGINAL"
        proto.inputs["Object"].default_value = prototype
        uv = attr(spec.uv_map)
        root_uv, direction_uv, scale = (
            attr(ATTRS[k]) for k in ("root", "direction", "scale")
        )
        position = node("GeometryNodeInputPosition", "Position")
        normal = node("GeometryNodeInputNormal", "Normal")

        def sample(name: str, coords: Any, value: Any, socket: str) -> Any:
            n = node("GeometryNodeSampleUVSurface", name)
            n.data_type = "FLOAT_VECTOR"
            wire(target, "Geometry", n, "Mesh")
            wire(uv, "Attribute", n, "UV Map")
            wire(coords, "Attribute", n, "Sample UV")
            wire(value, socket, n, "Value")
            return n

        root = sample("Root", root_uv, position, "Position")
        direction = sample("Direction", direction_uv, position, "Position")
        normals = sample("Surface normal", root_uv, normal, "Normal")
        unit_normal = node("ShaderNodeVectorMath", "Unit normal")
        unit_normal.operation = "NORMALIZE"
        links.new(normals.outputs["Value"], unit_normal.inputs[0])
        tangent = node("ShaderNodeVectorMath", "Tangent")
        tangent.operation = "SUBTRACT"
        links.new(direction.outputs["Value"], tangent.inputs[0])
        links.new(root.outputs["Value"], tangent.inputs[1])
        rotation = node("FunctionNodeAxesToRotation", "Frame")
        rotation.primary_axis = "Z"
        rotation.secondary_axis = "Y"
        wire(unit_normal, "Vector", rotation, "Primary Axis")
        wire(tangent, "Vector", rotation, "Secondary Axis")
        offset = node("ShaderNodeVectorMath", "Root offset")
        offset.operation = "SCALE"
        links.new(unit_normal.outputs["Vector"], offset.inputs[0])
        offset.inputs["Scale"].default_value = spec.surface_offset
        points = node("GeometryNodeSetPosition", "Attached roots")
        wire(original, "Geometry", points, "Geometry")
        wire(root, "Value", points, "Position")
        wire(offset, "Vector", points, "Offset")
        valid = node("FunctionNodeBooleanMath", "Valid UV bindings")
        valid.operation = "AND"
        links.new(root.outputs["Is Valid"], valid.inputs[0])
        links.new(direction.outputs["Is Valid"], valid.inputs[1])
        instances = node("GeometryNodeInstanceOnPoints", "Instances")
        wire(points, "Geometry", instances, "Points")
        wire(proto, "Geometry", instances, "Instance")
        wire(rotation, "Rotation", instances, "Rotation")
        wire(scale, "Attribute", instances, "Scale")
        wire(valid, "Boolean", instances, "Selection")
        wire(instances, "Instances", output, "Geometry")
        for i, n in enumerate(nodes):
            n.location = ((i % 5) * 220, -(i // 5) * 200)
        group[KEY] = True
        group[KEY + "_signature"] = graph_signature(group)
        return group
    except BaseException:
        bpy.data.node_groups.remove(group)
        raise


def graph_signature(group: Any) -> str:
    """Detect edits to owned behavior while permitting layout and object renames."""
    nodes = []
    for node in group.nodes:
        properties = {
            key: getattr(node, key)
            for key in (
                "operation",
                "data_type",
                "primary_axis",
                "secondary_axis",
                "transform_space",
            )
            if hasattr(node, key)
        }
        defaults = []
        for socket in node.inputs:
            if not hasattr(socket, "default_value") or socket.is_linked:
                continue
            value = socket.default_value
            if isinstance(value, bpy.types.ID):
                continue
            if value is not None and not isinstance(value, (str, bool, float, int)):
                value = list(value)
            defaults.append((socket.identifier, value))
        nodes.append((node.name, node.bl_idname, node.mute, properties, defaults))
    links = sorted(
        (
            link.from_node.name,
            link.from_socket.identifier,
            link.to_node.name,
            link.to_socket.identifier,
        )
        for link in group.links
    )
    return hashlib.sha256(
        json.dumps([nodes, links], sort_keys=True).encode()
    ).hexdigest()


def owned(obj: Any) -> tuple[Any, dict[str, Any]]:
    if obj.library or obj.override_library or obj.data.users != 1 or KEY not in obj:
        fail("Object is not an exclusively owned surface-instance system")
    if len(obj.modifiers) != 1 or obj.modifiers[0].type != "NODES":
        fail("Surface-instance modifier ownership changed")
    group = obj.modifiers[0].node_group
    if group is None or not group.get(KEY) or group.users != 1:
        fail("Surface-instance graph ownership changed")
    if group.get(KEY + "_signature") != graph_signature(group):
        fail("Owned graph was edited; preserve it instead of replacing it")
    try:
        metadata = json.loads(obj[KEY])
    except (ValueError, TypeError):
        fail("Surface-instance metadata is invalid")
    return group, metadata


def evaluation_dependencies(obj: Any) -> list[Any]:
    """Preflight an owned, non-realizing graph without evaluating arbitrary nodes."""
    group, metadata = owned(obj)
    spec = SurfaceDistribution.model_validate(metadata["distribution"])
    if (
        len(obj.data.vertices) != spec.rows * spec.columns
        or len(obj.data.edges)
        or len(obj.data.polygons)
    ):
        fail("Surface-instance point topology changed")
    for name in ATTRS.values():
        attribute = obj.data.attributes.get(name)
        if (
            attribute is None
            or attribute.domain != "POINT"
            or attribute.data_type != "FLOAT_VECTOR"
        ):
            fail("Surface-instance binding attributes changed")
    source = group.nodes["Surface"].inputs["Object"].default_value
    prototype = group.nodes["Prototype"].inputs["Object"].default_value
    if source is None or prototype is None:
        fail("Bound surface or prototype was removed")
    # References remain authoritative after object renames.
    validate_inputs(
        spec.model_copy(
            update={"surface_object": source.name, "prototype_object": prototype.name}
        ),
        obj,
    )
    return [source, prototype]


def configure(
    name: str, spec: SurfaceDistribution, *, create: bool
) -> SurfaceInstancesSummary:
    start = time.perf_counter()
    obj = None if create else object_mesh(name)
    if create and bpy.data.objects.get(name) is not None:
        fail("Choose an unused object name")
    if obj is not None:
        owned(obj)
    source, prototype = validate_inputs(spec, obj)
    dg = bpy.context.evaluated_depsgraph_get()
    pe = prototype.evaluated_get(dg)
    pm = pe.to_mesh()
    try:
        if (
            not len(pm.polygons)
            or len(pm.vertices) * spec.rows * spec.columns > MAX_EQUIVALENT_VERTICES
        ):
            fail(
                "Prototype is empty or distribution exceeds equivalent geometry budget"
            )
    finally:
        pe.to_mesh_clear()
    data = group = staged = None
    try:
        with evaluated_surface(source, spec.uv_map) as surface:
            data, metadata = make_points(surface, spec)
        group = graph(source, prototype, spec)
        staged = bpy.data.objects.new(name + " staging", data)
        bpy.context.scene.collection.objects.link(staged)
        staged.parent = source
        staged.matrix_parent_inverse = Matrix.Identity(4)
        staged.matrix_basis = Matrix.Identity(4)
        staged.modifiers.new("Surface Instances", "NODES").node_group = group
        staged[KEY] = json.dumps(metadata)
        bpy.context.view_layer.update()
        result = inspect(staged, 0)
        if result.evaluated_instance_count != result.instance_count:
            fail("Native UV evaluation rejected one or more instance bindings")
        if obj is None:
            staged.name = name
            result = result.model_copy(update={"object_name": staged.name})
            staged = None
        else:
            old_data = obj.data
            old_group = obj.modifiers[0].node_group
            obj.data = data
            obj.modifiers[0].node_group = group
            obj.parent = source
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = Matrix.Identity(4)
            obj[KEY] = json.dumps(metadata)
            bpy.data.objects.remove(staged, do_unlink=True)
            staged = None
            if old_data.users == 0:
                bpy.data.meshes.remove(old_data)
            if old_group.users == 0:
                bpy.data.node_groups.remove(old_group)
            result = result.model_copy(update={"object_name": obj.name})
        return result.model_copy(
            update={"generation_seconds": time.perf_counter() - start}
        )
    except BaseException:
        if staged is not None:
            bpy.data.objects.remove(staged, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
        raise


def inspect(obj: Any, sample_limit: int) -> SurfaceInstancesSummary:
    start = time.perf_counter()
    group, metadata = owned(obj)
    spec = SurfaceDistribution.model_validate(metadata["distribution"])
    source = group.nodes["Surface"].inputs["Object"].default_value
    prototype = group.nodes["Prototype"].inputs["Object"].default_value
    if source is None or prototype is None:
        fail("Bound surface or prototype was removed")
    dg = bpy.context.evaluated_depsgraph_get()
    matrices = [
        i.matrix_world.copy()
        for i in dg.object_instances
        if i.is_instance and i.parent and i.parent.original == obj
    ]
    pe = prototype.evaluated_get(dg)
    pm = pe.to_mesh()
    try:
        pv, pf = len(pm.vertices), len(pm.polygons)
        # Bounded geometric clearance QA, not a collision solver or visibility metric.
        probes = [
            v.co.copy()
            for i, v in enumerate(pm.vertices)
            if i % max(1, len(pm.vertices) // 32) == 0
        ][:32]
    finally:
        pe.to_mesh_clear()
    attrs = {k: obj.data.attributes.get(v) for k, v in ATTRS.items()}
    count = len(obj.data.vertices)
    if any(a is None or len(a.data) != count for a in attrs.values()):
        fail("Stored instance binding attributes are missing or invalid")
    digest = hashlib.sha256()
    samples: list[InstanceSample] = []
    invalid = 0
    max_error = 0.0
    clearances = []
    inverse = source.matrix_world.inverted()
    with evaluated_surface(source, spec.uv_map) as surface:
        expected = []
        for i in range(count):
            uv = attrs["root"].data[i].vector.copy()
            duv = attrs["direction"].data[i].vector.copy()
            scale = list(attrs["scale"].data[i].vector)
            digest.update(json.dumps([list(uv), list(duv), scale]).encode())
            try:
                root, normal = surface.sample(uv)
                direction, _ = surface.sample(duv)
            except OperationError:
                invalid += 1
                continue
            world = source.matrix_world @ (root + normal * spec.surface_offset)
            expected.append(world)
            if len(samples) < sample_limit:
                tangent = direction - root
                tangent -= normal * tangent.dot(normal)
                samples.append(
                    InstanceSample(
                        id=i,
                        root_uv=list(uv)[:2],
                        direction_uv=list(duv)[:2],
                        root_world=list(world),
                        normal_world=list(
                            (source.matrix_world.to_3x3() @ normal).normalized()
                        ),
                        direction_world=list(
                            (source.matrix_world.to_3x3() @ tangent).normalized()
                        ),
                        scale=scale,
                        layer=spec.layer,
                    )
                )
        # Instance order is host-owned. Match actual output roots spatially.
        if expected and matrices:
            from mathutils.kdtree import KDTree  # type: ignore[import-not-found]

            tree = KDTree(len(expected))
            for i, p in enumerate(expected):
                tree.insert(p, i)
            tree.balance()
            max_error = max(tree.find(m.translation)[2] for m in matrices)
        for matrix in matrices[:: max(1, len(matrices) // 128)][:128]:
            local = inverse @ matrix
            for p in probes:
                point = local @ p
                near, normal, _, _ = surface.bvh.find_nearest(point)
                if near is not None:
                    clearances.append((point - near).dot(normal))
    return SurfaceInstancesSummary(
        object_name=obj.name,
        surface_object=source.name,
        prototype_object=prototype.name,
        uv_map=spec.uv_map,
        instance_count=count,
        evaluated_instance_count=len(matrices),
        prototype_vertices=pv,
        prototype_faces=pf,
        equivalent_vertices=pv * len(matrices),
        equivalent_faces=pf * len(matrices),
        stored_point_count=count,
        node_count=len(group.nodes),
        binding_sha256=digest.hexdigest(),
        region_area=metadata["area"],
        root_spacing_min=metadata["spacing_min"],
        root_spacing_mean=metadata["spacing_mean"],
        invalid_binding_count=invalid,
        root_position_max_error=max_error,
        surface_sample_count=len(clearances),
        minimum_surface_clearance=min(clearances) if clearances else None,
        buried_surface_sample_count=sum(v < -0.0001 for v in clearances),
        inspection_seconds=time.perf_counter() - start,
        samples=samples,
        limitations=[
            "Keep UV layout unique and unchanged; inspect after surface changes.",
            "Rigid instances follow UV roots/tangents; bending is not simulated.",
            "Clearance samples estimate surface distance, not instance collisions.",
            "Area and spacing describe binding creation, not current deformation.",
        ],
    )
