"""GPU discovery must not enable devices or mistake stale preferences for hardware."""

from types import SimpleNamespace as NS
from typing import Any

import pytest
from tyvrana_protocol import OperationSuccess

from tyvrana_blender.operations import REGISTRY
from tyvrana_blender.render_devices import inspect_devices

from .test_operations import Backend, call


def context(backend: str = "OPTIX", scene_device: str = "GPU") -> Any:
    return NS(
        scene=NS(render=NS(engine="CYCLES"), cycles=NS(device=scene_device)),
        preferences=NS(
            addons={
                "cycles": NS(
                    preferences=NS(
                        get_compute_device_type=lambda: backend,
                        get_device_types=lambda _: [("NONE",), ("CUDA",), ("OPTIX",)],
                        devices=(
                            NS(id="gpu", type="OPTIX", use=True),
                            NS(id="gpu", type="CUDA", use=False),
                            NS(id="cpu", type="CPU", use=False),
                        ),
                    )
                )
            }
        ),
    )


def test_selected_backend_probe_preserves_preferences_and_reports_unknown() -> None:
    state = context()
    preferences = state.preferences.addons["cycles"].preferences
    before = [(d.id, d.type, d.use) for d in preferences.devices]

    def probe(backend: str) -> list[tuple[str, str, str]]:
        assert backend == "OPTIX"
        return [("GPU", "OPTIX", "gpu"), ("CPU", "CPU", "cpu"), ("New", "OPTIX", "new")]

    result = inspect_devices(state, probe)
    assert result.configured_gpu_available and result.scene_uses_gpu
    assert [d.enabled for d in result.devices] == [True, False, None]
    assert before == [(d.id, d.type, d.use) for d in preferences.devices]
    assert state.scene.cycles.device == "GPU"


@pytest.mark.parametrize("backend,device", [("CUDA", "GPU"), ("OPTIX", "CPU")])
def test_disabled_backend_or_cpu_scene_is_not_gpu_rendering(
    backend: str, device: str
) -> None:
    result = inspect_devices(
        context(backend, device), lambda _: [("GPU", backend, "gpu")]
    )
    assert not result.scene_uses_gpu


def test_removed_hardware_cannot_pass_from_saved_enable_flag() -> None:
    result = inspect_devices(context(), lambda _: [("CPU", "CPU", "cpu")])
    assert not result.configured_gpu_available


def test_no_compute_backend_probes_only_cpu() -> None:
    def probe(backend: str) -> list[tuple[str, str, str]]:
        assert backend == "CPU"
        return [("CPU", "CPU", "cpu")]

    result = inspect_devices(context("NONE"), probe)
    assert result.compute_backend == "NONE" and not result.configured_gpu_available


def test_no_cycles_does_not_probe_and_device_output_is_bounded() -> None:
    state = context()
    state.preferences.addons = {}
    result = inspect_devices(state, lambda _: pytest.fail("No Cycles probe expected"))
    assert not result.cycles_available and result.devices == []
    result = inspect_devices(
        context(), lambda _: [(str(i), "OPTIX", str(i)) for i in range(200)]
    )
    assert len(result.devices) == 128 and result.device_count == 200


def test_discovery_dispatch_and_probe_failures_remain_visible() -> None:
    backend = Backend()
    result = call(backend, "blender.render.devices", {})
    assert isinstance(result, OperationSuccess)
    assert backend.calls == ["render_devices"]
    assert REGISTRY["blender.render.devices"].contract.effect == "read_only"

    def broken(_: str) -> list[tuple[str, str, str]]:
        raise RuntimeError("Driver probe failed")

    with pytest.raises(RuntimeError, match="Driver probe failed"):
        inspect_devices(context(), broken)


def test_gpu_intent_never_silently_downgrades() -> None:
    from tyvrana_blender.errors import OperationError
    from tyvrana_blender.models import CyclesRenderOptions
    from tyvrana_blender.render_devices import requested_device

    with pytest.raises(OperationError, match="No enabled available Cycles GPU"):
        requested_device(
            context(),
            CyclesRenderOptions(device="gpu"),
            lambda _: [("CPU", "CPU", "cpu")],
        )
    value = requested_device(
        context(),
        CyclesRenderOptions(device="gpu"),
        lambda _: [("RTX", "OPTIX", "gpu")],
    )
    assert value.effective == "gpu" and value.compute_backend == "OPTIX"
    assert value.enabled_devices == ["RTX"] and value.requested == "gpu"


def test_explicit_cpu_and_scene_device_reporting() -> None:
    from tyvrana_blender.models import CyclesRenderOptions
    from tyvrana_blender.render_devices import requested_device

    value = requested_device(
        context(),
        CyclesRenderOptions(device="cpu"),
        lambda _: pytest.fail("CPU needs no GPU probe"),
    )
    assert value.requested == value.effective == "cpu"
    assert value.compute_backend is None
    value = requested_device(context(), None, lambda _: [("RTX", "OPTIX", "gpu")])
    assert value.requested == "scene" and value.effective == "gpu"
