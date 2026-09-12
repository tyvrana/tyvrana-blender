"""Shader tree edits called only by the Blender main-thread backend."""

from typing import Any

from pydantic import ValidationError

from .operations import OperationError
from .shader_models import (
    NODE_SOCKETS,
    NODE_TYPES,
    BumpSettings,
    ConnectArguments,
    DisconnectArguments,
    DisconnectResult,
    ImageTextureSettings,
    LinkSummary,
    MappingSettings,
    MaterialOutputSettings,
    NodeCreateArguments,
    NodeDeleteArguments,
    NodeDeleteResult,
    NodePatch,
    NodeSettings,
    NodeSummary,
    NormalMapSettings,
    ShaderGraphSummary,
    SocketSummary,
    TextureCoordinateSettings,
    compatible_sockets,
    safe_socket_default,
)


def find_node(tree: Any, name: str) -> Any:
    node = tree.nodes.get(name)
    if node is None:
        raise OperationError("node_not_found", "Node does not exist", {"name": name})
    return node


def socket(items: Any, identifier: str) -> Any:
    matches = [item for item in items if item.identifier == identifier]
    if len(matches) != 1:
        raise OperationError(
            "socket_not_found",
            "Socket identifier is missing or ambiguous",
            {"identifier": identifier},
        )
    return matches[0]


def socket_summary(item: Any) -> SocketSummary:
    value = getattr(item, "default_value", None)
    if item.type in {"VECTOR", "RGBA"} and value is not None and len(value) <= 4:
        value = list(value)
    return SocketSummary(
        name=str(item.name),
        identifier=str(item.identifier),
        socket_type=str(item.type),
        linked=bool(item.is_linked),
        enabled=bool(item.enabled and not item.is_unavailable),
        default_value=safe_socket_default(value),
    )


def node_kind(node: Any) -> str:
    for kind, identifier in NODE_TYPES.items():
        if node.bl_idname == identifier:
            return kind
    raise OperationError("node_type_unsupported", "Node type is not configurable")


def settings(node: Any) -> NodeSettings | None:
    def scalar(identifier: str) -> float:
        return float(socket(node.inputs, identifier).default_value)

    def vector(identifier: str) -> list[float]:
        return list(socket(node.inputs, identifier).default_value)

    match node.bl_idname:
        case "ShaderNodeOutputMaterial":
            return MaterialOutputSettings(
                active=node.is_active_output, target=node.target.lower()
            )
        case "ShaderNodeTexImage":
            return ImageTextureSettings(
                image_name=str(node.image.name) if node.image else None,
                interpolation=node.interpolation.lower(),
                projection=node.projection.lower(),
                extension=node.extension.lower(),
            )
        case "ShaderNodeTexCoord":
            return TextureCoordinateSettings(
                from_instancer=bool(node.from_instancer),
                object_name=str(node.object.name) if node.object else None,
            )
        case "ShaderNodeMapping":
            return MappingSettings(
                vector_type=node.vector_type.lower(),
                location=vector("Location"),
                rotation=vector("Rotation"),
                scale=vector("Scale"),
            )
        case "ShaderNodeNormalMap":
            return NormalMapSettings(
                strength=scalar("Strength"),
                space=node.space.lower(),
                uv_map=node.uv_map,
            )
        case "ShaderNodeBump":
            return BumpSettings(
                strength=scalar("Strength"),
                distance=scalar("Distance"),
                invert=node.invert,
            )
    return None


def node_summary(node: Any) -> NodeSummary:
    try:
        state = settings(node)
    except ValidationError:
        state = None
    return NodeSummary(
        node_name=str(node.name),
        node_type=str(node.bl_idname),
        label=str(node.label),
        muted=bool(node.mute),
        inputs=[socket_summary(s) for s in node.inputs],
        outputs=[socket_summary(s) for s in node.outputs],
        settings=state,
    )


def link_summary(link: Any) -> LinkSummary:
    return LinkSummary(
        from_node=str(link.from_node.name),
        from_socket=str(link.from_socket.identifier),
        to_node=str(link.to_node.name),
        to_socket=str(link.to_socket.identifier),
        valid=bool(link.is_valid),
        muted=bool(link.is_muted),
    )


def graph_summary(material: Any) -> ShaderGraphSummary:
    tree = material.node_tree
    return ShaderGraphSummary(
        material_name=str(material.name),
        node_tree_present=tree is not None,
        nodes=[]
        if tree is None
        else [node_summary(n) for n in sorted(tree.nodes, key=lambda n: n.name)],
        links=[]
        if tree is None
        else [
            link_summary(link)
            for link in sorted(
                tree.links,
                key=lambda link: (
                    link.from_node.name,
                    link.from_socket.identifier,
                    link.to_node.name,
                    link.to_socket.identifier,
                ),
            )
        ],
    )


def editable_tree(material: Any) -> Any:
    tree = material.node_tree
    if tree is None or tree.bl_idname != "ShaderNodeTree" or material.is_grease_pencil:
        raise OperationError(
            "unsupported_material_graph", "Material has no editable shader tree"
        )
    if not material.is_editable or not tree.is_editable:
        raise OperationError("invalid_context", "Material shader data is not editable")
    return tree


def configure_node(
    node: Any, arguments: NodePatch, images: Any, update: Any
) -> NodeSummary:
    kind = node_kind(node)
    try:
        requested = arguments.settings_for(kind)
    except ValueError as exc:
        raise OperationError("invalid_arguments", str(exc)) from exc
    resulting_type = requested.get(
        "vector_type", getattr(node, "vector_type", "").lower()
    )
    if kind == "mapping" and resulting_type in {"vector", "normal"}:
        if "location" in requested or socket(node.inputs, "Location").is_linked:
            raise OperationError(
                "invalid_arguments", "Location requires point or texture mapping"
            )
    resulting_space = requested.get("space", getattr(node, "space", "").lower())
    if kind == "normal_map" and "uv_map" in requested and resulting_space != "tangent":
        raise OperationError(
            "invalid_arguments", "uv_map applies only to tangent-space normals"
        )
    writes: list[tuple[Any, str, Any, Any]] = []
    for key, value in requested.items():
        owner = node
        attribute = key
        if key == "image_name":
            matches = [image for image in images if image.name == value]
            if not matches:
                raise OperationError("image_not_found", "Image does not exist")
            if len(matches) != 1:
                raise OperationError(
                    "invalid_arguments", "Image name is ambiguous across libraries"
                )
            value = matches[0]
            attribute = "image"
        elif key in NODE_SOCKETS:
            owner = socket(node.inputs, NODE_SOCKETS[key])
            if owner.is_linked:
                raise OperationError(
                    "invalid_context",
                    "Disconnect the driven input before configuring its default",
                )
            attribute = "default_value"
        elif key == "interpolation":
            value = str(value).title()
        elif key in {"projection", "extension", "vector_type", "space"}:
            value = str(value).upper()
        if owner.is_property_readonly(attribute):
            raise OperationError("invalid_context", "Node property is not editable")
        original = getattr(owner, attribute)
        if key in {"location", "rotation", "scale"}:
            original = list(original)
        writes.append((owner, attribute, original, value))
    try:
        for owner, attribute, _original, value in writes:
            setattr(owner, attribute, value)
        update()
        for owner, attribute, _original, value in writes:
            stored = getattr(owner, attribute)
            if isinstance(value, list):
                stored = list(stored)
            if stored != value:
                raise OperationError(
                    "operation_failed",
                    "Blender did not retain the requested node setting",
                )
        return node_summary(node)
    except Exception:
        for owner, attribute, original, _value in reversed(writes):
            setattr(owner, attribute, original)
        update()
        raise


def create_node(
    tree: Any, arguments: NodeCreateArguments, images: Any, update: Any
) -> NodeSummary:
    if arguments.name is not None and tree.nodes.get(arguments.name) is not None:
        raise OperationError("invalid_arguments", "Node name already exists")
    active = tree.nodes.active
    selected = [(node, node.select) for node in tree.nodes]
    node = tree.nodes.new(NODE_TYPES[arguments.node_type])
    try:
        if arguments.name is not None:
            node.name = arguments.name
            if node.name != arguments.name:
                raise OperationError(
                    "invalid_arguments",
                    "Blender cannot store the requested node name exactly",
                )
        return configure_node(node, arguments, images, update)
    except Exception:
        tree.nodes.remove(node)
        raise
    finally:
        tree.nodes.active = active
        for existing, select in selected:
            existing.select = select


def delete_node(tree: Any, arguments: NodeDeleteArguments) -> NodeDeleteResult:
    node = find_node(tree, arguments.node_name)
    if node.bl_idname in {"ShaderNodeOutputMaterial", "ShaderNodeBsdfPrincipled"}:
        raise OperationError(
            "node_protected", "Principled and Material Output nodes are protected"
        )
    node_kind(node)
    tree.nodes.remove(node)
    return NodeDeleteResult(
        material_name=arguments.material_name, deleted=arguments.node_name
    )


def endpoint(tree: Any, node_name: str, identifier: str, *, output: bool) -> Any:
    node = find_node(tree, node_name)
    item = socket(node.outputs if output else node.inputs, identifier)
    if (
        bool(item.is_output) != output
        or not item.enabled
        or item.is_unavailable
        or item.is_multi_input
    ):
        raise OperationError(
            "invalid_arguments", "Socket direction or availability is unsupported"
        )
    if (
        not output
        and node.bl_idname == "ShaderNodeOutputMaterial"
        and identifier != "Surface"
    ):
        raise OperationError(
            "invalid_arguments",
            "Only Material Output Surface connections are supported",
        )
    return item


def connect(tree: Any, arguments: ConnectArguments, update: Any) -> LinkSummary:
    source = endpoint(tree, arguments.from_node, arguments.from_socket, output=True)
    target = endpoint(tree, arguments.to_node, arguments.to_socket, output=False)
    if not compatible_sockets(source.type, target.type):
        raise OperationError(
            "invalid_arguments", "Shader socket types are incompatible or unsupported"
        )
    previous = [
        (link.from_socket, link.to_socket, link.is_muted) for link in target.links
    ]
    if previous and not arguments.replace_existing:
        raise OperationError(
            "socket_already_connected",
            "Input is already connected; set replace_existing explicitly",
        )
    if len(source.links) >= source.link_limit and not any(
        a == source for a, b, mute in previous
    ):
        raise OperationError("invalid_arguments", "Source socket link limit reached")
    # Do not create cycles, including through unknown nodes in existing graphs.
    pending = [target.node]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == source.node:
            raise OperationError(
                "invalid_arguments", "Connection would create a shader cycle"
            )
        if node.name not in visited:
            visited.add(node.name)
            pending.extend(
                link.to_node
                for link in tree.links
                if link.from_node == node and link.to_socket != target
            )
    try:
        for link in list(target.links):
            tree.links.remove(link)
        link = tree.links.new(source, target, verify_limits=False)
        update()
        if not link.is_valid or link.is_muted or len(target.links) != 1:
            raise OperationError(
                "operation_failed", "Blender did not retain a valid shader link"
            )
        return link_summary(link)
    except Exception:
        for link in list(target.links):
            tree.links.remove(link)
        for old_source, old_target, muted in previous:
            restored = tree.links.new(old_source, old_target, verify_limits=False)
            restored.is_muted = muted
        update()
        raise


def disconnect(tree: Any, arguments: DisconnectArguments) -> DisconnectResult:
    target = endpoint(tree, arguments.to_node, arguments.to_socket, output=False)
    links = list(target.links)
    for link in links:
        tree.links.remove(link)
    return DisconnectResult(
        material_name=arguments.material_name,
        to_node=arguments.to_node,
        to_socket=arguments.to_socket,
        removed=len(links),
    )
