"""Bounded cancellable queue consumed only by Blender's main thread."""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from tyvrana_protocol import OperationRequest, ProtocolError

from .operations import Response, failure


@dataclass
class _Job:
    request: OperationRequest
    cancelled: bool = False


class CommandQueue:
    def __init__(
        self,
        handler: Callable[[OperationRequest], Response],
        capacity: int = 128,
        discard: Callable[[Response], None] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("Capacity must be positive")
        self._handler = handler
        self._discard = discard
        self._capacity = capacity
        self._jobs: dict[str, _Job] = {}
        self._queue: deque[_Job] = deque()
        self._lock = threading.Lock()
        self._closed = False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def submit(self, request: OperationRequest) -> Response | None:
        with self._lock:
            if request.request_id in self._jobs:
                raise ValueError("Duplicate pending request ID")
            if self._closed or len(self._jobs) >= self._capacity:
                return failure(
                    request,
                    ProtocolError(
                        code="adapter_busy",
                        message="Adapter command queue is unavailable",
                    ),
                )
            job = _Job(request)
            self._jobs[request.request_id] = job
            self._queue.append(job)
        return None

    def cancel(self, request_id: str) -> None:
        with self._lock:
            job = self._jobs.get(request_id)
            if job is not None:
                job.cancelled = True
                if job in self._queue:
                    self._queue.remove(job)
                    self._jobs.pop(request_id)

    def clear(self, *, preserve_operations: frozenset[str] = frozenset()) -> None:
        with self._lock:
            for identifier, job in list(self._jobs.items()):
                if job.request.operation not in preserve_operations:
                    job.cancelled = True
                    del self._jobs[identifier]
            self._queue = deque(job for job in self._queue if not job.cancelled)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.clear()

    def drain(self, send: Callable[[Response], None], *, budget: float = 0.01) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Blender operations require the main thread")
        deadline = time.monotonic() + budget
        for _ in range(8):
            with self._lock:
                if not self._queue:
                    return
                job = self._queue.popleft()
            result = self._handler(job.request)
            with self._lock:
                if self._jobs.get(job.request.request_id) is job:
                    self._jobs.pop(job.request.request_id)
                cancelled = job.cancelled
            if not cancelled:
                send(result)
            elif self._discard is not None:
                self._discard(result)
            if time.monotonic() >= deadline:
                return
