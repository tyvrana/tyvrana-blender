"""Owned process termination and failed-start cleanup never touch unrelated hosts."""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tyvrana_protocol import ProofArtifact, ProofHostControl, ProofHostStart, ProofLease

from tyvrana_blender import deployment, proof_hosts


def intent(path: Path, build: str = "a" * 64) -> ProofHostStart:
    return ProofHostStart(
        lease=ProofLease(lease_id="lease", token="b" * 64, parent_adapter_id="parent"),
        artifact=ProofArtifact(
            locator=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            project_id="document",
        ),
        expected_build=build,
        ttl_seconds=60,
    )


def test_start_failure_removes_owned_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "document.blend"
    artifact.write_bytes(b"fixture")
    build = deployment.identity(Path(proof_hosts.__file__).parent)
    monkeypatch.setitem(
        sys.modules,
        "bpy",
        SimpleNamespace(app=SimpleNamespace(binary_path="fixture-blender")),
    )
    monkeypatch.setitem(
        sys.modules,
        "tyvrana_blender.blender",
        SimpleNamespace(
            IMPLEMENTATION_BUILD=build,
            INSTANCE_ID="parent",
            _runtime=SimpleNamespace(
                config=SimpleNamespace(host="127.0.0.1", port=1234)
            ),
        ),
    )
    owned = tmp_path / "owned"

    def directory(*args: Any, **kwargs: Any) -> str:
        owned.mkdir()
        return str(owned)

    def failure(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Process launch failed")

    monkeypatch.setattr(tempfile, "mkdtemp", directory)
    monkeypatch.setattr(subprocess, "Popen", failure)
    with pytest.raises(OSError, match="launch failed"):
        proof_hosts.start(intent(artifact, build))
    assert not owned.exists()
    assert not proof_hosts._hosts


@pytest.mark.parametrize("reason", ["release", "ttl", "shutdown"])
def test_owned_process_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    artifact = tmp_path / "document.blend"
    artifact.write_bytes(b"fixture")
    request = intent(artifact)
    owned = tmp_path / "owned"
    owned.mkdir()
    waits: list[float] = []
    signals: list[int] = []

    class Process:
        pid = 12345

        def wait(self, timeout: float) -> int:
            waits.append(timeout)
            if len(waits) < 3:
                raise subprocess.TimeoutExpired("owned", timeout)
            return 0

    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append(pid))
    monkeypatch.setitem(
        proof_hosts._hosts,
        "lease",
        proof_hosts.Host(request, owned, Process(), 0),  # type: ignore[arg-type]
    )
    if reason == "release":
        result = proof_hosts.stop(ProofHostControl(lease=request.lease))
        assert result.state == "stopped"
    elif reason == "ttl":
        proof_hosts.tick()
    else:
        proof_hosts.shutdown()
    assert signals == [12345, 12345]
    assert waits == [0.5, 2, 2]
    assert not proof_hosts._hosts and not owned.exists()
