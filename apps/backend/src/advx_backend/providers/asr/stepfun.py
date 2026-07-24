import asyncio
import base64
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final

import httpx

from advx_backend.application.ai_call_logging import (
    AiCallLifecycle,
    AiCallScope,
    AiCallSink,
    build_audio_request_summary,
    build_http_response_summary,
)
from advx_backend.application.ports.asr import AudioChunk, TranscriptSegment
from advx_backend.contracts.debug import AiCallRole


class StepFunAsrError(RuntimeError):
    """Normalized failure returned by the StepFun ASR adapter."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class StepFunAsrConfig:
    api_key: str = field(repr=False)
    base_url: str = "https://api.stepfun.com/step_plan/v1"
    model: str = "stepaudio-2.5-asr"
    language: str = "zh"
    enable_itn: bool = True
    enable_timestamp: bool = True
    request_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("StepFun API key is required")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request timeout must be greater than zero")


@dataclass(frozen=True)
class _AudioSegment:
    session_id: str
    started_at_ms: int
    ended_at_ms: int
    sample_rate: int
    channels: int
    sample_width_bits: int
    pcm: bytes


class _EndOfResults:
    pass


_END_OF_RESULTS: Final = _EndOfResults()
_ResultItem = TranscriptSegment | Exception | _EndOfResults


class StepFunAsrProvider:
    """Step Plan HTTP + SSE ASR adapter.

    Audio chunks are buffered until ``commit`` marks an utterance boundary.
    Committed segments are processed in order so room events cannot be reordered
    by requests that finish at different times.
    """

    def __init__(
        self,
        config: StepFunAsrConfig,
        *,
        client: httpx.AsyncClient | None = None,
        ai_call_sink: AiCallSink | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._ai_call_sink = ai_call_sink
        self._owns_client = client is None
        self._segments: asyncio.Queue[_AudioSegment] = asyncio.Queue()
        self._results: asyncio.Queue[_ResultItem] = asyncio.Queue()
        self._buffer: list[AudioChunk] = []
        self._worker: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._segments = asyncio.Queue()
        self._results = asyncio.Queue()
        self._buffer.clear()
        if self._client is None:
            self._client = httpx.AsyncClient()
        self._started = True
        self._worker = asyncio.create_task(self._run(), name="stepfun-asr")

    async def push_audio(self, chunk: AudioChunk) -> None:
        self._ensure_started()
        self._validate_chunk(chunk)
        if self._buffer:
            first = self._buffer[0]
            if chunk.session_id != first.session_id:
                raise ValueError("cannot mix sessions in one ASR segment")
            if (
                chunk.sample_rate,
                chunk.channels,
                chunk.sample_width_bits,
            ) != (
                first.sample_rate,
                first.channels,
                first.sample_width_bits,
            ):
                raise ValueError("cannot change audio format within an ASR segment")
            if chunk.started_at_ms < self._buffer[-1].started_at_ms:
                raise ValueError("audio chunks must be ordered by capture time")
        self._buffer.append(chunk)

    async def commit(self) -> None:
        self._ensure_started()
        if not self._buffer:
            return

        first = self._buffer[0]
        last = self._buffer[-1]
        segment = _AudioSegment(
            session_id=first.session_id,
            started_at_ms=first.started_at_ms,
            ended_at_ms=last.ended_at_ms,
            sample_rate=first.sample_rate,
            channels=first.channels,
            sample_width_bits=first.sample_width_bits,
            pcm=b"".join(chunk.pcm for chunk in self._buffer),
        )
        self._buffer.clear()
        await self._segments.put(segment)

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            item = await self._results.get()
            if item is _END_OF_RESULTS:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._buffer.clear()

        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

        await self._results.put(_END_OF_RESULTS)

    async def _run(self) -> None:
        while True:
            segment = await self._segments.get()
            try:
                async for result in self._transcribe(segment):
                    await self._results.put(result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc if isinstance(exc, StepFunAsrError) else StepFunAsrError(str(exc))
                await self._results.put(error)
            finally:
                self._segments.task_done()

    async def _transcribe(self, segment: _AudioSegment) -> AsyncIterator[TranscriptSegment]:
        assert self._client is not None
        url = f"{self.config.base_url.rstrip('/')}/audio/asr/sse"
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "audio": {
                "data": base64.b64encode(segment.pcm).decode("ascii"),
                "input": {
                    "transcription": {
                        "model": self.config.model,
                        "language": self.config.language,
                        "enable_itn": self.config.enable_itn,
                        "enable_timestamp": self.config.enable_timestamp,
                    },
                    "format": {
                        "type": "pcm",
                        "codec": "pcm_s16le",
                        "rate": segment.sample_rate,
                        "bits": segment.sample_width_bits,
                        "channel": segment.channels,
                    },
                },
            }
        }
        wire_body = httpx.Request("POST", url, json=payload).content
        pcm_digest = hashlib.sha256(segment.pcm).hexdigest()
        utterance_id = (
            f"asr-{segment.started_at_ms}-{segment.ended_at_ms}-{pcm_digest[:16]}"
        )
        lifecycle = AiCallLifecycle(
            sink=self._ai_call_sink,
            role=AiCallRole.ASR,
            correlation_id=utterance_id,
            provider="stepfun",
            model_id=self.config.model,
            endpoint=url,
            scope=AiCallScope(
                session_id=segment.session_id,
                utterance_id=utterance_id,
            ),
        )

        try:
            received_done = False
            final_text = ""
            partial_count = 0
            lifecycle.sent(
                build_audio_request_summary(
                    pcm=segment.pcm,
                    wire_body=wire_body,
                    started_at_ms=segment.started_at_ms,
                    ended_at_ms=segment.ended_at_ms,
                    sample_rate=segment.sample_rate,
                    channels=segment.channels,
                    sample_width_bits=segment.sample_width_bits,
                    language=self.config.language,
                )
            )
            async with self._client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=self.config.request_timeout_seconds,
            ) as response:
                lifecycle.received(
                    build_http_response_summary(
                        response,
                        include_body_digest=False,
                    )
                )
                response.raise_for_status()
                async for line in response.aiter_lines():
                    event = self._parse_sse_line(line)
                    if event is None:
                        continue
                    event_type = event.get("type")
                    if event_type == "transcript.text.delta":
                        text = event.get("delta")
                        if isinstance(text, str) and text:
                            partial_count += 1
                            if partial_count == 1:
                                lifecycle.streaming(
                                    {
                                        "partial_text": text,
                                        "partial_count": partial_count,
                                    },
                                    detail={"event_type": event_type},
                                )
                            yield TranscriptSegment(
                                session_id=segment.session_id,
                                text=text,
                                started_at_ms=self._event_time(
                                    event,
                                    "start_time",
                                    segment.started_at_ms,
                                    segment.started_at_ms,
                                ),
                                ended_at_ms=self._event_time(
                                    event,
                                    "end_time",
                                    segment.started_at_ms,
                                    segment.ended_at_ms,
                                ),
                                final=False,
                                utterance_id=utterance_id,
                            )
                    elif event_type == "transcript.text.done":
                        text = event.get("text")
                        if not isinstance(text, str):
                            raise StepFunAsrError("StepFun ASR returned a done event without text")
                        yield TranscriptSegment(
                            session_id=segment.session_id,
                            text=text,
                            started_at_ms=segment.started_at_ms,
                            ended_at_ms=segment.ended_at_ms,
                            final=True,
                            utterance_id=utterance_id,
                        )
                        final_text = text
                        received_done = True
                    elif event_type == "error":
                        message = event.get("message")
                        raise StepFunAsrError(
                            message if isinstance(message, str) else "StepFun ASR request failed"
                        )
            if not received_done:
                raise StepFunAsrError("StepFun ASR stream ended without a final transcript")
            lifecycle.succeeded(
                {
                    "final": True,
                    "text": final_text,
                    "partial_count": partial_count,
                }
            )
        except asyncio.CancelledError:
            lifecycle.cancelled()
            raise
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error = StepFunAsrError(
                f"StepFun ASR returned HTTP {status_code}",
                status_code=status_code,
                retryable=(
                    status_code in {408, 429}
                    or 500 <= status_code <= 599
                ),
            )
            lifecycle.failed(error)
            raise error from exc
        except httpx.HTTPError as exc:
            error = StepFunAsrError(
                "StepFun ASR transport failed",
                retryable=True,
            )
            lifecycle.failed(error)
            raise error from exc
        except Exception as error:
            lifecycle.failed(error)
            raise

    @staticmethod
    def _parse_sse_line(line: str) -> dict[str, object] | None:
        if not line.startswith("data:"):
            return None
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            return None
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise StepFunAsrError("StepFun ASR returned invalid SSE JSON") from exc
        if not isinstance(event, dict):
            raise StepFunAsrError("StepFun ASR returned a non-object SSE event")
        return event

    @staticmethod
    def _event_time(
        event: dict[str, object],
        key: str,
        segment_offset_ms: int,
        fallback: int,
    ) -> int:
        value = event.get(key)
        if isinstance(value, int) and value >= 0:
            return segment_offset_ms + value
        return fallback

    @staticmethod
    def _validate_chunk(chunk: AudioChunk) -> None:
        if chunk.sample_rate != 16_000:
            raise ValueError("StepFun ASR requires 16000 Hz PCM audio")
        if chunk.channels != 1:
            raise ValueError("StepFun ASR requires mono PCM audio")
        if chunk.sample_width_bits != 16:
            raise ValueError("StepFun ASR requires 16-bit PCM audio")

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("ASR provider is not started")
