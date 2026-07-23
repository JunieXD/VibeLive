from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field


class AudioChunk(BaseModel):
    session_id: str
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    pcm: bytes


class TranscriptSegment(BaseModel):
    session_id: str
    text: str
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    final: bool


class AsrProvider(Protocol):
    async def start(self) -> None: ...

    async def push_audio(self, chunk: AudioChunk) -> None: ...

    def results(self) -> AsyncIterator[TranscriptSegment]: ...

    async def stop(self) -> None: ...
