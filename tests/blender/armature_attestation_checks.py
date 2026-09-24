"""Flat armature graph, authored sensitivity and native host determinism."""

import importlib
import json
import os
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

a = importlib.import_module("bl_ext.user_default.tyvrana_blender.attestation")
root = Path(os.environ["TYVRANA_TEST_CONTROL"])
phase = os.environ["TYVRANA_ATTEST_PHASE"]
original_value, original_rna = a.Hasher.value, a.Hasher.rna
stats: dict[str, Any] = {}


def value(self: Any, item: Any, depth: int = 0) -> None:
    stats["max_depth"] = max(stats.get("max_depth", 0), depth)
    original_value(self, item, depth)


def rna(self: Any, item: Any, depth: int = 0) -> None:
    stats["max_depth"] = max(stats.get("max_depth", 0), depth)
    kind = item.bl_rna.identifier
    if kind in a.ARMATURE_REFERENCES:
        rows = stats.setdefault("rows", {})
        key = kind + ":" + item.name
        rows[key] = rows.get(key, 0) + 1
    original_rna(self, item, depth)


a.Hasher.value, a.Hasher.rna = value, rna


def attest() -> dict[str, Any]:
    stats.clear()
    bpy.context.view_layer.update()
    result = a.inspect().root
    assert result["status"] == "complete", result
    assert max(stats.get("rows", {"none": 0}).values()) <= 1, stats
    return {
        "digest": result["digest"],
        "format": result["format"],
        "elapsed_ms": result["elapsed_ms"],
        "work": result["work"],
        "max_depth": stats["max_depth"],
        "row_count": len(stats.get("rows", {})),
        "response_bytes": len(json.dumps(result, separators=(",", ":")).encode()),
    }


def fixture(count: int, *, branched: bool = False, reverse: bool = False) -> Any:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    armature = bpy.data.armatures.new("Structure")
    obj = bpy.data.objects.new("Structure", armature)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    indices = list(range(count))
    for i in reversed(indices) if reverse else indices:
        bone = armature.edit_bones.new(f"Joint{i:03}")
        bone.head = (i % 4 if branched else 0, i, 0)
        bone.tail = (i % 4 if branched else 0, i + 1, 0)
    for i in indices:
        parent = (i - 4) // 2 if branched else i - 1
        if parent >= 0:
            armature.edit_bones[f"Joint{i:03}"].parent = armature.edit_bones[
                f"Joint{parent:03}"
            ]
    bpy.ops.object.mode_set(mode="OBJECT")
    # Adjacent hierarchy uses the same flat-reference primitive.
    previous = None
    for i in range(64):
        collection = armature.collections.new(f"Group{i:03}", parent=previous)
        collection.assign(armature.bones[f"Joint{i:03}"])
        previous = collection
    armature.bones["Joint000"]["purpose"] = "root"
    target = bpy.data.objects.new("Target", None)
    bpy.context.scene.collection.objects.link(target)
    other = bpy.data.objects.new("Other datum", None)
    bpy.context.scene.collection.objects.link(other)
    constraint = obj.pose.bones["Joint001"].constraints.new("COPY_LOCATION")
    constraint.name = "Datum"
    constraint.target = target
    curve = obj.pose.bones["Joint002"].driver_add("scale", 0)
    driver = curve.driver
    driver.type = "SUM"
    variable = driver.variables.new()
    variable.name = "position"
    variable.type = "TRANSFORMS"
    variable.targets[0].id = target
    variable.targets[0].transform_type = "LOC_X"
    return obj


def check() -> None:
    if phase != "create":
        bpy.ops.wm.open_mainfile(filepath=str(root / "branched.blend"))
        first, second = attest(), attest()
        assert first["digest"] == second["digest"]
        expected = json.loads((root / "armature-create.json").read_text())
        assert first["digest"] == expected["branched"]["digest"], (first, expected)
        (root / f"armature-{phase}.json").write_text(json.dumps(first, indent=2))
        return
    fixture(128)
    chain = attest()
    assert attest()["digest"] == chain["digest"]
    assert chain["row_count"] == 128 * 2 + 64, chain
    assert chain["max_depth"] < 30, chain
    fixture(256, branched=True)
    branched = attest()
    assert branched["row_count"] == 256 * 2 + 64, branched
    fixture(256, branched=True, reverse=True)
    assert attest()["digest"] == branched["digest"], "bone enumeration affected digest"
    # Collection sibling order and membership enumeration are also nonsemantic.
    bpy.ops.wm.save_as_mainfile(filepath=str(root / "branched.blend"))
    assert attest()["digest"] == branched["digest"]
    sensitivity = {}
    for change in (
        "transform",
        "parent",
        "roll",
        "flags",
        "constraint",
        "driver",
        "pose",
        "animation",
    ):
        bpy.ops.wm.open_mainfile(filepath=str(root / "branched.blend"))
        obj = bpy.data.objects["Structure"]
        if change in {"transform", "parent", "roll"}:
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
            bone = obj.data.edit_bones["Joint127"]
            if change == "transform":
                bone.head.x += 0.25
            elif change == "parent":
                bone.parent = obj.data.edit_bones["Joint010"]
            else:
                bone.roll += 0.25
            bpy.ops.object.mode_set(mode="OBJECT")
        elif change == "flags":
            obj.data.bones["Joint127"].use_deform = False
        elif change == "constraint":
            obj.pose.bones["Joint001"].constraints["Datum"].target = bpy.data.objects[
                "Other datum"
            ]
        elif change == "driver":
            obj.animation_data.drivers[0].driver.variables[0].targets[
                0
            ].transform_type = "LOC_Y"
        elif change == "pose":
            obj.pose.bones["Joint127"].location.x = 0.25
        else:
            obj.pose.bones["Joint127"].keyframe_insert(data_path="location", frame=4)
        sensitivity[change] = attest()["digest"] != branched["digest"]
        assert sensitivity[change], change
    bpy.ops.wm.open_mainfile(filepath=str(root / "branched.blend"))
    obj = bpy.data.objects["Structure"]
    obj.data.bones.active = obj.data.bones["Joint127"]
    obj.pose.bones["Joint127"].select = not obj.pose.bones["Joint127"].select
    obj.data.collections_all["Group063"].is_expanded = not obj.data.collections_all[
        "Group063"
    ].is_expanded
    assert attest()["digest"] == branched["digest"], "runtime selection"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="POSE")
    assert attest()["digest"] == branched["digest"], "pose editor context"
    bpy.ops.object.mode_set(mode="EDIT")
    incomplete = a.inspect().root
    assert incomplete["status"] != "complete" and incomplete["digest"] is None
    bpy.ops.object.mode_set(mode="OBJECT")
    assert attest()["digest"] == branched["digest"], "edit context roundtrip"
    # A general recursive value still stops at the existing bounded guard.
    nested: dict[str, Any] = {}
    nested["next"] = nested
    try:
        a.Hasher().value(nested)
    except a.Unqualified as exc:
        assert "nesting" in str(exc)
    else:
        raise AssertionError("recursive structure escaped guard")
    material = bpy.data.materials.new("Surface")
    for i in range(156):
        mesh = bpy.data.meshes.new(f"Mesh{i:03}")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.materials.append(material)
        part = bpy.data.objects.new(mesh.name, mesh)
        bpy.context.scene.collection.objects.link(part)
        part.modifiers.new("Thickness", "SOLIDIFY").thickness = 0.05
        empty = bpy.data.objects.new(f"Datum{i:03}", None)
        bpy.context.scene.collection.objects.link(empty)
    production = attest()
    production.update(
        objects=len(bpy.data.objects),
        meshes=len(bpy.data.meshes),
        bones=len(obj.data.bones),
    )
    assert production["response_bytes"] < 12000, production
    bpy.ops.wm.save_as_mainfile(filepath=str(root / "production.blend"))
    result = dict(
        chain=chain,
        branched=branched,
        sensitivity=sensitivity,
        runtime="PASS (edit mode rejects pending rest edits)",
        production=production,
        recursive_guard="PASS",
    )
    (root / "armature-create.json").write_text(json.dumps(result, indent=2))
    print("ARMATURE_ATTESTATION_PASSED", json.dumps(result), flush=True)


check()
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
