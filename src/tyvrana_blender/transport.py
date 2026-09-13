"""Nonblocking parent pipe endpoint and deterministic worker process ownership."""

import logging
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from tyvrana_protocol import (
    AdapterRegistration,
    Message,
    OperationSuccess,
    decode_message,
    encode_message,
)

from .artifacts import ArtifactSpool
from .models import ConnectionConfig

logger = logging.getLogger(__name__)
MAX_FRAME = 1024 * 1024


class WorkerProcess:
    def __init__(
        self, config: ConnectionConfig, registration: AdapterRegistration
    ) -> None:
        self.spool = ArtifactSpool()
        # Blender supplies extension-managed wheel paths. The isolated child uses
        # those exact paths and its bundled interpreter, never a global install.
        bootstrap = (
            "import pathlib,runpy,sys; sys.path[:0]=sys.argv[1:-3]; "
            "sys.argv=sys.argv[-3:]; "
            "module=pathlib.Path(sys.argv[0]); "
            "sys.path.insert(0,str(module.parent.parent)); "
            "runpy.run_module(module.parent.name+'.worker',run_name='__main__')"
        )
        try:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-u",
                    "-c",
                    bootstrap,
                    *[p for p in sys.path if p and Path(p).is_absolute()],
                    str(Path(__file__).with_name("worker.py")),
                    config.uri,
                    str(self.spool.root),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                bufsize=0,
            )
        except BaseException:
            self.spool.close()
            raise
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self._incoming = bytearray()
        self._outgoing: deque[bytes] = deque()
        self._offset = 0
        self._closed = False
        self.send(registration)

    def send(self, message: Message) -> None:
        frame = encode_message(message) + b"\n"
        if len(frame) > MAX_FRAME + 1 or len(self._outgoing) >= 129:
            raise ValueError("Worker output queue exceeded its bound")
        if self._closed:
            raise RuntimeError("Networking worker is closed")
        self._outgoing.append(frame)

    def poll(self) -> list[Message]:
        if self.process.poll() is not None:
            raise RuntimeError(
                f"Networking worker exited with {self.process.returncode}"
            )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        # Bound pipe IO work per timer tick; neither read nor write can block UI.
        remaining = 256 * 1024
        while self._outgoing and remaining > 0:
            try:
                count = os.write(
                    self.process.stdin.fileno(),
                    self._outgoing[0][self._offset : self._offset + remaining],
                )
            except BlockingIOError:
                break
            self._offset += count
            remaining -= count
            if self._offset == len(self._outgoing[0]):
                self._outgoing.popleft()
                self._offset = 0
        remaining = 256 * 1024
        while remaining > 0:
            try:
                chunk = os.read(self.process.stdout.fileno(), min(65536, remaining))
            except BlockingIOError:
                break
            if not chunk:
                raise RuntimeError("Networking worker closed its output")
            self._incoming.extend(chunk)
            remaining -= len(chunk)
        messages: list[Message] = []
        while b"\n" in self._incoming:
            line, _, rest = self._incoming.partition(b"\n")
            self._incoming = bytearray(rest)
            if len(line) > MAX_FRAME:
                raise ValueError("Worker frame is too large")
            messages.append(decode_message(bytes(line)))
        if len(self._incoming) > MAX_FRAME:
            raise ValueError("Worker frame is too large")
        return messages

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.close()  # EOF cancels networking and closes the socket.
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            logger.error("Networking worker did not stop on EOF; terminating it")
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            self.process.stdout.close()
            self._outgoing.clear()
            self._incoming.clear()
            self.spool.close()

    def discard(self, message: Message) -> None:
        if isinstance(message, OperationSuccess):
            self.spool.release(message.artifacts)
