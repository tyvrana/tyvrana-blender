"""Owned, bounded background Blender processes for Core-issued proof leases."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from tyvrana_protocol import (
    AdapterRuntime,
    ProofHostControl,
    ProofHostStart,
    ProofHostStatus,
    ProofLease,
    ProtocolError,
)

from . import deployment
from .errors import OperationError


@dataclass
class Host:
    request: ProofHostStart
    directory: Path
    process: subprocess.Popen[bytes]
    expires: float


_hosts: dict[str, Host] = {}
_context: dict[str, object] | None = None


def configure(value: dict[str, object]) -> None:
    global _context
    _context = value


def runtime_identity() -> AdapterRuntime:
    import bpy  # type: ignore[import-not-found]

    from .blender import IMPLEMENTATION_BUILD

    lease = ProofLease.model_validate(_context["lease"]) if _context else None
    return AdapterRuntime(
        role="proof" if lease else "work",
        build=IMPLEMENTATION_BUILD,
        process_id=os.getpid(),
        background=bool(bpy.app.background),
        proof_lease=lease,
    )


def connection() -> tuple[str, int] | None:
    if _context is None:
        return None
    return str(_context["host"]), int(str(_context["port"]))


def start(request: ProofHostStart) -> ProofHostStatus:
    import bpy

    from .blender import IMPLEMENTATION_BUILD, INSTANCE_ID, _runtime

    if _context or request.lease.parent_adapter_id != INSTANCE_ID or _runtime is None:
        raise OperationError(
            "proof_origin", "Proof lifecycle requires the originating work host"
        )
    if request.expected_build != IMPLEMENTATION_BUILD:
        raise OperationError(
            "proof_build", "Requested proof implementation is not active"
        )
    existing = _hosts.get(request.lease.lease_id)
    if existing:
        if existing.request != request:
            raise OperationError(
                "proof_conflict", "Lease is already owned by another request"
            )
        return status(ProofHostControl(lease=request.lease))
    source = Path(request.artifact.locator)
    if not source.is_file():
        raise OperationError(
            "proof_artifact_missing", "Trusted artifact cannot be opened"
        )
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != request.artifact.sha256:
        raise OperationError("proof_file_mismatch", "Trusted artifact SHA256 changed")
    directory = Path(tempfile.mkdtemp(prefix="tyvrana-proof-"))
    try:
        implementation = Path(__file__).parent
        target = directory / "tyvrana_blender"
        shutil.copytree(
            implementation,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if deployment.identity(target) != IMPLEMENTATION_BUILD:
            raise OperationError(
                "proof_build", "Installed files differ from the active implementation"
            )
        context = request.model_dump(mode="json")
        context.update(
            host=_runtime.config.host,
            port=_runtime.config.port,
            parent_pid=os.getpid(),
            paths=[p for p in sys.path if p and os.path.isabs(p)],
        )
        config = directory / "request.json"
        config.write_text(json.dumps(context))
        config.chmod(0o600)
        environment = dict(
            os.environ,
            TYVRANA_PROOF_CONFIG=str(config),
            BLENDER_USER_CONFIG=str(directory / "profile"),
        )
        with (directory / "process.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    bpy.app.binary_path,
                    "--background",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(target / "proof_bootstrap.py"),
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _hosts[request.lease.lease_id] = Host(
            request, directory, process, time.monotonic() + request.ttl_seconds
        )
    except BaseException:
        shutil.rmtree(directory)
        raise
    return ProofHostStatus(
        lease_id=request.lease.lease_id, state="starting", process_id=process.pid
    )


def _owned(request: ProofHostControl) -> Host | None:
    host = _hosts.get(request.lease.lease_id)
    if host is not None and host.request.lease != request.lease:
        raise OperationError("proof_lease", "Proof lease capability does not match")
    return host


def _diagnostic(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 768))
        return stream.read(768).decode("utf-8", errors="replace")


def status(request: ProofHostControl) -> ProofHostStatus:
    host = _owned(request)
    if host is None:
        return ProofHostStatus(lease_id=request.lease.lease_id, state="stopped")
    result = host.directory / "status.json"
    if result.exists():
        value = ProofHostStatus.model_validate_json(result.read_text())
        if value.state == "failed":
            return value
    if host.process.poll() is not None:
        return ProofHostStatus(
            lease_id=request.lease.lease_id,
            state="failed",
            process_id=host.process.pid,
            error=ProtocolError(
                code="proof_exited",
                message="Proof exited: " + _diagnostic(host.directory / "process.log"),
            ),
        )
    if result.exists():
        return ProofHostStatus.model_validate_json(result.read_text())
    return ProofHostStatus(
        lease_id=request.lease.lease_id, state="starting", process_id=host.process.pid
    )


def stop(request: ProofHostControl) -> ProofHostStatus:
    host = _owned(request)
    if host is not None:
        (host.directory / "stop").touch()
        try:
            host.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            os.killpg(host.process.pid, signal.SIGTERM)
            try:
                host.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(host.process.pid, signal.SIGKILL)
                host.process.wait(timeout=2)
        _hosts.pop(request.lease.lease_id)
        shutil.rmtree(host.directory)
    return ProofHostStatus(lease_id=request.lease.lease_id, state="stopped")


def tick() -> None:
    for host in tuple(_hosts.values()):
        if time.monotonic() >= host.expires:
            stop(ProofHostControl(lease=host.request.lease))


def shutdown() -> None:
    for host in tuple(_hosts.values()):
        stop(ProofHostControl(lease=host.request.lease))
