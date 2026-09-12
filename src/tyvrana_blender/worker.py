"""Owned networking subprocess. This module must never import Blender modules.

Local pipes carry newline-framed canonical Tyvrana messages. Only connection
state events are local to the parent; they are not sent to core.
"""

import asyncio
import logging
import os
import sys
import time

from tyvrana_protocol import (
    AdapterEvent,
    AdapterRegistration,
    CancelRequest,
    Message,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
    decode_message,
    encode_message,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

logger = logging.getLogger(__name__)
MAX_FRAME = 1024 * 1024
MAX_PENDING = 128


class Backoff:
    def __init__(self) -> None:
        self.delay = 0.25

    def next_delay(self, *, healthy: bool = False) -> float:
        if healthy:
            self.delay = 0.25
        value = self.delay
        self.delay = min(8.0, self.delay * 2)
        return value


class Output:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self._lock = asyncio.Lock()
        os.set_blocking(descriptor, False)

    async def send(self, message: Message) -> None:
        view = memoryview(encode_message(message) + b"\n")
        async with self._lock:
            while view:
                try:
                    count = os.write(self.descriptor, view)
                    view = view[count:]
                except BlockingIOError:
                    loop = asyncio.get_running_loop()
                    ready: asyncio.Future[None] = loop.create_future()

                    def writable(future: asyncio.Future[None] = ready) -> None:
                        if not future.done():
                            future.set_result(None)

                    loop.add_writer(self.descriptor, writable)
                    try:
                        await ready
                    finally:
                        loop.remove_writer(self.descriptor)

    async def state(self, state: str) -> None:
        await self.send(
            AdapterEvent(
                type="adapter.event",
                event="blender.connection.state",
                payload={"state": state},
            )
        )


class NetworkClient:
    def __init__(
        self, uri: str, registration: AdapterRegistration, output: Output
    ) -> None:
        self.uri = uri
        self.registration = registration
        self.output = output
        self.socket: ClientConnection | None = None
        self.pending: set[str] = set()

    async def connections(self) -> None:
        backoff = Backoff()
        while True:
            connected_at: float | None = None
            try:
                logger.info("Connecting to %s", self.uri)
                await self.output.state("connecting")
                async with connect(
                    self.uri,
                    proxy=None,
                    compression=None,
                    open_timeout=2,
                    close_timeout=0.5,
                    max_size=MAX_FRAME,
                    max_queue=8,
                ) as websocket:
                    self.socket = websocket
                    await websocket.send(encode_message(self.registration))
                    connected_at = time.monotonic()
                    logger.info(
                        "Connected and registration sent: %s",
                        self.registration.instance_id,
                    )
                    await self.output.state("connected")
                    async for data in websocket:
                        try:
                            message = decode_message(
                                data.encode() if isinstance(data, str) else data
                            )
                            if isinstance(message, OperationRequest):
                                if message.request_id in self.pending:
                                    raise ValueError("Duplicate pending request ID")
                                if len(self.pending) >= MAX_PENDING:
                                    await websocket.send(
                                        encode_message(
                                            OperationFailure(
                                                type="operation.failure",
                                                request_id=message.request_id,
                                                error=ProtocolError(
                                                    code="adapter_busy",
                                                    message="Too many requests",
                                                ),
                                            )
                                        )
                                    )
                                    continue
                                self.pending.add(message.request_id)
                                await self.output.send(message)
                            elif isinstance(message, CancelRequest):
                                self.pending.discard(message.request_id)
                                await self.output.send(message)
                            else:
                                raise ValueError("Unsupported core message direction")
                        except (ValueError, RecursionError) as exc:
                            logger.warning(
                                "Malformed core protocol message: %s",
                                type(exc).__name__,
                            )
                            await websocket.close(
                                code=1008, reason="Invalid core message"
                            )
                            break
            except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake) as exc:
                logger.info("Core connection unavailable: %s", type(exc).__name__)
            finally:
                self.socket = None
                self.pending.clear()
            await self.output.state("disconnected")
            delay = backoff.next_delay(
                healthy=connected_at is not None
                and time.monotonic() - connected_at >= 5
            )
            logger.info("Disconnected; reconnect in %.2f seconds", delay)
            await asyncio.sleep(delay)

    async def responses(self, reader: asyncio.StreamReader) -> None:
        while line := await reader.readline():
            if not line.endswith(b"\n"):
                return  # Parent closed mid-frame during shutdown.
            message = decode_message(line)
            if not isinstance(message, (OperationSuccess, OperationFailure)):
                raise ValueError("Unsupported parent message direction")
            if message.request_id not in self.pending:
                continue  # Cancelled, disconnected, or completed in an earlier session.
            self.pending.remove(message.request_id)
            socket = self.socket
            if socket is not None:
                try:
                    await socket.send(encode_message(message))
                except ConnectionClosed:
                    pass  # The connection loop owns reconnect and pending cleanup.


async def run(uri: str) -> None:
    reader = asyncio.StreamReader(limit=MAX_FRAME + 1)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    try:
        first = await reader.readline()
        if not first.endswith(b"\n"):
            return  # Extension can be disabled before the first timer tick.
        registration = decode_message(first)
        if not isinstance(registration, AdapterRegistration):
            raise ValueError("Parent must send registration first")
        client = NetworkClient(uri, registration, Output(sys.stdout.fileno()))
        tasks = [
            asyncio.create_task(client.connections()),
            asyncio.create_task(client.responses(reader)),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        transport.close()
        logger.info("Networking worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s tyvrana_blender.network: %(message)s",
    )
    try:
        asyncio.run(run(sys.argv[1]))
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Networking worker failed")
        raise SystemExit(1) from None
