"""Exercise bounded inspection on isolated large native data fixtures."""

import importlib

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

adapter = importlib.import_module("bl_ext.user_default.tyvrana_blender.blender")
operations = importlib.import_module("bl_ext.user_default.tyvrana_blender.operations")
backend = adapter.BlenderBackend(adapter._runtime.worker.spool)


def call(operation: str, **arguments):  # type: ignore[no-untyped-def]
    result = operations.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="inspection",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(result, OperationSuccess), result
    return result.result


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
material = bpy.data.materials.new("Shared")
mesh = bpy.data.meshes.new("Fixture")
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
mesh.materials.append(material)
for index in range(70):
    obj = bpy.data.objects.new(f"Part {index:03}", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
scene = call("scene.inspect")
assert scene["object_count"] == scene["page"]["matched_count"] == 70
assert len(scene["objects"]) == len(scene["selected_objects"]) == 32
assert scene["selected_object_count"] == 70 and scene["selected_objects_truncated"]
names = [o["name"] for o in scene["objects"]]
for offset in (32, 64):
    names.extend(o["name"] for o in call("scene.inspect", offset=offset)["objects"])
assert names == [f"Part {index:03}" for index in range(70)]
assert call("scene.inspect", names=["Part 042"])["objects"][0]["name"] == "Part 042"
assert call("scene.inspect", types=["LIGHT"])["page"]["matched_count"] == 0
shared = call("material.inspect", names=["Shared"])["materials"][0]
assert shared["assignment_count"] == 70 and shared["assignments_truncated"]
assert len(shared["assignments"]) == 32
call("camera.create", name="Active", make_active=True)
call("camera.create", name="Other")
cameras = call("camera.inspect", names=["Other"])
assert cameras["active_camera"] == "Active" and len(cameras["cameras"]) == 1
call("material.create_principled", name="Graph")
tree = bpy.data.materials["Graph"].node_tree
for index in range(70):
    tree.nodes.new("ShaderNodeMapping").name = f"Mapping {index:03}"
graph = call("shader.inspect", material_name="Graph")
assert graph["node_page"]["matched_count"] == 72 and len(graph["nodes"]) == 32
assert all(not n["sockets_included"] and not n["inputs"] for n in graph["nodes"])
focused = call(
    "shader.inspect", material_name="Graph", names=["Mapping 042"], include_sockets=True
)
assert len(focused["nodes"]) == 1 and focused["nodes"][0]["inputs"]
detailed = call("shader.inspect", material_name="Graph", include_sockets=True)
assert len(detailed["nodes"]) == 8 and detailed["node_page"]["next_offset"] == 8
# Unknown/custom nodes must remain inspectable, including large interfaces.
group = bpy.data.node_groups.new("Large interface", "ShaderNodeTree")
for index in range(80):
    group.interface.new_socket(
        name=f"Input {index}", in_out="INPUT", socket_type="NodeSocketFloat"
    )
node = tree.nodes.new("ShaderNodeGroup")
node.name, node.node_tree = "Custom", group
custom = call(
    "shader.inspect", material_name="Graph", names=["Custom"], include_sockets=True
)["nodes"][0]
assert (
    custom["input_count"] == 80
    and len(custom["inputs"]) == 64
    and custom["sockets_truncated"]
)
call(
    "armature.create",
    name="Rig",
    bones=[
        {
            "name": "Bone",
            "head": [0.0, 0.0, 0.0],
            "tail": [0.0, 1.0, 0.0],
            "head_radius": 2.0,
            "tail_radius": 2.0,
        }
    ],
)
for index in range(17):
    bpy.data.objects[f"Part {index:03}"].data = mesh.copy()
    call(
        "armature.bind",
        object_name=f"Part {index:03}",
        armature_object="Rig",
        weights={"method": "envelopes", "bones": ["Bone"]},
    )
rig = call("armature.inspect", object_name="Rig")
assert (
    rig["binding_count"] == 17
    and rig["bindings_truncated"]
    and len(rig["bindings"]) == 16
)
assert len(rig["bones"]) == 1
adapter.unregister()
print("BOUNDED_INSPECTION_NATIVE_PASSED")
