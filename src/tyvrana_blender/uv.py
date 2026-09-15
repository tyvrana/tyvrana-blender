"""Main-thread whole-mesh UV edits using Blender's production operators."""

from array import array
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

from .operations import OperationError
from .uv_models import (
    UVCreateArguments,
    UVInspectResult,
    UVSetActiveArguments,
    UVUnwrapArguments,
    map_summary,
)


def mesh_object(name: str) -> Any:
    matches = [obj for obj in bpy.data.objects if obj.name == name]
    if not matches:
        raise OperationError("object_not_found", "Object does not exist")
    if len(matches) != 1:
        raise OperationError(
            "invalid_arguments", "Object name is ambiguous across libraries"
        )
    obj = matches[0]
    if obj.type != "MESH":
        raise OperationError("object_not_mesh", "UV operations require a mesh object")
    return obj


def mesh_users(mesh: Any) -> int:
    return sum(obj.type == "MESH" and obj.data == mesh for obj in bpy.data.objects)


def inspect(obj: Any) -> UVInspectResult:
    mesh = obj.data
    layers = mesh.uv_layers
    bm = bmesh.from_edit_mesh(mesh) if obj.mode == "EDIT" else None
    summaries = []
    for layer in sorted(layers, key=lambda layer: layer.name):
        if bm is None:
            coordinates = (
                (float(point.vector[0]), float(point.vector[1])) for point in layer.uv
            )
            pins = sum(bool(pin.value) for pin in layer.pin)
        else:
            uv_layer = bm.loops.layers.uv[layer.name]
            coordinates = (
                (float(loop[uv_layer].uv[0]), float(loop[uv_layer].uv[1]))
                for face in bm.faces
                for loop in face.loops
            )
            pins = sum(
                bool(loop[uv_layer].pin_uv) for face in bm.faces for loop in face.loops
            )
        summaries.append(
            map_summary(
                layer.name, layer.active, layer.active_render, coordinates, pins
            )
        )
    return UVInspectResult(
        object_name=obj.name,
        active_map=layers.active.name if layers.active is not None else None,
        active_render_map=next(
            (layer.name for layer in layers if layer.active_render), None
        ),
        mesh_users=mesh_users(mesh),
        maps=summaries,
    )


class _Selection:
    def __init__(self, bm: Any) -> None:
        self.elements = []
        self.history = []
        for kind in ("verts", "edges", "faces"):
            collection = getattr(bm, kind)
            collection.ensure_lookup_table()
            collection.index_update()
            self.elements.append(
                (kind, [(item.select, item.hide, item.tag) for item in collection])
            )
        for index, item in enumerate(bm.select_history):
            kind = (
                "verts"
                if isinstance(item, bmesh.types.BMVert)
                else "edges"
                if isinstance(item, bmesh.types.BMEdge)
                else "faces"
            )
            self.history.append((index, kind, item.index))
        self.face_active = bm.faces.active.index if bm.faces.active else None
        self.uv_faces = [face.uv_select for face in bm.faces]
        self.uv_loops = [
            (loop.uv_select_vert, loop.uv_select_edge)
            for face in bm.faces
            for loop in face.loops
        ]
        self.uv_valid = bm.uv_select_sync_valid

    def restore(self, bm: Any) -> None:
        # Hiding an element can deselect linked elements. Restore all visibility
        # first, then exact selection flags, including visible shared vertices.
        for kind, values in self.elements:
            collection = getattr(bm, kind)
            collection.ensure_lookup_table()
            for item, (_, hidden, tag) in zip(collection, values, strict=True):
                item.hide = hidden
                item.tag = tag
        # Face/edge selection setters propagate down to vertices.
        for kind, values in reversed(self.elements):
            for item, (selected, _, _) in zip(getattr(bm, kind), values, strict=True):
                item.select = selected
        for face, selected in zip(bm.faces, self.uv_faces, strict=True):
            face.uv_select = selected
        for loop, (vertex, edge) in zip(
            (loop for face in bm.faces for loop in face.loops),
            self.uv_loops,
            strict=True,
        ):
            loop.uv_select_vert = vertex
            loop.uv_select_edge = edge
        bm.uv_select_sync_valid = self.uv_valid
        bm.select_history.clear()
        for _, kind, index in self.history:
            bm.select_history.add(getattr(bm, kind)[index])
        bm.faces.active = (
            bm.faces[self.face_active] if self.face_active is not None else None
        )


class _UVState:
    def __init__(self, mesh: Any) -> None:
        self.layers: dict[str, array[float]] = {}
        for layer in mesh.uv_layers:
            values = array("f", [0.0]) * (len(layer.uv) * 2)
            layer.uv.foreach_get("vector", values)
            self.layers[layer.name] = values
        self.active = mesh.uv_layers.active.name if mesh.uv_layers.active else None
        self.render = next(
            (layer.name for layer in mesh.uv_layers if layer.active_render), None
        )

    def restore(self, mesh: Any) -> None:
        for layer in list(mesh.uv_layers):
            if layer.name not in self.layers:
                mesh.uv_layers.remove(layer)
        for name, values in self.layers.items():
            mesh.uv_layers[name].uv.foreach_set("vector", values)
        if self.active is not None:
            mesh.uv_layers.active = mesh.uv_layers[self.active]
        if self.render is not None:
            mesh.uv_layers[self.render].active_render = True
        mesh.update()


@contextmanager
def mutation(obj: Any) -> Iterator[Any]:
    context = bpy.context
    if (
        context.mode not in {"OBJECT", "EDIT_MESH"}
        or bpy.app.is_job_running("RENDER")
        or obj.name not in context.view_layer.objects
        or not obj.is_editable
        or not obj.data.is_editable
        or obj.library is not None
        or obj.data.library is not None
        or obj.override_library is not None
        or obj.data.override_library is not None
    ):
        raise OperationError(
            "invalid_context", "UV editing requires a visible editable local mesh"
        )
    editing = context.mode == "EDIT_MESH"
    if editing and (
        context.view_layer.objects.active != obj or len(context.objects_in_mode) != 1
    ):
        raise OperationError(
            "invalid_context",
            "Finish other objects' Edit Mode before editing this mesh",
        )
    active = context.view_layer.objects.active
    selected = [(item, item.select_get()) for item in context.view_layer.objects]
    hidden = obj.hide_get()
    hide_select = obj.hide_select
    prior_selection = _Selection(bmesh.from_edit_mesh(obj.data)) if editing else None
    if editing:
        bpy.ops.object.mode_set(mode="OBJECT")
    original = obj.data
    state = _UVState(original)
    copied = None
    try:
        obj.hide_set(False)
        obj.hide_select = False
        if not obj.visible_get():
            obj.hide_set(hidden)
            obj.hide_select = hide_select
            raise OperationError(
                "invalid_context",
                "UV editing cannot expose excluded collections or disabled objects",
            )
        for item, _ in selected:
            item.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if mesh_users(original) > 1:
            copied = original.copy()
            obj.data = copied
        yield obj.data
        obj.data.update()
    except Exception:
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if copied is not None:
            obj.data = original
            bpy.data.meshes.remove(copied)
        else:
            state.restore(original)
        raise
    finally:
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for item, was_selected in selected:
            item.select_set(was_selected)
        context.view_layer.objects.active = active
        if editing:
            bpy.ops.object.mode_set(mode="EDIT")
            assert prior_selection is not None
            prior_selection.restore(bmesh.from_edit_mesh(obj.data))
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        obj.hide_set(hidden)
        obj.hide_select = hide_select


def layer_for(mesh: Any, name: str | None) -> Any:
    layer = mesh.uv_layers.get(name) if name is not None else mesh.uv_layers.active
    if layer is None:
        raise OperationError(
            "uv_map_not_found", "UV map does not exist; create a map first"
        )
    return layer


def create(obj: Any, arguments: UVCreateArguments) -> UVInspectResult:
    if arguments.name is not None and arguments.name in obj.data.uv_layers:
        raise OperationError("invalid_arguments", "UV map name already exists")
    with mutation(obj) as mesh:
        prior = mesh.uv_layers.active
        prior_name = prior.name if prior is not None else None
        had_render = any(layer.active_render for layer in mesh.uv_layers)
        layer = mesh.uv_layers.new(name=arguments.name or "UVMap", do_init=True)
        if layer is None:
            raise OperationError("invalid_context", "Mesh cannot hold another UV map")
        if arguments.name is not None and layer.name != arguments.name:
            raise OperationError(
                "invalid_arguments", "Blender cannot store the UV map name exactly"
            )
        if arguments.set_active or prior_name is None:
            mesh.uv_layers.active = layer
        else:
            mesh.uv_layers.active = mesh.uv_layers[prior_name]
        if arguments.set_render or not had_render:
            layer.active_render = True
    return inspect(obj)


def set_active(obj: Any, arguments: UVSetActiveArguments) -> UVInspectResult:
    layer_for(obj.data, arguments.name)
    with mutation(obj) as mesh:
        layer = layer_for(mesh, arguments.name)
        if arguments.set_active:
            mesh.uv_layers.active = layer
        if arguments.set_render:
            layer.active_render = True
    return inspect(obj)


def _operator(obj: Any, call: Callable[[], set[str]]) -> None:
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    selection = _Selection(bm)
    try:
        for collection in (bm.verts, bm.edges, bm.faces):
            for element in collection:
                element.hide = False
                element.select = True
        bm.select_flush_mode()
        for face in bm.faces:
            face.uv_select = True
            for loop in face.loops:
                loop.uv_select_vert = True
                loop.uv_select_edge = True
        bm.uv_select_sync_valid = True
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        # No UV editor selection, viewport direction, pivot, cursor or UDIM state.
        # Operators run synchronously, never as interactive/modal jobs.
        with bpy.context.temp_override(area=None, region=None):
            if call() != {"FINISHED"}:
                raise OperationError(
                    "uv_unwrap_failed", "Blender UV operator did not finish"
                )
    except RuntimeError as exc:
        raise OperationError(
            "uv_unwrap_failed", "Blender could not complete the UV operator"
        ) from exc
    finally:
        bm = bmesh.from_edit_mesh(obj.data)
        selection.restore(bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        bpy.ops.object.mode_set(mode="OBJECT")


def unwrap(obj: Any, arguments: UVUnwrapArguments) -> UVInspectResult:
    layer_for(obj.data, arguments.uv_map)
    if not len(obj.data.polygons):
        raise OperationError("invalid_context", "UV unwrapping requires mesh faces")
    with mutation(obj) as mesh:
        previous = mesh.uv_layers.active.name
        mesh.uv_layers.active = layer_for(mesh, arguments.uv_map)
        settings: dict[str, Any] = {"correct_aspect": arguments.correct_aspect}
        for key in (
            "margin",
            "fill_holes",
            "angle_limit",
            "island_margin",
            "area_weight",
            "cube_size",
            "scale_to_bounds",
            "iterations",
            "no_flip",
        ):
            value = getattr(arguments, key)
            if value is not None:
                settings[key] = value
        if arguments.method in {"angle_based", "conformal", "minimum_stretch"}:
            settings.update(method=arguments.method.upper(), margin_method="FRACTION")
            settings.setdefault("margin", 0.001)
            settings.setdefault("fill_holes", False)
            if arguments.method == "minimum_stretch":
                settings.setdefault("iterations", 20)
                settings.setdefault("no_flip", True)
            operator = bpy.ops.uv.unwrap
        else:
            operator = getattr(bpy.ops.uv, arguments.method)
            if arguments.method == "smart_project":
                settings.update(
                    margin_method="FRACTION", rotate_method="AXIS_ALIGNED_Y"
                )
            if arguments.method in {"cylinder_project", "sphere_project"}:
                settings.update(
                    direction="ALIGN_TO_OBJECT",
                    align="POLAR_ZX",
                    pole="PINCH",
                    seam=True,
                )
        _operator(obj, lambda: operator("EXEC_DEFAULT", **settings))
        mesh.uv_layers.active = mesh.uv_layers[previous]
    return inspect(obj)
