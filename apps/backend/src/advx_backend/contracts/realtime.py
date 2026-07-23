from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.contracts.session import SessionSnapshot


class RealtimeProtocolErrorCode(StrEnum):
    INVALID_MESSAGE = "invalid_message"
    AUTHENTICATION_FAILED = "authentication_failed"
    VERSION_MISMATCH = "version_mismatch"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    MESSAGE_TOO_LARGE = "message_too_large"
    UNEXPECTED_MESSAGE = "unexpected_message"


class RealtimeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int = Field(ge=1)


class ClientHello(RealtimeMessage):
    type: Literal["client.hello"] = "client.hello"
    token: str = Field(min_length=1, max_length=256, repr=False)


class ClientPing(RealtimeMessage):
    type: Literal["client.ping"] = "client.ping"
    request_id: str = Field(min_length=1, max_length=128)


ClientMessage = Annotated[ClientHello | ClientPing, Field(discriminator="type")]


class ClientMessageEnvelope(RootModel[ClientMessage]):
    pass


class BackendReady(RealtimeMessage):
    type: Literal["backend.ready"] = "backend.ready"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    session: SessionSnapshot


class BackendPong(RealtimeMessage):
    type: Literal["backend.pong"] = "backend.pong"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str


class SessionStatusEvent(RealtimeMessage):
    type: Literal["session.status"] = "session.status"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    session: SessionSnapshot


class RealtimeProtocolError(RealtimeMessage):
    type: Literal["protocol.error"] = "protocol.error"
    protocol_version: Literal[1] = PROTOCOL_VERSION
    code: RealtimeProtocolErrorCode
    message: str = Field(min_length=1, max_length=256)
    supported_version: int | None = None


ServerMessage = Annotated[
    BackendReady | BackendPong | SessionStatusEvent | RealtimeProtocolError,
    Field(discriminator="type"),
]


class ServerMessageEnvelope(RootModel[ServerMessage]):
    pass
