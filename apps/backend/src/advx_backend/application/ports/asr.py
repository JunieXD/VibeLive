from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class AudioSource(StrEnum):
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"


class AudioChunk(BaseModel):
    session_id: str
    source: AudioSource = AudioSource.MICROPHONE
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width_bits: int = Field(gt=0)
    pcm: bytes

    @model_validator(mode="after")
    def validate_time_range(self) -> "AudioChunk":
        if self.ended_at_ms < self.started_at_ms:
            raise ValueError("ended_at_ms must be greater than or equal to started_at_ms")
        return self


class TranscriptSegment(BaseModel):
    session_id: str
    source: AudioSource = AudioSource.MICROPHONE
    text: str
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    final: bool
    utterance_id: str | None = None
    revision: int = Field(default=1, ge=1)


class TranscriptTargetResolution(BaseModel):
    resolver_id: str = Field(min_length=1, max_length=128)
    target_viewer_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_persona_id: str | None = Field(default=None, min_length=1, max_length=128)
    ambiguous: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> "TranscriptTargetResolution":
        targets = sum(
            value is not None
            for value in (self.target_viewer_id, self.target_persona_id)
        )
        if targets > 1:
            raise ValueError("a transcript can target either a Viewer or a Persona")
        if self.ambiguous and targets:
            raise ValueError("an ambiguous transcript target must broadcast")
        return self


class TranscriptTargetResolver(Protocol):
    async def resolve(
        self,
        segment: TranscriptSegment,
    ) -> TranscriptTargetResolution: ...


class AsrProvider(Protocol):
    async def start(self) -> None: ...

    async def push_audio(self, chunk: AudioChunk) -> None: ...

    async def commit(self, source: AudioSource = AudioSource.MICROPHONE) -> None: ...

    async def discard(self, source: AudioSource = AudioSource.MICROPHONE) -> None: ...

    def results(self) -> AsyncIterator[TranscriptSegment]: ...

    async def stop(self) -> None: ...
