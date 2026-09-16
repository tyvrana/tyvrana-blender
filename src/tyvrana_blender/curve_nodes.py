"""Owned native geometry graphs for attached paths, sweeps and bounded probes."""

import hashlib
import json
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Matrix  # type: ignore[import-not-found]

KEY = "tyvrana_curve"
MODIFIER = "Tyvrana Curve"


def signature(group: Any) -> str:
    """Hash behavior and matrix sockets; ID pointers are checked separately."""

    def value(item: Any) -> Any:
        if isinstance(item, bpy.types.ID):
            return None
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        return [value(v) for v in item]

    nodes = []
    for node in group.nodes:
        properties = {
            k: getattr(node, k)
            for k in (
                "operation",
                "data_type",
                "transform_space",
                "domain",
                "mode",
                "spline_type",
                "clamp",
                "is_active_output",
            )
            if hasattr(node, k)
        }
        defaults = [
            (s.identifier, value(s.default_value))
            for s in node.inputs
            if hasattr(s, "default_value") and not s.is_linked
        ]
        nodes.append((node.name, node.bl_idname, node.mute, properties, defaults))
    links = sorted(
        (
            e.from_node.name,
            e.from_socket.identifier,
            e.to_node.name,
            e.to_socket.identifier,
        )
        for e in group.links
    )
    return hashlib.sha256(
        json.dumps([nodes, links], sort_keys=True).encode()
    ).hexdigest()


class Graph:
    def __init__(self, name: str, *, path: bool = False) -> None:
        self.group = bpy.data.node_groups.new(name, "GeometryNodeTree")
        self.group.interface.new_socket(
            name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
        self.group.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        if path:
            self.group.interface.new_socket(
                name="Path", in_out="OUTPUT", socket_type="NodeSocketGeometry"
            )
        self.input = self.node("NodeGroupInput", "Input")
        self.output = self.node("NodeGroupOutput", "Output")

    def node(self, kind: str, name: str = "") -> Any:
        n = self.group.nodes.new(kind)
        if name:
            n.name = name
        return n

    def wire(self, output: Any, input_socket: Any) -> None:
        self.group.links.new(output, input_socket)

    def vector(self, operation: str, a: Any, b: Any = None) -> Any:
        n = self.node("ShaderNodeVectorMath")
        n.operation = operation
        for socket, value in [
            (n.inputs[0], a),
            (n.inputs["Scale"] if operation == "SCALE" else n.inputs[1], b),
        ]:
            if value is None:
                continue
            if hasattr(value, "is_output"):
                self.wire(value, socket)
            else:
                socket.default_value = value
        return (
            n.outputs["Value"]
            if operation in {"LENGTH", "DOT_PRODUCT", "DISTANCE"}
            else n.outputs["Vector"]
        )

    def transform(self, value: Any, matrix: Any) -> Any:
        if not hasattr(matrix, "is_output"):
            split = self.node("ShaderNodeSeparateXYZ")
            self.wire(value, split.inputs["Vector"])
            transformed = self.vector(
                "SCALE", list(matrix.col[0].xyz), split.outputs["X"]
            )
            for i, axis in enumerate("YZ", 1):
                transformed = self.vector(
                    "ADD",
                    transformed,
                    self.vector("SCALE", list(matrix.col[i].xyz), split.outputs[axis]),
                )
            return self.vector("ADD", transformed, list(matrix.translation))
        n = self.node("FunctionNodeTransformPoint")
        if hasattr(value, "is_output"):
            self.wire(value, n.inputs["Vector"])
        else:
            n.inputs["Vector"].default_value = value
        self.wire(matrix, n.inputs["Transform"])
        return n.outputs["Vector"]

    def info(self, obj: Any, name: str, space: str = "RELATIVE") -> Any:
        n = self.node("GeometryNodeObjectInfo", name)
        n.transform_space = space
        n.inputs["Object"].default_value = obj
        return n

    def store(
        self, geometry: Any, name: str, value: Any, kind: str, domain: str = "POINT"
    ) -> Any:
        n = self.node("GeometryNodeStoreNamedAttribute")
        n.data_type = kind
        n.domain = domain
        n.inputs["Name"].default_value = "tyvrana_probe_" + name
        self.wire(geometry, n.inputs["Geometry"])
        self.wire(value, n.inputs["Value"])
        return n.outputs["Geometry"]

    def surface(
        self, target: Any, vertices: list[int], weights: list[float], ordinal: int
    ) -> tuple[Any, Any, Any, Any]:
        source = self.info(target, f"Surface {ordinal}", "ORIGINAL")
        position = self.node("GeometryNodeInputPosition")
        sampled = []
        for index in vertices:
            n = self.node("GeometryNodeSampleIndex")
            n.data_type = "FLOAT_VECTOR"
            n.domain = "POINT"
            n.clamp = False
            n.inputs["Index"].default_value = index
            self.wire(source.outputs["Geometry"], n.inputs["Geometry"])
            self.wire(position.outputs["Position"], n.inputs["Value"])
            sampled.append(n.outputs["Value"])
        edge = self.vector("SUBTRACT", sampled[1], sampled[0])
        x = self.vector("NORMALIZE", edge)
        z = self.vector(
            "NORMALIZE",
            self.vector(
                "CROSS_PRODUCT", edge, self.vector("SUBTRACT", sampled[2], sampled[0])
            ),
        )
        y = self.vector("CROSS_PRODUCT", z, x)
        center = self.vector(
            "ADD",
            self.vector(
                "ADD",
                self.vector("SCALE", sampled[0], weights[0]),
                self.vector("SCALE", sampled[1], weights[1]),
            ),
            self.vector("SCALE", sampled[2], weights[2]),
        )
        return center, x, y, z


def build(obj: Any, metadata: dict[str, Any], targets: list[Any], profile: Any) -> Any:
    graph = Graph("Curve Evaluation", path=True)
    try:
        geometry = graph.input.outputs["Geometry"]
        position = graph.node("GeometryNodeInputPosition").outputs["Position"]
        index = graph.node("GeometryNodeInputIndex").outputs["Index"]
        for ordinal, (binding, target) in enumerate(
            zip(metadata["bindings"], targets, strict=True)
        ):
            spec = binding["spec"]
            source = graph.info(target, f"Target {ordinal}")
            if spec["follow"] == "point":
                local = spec["offset"]
            else:
                local = graph.vector(
                    "ADD",
                    graph.transform(position, Matrix(binding["inverse"])),
                    binding["shift"],
                )
            if spec["target"]["kind"] == "surface":
                center, x, y, z = graph.surface(
                    target, binding["vertices"], spec["target"]["barycentric"], ordinal
                )
                if hasattr(local, "is_output"):
                    split = graph.node("ShaderNodeSeparateXYZ")
                    graph.wire(local, split.inputs["Vector"])
                    coords = [split.outputs[a] for a in "XYZ"]
                else:
                    coords = local
                target_point = center
                for axis, value in zip([x, y, z], coords, strict=True):
                    target_point = graph.vector(
                        "ADD", target_point, graph.vector("SCALE", axis, value)
                    )
            else:
                target_point = local
            transformed = graph.transform(target_point, source.outputs["Transform"])
            selected = graph.node("FunctionNodeCompare")
            selected.data_type = "INT"
            selected.operation = (
                "EQUAL" if spec["follow"] == "point" else "GREATER_EQUAL"
            )
            graph.wire(index, selected.inputs["A"])
            selected.inputs["B"].default_value = binding["start"]
            selection = selected.outputs["Result"]
            if spec["follow"] == "spline":
                upper = graph.node("FunctionNodeCompare")
                upper.data_type = "INT"
                upper.operation = "LESS_THAN"
                graph.wire(index, upper.inputs["A"])
                upper.inputs["B"].default_value = binding["end"]
                both = graph.node("FunctionNodeBooleanMath")
                both.operation = "AND"
                graph.wire(selection, both.inputs[0])
                graph.wire(upper.outputs["Result"], both.inputs[1])
                selection = both.outputs["Boolean"]
            n = graph.node("GeometryNodeSetPosition")
            graph.wire(geometry, n.inputs["Geometry"])
            graph.wire(selection, n.inputs["Selection"])
            graph.wire(transformed, n.inputs["Position"])
            geometry = n.outputs["Geometry"]
        normal = graph.node("GeometryNodeSetCurveNormal")
        normal.inputs["Mode"].default_value = "Minimum Twist"
        graph.wire(geometry, normal.inputs["Curve"])
        geometry = normal.outputs["Curve"]
        graph.wire(geometry, graph.output.inputs["Path"])
        settings = metadata["settings"]
        sweep = settings["profile"]
        if sweep["kind"] != "none":
            if sweep["kind"] == "circle":
                cross = graph.node("GeometryNodeCurvePrimitiveCircle")
                cross.mode = "RADIUS"
                cross.inputs["Radius"].default_value = sweep["radius"]
                cross.inputs["Resolution"].default_value = sweep["resolution"]
                cross_section = cross.outputs["Curve"]
                scale = 1.0
            else:
                cross = graph.info(profile, "Profile", "ORIGINAL")
                cross_section = cross.outputs["Geometry"]
                scale = sweep["scale"]
            convert = graph.node("GeometryNodeCurveToMesh")
            graph.wire(geometry, convert.inputs["Curve"])
            graph.wire(cross_section, convert.inputs["Profile Curve"])
            convert.inputs["Fill Caps"].default_value = sweep["caps"]
            radius = graph.node("GeometryNodeInputRadius")
            multiply = graph.node("ShaderNodeMath")
            multiply.operation = "MULTIPLY"
            multiply.inputs[1].default_value = scale
            graph.wire(radius.outputs["Radius"], multiply.inputs[0])
            graph.wire(multiply.outputs[0], convert.inputs["Scale"])
            geometry = convert.outputs["Mesh"]
            if settings["material"] is not None:
                mat = graph.node("GeometryNodeSetMaterial", "Material")
                mat.inputs["Material"].default_value = bpy.data.materials[
                    settings["material"]
                ]
                graph.wire(geometry, mat.inputs["Geometry"])
                geometry = mat.outputs["Geometry"]
        graph.wire(geometry, graph.output.inputs["Geometry"])
        graph.group[KEY] = True
        graph.group[KEY + "_signature"] = signature(graph.group)
        return graph.group
    except BaseException:
        bpy.data.node_groups.remove(graph.group)
        raise


def sample(
    obj: Any, group: Any, count: int, *, control: bool = False
) -> list[dict[str, Any]]:
    """Probe a temporary copy; original geometry, settings and selection stay intact."""
    graph = Graph("Curve sampling")
    clone = None
    mesh = None
    evaluated = None
    try:
        path = graph.node("GeometryNodeGroup")
        path.node_tree = group
        graph.wire(graph.input.outputs["Geometry"], path.inputs["Geometry"])
        geometry = path.outputs["Path"]
        if control:
            convert = graph.node("GeometryNodeCurveSplineType")
            convert.spline_type = "POLY"
            graph.wire(geometry, convert.inputs["Curve"])
            geometry = convert.outputs["Curve"]
        length = graph.node("GeometryNodeSplineLength")
        geometry = graph.store(
            geometry, "curve_length", length.outputs["Length"], "FLOAT", "CURVE"
        )
        index = graph.node("GeometryNodeInputIndex")
        geometry = graph.store(
            geometry, "spline_index", index.outputs["Index"], "INT", "CURVE"
        )
        for name, kind in [
            ("radius", "GeometryNodeInputRadius"),
            ("tilt", "GeometryNodeInputCurveTilt"),
        ]:
            n = graph.node(kind)
            geometry = graph.store(geometry, name, n.outputs[0], "FLOAT")
        points = graph.node("GeometryNodeCurveToPoints")
        points.mode = "EVALUATED" if control else "COUNT"
        if not control:
            points.inputs["Count"].default_value = count
        graph.wire(geometry, points.inputs["Curve"])
        geometry = points.outputs["Points"]
        for name in ["Tangent", "Normal"]:
            geometry = graph.store(
                geometry, name.lower(), points.outputs[name], "FLOAT_VECTOR"
            )
        vertices = graph.node("GeometryNodePointsToVertices")
        graph.wire(geometry, vertices.inputs["Points"])
        graph.wire(vertices.outputs["Mesh"], graph.output.inputs["Geometry"])
        clone = obj.copy()
        clone.data = obj.data
        clone.name = "Curve sampling"
        for modifier in list(clone.modifiers):
            clone.modifiers.remove(modifier)
        modifier = clone.modifiers.new("Sampling", "NODES")
        modifier.node_group = graph.group
        bpy.context.scene.collection.objects.link(clone)
        clone.matrix_world = obj.matrix_world.copy()
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        evaluated = clone.evaluated_get(dg)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
        if len(mesh.vertices) > 8192:
            raise RuntimeError("Curve sampling exceeded its bounded result budget")
        return [
            dict(
                position=list(v.co),
                **{
                    name: (
                        list(attr.data[i].vector)
                        if attr.data_type == "FLOAT_VECTOR"
                        else attr.data[i].value
                    )
                    for name, attr in (
                        (name, mesh.attributes["tyvrana_probe_" + name])
                        for name in [
                            "curve_length",
                            "spline_index",
                            "radius",
                            "tilt",
                            "tangent",
                            "normal",
                        ]
                    )
                },
            )
            for i, v in enumerate(mesh.vertices)
        ]
    finally:
        if evaluated is not None and mesh is not None:
            evaluated.to_mesh_clear()
        if clone is not None:
            bpy.data.objects.remove(clone, do_unlink=True)
        bpy.data.node_groups.remove(graph.group)
