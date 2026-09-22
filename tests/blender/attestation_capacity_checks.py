"""Material mutation sensitivity and production-sized canonical attestation."""

import importlib
import json
import os
import resource
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
from bpy_extras import anim_utils  # type: ignore[import-not-found]

A = importlib.import_module("bl_ext.user_default.tyvrana_blender.attestation")
ROOT = Path(os.environ["TYVRANA_TEST_CONTROL"])
PHASE = os.environ.get("TYVRANA_ATTEST_PHASE", "create")
REPORT: dict[str, Any] = {}


def observe(label: str) -> dict[str, Any]:
    bpy.context.view_layer.update()
    value = dict(A.inspect().root)
    REPORT[label] = value
    (ROOT / f"capacity-{PHASE}.json").write_text(json.dumps(REPORT, indent=2))
    assert value["status"] == "complete", (label, value)
    return value


def change(label: str, owner: Any, key: str, value: Any) -> None:
    before = observe(label + "_before")["digest"]
    current = owner()
    old = getattr(current, key)
    if hasattr(old, "copy"):
        old = old.copy()
    setattr(current, key, value)
    bpy.data.objects["Cube"].data.update()
    assert observe(label + "_changed")["digest"] != before, label
    # Resolve fresh native elements after dependency/storage updates.
    setattr(owner(), key, old)
    bpy.data.objects["Cube"].data.update()
    assert observe(label + "_restored")["digest"] == before, label
    REPORT[label] = "PASS"


def create() -> None:
    cube = bpy.data.objects["Cube"]
    material = bpy.data.materials.new("AttestedSurface")
    cube.data.materials.append(material)
    tree = material.node_tree
    principled = tree.nodes.get("Principled BSDF")
    source = tree.nodes.new("ShaderNodeValue")
    source.outputs[0].default_value = 0.25
    link = tree.links.new(source.outputs[0], principled.inputs["Metallic"])
    cube.shape_key_add(name="Basis")
    key = cube.shape_key_add(name="Expression")
    key.data[0].co.z += 0.25
    key.value = 0.2
    attribute = cube.data.attributes.new("Measurement", "FLOAT", "POINT")
    attribute.data[0].value = 0.75
    modifier = cube.modifiers.new("Subdivision", "SUBSURF")
    constraint = cube.constraints.new("LIMIT_LOCATION")
    constraint.use_min_x = True
    cube.location.x = 0
    cube.keyframe_insert(data_path="location", frame=1)
    cube.location.x = 1
    cube.keyframe_insert(data_path="location", frame=20)
    bpy.context.scene.frame_set(1)
    channelbag = anim_utils.action_get_channelbag_for_slot(
        cube.animation_data.action, cube.animation_data.action_slot
    )
    curve = channelbag.fcurves.find("location", index=0)
    # Allocate optional face layers before taking the restoration baseline.
    cube.data.polygons[0].material_index = 1
    cube.data.polygons[0].material_index = 0
    cube.data.polygons[0].use_smooth = True
    cube.data.polygons[0].use_smooth = False
    cube.data.update()
    before = observe("unchanged")
    assert observe("unchanged_repeat")["digest"] == before["digest"]
    collection = cube.users_collection[0]
    collection.objects.unlink(cube)
    collection.objects.link(cube)
    assert observe("enumeration_reordered")["digest"] == before["digest"]
    REPORT["enumeration"] = "PASS"
    change("vertex", lambda: cube.data.vertices[0], "co", (-0.75, -1, -1))
    change(
        "topology",
        lambda: cube.data.loops[0],
        "vertex_index",
        (cube.data.loops[0].vertex_index + 1) % 8,
    )
    change("material", lambda: principled.inputs["Roughness"], "default_value", 0.125)
    change("animation", lambda: curve.keyframe_points[1], "co", (20, 1.5))
    change("modifier", lambda: modifier, "levels", 2)
    change("constraint", lambda: constraint, "min_x", 0.125)
    change("shape_key", lambda: key, "value", 0.4)
    change("shape_key_coordinate", lambda: key.data[0], "co", (-1.0, -1.0, 0.5))
    change("face_material", lambda: cube.data.polygons[0], "material_index", 1)
    change("smoothing", lambda: cube.data.polygons[0], "use_smooth", True)
    change(
        "attribute", lambda: cube.data.attributes["Measurement"].data[0], "value", 0.5
    )
    before_links = observe("links_before")["digest"]
    tree.links.remove(link)
    alternate = tree.links.new(source.outputs[0], principled.inputs["Roughness"])
    assert observe("links_changed")["digest"] != before_links
    tree.links.remove(alternate)
    tree.links.new(source.outputs[0], principled.inputs["Metallic"])
    assert observe("links_restored")["digest"] == before_links
    REPORT["shader_link"] = "PASS"

    # Four dense grids and more than 150 meshes / 300 objects.
    side = 180
    coordinates = [(x / side, y / side, 0) for y in range(side) for x in range(side)]
    faces = [
        (y * side + x, y * side + x + 1, (y + 1) * side + x + 1, (y + 1) * side + x)
        for y in range(side - 1)
        for x in range(side - 1)
    ]
    for index in range(160):
        mesh = bpy.data.meshes.new(f"ProductionMesh{index:03d}")
        if index < 4:
            mesh.from_pydata(coordinates, [], faces)
            mesh.attributes.new("Density", "FLOAT", "POINT")
        else:
            mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.update()
        mesh.materials.append(
            material if index % 2 else bpy.data.materials.get("Material")
        )
        for instance in range(2):
            obj = bpy.data.objects.new(f"ProductionObject{index:03d}_{instance}", mesh)
            bpy.context.scene.collection.objects.link(obj)
    # Two 4K buffers alone exceed the old aggregate 512 MiB decoded-byte cap.
    for index in range(2):
        image = bpy.data.images.new(f"ProductionImage{index}", width=4096, height=4096)
        image.use_fake_user = True
    scale = observe("scale")
    REPORT["scale_geometry"] = dict(
        objects=len(bpy.data.objects),
        meshes=len(bpy.data.meshes),
        vertices=sum(len(m.vertices) for m in bpy.data.meshes),
        polygons=sum(len(m.polygons) for m in bpy.data.meshes),
        max_rss_KiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "production.blend"))
    saved = observe("saved")
    assert saved["digest"] == scale["digest"]
    REPORT["response_bytes"] = len(json.dumps(saved, separators=(",", ":")).encode())
    (ROOT / "capacity-create.json").write_text(json.dumps(REPORT, indent=2))


def reopen() -> None:
    bpy.ops.wm.open_mainfile(
        filepath=str(ROOT / "production.blend"), load_ui=False, use_scripts=False
    )
    before = json.loads((ROOT / "capacity-create.json").read_text())["saved"]
    current = observe("independent")
    assert current["digest"] == before["digest"], (before, current)
    assert current["host_session_id"] != before["host_session_id"]
    assert current["document_session_id"] != before["document_session_id"]
    material = bpy.data.materials["AttestedSurface"]
    material.node_tree.nodes.get("Principled BSDF").inputs[
        "Roughness"
    ].default_value = 0.375
    changed = observe("independent_changed")
    assert changed["digest"] != before["digest"]
    REPORT["equal"] = True
    REPORT["changed_mismatch"] = True
    (ROOT / "capacity-reopen.json").write_text(json.dumps(REPORT, indent=2))


if PHASE == "create":
    create()
else:
    reopen()
print("ATTESTATION_CAPACITY_PASSED", PHASE, flush=True)
