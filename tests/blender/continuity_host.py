"""Disposable host with explicit out-of-band edits for freshness negative tests."""

import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

PACKAGE = "bl_ext.user_default.tyvrana_blender"
control = Path(os.environ["TYVRANA_TEST_CONTROL"])
lifecycle = importlib.import_module(PACKAGE + ".lifecycle")
preferences = bpy.context.preferences.addons[PACKAGE].preferences
preferences.port = int(os.environ["TYVRANA_TEST_PORT"])
lifecycle._backend.restart()
deadline = time.monotonic() + 600
# Fault injection applies only after the named typed fixture mutation succeeds.
attestation = importlib.import_module(PACKAGE + ".attestation")
original_rna = attestation.Hasher.rna


def qualify_rna(self: Any, owner: Any, depth: int = 0) -> None:
    if owner.bl_rna.identifier == "Object" and owner.name == "Unattestable":
        raise attestation.Unqualified("Fixture simulates incomplete authored coverage")
    original_rna(self, owner, depth)


attestation.Hasher.rna = qualify_rna
original_vertex = None
original_roughness = None
try:
    while time.monotonic() < deadline:
        backend = lifecycle._backend
        backend.pump()
        if bpy.app.timers.is_registered(lifecycle._poll):
            if lifecycle._poll() is None:
                bpy.app.timers.unregister(lifecycle._poll)
        command = control / "command.json"
        if command.exists():
            payload = json.loads(command.read_text())
            action = payload["action"]
            command.unlink()
            if action == "typed_delta":
                operations = importlib.import_module(PACKAGE + ".operations")
                protocol = importlib.import_module("tyvrana_protocol")
                for index, (operation, arguments) in enumerate(payload["steps"]):
                    response = operations.execute(
                        backend.BlenderBackend(),
                        protocol.OperationRequest(
                            type="operation.request",
                            request_id=f"legacy-{index}",
                            operation=operation,
                            arguments=arguments,
                        ),
                    )
                    assert response.type == "operation.success", response
            elif action == "reconnect":
                backend.restart()
            elif action in {"geometry_changed", "restore_geometry"}:
                mesh = bpy.data.objects["Fixture"].data
                if action == "geometry_changed":
                    original_vertex = float(mesh.vertices[0].co.x)
                    mesh.vertices[0].co.x = original_vertex + 0.125
                else:
                    mesh.vertices[0].co.x = original_vertex
                mesh.update()
                bpy.context.view_layer.update()
            elif action in {"shader_changed", "restore_shader"}:
                socket = (
                    bpy.data.materials["Surface"]
                    .node_tree.nodes.get("Principled BSDF")
                    .inputs["Roughness"]
                )
                if action == "shader_changed":
                    original_roughness = float(socket.default_value)
                    socket.default_value = 0.125
                else:
                    socket.default_value = original_roughness
                bpy.context.view_layer.update()
            elif action == "remove_unattestable":
                bpy.data.objects.remove(
                    bpy.data.objects["Unattestable"], do_unlink=True
                )
            elif action == "different_document":
                bpy.ops.wm.read_homefile(
                    use_empty=True,
                    use_factory_startup=True,
                    use_splash=False,
                    load_ui=False,
                )
            elif action == "open_saved":
                bpy.ops.wm.open_mainfile(
                    filepath=str(control.parent / "accepted.blend")
                )
            else:
                raise AssertionError(action)
            (control / "ack.json").write_text(json.dumps({"action": action}))
        if (control / "stop").exists():
            break
        time.sleep(0.02)
    else:
        raise AssertionError("Fixture host deadline exceeded")
finally:
    lifecycle.disable()
