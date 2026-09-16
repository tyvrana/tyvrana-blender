"""Bounded owned native interpolation, surface deformation and template graphs."""

import hashlib
import json
from typing import Any

import bpy  # type: ignore[import-not-found]

from .curve_nodes import Graph
from .curve_nodes import signature as curve_signature
from .growth_models import GrowthCreateArguments

KEY = "tyvrana_growth"
MODIFIER = "Tyvrana Growth"
ROOT_ATTRIBUTES = [
    "growth_root_id",
    "growth_region",
    "growth_family",
    "growth_root",
    "growth_normal",
    "growth_flow",
    "growth_offset",
    "growth_radius",
    "growth_tip",
    "growth_roll",
    "growth_layer",
    "growth_variation",
    "growth_triangle",
    "surface_uv_coordinate",
]


def signature(group: Any) -> str:
    pointers = [
        (n.name, s.identifier, s.default_value.name_full)
        for n in group.nodes
        for s in n.inputs
        if hasattr(s, "default_value") and isinstance(s.default_value, bpy.types.ID)
    ]
    extra = [
        (
            n.name,
            [
                (p, getattr(n, p))
                for p in ["primary_axis", "secondary_axis", "use_all_curves"]
                if hasattr(n, p)
            ],
        )
        for n in group.nodes
    ]
    return hashlib.sha256(
        (curve_signature(group) + json.dumps([extra, pointers])).encode()
    ).hexdigest()


class GrowthGraph(Graph):
    def attr(self, name: str, kind: str = "FLOAT") -> Any:
        n = self.node("GeometryNodeInputNamedAttribute")
        n.data_type = kind
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    def put(
        self,
        geometry: Any,
        name: str,
        value: Any,
        kind: str = "FLOAT",
        domain: str = "POINT",
    ) -> Any:
        n = self.node("GeometryNodeStoreNamedAttribute")
        n.data_type = kind
        n.domain = domain
        n.inputs["Name"].default_value = name
        self.wire(geometry, n.inputs["Geometry"])
        if hasattr(value, "is_output"):
            self.wire(value, n.inputs["Value"])
        else:
            n.inputs["Value"].default_value = value
        return n.outputs["Geometry"]

    def math(self, op: str, a: Any, b: Any) -> Any:
        n = self.node("ShaderNodeMath")
        n.operation = op
        for socket, value in zip(list(n.inputs)[:2], [a, b], strict=True):
            if hasattr(value, "is_output"):
                self.wire(value, socket)
            else:
                socket.default_value = value
        return n.outputs[0]

    def equal(self, a: Any, b: int) -> Any:
        n = self.node("FunctionNodeCompare")
        n.data_type = "INT"
        n.operation = "EQUAL"
        self.wire(a, n.inputs["A"])
        n.inputs["B"].default_value = b
        return n.outputs["Result"]


def build(
    spec: GrowthCreateArguments,
    roots: Any,
    materials: list[Any],
    prototypes: list[Any],
    owner_id: str,
) -> Any:
    g = GrowthGraph("Growth Evaluation", path=True)
    try:
        guides = g.input.outputs["Geometry"]
        for name in ROOT_ATTRIBUTES:
            remove = g.node("GeometryNodeRemoveAttribute")
            remove.inputs["Name"].default_value = name
            g.wire(guides, remove.inputs["Geometry"])
            guides = remove.outputs["Geometry"]
        source = g.info(roots, "Root Carrier", "ORIGINAL")
        points = g.node("GeometryNodeMeshToPoints")
        points.mode = "VERTICES"
        g.wire(source.outputs["Geometry"], points.inputs["Mesh"])
        inter = g.node("GeometryNodeInterpolateCurves")
        g.wire(guides, inter.inputs["Guide Curves"])
        g.wire(points.outputs["Points"], inter.inputs["Points"])
        g.wire(g.attr("guide_up", "FLOAT_VECTOR"), inter.inputs["Guide Up"])
        g.wire(g.attr("growth_normal", "FLOAT_VECTOR"), inter.inputs["Point Up"])
        g.wire(g.attr("guide_group", "INT"), inter.inputs["Guide Group ID"])
        g.wire(g.attr("growth_group", "INT"), inter.inputs["Point Group ID"])
        inter.inputs["Max Neighbors"].default_value = spec.neighbors
        geometry = inter.outputs["Curves"]
        position = g.node("GeometryNodeInputPosition").outputs["Position"]
        root = g.attr("growth_root", "FLOAT_VECTOR")
        adjusted = g.vector(
            "ADD",
            root,
            g.vector(
                "SCALE",
                g.vector("SUBTRACT", position, root),
                g.attr("growth_variation"),
            ),
        )
        adjusted = g.vector(
            "ADD",
            adjusted,
            g.vector(
                "SCALE",
                g.attr("growth_normal", "FLOAT_VECTOR"),
                g.attr("growth_offset"),
            ),
        )
        setpos = g.node("GeometryNodeSetPosition")
        g.wire(geometry, setpos.inputs["Geometry"])
        g.wire(adjusted, setpos.inputs["Position"])
        deform = g.node("GeometryNodeDeformCurvesOnSurface")
        g.wire(setpos.outputs["Geometry"], deform.inputs["Curves"])
        geometry = deform.outputs["Curves"]
        normal = g.node("GeometryNodeSetCurveNormal")
        normal.inputs["Mode"].default_value = "Minimum Twist"
        g.wire(geometry, normal.inputs["Curve"])
        tilt = g.node("GeometryNodeSetCurveTilt")
        g.wire(normal.outputs["Curve"], tilt.inputs["Curve"])
        g.wire(g.attr("growth_roll"), tilt.inputs["Tilt"])
        factor = g.node("GeometryNodeSplineParameter").outputs["Factor"]
        radius = g.math(
            "MULTIPLY",
            g.attr("growth_radius"),
            g.math(
                "ADD",
                1,
                g.math("MULTIPLY", factor, g.math("SUBTRACT", g.attr("growth_tip"), 1)),
            ),
        )
        rad = g.node("GeometryNodeSetCurveRadius")
        g.wire(tilt.outputs["Curve"], rad.inputs["Curve"])
        g.wire(radius, rad.inputs["Radius"])
        framed = g.put(
            rad.outputs["Curve"],
            "growth_frame_normal",
            g.node("GeometryNodeInputNormal").outputs["Normal"],
            "FLOAT_VECTOR",
        )
        path = g.put(
            framed,
            "growth_curve_index",
            g.node("GeometryNodeInputIndex").outputs["Index"],
            "INT",
            "CURVE",
        )
        g.wire(path, g.output.inputs["Path"])
        join = g.node("GeometryNodeJoinGeometry")
        for i, family in enumerate(spec.families):
            select = g.node("GeometryNodeSeparateGeometry")
            select.domain = "CURVE"
            g.wire(path, select.inputs["Geometry"])
            g.wire(
                g.equal(g.attr("growth_family", "INT"), i), select.inputs["Selection"]
            )
            part = select.outputs["Selection"]
            if family.template:
                curvepoints = g.node("GeometryNodeCurveToPoints")
                curvepoints.mode = "COUNT"
                curvepoints.inputs["Count"].default_value = 2
                g.wire(part, curvepoints.inputs["Curve"])
                endpoint = g.node("GeometryNodeSeparateGeometry")
                endpoint.domain = "POINT"
                g.wire(curvepoints.outputs["Points"], endpoint.inputs["Geometry"])
                modulo = g.math(
                    "MODULO", g.node("GeometryNodeInputIndex").outputs["Index"], 2
                )
                g.wire(g.equal(modulo, 0), endpoint.inputs["Selection"])
                proto = g.info(prototypes[i], f"Template {i}", "ORIGINAL")
                template = proto.outputs["Geometry"]
                if materials[i] is not None:
                    material = g.node("GeometryNodeSetMaterial")
                    material.inputs["Material"].default_value = materials[i]
                    g.wire(template, material.inputs["Geometry"])
                    template = material.outputs["Geometry"]
                if family.template.mode == "deform":
                    template = g.put(
                        template,
                        "growth_template_position",
                        g.node("GeometryNodeInputPosition").outputs["Position"],
                        "FLOAT_VECTOR",
                    )
                inst = g.node("GeometryNodeInstanceOnPoints")
                g.wire(endpoint.outputs["Selection"], inst.inputs["Points"])
                g.wire(template, inst.inputs["Instance"])
                if family.template.mode == "instances":
                    rot = g.node("FunctionNodeAxesToRotation")
                    rot.primary_axis = "Z"
                    rot.secondary_axis = "X"
                    g.wire(curvepoints.outputs["Tangent"], rot.inputs["Primary Axis"])
                    g.wire(curvepoints.outputs["Normal"], rot.inputs["Secondary Axis"])
                    g.wire(rot.outputs["Rotation"], inst.inputs["Rotation"])
                    scale = g.node("ShaderNodeCombineXYZ")
                    scale.inputs["X"].default_value = family.template.width
                    scale.inputs["Y"].default_value = family.template.thickness
                    length = g.node("GeometryNodeSplineLength").outputs["Length"]
                    # Store length on curves before sampling root points.
                    with_length = g.put(part, "growth_length", length, "FLOAT", "CURVE")
                    g.wire(with_length, curvepoints.inputs["Curve"])
                    g.wire(g.attr("growth_length"), scale.inputs["Z"])
                    g.wire(scale.outputs["Vector"], inst.inputs["Scale"])
                    part = inst.outputs["Instances"]
                else:
                    realize = g.node("GeometryNodeRealizeInstances")
                    g.wire(inst.outputs["Instances"], realize.inputs["Geometry"])
                    coords = g.node("ShaderNodeSeparateXYZ")
                    g.wire(
                        g.attr("growth_template_position", "FLOAT_VECTOR"),
                        coords.inputs["Vector"],
                    )
                    sample = g.node("GeometryNodeSampleCurve")
                    sample.mode = "FACTOR"
                    sample.use_all_curves = False
                    g.wire(path, sample.inputs["Curves"])
                    g.wire(
                        g.attr("growth_curve_index", "INT"),
                        sample.inputs["Curve Index"],
                    )
                    g.wire(coords.outputs["Z"], sample.inputs["Factor"])
                    pos = g.vector(
                        "ADD",
                        sample.outputs["Position"],
                        g.vector(
                            "SCALE",
                            sample.outputs["Normal"],
                            g.math(
                                "MULTIPLY", coords.outputs["X"], family.template.width
                            ),
                        ),
                    )
                    pos = g.vector(
                        "ADD",
                        pos,
                        g.vector(
                            "SCALE",
                            g.vector(
                                "CROSS_PRODUCT",
                                sample.outputs["Tangent"],
                                sample.outputs["Normal"],
                            ),
                            g.math(
                                "MULTIPLY",
                                coords.outputs["Y"],
                                family.template.thickness,
                            ),
                        ),
                    )
                    bend = g.node("GeometryNodeSetPosition")
                    g.wire(realize.outputs["Geometry"], bend.inputs["Geometry"])
                    g.wire(pos, bend.inputs["Position"])
                    part = bend.outputs["Geometry"]
            if materials[i] is not None and family.template is None:
                mat = g.node("GeometryNodeSetMaterial")
                mat.inputs["Material"].default_value = materials[i]
                g.wire(part, mat.inputs["Geometry"])
                part = mat.outputs["Geometry"]
            g.wire(part, join.inputs["Geometry"])
        g.wire(join.outputs["Geometry"], g.output.inputs["Geometry"])
        if len(g.group.nodes) > 350:
            raise ValueError("Owned growth graph exceeds350 nodes")
        g.group[KEY] = owner_id
        g.group[KEY + "_signature"] = signature(g.group)
        return g.group
    except BaseException:
        bpy.data.node_groups.remove(g.group)
        raise
