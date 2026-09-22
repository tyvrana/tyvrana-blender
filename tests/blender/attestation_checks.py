"""Native material-content and runtime-session qualification."""

import importlib
import json
import os
from pathlib import Path

import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender"
a = importlib.import_module(PACKAGE + ".attestation")


def check() -> None:
    first = a.inspect().root
    print("ATTESTATION_PROBE", json.dumps(first), flush=True)
    assert first["status"] == "complete", first
    assert a.inspect().root["digest"] == first["digest"]
    bpy.data.objects["Cube"].data.vertices[0].co.x += 0.123
    second = a.inspect().root
    assert second["digest"] != first["digest"] and second["status"] == "complete", (
        second
    )
    material = bpy.data.materials.new("Fixture material")
    bpy.data.objects["Cube"].data.materials.append(material)
    before = a.inspect().root
    material.node_tree.nodes.get("Principled BSDF").inputs[
        "Roughness"
    ].default_value = 0.123
    after = a.inspect().root
    assert after["digest"] != before["digest"] and after["status"] == "complete", after
    session = a.identity()
    path = str(Path(os.environ["TYVRANA_TEST_CONTROL"]) / "fixture.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    saved = a.inspect().root
    bpy.ops.wm.open_mainfile(filepath=path)
    reopened = a.inspect().root
    print(
        "ATTESTATION_REOPEN",
        json.dumps({"saved": saved, "reopened": reopened}),
        flush=True,
    )
    assert reopened["digest"] == saved["digest"]
    assert a.identity()["host"] == session["host"]
    assert a.identity()["document"] != session["document"]
    print("ATTESTATION_NATIVE_PASSED", flush=True)


check()
