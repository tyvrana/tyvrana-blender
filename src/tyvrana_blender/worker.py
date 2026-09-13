"""Owned networking subprocess. This module must never import Blender modules.

Local pipes carry newline-framed canonical Tyvrana messages. Only connection
state events are local to the parent; they are not sent to core.
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from tyvrana_protocol import (
    MAX_ARTIFACT_CHUNK_SIZE,
    AdapterEvent,
    AdapterRegistration,
    ArtifactAbort,
    ArtifactAccepted,
    ArtifactBegin,
    ArtifactChunk,
    ArtifactComplete,
    ArtifactReady,
    CancelRequest,
    Message,
    OperationFailure,
    OperationRequest,
    OperationSuccess,
    ProtocolError,
    decode_artifact_chunk,
    decode_message,
    encode_artifact_chunk,
    encode_message,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .incoming import InputError, InputStore

logger = logging.getLogger(__name__)
MAX_FRAME = 1024 * 1024
MAX_PENDING = 128
ACK_TIMEOUT = 10.0


class TransferRejected(Exception):
    pass


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
        self, uri: str, registration: AdapterRegistration, output: Output, spool: Path
    ) -> None:
        self.uri = uri
        self.registration = registration
        self._registered: AdapterRegistration | None = None
        self.output = output
        self.socket: ClientConnection | None = None
        self.pending: set[str] = set()
        self.spool = spool
        self.inputs = InputStore(spool)
        self._tasks: set[asyncio.Task[None]] = set()
        self._transfers: dict[
            str,
            tuple[str, asyncio.Queue[ArtifactReady | ArtifactAccepted | ArtifactAbort]],
        ] = {}

    async def send(self, socket: ClientConnection, data: str | bytes) -> None:
        try:
            async with asyncio.timeout(5):
                await socket.send(data)
        except (asyncio.CancelledError, TimeoutError):
            # Interrupted writes have uncertain delivery; never reuse that socket.
            socket.transport.abort()
            raise

    def cancel_request(self, request_id: str) -> None:
        self.pending.discard(request_id)
        self.inputs.discard_request(request_id)
        for transfer_id, (related, queue) in self._transfers.items():
            if related == request_id:
                if not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(
                    ArtifactAbort(
                        type="artifact.abort",
                        transfer_id=transfer_id,
                        error=ProtocolError(
                            code="cancelled", message="Operation cancelled"
                        ),
                    )
                )

    async def cleanup(self) -> None:
        self.pending.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._transfers.clear()
        self.inputs.clear()

    def release(self, response: OperationSuccess | OperationFailure) -> None:
        if isinstance(response, OperationSuccess):
            for descriptor in response.artifacts:
                (self.spool / (descriptor.artifact_id + ".png")).unlink(missing_ok=True)

    async def deliver(
        self, socket: ClientConnection, response: OperationSuccess | OperationFailure
    ) -> None:
        transfer_id: str | None = None
        try:
            if isinstance(response, OperationSuccess):
                for descriptor in response.artifacts:
                    if response.request_id not in self.pending:
                        return
                    transfer_id = uuid4().hex
                    queue: asyncio.Queue[
                        ArtifactReady | ArtifactAccepted | ArtifactAbort
                    ] = asyncio.Queue(maxsize=1)
                    self._transfers[transfer_id] = (response.request_id, queue)
                    try:
                        await self.send(
                            socket,
                            encode_message(
                                ArtifactBegin(
                                    type="artifact.begin",
                                    transfer_id=transfer_id,
                                    request_id=response.request_id,
                                    descriptor=descriptor,
                                )
                            ).decode(),
                        )
                        async with asyncio.timeout(ACK_TIMEOUT):
                            ready = await queue.get()
                        if not isinstance(ready, ArtifactReady):
                            raise TransferRejected("Artifact admission rejected")
                        offset = 0
                        # The path is private IPC within this extension, derived from
                        # a validated opaque ID; it never enters a protocol message.
                        with (self.spool / (descriptor.artifact_id + ".png")).open(
                            "rb"
                        ) as stream:
                            while chunk := stream.read(MAX_ARTIFACT_CHUNK_SIZE):
                                if response.request_id not in self.pending:
                                    return
                                if offset + len(chunk) > descriptor.byte_size:
                                    raise TransferRejected("Render file size changed")
                                await self.send(
                                    socket,
                                    encode_artifact_chunk(transfer_id, offset, chunk),
                                )
                                offset += len(chunk)
                                await asyncio.sleep(
                                    0
                                )  # Let cancellation/control input run between chunks.
                        if response.request_id not in self.pending:
                            return
                        if offset != descriptor.byte_size:
                            raise TransferRejected("Render file size changed")
                        await self.send(
                            socket,
                            encode_message(
                                ArtifactComplete(
                                    type="artifact.complete", transfer_id=transfer_id
                                )
                            ).decode(),
                        )
                        async with asyncio.timeout(ACK_TIMEOUT):
                            accepted = await queue.get()
                        if not isinstance(accepted, ArtifactAccepted):
                            raise TransferRejected("Artifact completion rejected")
                    finally:
                        self._transfers.pop(transfer_id, None)
            if response.request_id in self.pending:
                await self.send(socket, encode_message(response).decode())
        except (OSError, TimeoutError, TransferRejected, ConnectionClosed) as exc:
            logger.info("Artifact response failed: %s", type(exc).__name__)
            error = ProtocolError(
                code="artifact_transfer_failed",
                message="Could not transfer operation artifacts",
            )
            if response.request_id in self.pending:
                try:
                    if transfer_id is not None:
                        await self.send(
                            socket,
                            encode_message(
                                ArtifactAbort(
                                    type="artifact.abort",
                                    transfer_id=transfer_id,
                                    error=error,
                                )
                            ).decode(),
                        )
                    await self.send(
                        socket,
                        encode_message(
                            OperationFailure(
                                type="operation.failure",
                                request_id=response.request_id,
                                error=error,
                            )
                        ).decode(),
                    )
                except (OSError, TimeoutError, ConnectionClosed):
                    pass  # Connection loop performs disconnect cleanup.
        finally:
            self.pending.discard(response.request_id)
            self.inputs.discard_request(response.request_id)
            self.release(response)

    async def input_message(
        self,
        socket: ClientConnection,
        message: ArtifactBegin | ArtifactChunk | ArtifactComplete | ArtifactAbort,
    ) -> None:
        transfer_id = message.transfer_id
        if transfer_id in self._transfers:
            raise ValueError("Transfer ID conflicts with an output transfer")
        request_id = self.inputs.request_for(transfer_id)
        try:
            if isinstance(message, ArtifactBegin):
                if request_id is not None:
                    raise ValueError("Input transfer ID collision")
                request_id = message.request_id
                if request_id in self.pending:
                    raise InputError("Operation already started")
                self.inputs.begin(message)
                await self.send(
                    socket,
                    encode_message(
                        ArtifactReady(type="artifact.ready", transfer_id=transfer_id)
                    ).decode(),
                )
            elif isinstance(message, ArtifactChunk):
                self.inputs.write(message)
            elif isinstance(message, ArtifactComplete):
                self.inputs.complete(transfer_id)
                await self.send(
                    socket,
                    encode_message(
                        ArtifactAccepted(
                            type="artifact.accepted", transfer_id=transfer_id
                        )
                    ).decode(),
                )
            else:
                if request_id is not None:
                    self.cancel_request(request_id)
                    await self.output.send(
                        CancelRequest(type="operation.cancel", request_id=request_id)
                    )
        except InputError as exc:
            if request_id is not None:
                self.cancel_request(request_id)
                await self.output.send(
                    CancelRequest(type="operation.cancel", request_id=request_id)
                )
            await self.send(
                socket,
                encode_message(
                    ArtifactAbort(
                        type="artifact.abort",
                        transfer_id=transfer_id,
                        error=ProtocolError(
                            code="artifact_transfer_failed", message=str(exc)
                        ),
                    )
                ).decode(),
            )

    async def refresh_registration(self) -> None:
        # A save may occur inside a pending operation. Keep its response and all
        # admitted transfers alive before reconnecting with the new project path.
        if (
            self.socket is not None
            and self._registered is not None
            and self.registration != self._registered
            and not self.pending
            and not self.inputs.entries
        ):
            await self.socket.close(code=1000, reason="Project path changed")

    async def expire_inputs(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            socket = self.socket
            if socket is None:
                continue
            for request_id, transfer_id in self.inputs.expired(time.monotonic()):
                self.cancel_request(request_id)
                try:
                    await self.send(
                        socket,
                        encode_message(
                            ArtifactAbort(
                                type="artifact.abort",
                                transfer_id=transfer_id,
                                error=ProtocolError(
                                    code="artifact_transfer_failed",
                                    message="Input admission timed out",
                                ),
                            )
                        ).decode(),
                    )
                except (OSError, TimeoutError, ConnectionClosed):
                    socket.transport.abort()
                    break
            await self.refresh_registration()

    def completed(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Response task failed", exc_info=task.exception())
            if self.socket is not None:
                self.socket.transport.abort()

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
                    self._registered = self.registration
                    await self.send(
                        websocket, encode_message(self._registered).decode()
                    )
                    connected_at = time.monotonic()
                    logger.info(
                        "Connected and registration sent: %s",
                        self.registration.instance_id,
                    )
                    await self.output.state("connected")
                    async for data in websocket:
                        try:
                            if isinstance(data, bytes):
                                await self.input_message(
                                    websocket, decode_artifact_chunk(data)
                                )
                                continue
                            message = decode_message(data.encode())
                            if isinstance(message, OperationRequest):
                                if message.request_id in self.pending:
                                    raise ValueError("Duplicate pending request ID")
                                if len(self.pending) >= MAX_PENDING:
                                    self.inputs.discard_request(message.request_id)
                                    await self.send(
                                        websocket,
                                        encode_message(
                                            OperationFailure(
                                                type="operation.failure",
                                                request_id=message.request_id,
                                                error=ProtocolError(
                                                    code="adapter_busy",
                                                    message="Too many requests",
                                                ),
                                            )
                                        ).decode(),
                                    )
                                    continue
                                try:
                                    self.inputs.claim(message)
                                except InputError as exc:
                                    self.inputs.discard_request(message.request_id)
                                    await self.send(
                                        websocket,
                                        encode_message(
                                            OperationFailure(
                                                type="operation.failure",
                                                request_id=message.request_id,
                                                error=ProtocolError(
                                                    code="artifact_transfer_failed",
                                                    message=str(exc),
                                                ),
                                            )
                                        ).decode(),
                                    )
                                    continue
                                self.pending.add(message.request_id)
                                await self.output.send(message)
                            elif isinstance(message, CancelRequest):
                                self.cancel_request(message.request_id)
                                await self.output.send(message)
                            elif isinstance(message, (ArtifactBegin, ArtifactComplete)):
                                await self.input_message(websocket, message)
                            elif isinstance(
                                message,
                                (ArtifactReady, ArtifactAccepted, ArtifactAbort),
                            ):
                                transfer = self._transfers.get(message.transfer_id)
                                if transfer is not None:
                                    _, queue = transfer
                                    if queue.full():
                                        raise ValueError(
                                            "Unexpected artifact acknowledgement"
                                        )
                                    queue.put_nowait(message)
                                elif isinstance(message, ArtifactAbort):
                                    await self.input_message(websocket, message)
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
                self._registered = None
                await self.cleanup()
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
            if isinstance(message, AdapterRegistration):
                if (
                    message.model_copy(
                        update={"project_path": self.registration.project_path}
                    )
                    != self.registration
                ):
                    raise ValueError("A project refresh cannot change adapter identity")
                self.registration = message
                continue
            if not isinstance(message, (OperationSuccess, OperationFailure)):
                raise ValueError("Unsupported parent message direction")
            # Main-thread execution is finished; input files are no longer needed,
            # even if output artifacts still await network acknowledgement.
            self.inputs.discard_request(message.request_id)
            if message.request_id not in self.pending:
                self.release(message)
                continue  # Cancelled, disconnected, or completed in an earlier session.
            socket = self.socket
            if socket is not None:
                task = asyncio.create_task(self.deliver(socket, message))
                self._tasks.add(task)
                task.add_done_callback(self.completed)
            else:
                self.release(message)


async def run(uri: str, spool: Path) -> None:
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
        client = NetworkClient(uri, registration, Output(sys.stdout.fileno()), spool)
        tasks = [
            asyncio.create_task(client.connections()),
            asyncio.create_task(client.responses(reader)),
            asyncio.create_task(client.expire_inputs()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await client.cleanup()
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
        asyncio.run(run(sys.argv[1], Path(sys.argv[2])))
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Networking worker failed")
        raise SystemExit(1) from None
