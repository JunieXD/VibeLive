from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class AudioChunk(BaseModel):
    session_id: str
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
    text: str
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    final: bool


class AsrProvider(Protocol):
    async def start(self) -> None: ...

    async def push_audio(self, chunk: AudioChunk) -> None: ...

    async def commit(self) -> None: ...

    def results(self) -> AsyncIterator[TranscriptSegment]: ...

    async def stop(self) -> None: ...
