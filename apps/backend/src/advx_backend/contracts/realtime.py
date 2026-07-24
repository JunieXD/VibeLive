from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from advx_backend.application.ports.asr import AudioSource
from advx_backend.contracts.audience import ViewerPresenceEvent
from advx_backend.contracts.session import SessionSnapshot
from advx_backend.contracts.viewer_runtime import ViewerBarrageEvent
from advx_backend.domain.barrage import BarrageEvent

REALTIME_PROTOCOL_VERSION = 4
SUPPORTED_REALTIME_PROTOCOL_VERSIONS = (3, 4)
RealtimeProtocolVersion = Literal[3, 4]


class RealtimeProtocolErrorCode(StrEnum):
    INVALID_MESSAGE = "invalid_message"
    AUTHENTICATION_FAILED = "authentication_failed"
    VERSION_MISMATCH = "version_mismatch"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    MESSAGE_TOO_LARGE = "message_too_large"
    UNEXPECTED_MESSAGE = "unexpected_message"


class IngestInputKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    FRAME = "frame"


class IngestAckStage(StrEnum):
    RECEIVED = "received"
    COMMITTED = "committed"


class IngestRejectionCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    SESSION_NOT_ACTIVE = "session_not_active"
    DUPLICATE_INPUT = "duplicate_input"
    UNKNOWN_INPUT = "unknown_input"
    OUT_OF_ORDER = "out_of_order"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNSUPPORTED_BINARY_VERSION = "unsupported_binary_version"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    MALFORMED_BINARY_ENVELOPE = "malformed_binary_envelope"
    PIPELINE_UNAVAILABLE = "pipeline_unavailable"


MAX_INGEST_IDENTIFIER_LENGTH = 128
MAX_TEXT_INPUT_LENGTH = 4_000


class RealtimeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int = Field(ge=1)


class ClientHello(RealtimeMessage):
    type: Literal["client.hello"] = "client.hello"
    token: str = Field(min_length=1, max_length=256, repr=False)
    supported_protocol_versions: list[int] | None = Field(
        default=None,
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_supported_versions(self) -> "ClientHello":
        versions = self.supported_protocol_versions
        if versions is not None:
            if any(
                isinstance(version, bool) or version < 1
                for version in versions
            ):
                raise ValueError("supported protocol versions must be positive integers")
            if len(set(versions)) != len(versions):
                raise ValueError("supported protocol versions must not contain duplicates")
            if self.protocol_version not in versions:
                raise ValueError("protocol_version must be included in supported versions")
        return self


class ClientPing(RealtimeMessage):
    type: Literal["client.ping"] = "client.ping"
    request_id: str = Field(min_length=1, max_length=128)


class ClientTextSubmit(RealtimeMessage):
    type: Literal["client.text.submit"] = "client.text.submit"
    session_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    input_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    created_at_ms: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=MAX_TEXT_INPUT_LENGTH, repr=False)
    target_viewer_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INGEST_IDENTIFIER_LENGTH,
    )
    target_persona_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INGEST_IDENTIFIER_LENGTH,
    )

    @model_validator(mode="after")
    def validate_target(self) -> "ClientTextSubmit":
        if self.target_viewer_id is not None and self.target_persona_id is not None:
            raise ValueError("text input can target either a Viewer or a Persona")
        return self


class ClientAudioCommit(RealtimeMessage):
    type: Literal["client.audio.commit"] = "client.audio.commit"
    session_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    input_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    committed_at_ms: int = Field(ge=0)
    source: AudioSource = AudioSource.MICROPHONE
    turn_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INGEST_IDENTIFIER_LENGTH,
    )
    system_audio_required: bool = False

    @model_validator(mode="after")
    def validate_turn_requirements(self) -> "ClientAudioCommit":
        if self.source is not AudioSource.MICROPHONE and self.system_audio_required:
            raise ValueError("only microphone audio can require system audio")
        if self.system_audio_required and self.turn_id is None:
            raise ValueError("system audio requirements need a turn_id")
        return self


class ClientVoiceActivity(RealtimeMessage):
    """Signals that the host resumed speaking before an utterance is final."""

    type: Literal["client.voice.activity"] = "client.voice.activity"
    session_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    occurred_at_ms: int = Field(ge=0)
    source: AudioSource = AudioSource.MICROPHONE


ClientMessage = Annotated[
    ClientHello | ClientPing | ClientTextSubmit | ClientAudioCommit | ClientVoiceActivity,
    Field(discriminator="type"),
]


class ClientMessageEnvelope(RootModel[ClientMessage]):
    pass


class BackendReady(RealtimeMessage):
    type: Literal["backend.ready"] = "backend.ready"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    session: SessionSnapshot


class BackendPong(RealtimeMessage):
    type: Literal["backend.pong"] = "backend.pong"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    request_id: str


class SessionStatusEvent(RealtimeMessage):
    type: Literal["session.status"] = "session.status"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    session: SessionSnapshot


class BarrageSnapshot(ViewerBarrageEvent):

    @classmethod
    def from_domain(cls, event: BarrageEvent) -> "BarrageSnapshot":
        return cls(
            barrage_id=event.barrage_id,
            room_id=event.room_id,
            session_id=event.session_id,
            audience_epoch=event.audience_epoch,
            observation_id=event.observation_id,
            generation_request_id=event.generation_request_id,
            viewer_instance_id=event.viewer_instance_id,
            persona_id=event.persona_id,
            display_name=event.display_name,
            viewer_sequence=event.viewer_sequence,
            reaction_type=event.reaction_type,
            intent=event.intent,
            target=(
                None
                if event.target_kind is None
                else {
                    "kind": event.target_kind,
                    "viewer_instance_id": event.target_viewer_instance_id,
                    "event_id": event.target_event_id,
                }
            ),
            evidence_refs=[
                {
                    "source": reference.source.value,
                    "event_id": reference.event_id,
                    "frame_index": reference.frame_index,
                }
                for reference in event.evidence_refs
            ],
            text=event.text,
            created_at_ms=event.created_at_ms,
            expires_at_ms=event.expires_at_ms,
        )


class BarrageEventMessage(RealtimeMessage):
    type: Literal["barrage.event"] = "barrage.event"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    barrage: BarrageSnapshot


class RealtimeProtocolError(RealtimeMessage):
    type: Literal["protocol.error"] = "protocol.error"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    code: RealtimeProtocolErrorCode
    message: str = Field(min_length=1, max_length=256)
    supported_version: int | None = None


class IngestAck(RealtimeMessage):
    type: Literal["ingest.ack"] = "ingest.ack"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    session_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    input_id: str = Field(min_length=1, max_length=MAX_INGEST_IDENTIFIER_LENGTH)
    input_kind: IngestInputKind
    stage: IngestAckStage
    accepted_at_ms: int = Field(ge=0)


class IngestRejected(RealtimeMessage):
    type: Literal["ingest.rejected"] = "ingest.rejected"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    code: IngestRejectionCode
    message: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INGEST_IDENTIFIER_LENGTH,
    )
    input_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INGEST_IDENTIFIER_LENGTH,
    )
    input_kind: IngestInputKind | None = None


class AsrTranscriptEvent(RealtimeMessage):
    type: Literal["asr.transcript"] = "asr.transcript"
    protocol_version: RealtimeProtocolVersion = REALTIME_PROTOCOL_VERSION
    source: AudioSource
    text: str = Field(min_length=1, max_length=MAX_TEXT_INPUT_LENGTH)
    final: bool
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    utterance_id: str | None = None
    revision: int = Field(ge=1)


ServerMessage = Annotated[
    BackendReady
    | BackendPong
    | SessionStatusEvent
    | BarrageEventMessage
    | RealtimeProtocolError
    | IngestAck
    | IngestRejected
    | AsrTranscriptEvent
    | ViewerPresenceEvent,
    Field(discriminator="type"),
]


class ServerMessageEnvelope(RootModel[ServerMessage]):
    pass
