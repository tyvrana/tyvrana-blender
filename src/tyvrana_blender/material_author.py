"Staged, bounded shader construction. All access runs on Blender's main thread."

import hashlib
import json
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

from pydantic import ValidationError

from .errors import OperationError
from .material_author_models import (
    Assignment,
    Coordinates,
    GraphAuthorArguments,
    MaterialAuthorArguments,
    NodeSpec,
)
from .material_models import (
    PRINCIPLED_SOCKETS,
    MaterialGraphSummary,
    SurfaceParameters,
    TextureChannelSummary,
)

OWNER = "tyvrana_material"
SURFACE_SOCKETS = {
    **PRINCIPLED_SOCKETS,
    "thin_wall": "Thin Wall",
    "diffuse_roughness": "Diffuse Roughness",
    "subsurface_anisotropy": "Subsurface Anisotropy",
    "specular_ior_level": "Specular IOR Level",
    "specular_tint": "Specular Tint",
    "anisotropy": "Anisotropic",
    "anisotropy_rotation": "Anisotropic Rotation",
    "coat_ior": "Coat IOR",
    "coat_tint": "Coat Tint",
    "sheen_weight": "Sheen Weight",
    "sheen_roughness": "Sheen Roughness",
    "sheen_tint": "Sheen Tint",
    "thin_film_thickness": "Thin Film Thickness",
    "thin_film_ior": "Thin Film IOR",
}
# Native class, input aliases, output aliases. Dynamic sockets are checked again
# after setting the typed node properties, so disabled inputs cannot be linked.
NODES: dict[str, tuple[str, dict[str, str], dict[str, str]]] = {
    "principled": (
        "ShaderNodeBsdfPrincipled",
        {
            **SURFACE_SOCKETS,
            "normal": "Normal",
            "tangent": "Tangent",
            "coat_normal": "Coat Normal",
        },
        {"bsdf": "BSDF"},
    ),
    "output": (
        "ShaderNodeOutputMaterial",
        {"surface": "Surface", "displacement": "Displacement"},
        {},
    ),
    "image_texture": (
        "ShaderNodeTexImage",
        {"vector": "Vector"},
        {"color": "Color", "alpha": "Alpha"},
    ),
    "texture_coordinate": (
        "ShaderNodeTexCoord",
        {},
        {"uv": "UV", "generated": "Generated", "object": "Object", "normal": "Normal"},
    ),
    "uv_map": ("ShaderNodeUVMap", {}, {"uv": "UV"}),
    "mapping": (
        "ShaderNodeMapping",
        {
            "vector": "Vector",
            "location": "Location",
            "rotation": "Rotation",
            "scale": "Scale",
        },
        {"vector": "Vector"},
    ),
    "noise": (
        "ShaderNodeTexNoise",
        {
            "vector": "Vector",
            "scale": "Scale",
            "detail": "Detail",
            "roughness": "Roughness",
            "lacunarity": "Lacunarity",
            "distortion": "Distortion",
        },
        {"factor": "Fac", "color": "Color"},
    ),
    "color_ramp": (
        "ShaderNodeValToRGB",
        {"factor": "Fac"},
        {"color": "Color", "alpha": "Alpha"},
    ),
    "mix_color": (
        "ShaderNodeMix",
        {"factor": "Factor_Float", "a": "A_Color", "b": "B_Color"},
        {"result": "Result_Color"},
    ),
    "math": ("ShaderNodeMath", {"a": "Value", "b": "Value_001"}, {"value": "Value"}),
    "vector_math": (
        "ShaderNodeVectorMath",
        {"a": "Vector", "b": "Vector_001", "scale": "Scale"},
        {"vector": "Vector", "value": "Value"},
    ),
    "normal_map": (
        "ShaderNodeNormalMap",
        {"color": "Color", "strength": "Strength"},
        {"normal": "Normal"},
    ),
    "bump": (
        "ShaderNodeBump",
        {
            "height": "Height",
            "normal": "Normal",
            "strength": "Strength",
            "distance": "Distance",
        },
        {"normal": "Normal"},
    ),
    "displacement": (
        "ShaderNodeDisplacement",
        {
            "height": "Height",
            "normal": "Normal",
            "scale": "Scale",
            "midlevel": "Midlevel",
        },
        {"displacement": "Displacement"},
    ),
    "layer_weight": (
        "ShaderNodeLayerWeight",
        {"blend": "Blend", "normal": "Normal"},
        {"fresnel": "Fresnel", "facing": "Facing"},
    ),
    "fresnel": (
        "ShaderNodeFresnel",
        {"ior": "IOR", "normal": "Normal"},
        {"factor": "Fac"},
    ),
    "tangent": ("ShaderNodeTangent", {}, {"tangent": "Tangent"}),
}
KINDS = {value[0]: key for key, value in NODES.items()}
PROPERTIES = {
    "principled": ("subsurface_method", "distribution"),
    "output": ("target", "is_active_output"),
    "image_texture": ("interpolation", "projection", "extension", "projection_blend"),
    "texture_coordinate": ("from_instancer",),
    "uv_map": ("uv_map", "from_instancer"),
    "mapping": ("vector_type",),
    "noise": ("noise_dimensions", "noise_type", "normalize"),
    "mix_color": (
        "data_type",
        "factor_mode",
        "blend_type",
        "clamp_factor",
        "clamp_result",
    ),
    "math": ("operation", "use_clamp"),
    "vector_math": ("operation",),
    "normal_map": ("space", "uv_map", "convention", "base"),
    "bump": ("invert",),
    "displacement": ("space",),
    "tangent": ("direction_type", "axis", "uv_map"),
}
SETTINGS = {
    "displacement": "displacement_method",
    "surface": "surface_render_method",
    "thickness": "thickness_mode",
}


def fail(message: str, code: str = "invalid_arguments") -> NoReturn:
    raise OperationError(code, message)


def plain(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return list(value)


def socket(node: Any, alias: str, *, output: bool = False) -> Any:
    kind = KINDS.get(node.bl_idname)
    if kind is None:
        fail("Unsupported node class")
    aliases = NODES[kind][2] if output else NODES[kind][1]
    identifier = aliases.get(alias)
    found = next(
        (
            s
            for s in (node.outputs if output else node.inputs)
            if s.identifier == identifier
        ),
        None,
    )
    if found is None or not found.enabled or found.is_unavailable:
        fail(f"Socket {node.name}.{alias} is unsupported or unavailable")
    return found


def set_input(node: Any, alias: str, value: Any) -> None:
    target = socket(node, alias)
    if target.is_linked:
        fail(f"Disconnect {node.name}.{alias} before writing its default")
    if target.type == "RGBA":
        value = [*value, 1] if len(value) == 3 else value
    numbers = value if isinstance(value, list) else [value]
    if any(
        v < getattr(target, "min_value", -float("inf"))
        or v > getattr(target, "max_value", float("inf"))
        for v in numbers
    ):
        fail(f"Value outside Blender's range for {node.name}.{alias}")
    target.default_value = value
    if plain(target.default_value) != value:
        fail(f"Blender did not retain {node.name}.{alias}", "operation_failed")


def unique_resource(collection: Any, name: str, kind: str) -> Any:
    matches = [item for item in collection if item.name == name]
    if len(matches) != 1:
        fail(f"{kind} {name!r} is missing or ambiguous")
    return matches[0]


def configure(node: Any, spec: NodeSpec, data: Any) -> None:
    values = spec.model_dump(mode="json", exclude_unset=True, exclude={"id", "type"})
    if any(value is None for value in values.values()):
        fail("Omit unchanged node fields; null is invalid")
    if spec.type == "principled":
        method = values.pop("subsurface_method", None)
        if method:
            node.subsurface_method = method.upper()
        for key, value in values.pop("parameters", {}).items():
            set_input(node, key, value)
    if spec.type == "image_texture":
        name = values.pop("image", None)
        if name:
            node.image = unique_resource(data.images, name, "Image")
        color_space = values.pop("color_space", None)
        if node.image is None:
            fail("Image texture requires an existing image")
        if node.image.source not in {"FILE", "GENERATED"}:
            fail("This graph subset supports single file and generated images")
        if color_space is None and name:
            fail("Image texture requires explicit color_space")
        if color_space and node.image.colorspace_settings.name != color_space:
            fail(
                f"Image {node.image.name!r} has color space "
                f"{node.image.colorspace_settings.name!r}; requested {color_space!r}. "
                "Configure or copy the image explicitly"
            )
        for key in ("interpolation", "projection", "extension"):
            if key in values:
                value = values.pop(key)
                setattr(
                    node,
                    key,
                    value.title() if key == "interpolation" else value.upper(),
                )
    if spec.type == "texture_coordinate" and "object_name" in values:
        node.object = unique_resource(data.objects, values.pop("object_name"), "Object")
    if spec.type == "color_ramp":
        ramp = node.color_ramp
        if "stops" in values:
            stops = values.pop("stops")
            while len(ramp.elements) > 2:
                ramp.elements.remove(ramp.elements[-1])
            for index, stop in enumerate(stops):
                element = (
                    ramp.elements[index]
                    if index < 2
                    else ramp.elements.new(stop["position"])
                )
                element.position = stop["position"]
                element.color = [*stop["color"], stop.get("alpha", 1)]
        if "interpolation" in values:
            ramp.interpolation = values.pop("interpolation").upper()
    for field, prop in {
        "subsurface_method": "subsurface_method",
        "space": "space",
        "convention": "convention",
        "base": "base",
        "direction": "direction_type",
        "axis": "axis",
        "operation": "operation",
        "blend": "blend_type",
    }.items():
        if field in values and not (spec.type == "layer_weight" and field == "blend"):
            setattr(node, prop, values.pop(field).upper())
    for field, prop in {
        "uv_map": "uv_map",
        "invert": "invert",
        "clamp": "use_clamp",
        "clamp_result": "clamp_result",
    }.items():
        if field in values:
            setattr(node, prop, values.pop(field))
    for key, value in values.items():
        set_input(node, key, value)


def add_node(tree: Any, spec: NodeSpec, data: Any) -> None:
    node = tree.nodes.get(spec.id)
    if node and node.bl_idname != NODES[spec.type][0]:
        fail("A node ID cannot change type in a patch; remove it and use a new ID")
    if node is None:
        node = tree.nodes.new(NODES[spec.type][0])
        node.name = spec.id
        if node.name != spec.id:
            fail("Blender did not retain node ID")
        if spec.type == "mix_color":
            node.data_type = "RGBA"
        if spec.type == "noise":
            node.noise_dimensions, node.noise_type, node.normalize = "3D", "FBM", True
    configure(node, spec, data)


def apply_graph(material: Any, arguments: GraphAuthorArguments, data: Any) -> None:
    tree = material.node_tree
    if arguments.mode != "patch":
        tree.nodes.clear()
    for name in arguments.remove_nodes:
        node = tree.nodes.get(name)
        if node is None:
            fail(f"Node {name!r} does not exist")
        tree.nodes.remove(node)
    for endpoint in arguments.disconnect:
        node = tree.nodes.get(endpoint.node)
        if node is None:
            fail(f"Node {endpoint.node!r} does not exist")
        for link in list(socket(node, endpoint.socket).links):
            tree.links.remove(link)
    for spec in arguments.nodes:
        add_node(tree, spec, data)
    for link in arguments.links:
        source = tree.nodes.get(link.source.node)
        target = tree.nodes.get(link.target.node)
        if source is None or target is None:
            fail("Link references an absent node")
        output = socket(source, link.source.socket, output=True)
        input_ = socket(target, link.target.socket)
        if (output.type == "SHADER") != (input_.type == "SHADER"):
            fail("Shader links require shader sockets at both ends")
        for old in list(input_.links):
            tree.links.remove(old)
        tree.links.new(output, input_)
    for field, value in arguments.settings.model_dump(exclude_unset=True).items():
        if value is None:
            fail("Omit unchanged settings; null is invalid")
        setattr(material, SETTINGS[field], value.upper())
    validate_graph(material)


def validate_graph(material: Any) -> None:
    tree = material.node_tree
    if len(tree.nodes) > 64 or len(tree.links) > 128:
        fail("Final graph exceeds 64 nodes or 128 links")
    if any(n.bl_idname not in KINDS or n.mute for n in tree.nodes):
        fail("Declarative graphs require supported, unmuted nodes")
    for node in tree.nodes:
        if node.bl_idname == "ShaderNodeMix" and node.data_type != "RGBA":
            fail("This subset supports color Mix nodes")
        if node.bl_idname == "ShaderNodeTexNoise" and (
            node.noise_dimensions != "3D"
            or node.noise_type != "FBM"
            or not node.normalize
        ):
            fail("This subset supports normalized 3D fBM Noise")
    images = {n.image for n in tree.nodes if n.bl_idname == "ShaderNodeTexImage"}
    if len(images) > 16 or None in images:
        fail("Final graph requires at most 16 resolved images")
    outputs = [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    if (
        len(outputs) != 1
        or outputs[0].target != "ALL"
        or not outputs[0].is_active_output
    ):
        fail("Final graph requires exactly one active all-engine output")
    if not outputs[0].inputs["Surface"].is_linked:
        fail("Final graph requires a connected surface")
    edges: dict[str, list[str]] = {n.name: [] for n in tree.nodes}
    for link in tree.links:
        if (
            not link.is_valid
            or link.is_muted
            or link.from_socket.is_unavailable
            or link.to_socket.is_unavailable
        ):
            fail("Graph contains an invalid, muted or unavailable link")
        edges[link.from_node.name].append(link.to_node.name)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            fail("Shader graph cycles are not supported")
        if name in visited:
            return
        visiting.add(name)
        for target in edges[name]:
            visit(target)
        visiting.remove(name)
        visited.add(name)

    for name in edges:
        visit(name)


def fingerprint(material: Any) -> tuple[str, bool]:
    tree = material.node_tree
    rows = []
    complete = (
        tree is not None
        and tree.animation_data is None
        and material.animation_data is None
    )
    for node in sorted(tree.nodes if tree else [], key=lambda n: n.name):
        kind = KINDS.get(node.bl_idname)
        complete = complete and kind is not None
        row = {
            "id": node.name,
            "class": node.bl_idname,
            "mute": node.mute,
            "defaults": [
                (s.identifier, plain(getattr(s, "default_value", None)))
                for s in node.inputs
            ],
            "properties": {
                key: getattr(node, key) for key in PROPERTIES.get(kind or "", ())
            },
        }
        if kind == "image_texture":
            image = node.image
            row["image"] = (
                None
                if image is None
                else [
                    image.name,
                    image.library.filepath if image.library else None,
                    image.colorspace_settings.name,
                    image.alpha_mode,
                    image.source,
                    list(image.size),
                    bool(image.packed_file),
                ]
            )
        if kind == "texture_coordinate":
            row["object"] = node.object.name if node.object else None
        if kind == "color_ramp":
            ramp = node.color_ramp
            row["ramp"] = [
                ramp.color_mode,
                ramp.interpolation,
                ramp.hue_interpolation,
                [(e.position, list(e.color)) for e in ramp.elements],
            ]
        rows.append(row)
    payload = {
        "nodes": rows,
        "links": sorted(
            (
                link.from_node.name,
                link.from_socket.identifier,
                link.to_node.name,
                link.to_socket.identifier,
                link.is_muted,
            )
            for link in tree.links
        )
        if tree
        else [],
        "settings": {key: getattr(material, prop) for key, prop in SETTINGS.items()},
    }

    def finite(value: Any) -> Any:
        nonlocal complete
        if isinstance(value, float) and not math.isfinite(value):
            complete = False
            return str(value)
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [finite(item) for item in value]
        return value

    encoded = json.dumps(finite(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest(), bool(complete)


def owner(material: Any) -> dict[str, Any]:
    try:
        result = json.loads(material.get(OWNER, "{}"))
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def mark(material: Any, kind: str, recipe: dict[str, Any] | None = None) -> None:
    signature, complete = fingerprint(material)
    if not complete:
        fail("Cannot own an incompletely fingerprinted material")
    material[OWNER] = json.dumps(
        {"kind": kind, "fingerprint": signature, "recipe": recipe},
        separators=(",", ":"),
    )


def inspect(material: Any) -> MaterialGraphSummary:
    signature, complete = fingerprint(material)
    metadata = owner(material)
    ownership = metadata.get("kind", "unowned")
    if ownership not in {"semantic", "declarative"}:
        ownership = "unowned"
    if ownership != "unowned" and signature != metadata.get("fingerprint"):
        ownership = "modified"
    nodes = list(material.node_tree.nodes) if material.node_tree else []
    outputs = [
        n
        for n in nodes
        if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output
    ]
    output = outputs[0] if len(outputs) == 1 else None
    surface = output.inputs["Surface"] if output else None
    principled = (
        surface.links[0].from_node if surface and len(surface.links) == 1 else None
    )
    if principled and principled.bl_idname != "ShaderNodeBsdfPrincipled":
        principled = None
    parameters = None
    features: list[str] = []
    textures: list[TextureChannelSummary] = []
    warnings = []
    if principled:
        values = {}
        for key, identifier in SURFACE_SOCKETS.items():
            value = plain(principled.inputs[identifier].default_value)
            values[key] = (
                value[:3] if isinstance(value, list) and len(value) == 4 else value
            )
        try:
            parameters = SurfaceParameters.model_validate(values)
        except ValidationError:
            warnings.append("Surface defaults contain values outside semantic bounds")
        for field, feature in [
            ("subsurface_weight", "subsurface"),
            ("anisotropy", "anisotropy"),
            ("thin_film_thickness", "thin_film"),
            ("transmission_weight", "transmission"),
            ("coat_weight", "coat"),
            ("sheen_weight", "sheen"),
            ("emission_strength", "emission"),
        ]:
            if values[field] or principled.inputs[SURFACE_SOCKETS[field]].is_linked:
                features.append(feature)
        if ownership == "semantic":
            for binding in metadata.get("recipe", {}).get("textures", []):
                node = material.node_tree.nodes.get("tex." + binding["channel"])
                if node and node.image:
                    textures.append(
                        TextureChannelSummary(
                            channel=binding["channel"],
                            image=node.image.name,
                            color_space=node.image.colorspace_settings.name,
                        )
                    )
        else:
            for field, identifier in SURFACE_SOCKETS.items():
                links = principled.inputs[identifier].links
                if (
                    len(links) == 1
                    and links[0].from_node.bl_idname == "ShaderNodeTexImage"
                    and links[0].from_node.image
                ):
                    image = links[0].from_node.image
                    textures.append(
                        TextureChannelSummary(
                            channel=field,
                            image=image.name,
                            color_space=image.colorspace_settings.name,
                        )
                    )
        if values["thin_wall"] and values["subsurface_weight"]:
            warnings.append("Thin Wall ignores subsurface radius and scale")
    kinds = {KINDS.get(n.bl_idname) for n in nodes}
    for kind, feature in [
        ("noise", "procedural_variation"),
        ("normal_map", "normal"),
        ("bump", "bump"),
        ("displacement", "displacement"),
        ("layer_weight", "angle_dependent"),
        ("fresnel", "angle_dependent"),
        ("tangent", "tangent"),
    ]:
        if kind in kinds and feature not in features:
            features.append(feature)
    if not complete:
        warnings.append(
            "Fingerprint excludes unsupported node properties or animation; "
            "use detailed inspection"
        )
    if ownership == "modified":
        warnings.append(
            "Owned graph changed externally; use a graph patch or explicit replacement"
        )
    if material.displacement_method != "BUMP":
        warnings.append(
            "True displacement requires sufficient evaluated geometry "
            "and engine support"
        )
    for texture in textures:
        expected = (
            "sRGB"
            if texture.channel in {"base_color", "emission_color"}
            else "Non-Color"
        )
        if texture.color_space != expected:
            warnings.append(
                f"Intentional non-default color space for {texture.channel}: "
                f"{texture.color_space}"
            )
    if "anisotropy" in features or "thin_film" in features or "subsurface" in features:
        warnings.append(
            "Verify advanced shading in the target engine; Cycles and Eevee differ"
        )
    return MaterialGraphSummary(
        ownership=ownership,
        fingerprint=signature,
        fingerprint_complete=complete,
        node_count=len(nodes),
        link_count=len(material.node_tree.links) if material.node_tree else 0,
        image_count=len(
            {n.image for n in nodes if n.bl_idname == "ShaderNodeTexImage" and n.image}
        ),
        surface_connected=bool(surface and surface.is_linked),
        displacement_connected=bool(output and output.inputs["Displacement"].is_linked),
        parameters=parameters,
        linked_inputs=[
            alias
            for alias, identifier in NODES["principled"][1].items()
            if principled and principled.inputs[identifier].is_linked
        ],
        subsurface_method=principled.subsurface_method.lower() if principled else None,
        displacement_method=material.displacement_method.lower(),
        textures=textures[:16],
        features=features,
        warnings=warnings[:16],
    )


def semantic_recipe(
    arguments: MaterialAuthorArguments, previous: Any
) -> dict[str, Any]:
    recipe: dict[str, Any] = {"parameters": {}, "textures": [], "settings": {}}
    if arguments.mode == "update":
        metadata = owner(previous)
        if (
            metadata.get("kind") != "semantic"
            or metadata.get("fingerprint") != fingerprint(previous)[0]
        ):
            fail(
                "Semantic update requires an unchanged semantic graph",
                "unsupported_material_graph",
            )
        recipe = dict(metadata["recipe"])
    recipe["parameters"] = {
        **recipe["parameters"],
        **arguments.parameters.model_dump(exclude_unset=True),
    }
    recipe["settings"] = {
        **recipe["settings"],
        **arguments.settings.model_dump(exclude_unset=True),
    }
    bindings = {binding["channel"]: binding for binding in recipe["textures"]}
    for channel in arguments.remove_textures:
        bindings.pop(channel, None)
    for texture in arguments.textures:
        bindings[texture.channel] = texture.model_dump(mode="json")
    recipe["textures"] = list(bindings.values())
    for field in ("coordinates", "variation", "tangent", "subsurface_method"):
        if field in arguments.model_fields_set:
            value = getattr(arguments, field)
            if value is None:
                fail(f"Omit unchanged {field}; use the explicit removal option")
            recipe[field] = (
                value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            )
    if arguments.remove_variation:
        recipe.pop("variation", None)
    if arguments.remove_tangent:
        recipe.pop("tangent", None)
    if recipe.get("variation", {}).get("channel") in bindings:
        fail(
            "A semantic channel cannot have both a texture and procedural "
            "variation; use a declarative Mix branch"
        )
    return recipe


def recipe_graph(name: str, recipe: dict[str, Any]) -> GraphAuthorArguments:
    nodes: list[dict[str, Any]] = [
        {"id": "surface", "type": "principled", "parameters": recipe["parameters"]},
        {"id": "output", "type": "output"},
    ]
    links: list[dict[str, Any]] = []
    if recipe.get("subsurface_method"):
        nodes[0]["subsurface_method"] = recipe["subsurface_method"]

    def link(a: str, output: str, b: str, input_: str) -> None:
        links.append(
            {
                "source": {"node": a, "socket": output},
                "target": {"node": b, "socket": input_},
            }
        )

    coordinate_cache: dict[str, str] = {}

    def coordinates(raw: dict[str, Any] | None) -> str:
        coordinate = Coordinates.model_validate(raw or recipe.get("coordinates") or {})
        key = coordinate.model_dump_json()
        if key in coordinate_cache:
            return coordinate_cache[key]
        prefix = "coords." + str(len(coordinate_cache))
        if coordinate.source == "uv":
            nodes.append({"id": prefix, "type": "uv_map", "uv_map": coordinate.uv_map})
        else:
            node = {"id": prefix, "type": "texture_coordinate"}
            if coordinate.object_name:
                node["object_name"] = coordinate.object_name
            nodes.append(node)
        mapping = prefix + ".mapping"
        nodes.append(
            {
                "id": mapping,
                "type": "mapping",
                "location": coordinate.location,
                "rotation": coordinate.rotation,
                "scale": coordinate.scale,
            }
        )
        link(prefix, coordinate.source, mapping, "vector")
        coordinate_cache[key] = mapping
        return mapping

    link("surface", "bsdf", "output", "surface")
    channels = {binding["channel"] for binding in recipe["textures"]}
    for binding in recipe["textures"]:
        channel = binding["channel"]
        node_id = "tex." + channel
        color_space = binding["color_space"] or (
            "sRGB" if channel in {"base_color", "emission_color"} else "Non-Color"
        )
        nodes.append(
            {
                "id": node_id,
                "type": "image_texture",
                "image": binding["image"],
                "color_space": color_space,
                "interpolation": binding["interpolation"],
                "projection": binding["projection"],
                "extension": binding["extension"],
            }
        )
        mapping = coordinates(binding["coordinates"])
        link(mapping, "vector", node_id, "vector")
        if channel == "normal":
            coordinate = Coordinates.model_validate(
                binding["coordinates"] or recipe.get("coordinates") or {}
            )
            if binding["normal_space"] == "tangent" and (
                coordinate.source != "uv" or binding["projection"] != "flat"
            ):
                fail("Tangent normal maps require flat UV coordinates")
            nodes.append(
                {
                    "id": "normal",
                    "type": "normal_map",
                    "space": binding["normal_space"],
                    "convention": binding["normal_convention"],
                    "uv_map": coordinate.uv_map,
                    "strength": binding["strength"],
                }
            )
            link(node_id, "color", "normal", "color")
            link(
                "normal",
                "normal",
                "bump" if "bump" in channels else "surface",
                "normal",
            )
        elif channel == "bump":
            nodes.append(
                {
                    "id": "bump",
                    "type": "bump",
                    "strength": binding["strength"],
                    "distance": binding["distance"],
                    "invert": binding["invert"],
                }
            )
            link(node_id, "color", "bump", "height")
            link("bump", "normal", "surface", "normal")
        elif channel == "displacement":
            nodes.append(
                {
                    "id": "displacement",
                    "type": "displacement",
                    "scale": binding["displacement_scale"],
                    "midlevel": binding["displacement_midlevel"],
                    "space": binding["displacement_space"],
                }
            )
            link(node_id, "color", "displacement", "height")
            link("displacement", "displacement", "output", "displacement")
        else:
            link(node_id, binding["image_output"], "surface", channel)
    if recipe.get("variation"):
        variation = recipe["variation"]
        mapping = coordinates(variation["coordinates"])
        nodes.extend(
            [
                {
                    "id": "variation",
                    "type": "noise",
                    "scale": variation["scale"],
                    "detail": variation["detail"],
                },
                {
                    "id": "variation.ramp",
                    "type": "color_ramp",
                    "stops": [
                        {"position": 0, "color": variation["low"]},
                        {"position": 1, "color": variation["high"]},
                    ],
                },
            ]
        )
        link(mapping, "vector", "variation", "vector")
        link("variation", "factor", "variation.ramp", "factor")
        link("variation.ramp", "color", "surface", variation["channel"])
    if recipe.get("tangent"):
        nodes.append({"id": "tangent", "type": "tangent", **recipe["tangent"]})
        link("tangent", "tangent", "surface", "tangent")
    return GraphAuthorArguments.model_validate(
        {"name": name, "nodes": nodes, "links": links, "settings": recipe["settings"]}
    )


def validate_uv(material: Any, obj: Any) -> None:
    """Do not silently sample a constant when an owned graph requires UVs."""
    if not owner(material) or obj.type != "MESH":
        return
    for node in material.node_tree.nodes:
        needs_uv = (
            node.bl_idname == "ShaderNodeUVMap"
            or node.bl_idname == "ShaderNodeNormalMap"
            and node.space == "TANGENT"
            or node.bl_idname == "ShaderNodeTangent"
            and node.direction_type == "UV_MAP"
            or node.bl_idname == "ShaderNodeTexCoord"
            and node.outputs["UV"].is_linked
        )
        if not needs_uv:
            continue
        name = getattr(node, "uv_map", "")
        layers = obj.data.uv_layers
        if (name and name not in layers) or (not name and not layers):
            fail(f"Object {obj.name!r} lacks required UV map {name or '(render UV)'!r}")


def validate_users(
    material: Any, previous: Any, data: Any, assignments: list[Assignment]
) -> None:
    objects = {
        unique_resource(data.objects, a.object_name, "Object") for a in assignments
    }
    if previous:
        objects.update(
            obj
            for obj in data.objects
            if any(slot.material == previous for slot in obj.material_slots)
        )
    for obj in objects:
        validate_uv(material, obj)


def writable(material: Any, data: Any, *, affect_shared: bool) -> None:
    if (
        not material.is_editable
        or material.library
        or material.override_library
        or material.is_grease_pencil
        or material.node_tree is None
        or not material.node_tree.is_editable
    ):
        fail("Material must be a local editable shader material", "invalid_context")
    if material.animation_data or material.node_tree.animation_data:
        fail(
            "Animated materials cannot be staged by these operations", "invalid_context"
        )
    tree_users = data.user_map(subset={material.node_tree}).get(
        material.node_tree, set()
    )
    if any(user != material for user in tree_users):
        fail("Material node tree has external ID references", "invalid_context")
    users = data.user_map(subset={material}).get(material, set())
    if any(
        not user.is_editable or user.library or user.override_library for user in users
    ):
        fail("A material user is linked, overridden or read-only", "invalid_context")
    assignment_count = sum(
        slot.material == material for obj in data.objects for slot in obj.material_slots
    )
    if not affect_shared and (
        material.users - int(material.use_fake_user) > 1 or assignment_count > 1
    ):
        fail(
            "Shared material update requires affect_shared=true or an independent copy"
        )


@contextmanager
def staged(
    data: Any, name: str, mode: str, affect_shared: bool, expected: str | None
) -> Iterator[tuple[Any, Any]]:
    matches = [m for m in data.materials if m.name == name]
    previous = None
    if mode == "create":
        if matches:
            fail("Material name already exists")
    else:
        previous = unique_resource(data.materials, name, "Material")
        writable(previous, data, affect_shared=affect_shared)
        if expected is not None and fingerprint(previous)[0] != expected:
            fail(
                "Material fingerprint changed; inspect before retrying",
                "state_conflict",
            )
    candidate = previous.copy() if previous else data.materials.new(name)
    try:
        yield previous, candidate
    except Exception:
        if previous:
            candidate.user_remap(previous)
            candidate.name = "__tyvrana_discarded"
            previous.name = name
        data.materials.remove(candidate)
        raise
    else:
        if previous:
            data.materials.remove(previous)


def publish(
    previous: Any, candidate: Any, name: str, update: Callable[[], None]
) -> None:
    if previous:
        previous.user_remap(candidate)
        previous.name = "__tyvrana_replaced"
    candidate.name = name
    update()
    if candidate.name != name:
        fail("Blender did not retain material name", "operation_failed")


@contextmanager
def assignment_transaction(
    data: Any,
    assignments: list[Assignment],
    snapshot_indices: Callable[[Any], Any],
    update: Callable[[], None],
) -> Iterator[None]:
    snapshots = []
    seen = set()
    for assignment in assignments:
        obj = unique_resource(data.objects, assignment.object_name, "Object")
        if obj in seen:
            continue
        seen.add(obj)
        if obj.data is None or not hasattr(obj.data, "materials"):
            fail("Object does not support material slots")
        snapshots.append(
            (
                obj,
                obj.data,
                [(s.link, s.material) for s in obj.material_slots],
                obj.active_material_index,
                snapshot_indices(obj.data),
            )
        )
    try:
        yield
    except Exception:
        discard = set()
        for obj, original, slots, active, indices in reversed(snapshots):
            if obj.data != original:
                discard.add(obj.data)
                obj.data = original
            while len(original.materials) > len(slots):
                if not slots:
                    original.materials.clear()
                else:
                    original.materials.pop(index=len(original.materials) - 1)
            for index, (link, material) in enumerate(slots):
                slot = obj.material_slots[index]
                slot.link = link
                if slot.material != material:
                    slot.material = material
            obj.active_material_index = active
            for item, property_name, value in indices:
                setattr(item, property_name, value)
        if discard:
            data.batch_remove(ids=tuple(discard))
        update()
        raise
