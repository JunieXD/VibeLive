import asyncio
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.session_service import SessionService
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.contracts.realtime import (
    BackendPong,
    BackendReady,
    BarrageEventMessage,
    BarrageSnapshot,
    ClientHello,
    ClientMessage,
    ClientMessageEnvelope,
    ClientPing,
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


@dataclass(frozen=True)
class ProtocolViolation(Exception):
    code: RealtimeProtocolErrorCode
    message: str
    close_code: int = 4400


def create_realtime_router(
    *,
    session_service: SessionService,
    broker: RealtimeBroker,
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
                    message = await _receive_message(websocket)
                except ProtocolViolation as violation:
                    await _send_error(websocket, violation, send_lock=send_lock)
                    if violation.close_code:
                        await websocket.close(code=violation.close_code)
                        return
                else:
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
            message = await _receive_message(websocket)
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


async def _receive_message(websocket: WebSocket) -> ClientMessage:
    payload = await websocket.receive()
    if payload["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(code=payload.get("code", 1000))
    if payload["type"] != "websocket.receive" or payload.get("text") is None:
        raise ProtocolViolation(
            code=RealtimeProtocolErrorCode.INVALID_MESSAGE,
            message="Realtime messages must be JSON text frames.",
        )

    text = payload["text"]
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
    ),
    *,
    send_lock: asyncio.Lock | None = None,
) -> None:
    if send_lock is None:
        await websocket.send_json(message.model_dump(mode="json"))
        return
    async with send_lock:
        await websocket.send_json(message.model_dump(mode="json"))
