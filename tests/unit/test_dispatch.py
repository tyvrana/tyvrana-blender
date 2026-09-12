import threading

import pytest
from tyvrana_protocol import OperationFailure, OperationRequest, OperationSuccess

from tyvrana_blender.dispatch import CommandQueue
from tyvrana_blender.operations import Response


def request(identifier: str = "request-a") -> OperationRequest:
    return OperationRequest(
        type="operation.request",
        request_id=identifier,
        operation="blender.scene.inspect",
        arguments={},
    )


def success(message: OperationRequest) -> Response:
    return OperationSuccess(
        type="operation.success",
        request_id=message.request_id,
        result={"value": [None, "[]"]},
    )


def test_queue_preserves_order_and_correlation() -> None:
    queue = CommandQueue(success)
    results: list[Response] = []
    for i in range(5):
        assert queue.submit(request(f"r-{i}")) is None
    queue.drain(results.append)
    assert [result.request_id for result in results] == [f"r-{i}" for i in range(5)]
    assert queue.pending_count == 0


def test_cancel_queued_work_and_ignore_unknown_cancellation() -> None:
    queue = CommandQueue(success)
    queue.submit(request())
    queue.cancel("unknown")
    queue.cancel("request-a")
    results: list[Response] = []
    queue.drain(results.append)
    assert results == [] and queue.pending_count == 0
    assert queue.submit(request()) is None


def test_cancel_running_work_suppresses_completion() -> None:
    entered = threading.Event()
    cancelled = threading.Event()

    def handler(message: OperationRequest) -> Response:
        entered.set()
        assert cancelled.wait(2)
        return success(message)

    discarded: list[Response] = []
    queue = CommandQueue(handler, discard=discarded.append)
    queue.submit(request())

    def cancel() -> None:
        assert entered.wait(2)
        queue.cancel("request-a")
        cancelled.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    try:
        results: list[Response] = []
        queue.drain(results.append)
        assert results == [] and queue.pending_count == 0
        assert discarded == [success(request())]
    finally:
        thread.join(2)
        assert not thread.is_alive()


def test_queue_bounds_duplicate_ids_and_shutdown() -> None:
    queue = CommandQueue(success, capacity=1)
    queue.submit(request())
    with pytest.raises(ValueError, match="Duplicate"):
        queue.submit(request())
    rejected = queue.submit(request("other"))
    assert isinstance(rejected, OperationFailure)
    assert rejected.error.code == "adapter_busy"
    queue.close()
    queue.close()
    assert queue.pending_count == 0
    assert isinstance(queue.submit(request()), OperationFailure)


def test_main_thread_assertion() -> None:
    errors: list[Exception] = []
    queue = CommandQueue(success)

    def wrong_thread() -> None:
        try:
            queue.drain(lambda response: None)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join(2)
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)


def test_timer_work_is_bounded() -> None:
    queue = CommandQueue(success)
    for i in range(12):
        queue.submit(request(f"r-{i}"))
    results: list[Response] = []
    queue.drain(results.append)
    assert len(results) <= 8
    queue.clear()
    assert queue.pending_count == 0
