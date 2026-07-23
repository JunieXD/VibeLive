import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from advx_backend.application.builtin_audiences import BUILTIN_AUDIENCES
from advx_backend.application.frame_store import FrameStoreSessionNotActiveError
from advx_backend.application.ports.asr import AudioChunk, TranscriptSegment
from advx_backend.application.ports.ingest import FrameInput, TextInput
from advx_backend.bootstrap import (
    DATA_DIRECTORY_ENV,
    LOCAL_TOKEN_ENV,
    PipelineConfig,
    build_runtime,
    build_runtime_from_environment,
)
from advx_backend.contracts.generation import GenerationRequest, GenerationResult
from advx_backend.domain.room import RoomEventSource


class RecordingAsrProvider:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.results_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()

    async def start(self) -> None:
        self.started += 1

    async def push_audio(self, chunk: AudioChunk) -> None:
        del chunk

    async def commit(self) -> None:
        pass

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            yield await self.results_queue.get()

    async def stop(self) -> None:
        self.stopped += 1


class RecordingModelProvider:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    async def health(self) -> bool:
        return True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(request_id=request.request_id, candidates=[])

    async def cancel(self, request_id: str) -> None:
        del request_id


def test_runtime_reads_ephemeral_local_token_without_revealing_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    token = "injected-local-token"
    monkeypatch.setenv(LOCAL_TOKEN_ENV, token)
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))

    runtime = build_runtime_from_environment()

    assert runtime.local_token == token
    assert runtime.database.path == tmp_path / "advx.sqlite3"
    assert token not in repr(runtime)


@pytest.mark.asyncio
async def test_runtime_initializes_audiences_and_uses_default_generation_policies(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(local_token="local-token", data_directory=tmp_path)
    model = RecordingModelProvider()
    await runtime.startup()
    try:
        running = await runtime.session_service.start()
        assert running.session_id is not None
        await runtime.room_service.append_event(
            running.session_id,
            source_type=RoomEventSource.USER_TEXT,
            source_id="host",
            text="hello",
        )
        observation = await runtime.context_builder.build(running.session_id)
        snapshot = await runtime.audience_service.get_snapshot(observation=observation)
        generation = runtime.build_generation_service(model_provider=model)

        outputs = await generation.generate(observation)

        assert {context.member.audience_id for context in snapshot.audiences} == {
            template.audience_id for template in BUILTIN_AUDIENCES
        }
        assert len(outputs) == 2
        assert [len(request.audiences) for request in model.requests] == [2, 1]
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_configured_ingest_pipeline_follows_session_lifecycle_and_config(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        local_token="local-token",
        data_directory=tmp_path,
        pipeline_config=PipelineConfig(ingest_max_tracked_input_ids=1),
    )
    asr = RecordingAsrProvider()
    model = RecordingModelProvider()
    ingest = runtime.configure_ingest_pipeline(
        asr_provider=asr,
        model_provider=model,
    )
    await runtime.startup()
    try:
        running = await runtime.session_service.start()
        assert running.session_id is not None
        receipt = await ingest.submit_frame(
            FrameInput(
                session_id=running.session_id,
                input_id="frame-1",
                captured_at_ms=runtime.clock.now_ms(),
                mime_type="image/jpeg",
                body=b"pixels",
            )
        )
        observation = await runtime.context_builder.build(running.session_id)
        frame = observation.frames[0]
        for input_id in ("text-1", "text-2", "text-1"):
            await ingest.submit_text(
                TextInput(
                    session_id=running.session_id,
                    input_id=input_id,
                    created_at_ms=runtime.clock.now_ms(),
                    text=input_id,
                )
            )

        assert asr.started == 1
        assert receipt.input_id == "frame-1"

        await runtime.session_service.stop(running.session_id)

        assert asr.stopped == 1
        with pytest.raises(FrameStoreSessionNotActiveError):
            await runtime.frame_store.resolve(
                session_id=running.session_id,
                frame=frame,
            )
    finally:
        await runtime.shutdown()
