"""Read Cycles' selected backend without refreshing or changing saved preferences."""

from collections.abc import Callable, Sequence
from typing import Any

from .render_models import RenderDevice, RenderDevicesResult


def inspect_devices(
    context: Any, available_devices: Callable[[str], Sequence[Sequence[Any]]]
) -> RenderDevicesResult:
    scene = context.scene
    addon = context.preferences.addons.get("cycles")
    engine = str(scene.render.engine)
    if addon is None:
        return RenderDevicesResult(
            scene_engine=engine,
            scene_device=None,
            cycles_available=False,
            compute_backend=None,
            supported_backends=[],
            devices=[],
            device_count=0,
            configured_gpu_available=False,
            scene_uses_gpu=False,
        )
    prefs = addon.preferences
    backend = str(prefs.get_compute_device_type())
    selected = {(str(d.id), str(d.type)): bool(d.use) for d in prefs.devices}
    # Calling prefs.get_device_list/refresh_devices would create preference entries.
    # Native "NONE" probes every driver; a CPU configuration needs only CPU.
    native = available_devices("CPU" if backend == "NONE" else backend)
    devices = [
        RenderDevice(
            name=str(d[0]),
            backend=str(d[1]),
            device_id=str(d[2]),
            enabled=selected.get((str(d[2]), str(d[1]))),
        )
        for d in native[:128]
    ]
    configured = backend != "NONE" and any(
        str(d[1]) == backend and selected.get((str(d[2]), str(d[1])), False)
        for d in native
    )
    scene_device = str(scene.cycles.device)
    return RenderDevicesResult(
        scene_engine=engine,
        scene_device=scene_device,
        cycles_available=True,
        compute_backend=backend,
        supported_backends=[str(d[0]) for d in prefs.get_device_types(context)],
        devices=devices,
        device_count=len(native),
        configured_gpu_available=configured,
        scene_uses_gpu=engine == "CYCLES" and scene_device == "GPU" and configured,
    )
