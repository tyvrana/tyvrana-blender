"""Native orphan-chain persistence and retained-content sensitivity."""

import importlib
import json
import os
from pathlib import Path

import addon_utils  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender"
addon_utils.enable(PACKAGE, default_set=False)
lifecycle = importlib.import_module(PACKAGE + ".lifecycle")
assert lifecycle._backend._runtime is None
lifecycle.disable()
attestation = importlib.import_module(PACKAGE + ".attestation")
root = Path(os.environ["TYVRANA_TEST_CONTROL"])

# An unused mesh still contributes a user to its material until reload removes
# the orphan chain. No purge is allowed to make the observation appear stable.
orphan = bpy.data.meshes.new("UnusedMesh")
ghost = bpy.data.materials.new("UnusedMaterial")
orphan.materials.append(ghost)
assert orphan.users == 0 and ghost.users == 1 and not ghost.use_fake_user

retained = bpy.data.materials.new("RetainedMaterial")
retained.use_fake_user = True
retained.diffuse_color = (0.2, 0.3, 0.4, 1)
cube = bpy.data.objects["Cube"]
cube.rotation_euler.z = 0
cube.keyframe_insert("rotation_euler", frame=1)
cube.rotation_euler.z = 0.55
cube.keyframe_insert("rotation_euler", frame=61)
bpy.context.scene.frame_set(61)


def snapshot() -> dict[str, object]:
    bpy.context.view_layer.update()
    result = dict(attestation.inspect().root)
    assert result["status"] == "complete", result
    return result


before = snapshot()
ghost.diffuse_color = (0.9, 0.1, 0.2, 1)
assert snapshot()["digest"] == before["digest"]
retained.diffuse_color = (0.7, 0.3, 0.4, 1)
changed_retained = snapshot()
assert changed_retained["digest"] != before["digest"]
cube.data.vertices[0].co.x += 0.125
changed_geometry = snapshot()
assert changed_geometry["digest"] != changed_retained["digest"]
path = root / "orphan-chain.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
saved = snapshot()
assert bpy.data.meshes.get("UnusedMesh") is not None
assert bpy.data.materials.get("UnusedMaterial") is not None
bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
reopened = snapshot()
assert bpy.data.meshes.get("UnusedMesh") is None
orphan_after = bpy.data.materials.get("UnusedMaterial")
# Blender can keep the now-zero-user leaf resident for another save cycle.
# Neither form belongs to the retained content, and no purge is performed.
assert orphan_after is None or orphan_after.users == 0
assert bpy.data.materials.get("RetainedMaterial") is not None
assert saved["digest"] == reopened["digest"]
assert saved["format"] == reopened["format"]
assert saved["file_sha256"] == reopened["file_sha256"]
(root / "orphan-attestation.json").write_text(
    json.dumps({"saved": saved, "reopened": reopened}, indent=2)
)
print("ORPHAN_ATTESTATION_PASSED", flush=True)
