"""Native execution phases, cancellation and exact isolated inverse proof."""

import importlib
from typing import Any
from unittest.mock import patch

import bpy  # type: ignore[import-not-found]
from tyvrana_protocol import OperationRequest, OperationSuccess

PACKAGE = "bl_ext.user_default.tyvrana_blender."
operations = importlib.import_module(PACKAGE + "operations")
backend = importlib.import_module(PACKAGE + "blender").BlenderBackend()
mutations = importlib.import_module(PACKAGE + "mutation_jobs")
attestations = importlib.import_module(PACKAGE + "attestation_jobs")
errors = importlib.import_module(PACKAGE + "errors")


def call(operation: str, /, **arguments: Any) -> Any:
    result = operations.execute(
        backend,
        OperationRequest(
            type="operation.request",
            request_id="fixture",
            operation="blender." + operation,
            arguments=arguments,
        ),
    )
    assert isinstance(result, OperationSuccess), result
    return result.result


def attest() -> dict[str, Any]:
    steps = attestations.guarded_steps()
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return dict(done.value.root)


def start(key: str, operation: str, arguments: dict[str, Any]) -> Any:
    before = attest()
    guard = {
        name: before[name]
        for name in (
            "host_session_id",
            "document_session_id",
            "project_id",
            "format",
            "digest",
        )
    }
    return call(
        "document.mutate",
        mutation_id=key,
        operation="blender." + operation,
        arguments=arguments,
        **guard,
    )


def finish(key: str) -> dict[str, Any]:
    for _ in range(1000):
        mutations.tick()
        value = mutations.status(key).model_dump(mode="json")
        if value["state"] not in {"queued", "running"}:
            return dict(value)
    raise AssertionError("Native receipt did not finish")


call("file.new", discard_current=True)
call(
    "armature.create",
    name="Rig",
    bones=[
        dict(name="A", head=[0, 0, 0], tail=[0, 1, 0]),
        dict(name="B", head=[0, 1, 0], tail=[0, 2, 0], parent="A", connected=True),
    ],
)
call(
    "armature.pose",
    object_name="Rig",
    bones=[dict(name=n, rotation=[0, 0, 0]) for n in ["A", "B"]],
)
call("project.bind", resources=[dict(resource_kind="object", name="Rig")])
original = attest()
spec = dict(
    couplings=[
        dict(
            name="Link",
            source=dict(
                kind="transform",
                object_name="Rig",
                bone="A",
                property="rotation",
                axis="z",
            ),
            target=dict(
                kind="transform",
                object_name="Rig",
                bone="B",
                property="rotation",
                axis="z",
            ),
            mapping=dict(kind="linear", scale=0.5),
        )
    ]
)
start("normal", "coupling.configure", spec)
normal = finish("normal")
assert normal["state"] == "completed" and normal["native_execution"] == "completed", (
    normal
)
assert normal["result"]["before"]["digest"] == original["digest"]
post = attest()
call("coupling.remove", names=["Link"])
assert attest()["digest"] == original["digest"]
start("cancelled", "coupling.configure", spec)
assert mutations.cancel("cancelled").native_execution == "not_started"
assert attest()["digest"] == original["digest"]
# Fail post-flight with a genuine notification after native execution. No second edit.
steps = attestations.guarded_steps
calls = 0


def notified() -> Any:
    global calls
    calls += 1
    iterator = steps()
    if calls == 2:
        progress = next(iterator)
        bpy.context.scene.update_tag()
        bpy.context.view_layer.update()
        yield progress
    return (yield from iterator)


start("partial", "coupling.configure", spec)
with patch.object(mutations, "guarded_steps", notified):
    result = finish("partial")
assert result["state"] == "failed" and result["native_execution"] == "completed", result
assert result["error"]["code"] == "application_changed", result
assert result["error"]["details"]["change_notifications"] == ["dependency_graph"], (
    result
)
assert attest()["digest"] == post["digest"]
# A genuine authored external change is still rejected by the same observation guard.
iterator = steps()
next(iterator)
bpy.data.objects["Rig"].location.x = 2
bpy.context.view_layer.update()
try:
    next(iterator)
except errors.OperationError as error:
    assert error.error.code == "application_changed"
else:
    raise AssertionError("External change was accepted")
print("MUTATION_RECOVERY_NATIVE_PASSED", flush=True)
