import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from advx_backend.application.frame_store import (
    DuplicateFrameInputError,
    FrameStoreSessionNotActiveError,
    FrameTooLargeError,
)
from advx_backend.application.ingest_gateway import (
    IngestGateway,
    IngestPipelineUnavailableError,
)
from advx_backend.application.ingest_service import (
    DuplicateIngestInputError,
    IngestCapacityExceededError,
    IngestInputOutOfOrderError,
    IngestSessionNotActiveError,
    UnknownAudioInputError,
    UnsupportedIngestFormatError,
)
from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    IngestReceipt,
    TextInput,
)
from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.session_service import SessionService
from advx_backend.contracts.binary import (
    BinaryEnvelopeError,
    BinaryInputEnvelope,
    BinaryMediaType,
    BinaryPayloadTooLargeError,
    UnsupportedBinaryMediaTypeError,
    UnsupportedBinaryVersionError,
    decode_binary_envelope,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.contracts.realtime import (
    BackendPong,
    BackendReady,
    BarrageEventMessage,
    BarrageSnapshot,
    ClientAudioCommit,
    ClientHello,
    ClientMessage,
    ClientMessageEnvelope,
    ClientPing,
    ClientTextSubmit,
    IngestAck,
    IngestAckStage,
    IngestInputKind,
    IngestRejected,
    IngestRejectionCode,
    RealtimeProtocolError,
    RealtimeProtocolErrorCode,
    SessionStatusEvent,
)
from advx_backend.contracts.session import SessionSnapshot
from advx_backend.domain.barrage import BarrageEvent
from advx_backend.domain.session import SessionStatus
from advx_backend.infrastructure.security.local_token import local_token_matches

HANDSHAKE_TIMEOUT_SECONDS = 5.0
MAX_MESSAGE_BYTES = 16_384
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProtocolViolation(Exception):
    code: RealtimeProtocolErrorCode
    message: str
    close_code: int = 4400


@dataclass(frozen=True)
class IngestViolation(Exception):
    code: IngestRejectionCode
    message: str


def create_realtime_router(
    *,
    session_service: SessionService,
    broker: RealtimeBroker,
    ingest_gateway: IngestGateway,
    local_token: str,
) -> APIRouter:
    router = APIRouter(tags=["realtime"])

    @router.websocket("/ws")
    async def realtime(websocket: WebSocket) -> None:
        await websocket.accept()
        subscription = None
        barrage_subscription = None
        status_sender: asyncio.Task[None] | None = None
        barrage_sender: asyncio.Task[None] | None = None
        send_lock = asyncio.Lock()
        try:
            hello = await _receive_hello(websocket, local_token=local_token)
            if hello is None:
                return

            subscription = await broker.subscribe()
            barrage_subscription = await broker.subscribe_barrages()
            current = await session_service.status()
            await _send_message(
                websocket,
                BackendReady(session=SessionSnapshot.from_domain(current)),
                send_lock=send_lock,
            )
            status_sender = asyncio.create_task(
                _forward_statuses(
                    websocket,
                    subscription=subscription,
                    send_lock=send_lock,
                    after_revision=current.revision,
                ),
                name="realtime-status-sender",
            )
            barrage_sender = asyncio.create_task(
                _forward_barrages(
                    websocket,
                    subscription=barrage_subscription,
                    send_lock=send_lock,
                ),
                name="realtime-barrage-sender",
            )

            while True:
                try:
                    message = await _receive_input(websocket, allow_binary=True)
                except IngestViolation as violation:
                    await _send_ingest_rejection(
                        websocket,
                        violation,
                        send_lock=send_lock,
                    )
                    continue
                except ProtocolViolation as violation:
                    await _send_error(websocket, violation, send_lock=send_lock)
                    if violation.close_code:
                        await websocket.close(code=violation.close_code)
                        return
                else:
                    if isinstance(message, BinaryInputEnvelope):
                        await _handle_ingest(
                            websocket,
                            message,
                            ingest_gateway=ingest_gateway,
                            send_lock=send_lock,
                        )
                        continue
                    if message.protocol_version != PROTOCOL_VERSION:
                        violation = ProtocolViolation(
                            code=RealtimeProtocolErrorCode.VERSION_MISMATCH,
                            message="The requested protocol version is not supported.",
                            close_code=4406,
                        )
                        await _send_error(websocket, violation, send_lock=send_lock)
                        await websocket.close(code=violation.close_code)
                        return
                    if isinstance(message, ClientPing):
                        await _send_message(
                            websocket,
                            BackendPong(request_id=message.request_id),
                            send_lock=send_lock,
                        )
                    elif isinstance(message, (ClientTextSubmit, ClientAudioCommit)):
                        await _handle_ingest(
                            websocket,
                            message,
                            ingest_gateway=ingest_gateway,
                            send_lock=send_lock,
                        )
                    else:
                        violation = ProtocolViolation(
                            code=RealtimeProtocolErrorCode.UNEXPECTED_MESSAGE,
                            message="client.hello is only valid as the first message.",
                        )
                        await _send_error(websocket, violation, send_lock=send_lock)
                        await websocket.close(code=violation.close_code)
                        return
        except WebSocketDisconnect:
            return
        finally:
            if status_sender is not None:
                status_sender.cancel()
                await asyncio.gather(status_sender, return_exceptions=True)
            if barrage_sender is not None:
                barrage_sender.cancel()
                await asyncio.gather(barrage_sender, return_exceptions=True)
            if subscription is not None:
                await broker.unsubscribe(subscription)
            if barrage_subscription is not None:
                await broker.unsubscribe_barrages(barrage_subscription)

    return router


async def _receive_hello(
    websocket: WebSocket,
    *,
    local_token: str,
) -> ClientHello | None:
    try:
        async with asyncio.timeout(HANDSHAKE_TIMEOUT_SECONDS):
            message = await _receive_input(websocket, allow_binary=False)
    except TimeoutError:
        violation = ProtocolViolation(
            code=RealtimeProtocolErrorCode.HANDSHAKE_TIMEOUT,
            message="client.hello was not received before the handshake timeout.",
            close_code=4408,
        )
        await _send_error(websocket, violation)
        await websocket.close(code=violation.close_code)
        return None
    except ProtocolViolation as violation:
        await _send_error(websocket, violation)
        await websocket.close(code=violation.close_code)
        return None
    except WebSocketDisconnect:
        return None

    if not isinstance(message, ClientHello):
        violation = ProtocolViolation(
            code=RealtimeProtocolErrorCode.UNEXPECTED_MESSAGE,
            message="The first realtime message must be client.hello.",
        )
        await _send_error(websocket, violation)
        await websocket.close(code=violation.close_code)
        return None
    if message.protocol_version != PROTOCOL_VERSION:
        violation = ProtocolViolation(
            code=RealtimeProtocolErrorCode.VERSION_MISMATCH,
            message="The requested protocol version is not supported.",
            close_code=4406,
        )
        await _send_error(websocket, violation)
        await websocket.close(code=violation.close_code)
        return None
    if not local_token_matches(local_token, message.token):
        violation = ProtocolViolation(
            code=RealtimeProtocolErrorCode.AUTHENTICATION_FAILED,
            message="The local token is invalid.",
            close_code=4401,
        )
        await _send_error(websocket, violation)
        await websocket.close(code=violation.close_code)
        return None
    return message


async def _receive_input(
    websocket: WebSocket,
    *,
    allow_binary: bool,
) -> ClientMessage | BinaryInputEnvelope:
    payload = await websocket.receive()
    if payload["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(code=payload.get("code", 1000))
    if payload["type"] != "websocket.receive":
        raise ProtocolViolation(
            code=RealtimeProtocolErrorCode.INVALID_MESSAGE,
            message="The realtime message has an invalid frame type.",
        )

    text = payload.get("text")
    if text is not None:
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ProtocolViolation(
                code=RealtimeProtocolErrorCode.MESSAGE_TOO_LARGE,
                message="The realtime message exceeds the allowed size.",
                close_code=1009,
            )
        try:
            return ClientMessageEnvelope.model_validate_json(text).root
        except ValidationError as error:
            raise ProtocolViolation(
                code=RealtimeProtocolErrorCode.INVALID_MESSAGE,
                message="The realtime message does not match the protocol schema.",
            ) from error

    binary = payload.get("bytes")
    if binary is None or not allow_binary:
        raise ProtocolViolation(
            code=RealtimeProtocolErrorCode.INVALID_MESSAGE,
            message="Realtime messages must use a supported text or binary frame.",
        )
    try:
        return decode_binary_envelope(binary)
    except BinaryPayloadTooLargeError as error:
        raise IngestViolation(
            code=IngestRejectionCode.PAYLOAD_TOO_LARGE,
            message="The binary ingest payload exceeds the allowed size.",
        ) from error
    except UnsupportedBinaryVersionError as error:
        raise IngestViolation(
            code=IngestRejectionCode.UNSUPPORTED_BINARY_VERSION,
            message="The binary envelope version is not supported.",
        ) from error
    except UnsupportedBinaryMediaTypeError as error:
        raise IngestViolation(
            code=IngestRejectionCode.UNSUPPORTED_MEDIA_TYPE,
            message="The binary envelope media type is not supported.",
        ) from error
    except BinaryEnvelopeError as error:
        raise IngestViolation(
            code=IngestRejectionCode.MALFORMED_BINARY_ENVELOPE,
            message="The binary ingest envelope is malformed.",
        ) from error


async def _handle_ingest(
    websocket: WebSocket,
    message: ClientTextSubmit | ClientAudioCommit | BinaryInputEnvelope,
    *,
    ingest_gateway: IngestGateway,
    send_lock: asyncio.Lock,
) -> None:
    session_id, input_id, input_kind = _ingest_metadata(message)
    try:
        receipt = await _dispatch_ingest(message, ingest_gateway=ingest_gateway)
    except Exception as error:
        violation = _map_ingest_error(error)
        if violation.code is IngestRejectionCode.PIPELINE_UNAVAILABLE and not isinstance(
            error,
            (IngestPipelineUnavailableError, IngestCapacityExceededError),
        ):
            logger.warning(
                "ingest pipeline rejected an input unexpectedly",
                extra={
                    "session_id": session_id,
                    "input_id": input_id,
                    "input_kind": input_kind.value,
                    "error_type": type(error).__name__,
                },
            )
        await _send_ingest_rejection(
            websocket,
            violation,
            session_id=session_id,
            input_id=input_id,
            input_kind=input_kind,
            send_lock=send_lock,
        )
        return

    await _send_message(
        websocket,
        _acknowledgement(receipt),
        send_lock=send_lock,
    )


async def _dispatch_ingest(
    message: ClientTextSubmit | ClientAudioCommit | BinaryInputEnvelope,
    *,
    ingest_gateway: IngestGateway,
) -> IngestReceipt:
    if isinstance(message, ClientTextSubmit):
        return await ingest_gateway.submit_text(
            TextInput(
                session_id=message.session_id,
                input_id=message.input_id,
                created_at_ms=message.created_at_ms,
                text=message.text,
            )
        )
    if isinstance(message, ClientAudioCommit):
        return await ingest_gateway.commit_audio(
            AudioCommit(
                session_id=message.session_id,
                input_id=message.input_id,
                committed_at_ms=message.committed_at_ms,
            )
        )

    header = message.header
    if header.media_type is BinaryMediaType.AUDIO:
        return await ingest_gateway.submit_audio(
            AudioInput(
                session_id=header.session_id,
                input_id=header.input_id,
                captured_at_ms=header.captured_at_ms,
                format=header.format,
                body=message.body,
            )
        )
    return await ingest_gateway.submit_frame(
        FrameInput(
            session_id=header.session_id,
            input_id=header.input_id,
            captured_at_ms=header.captured_at_ms,
            mime_type=header.format,
            body=message.body,
        )
    )


def _ingest_metadata(
    message: ClientTextSubmit | ClientAudioCommit | BinaryInputEnvelope,
) -> tuple[str, str, IngestInputKind]:
    if isinstance(message, ClientTextSubmit):
        return message.session_id, message.input_id, IngestInputKind.TEXT
    if isinstance(message, ClientAudioCommit):
        return message.session_id, message.input_id, IngestInputKind.AUDIO
    kind = (
        IngestInputKind.AUDIO
        if message.header.media_type is BinaryMediaType.AUDIO
        else IngestInputKind.FRAME
    )
    return message.header.session_id, message.header.input_id, kind


def _acknowledgement(receipt: IngestReceipt) -> IngestAck:
    return IngestAck(
        session_id=receipt.session_id,
        input_id=receipt.input_id,
        input_kind=IngestInputKind(receipt.input_kind.value),
        stage=IngestAckStage(receipt.stage.value),
        accepted_at_ms=receipt.accepted_at_ms,
    )


def _map_ingest_error(error: Exception) -> IngestViolation:
    if isinstance(error, IngestPipelineUnavailableError):
        return IngestViolation(
            IngestRejectionCode.PIPELINE_UNAVAILABLE,
            "The ingest pipeline is not configured.",
        )
    if isinstance(error, IngestCapacityExceededError):
        return IngestViolation(
            IngestRejectionCode.PIPELINE_UNAVAILABLE,
            "The ingest pipeline is busy.",
        )
    if isinstance(error, (IngestSessionNotActiveError, FrameStoreSessionNotActiveError)):
        return IngestViolation(
            IngestRejectionCode.SESSION_NOT_ACTIVE,
            "The target Session is not active.",
        )
    if isinstance(error, (DuplicateIngestInputError, DuplicateFrameInputError)):
        return IngestViolation(
            IngestRejectionCode.DUPLICATE_INPUT,
            "The ingest input was already accepted.",
        )
    if isinstance(error, UnknownAudioInputError):
        return IngestViolation(
            IngestRejectionCode.UNKNOWN_INPUT,
            "The audio input is not pending.",
        )
    if isinstance(error, IngestInputOutOfOrderError):
        return IngestViolation(
            IngestRejectionCode.OUT_OF_ORDER,
            "The ingest input is out of order.",
        )
    if isinstance(error, FrameTooLargeError):
        return IngestViolation(
            IngestRejectionCode.PAYLOAD_TOO_LARGE,
            "The frame payload exceeds the allowed size.",
        )
    if isinstance(error, UnsupportedIngestFormatError):
        return IngestViolation(
            IngestRejectionCode.UNSUPPORTED_FORMAT,
            "The ingest input format is not supported.",
        )
    if isinstance(error, ValueError):
        return IngestViolation(
            IngestRejectionCode.INVALID_INPUT,
            "The ingest input is invalid.",
        )
    return IngestViolation(
        IngestRejectionCode.PIPELINE_UNAVAILABLE,
        "The ingest pipeline could not accept the input.",
    )


async def _send_ingest_rejection(
    websocket: WebSocket,
    violation: IngestViolation,
    *,
    session_id: str | None = None,
    input_id: str | None = None,
    input_kind: IngestInputKind | None = None,
    send_lock: asyncio.Lock | None = None,
) -> None:
    await _send_message(
        websocket,
        IngestRejected(
            code=violation.code,
            message=violation.message,
            session_id=session_id,
            input_id=input_id,
            input_kind=input_kind,
        ),
        send_lock=send_lock,
    )


async def _send_error(
    websocket: WebSocket,
    violation: ProtocolViolation,
    *,
    send_lock: asyncio.Lock | None = None,
) -> None:
    await _send_message(
        websocket,
        RealtimeProtocolError(
            code=violation.code,
            message=violation.message,
            supported_version=(
                PROTOCOL_VERSION
                if violation.code is RealtimeProtocolErrorCode.VERSION_MISMATCH
                else None
            ),
        ),
        send_lock=send_lock,
    )


async def _forward_statuses(
    websocket: WebSocket,
    *,
    subscription: asyncio.Queue[SessionStatus],
    send_lock: asyncio.Lock,
    after_revision: int,
) -> None:
    last_revision = after_revision
    while True:
        status = await subscription.get()
        if status.revision <= last_revision:
            continue
        await _send_message(
            websocket,
            SessionStatusEvent(session=SessionSnapshot.from_domain(status)),
            send_lock=send_lock,
        )
        last_revision = status.revision


async def _forward_barrages(
    websocket: WebSocket,
    *,
    subscription: asyncio.Queue[BarrageEvent],
    send_lock: asyncio.Lock,
) -> None:
    while True:
        event = await subscription.get()
        await _send_message(
            websocket,
            BarrageEventMessage(barrage=BarrageSnapshot.from_domain(event)),
            send_lock=send_lock,
        )


async def _send_message(
    websocket: WebSocket,
    message: (
        BackendReady
        | BackendPong
        | SessionStatusEvent
        | BarrageEventMessage
        | RealtimeProtocolError
        | IngestAck
        | IngestRejected
    ),
    *,
    send_lock: asyncio.Lock | None = None,
) -> None:
    if send_lock is None:
        await websocket.send_json(message.model_dump(mode="json"))
        return
    async with send_lock:
        await websocket.send_json(message.model_dump(mode="json"))
