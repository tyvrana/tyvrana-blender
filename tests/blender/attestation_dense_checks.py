"""Dense non-biological mesh performance and incremental attestation behavior."""

import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np  # type: ignore[import-not-found]

A = importlib.import_module("bl_ext.user_default.tyvrana_blender.attestation")
J = importlib.import_module("bl_ext.user_default.tyvrana_blender.attestation_jobs")
ROOT = Path(os.environ["TYVRANA_TEST_CONTROL"])


def create() -> Any:
    side = 900
    mesh = bpy.data.meshes.new("DenseGrid")
    vertices = side * side
    faces = (side - 1) ** 2
    mesh.vertices.add(vertices)
    mesh.loops.add(faces * 4)
    mesh.polygons.add(faces)
    y, x = np.mgrid[:side, :side]
    points = np.stack((x / side, y / side, np.zeros_like(x)), axis=-1)
    mesh.vertices.foreach_set("co", points.astype(np.float32).ravel())
    y, x = np.mgrid[: side - 1, : side - 1]
    lower = y * side + x
    corners = np.stack((lower, lower + 1, lower + side + 1, lower + side), axis=-1)
    mesh.loops.foreach_set("vertex_index", corners.astype(np.int32).ravel())
    mesh.polygons.foreach_set("loop_start", np.arange(faces, dtype=np.int32) * 4)
    mesh.polygons.foreach_set("loop_total", np.full(faces, 4, dtype=np.int32))
    for name in ("SurfaceA", "SurfaceB"):
        mesh.materials.append(bpy.data.materials.new(name))
    mesh.polygons.foreach_set("material_index", np.arange(faces, dtype=np.int32) % 2)
    mesh.polygons.foreach_set("use_smooth", np.ones(faces, dtype=np.bool_))
    mesh.attributes.new("Density", "FLOAT", "POINT")
    mesh.attributes.new("Tint", "FLOAT_COLOR", "POINT")
    mesh.uv_layers.new(name="UVMap")
    mesh.update()
    obj = bpy.data.objects.new("DenseObject", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Expression")
    key.data[0].co.z = 0.125
    for i in range(2):
        image = bpy.data.images.new(f"DenseImage{i}", width=4096, height=4096)
        image.use_fake_user = True
    bpy.context.view_layer.update()
    return mesh


def mesh_cost(module: Any, mesh: Any) -> dict[str, Any]:
    h = module.Hasher()
    started = time.perf_counter()
    h.rna(mesh)
    return dict(
        elapsed_seconds=time.perf_counter() - started,
        items=h.items,
        bulk_elements=h.bulk_elements,
        bytes=h.bytes,
        peak_buffer=h.peak_buffer,
        digest=h.digest.hexdigest(),
    )


def qualify(mesh: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "geometry": {
            key: len(getattr(mesh, key))
            for key in ("vertices", "edges", "loops", "polygons", "attributes")
        }
    }
    report["mesh"] = mesh_cost(A, mesh)
    started = time.perf_counter()
    initial = J.start().root
    report["initial_seconds"] = time.perf_counter() - started
    assert initial["state"] == "queued" and report["initial_seconds"] < 1
    identifier = initial["job_id"]
    snapshots = []
    while J.busy():
        J.tick()
        current = J.status(identifier)
        snapshots.append(current.model_dump(mode="json", exclude={"result"}))
    result = J.status(identifier).model_dump(mode="json")
    report["job"] = result
    report["snapshots"] = snapshots
    assert result["state"] == "completed", result
    assert result["result"]["status"] == "complete", result
    assert len(snapshots) > 2
    assert len({s["progress"]["stream_bytes"] for s in snapshots if s["progress"]}) > 1
    report["response_bytes"] = len(json.dumps(result, separators=(",", ":")).encode())
    # Cooperative cancellation removes guards and leaves authored content untouched.
    next_job = J.start().root
    J.tick()
    cancelled = J.cancel(next_job["job_id"])
    assert cancelled.state == "cancelled" and not J.busy()
    assert cancelled.result is None
    # External edits between resources must invalidate the observation.
    changed_job = J.start().root
    J.tick()
    obj = bpy.data.objects["DenseObject"]
    obj.location.x = 0.25
    bpy.context.view_layer.update()
    J.tick()
    failed = J.status(changed_job["job_id"])
    assert failed.state == "failed" and failed.error.code == "application_changed", (
        failed
    )
    obj.location.x = 0
    bpy.context.view_layer.update()
    report["cancellation"] = "PASS"
    report["external_change"] = "PASS"
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "dense.blend"))
    (ROOT / "dense.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    qualify(create())
    print("ATTESTATION_DENSE_PASSED", flush=True)
