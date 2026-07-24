import hashlib
import json
import time

import httpx
import pytest

from advx_backend.application.ai_call_logging import (
    AiCallLifecycle,
    AiCallScope,
    build_http_response_summary,
    build_openai_request_summary,
)
from advx_backend.application.memory_extractor import OpenAICompatibleMemoryExtractor
from advx_backend.application.ports.memory import MemoryEvidence
from advx_backend.contracts.debug import AiCallRole, AiCallStatus, AiCallTrace
from advx_backend.contracts.viewer_runtime import ProviderRuntimeSpec, ViewerGenerationRequest
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstanceVariant, ViewerPrivateState
from advx_backend.infrastructure.logging.trace_store import assert_redacted_artifact
from advx_backend.providers.asr.stepfun import (
    StepFunAsrConfig,
    StepFunAsrError,
    StepFunAsrProvider,
    _AudioSegment,
)
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
    OpenAICompatibleViewerRuntimeProvider,
    ViewerRuntimeProtocolError,
    ViewerRuntimeProviderBlockedError,
    ViewerRuntimeProviderError,
)


class RecordingSink:
    def __init__(self) -> None:
        self.traces: list[AiCallTrace] = []

    def record_ai_call(self, trace: AiCallTrace) -> None:
        self.traces.append(trace)


def test_openai_summary_keeps_public_context_and_redacts_unsafe_fields() -> None:
    payload = {
        "model": "viewer-model",
        "messages": [
            {
                "role": "system",
                "content": "Return the required JSON. api_key=top-secret",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "public_context": [
                                    {
                                        "event_id": "event-1",
                                        "text": (
                                            "漂亮的一枪 "
                                            "model_api_key=namespaced-inline "
                                            "openaiAccessToken=camel-inline"
                                        ),
                                    }
                                ],
                                "room_memory_slice": {
                                    "room_id": "room-1",
                                    "memory_revision": 3,
                                    "memory_ids": ["memory-1"],
                                    "items": [
                                        {
                                            "memory_id": "memory-1",
                                            "content": "private memory body",
                                        }
                                    ],
                                },
                                "frame_bundle": {
                                    "frames": [
                                        {
                                            "frame_id": "frame-1",
                                            "data_ref": "secret-frame-ref",
                                        }
                                    ]
                                },
                                "provider": {
                                    "model_api_key": "namespaced-secret",
                                    "openaiAccessToken": "camel-secret",
                                },
                                "viewer_private_state": {
                                    "revision": 4,
                                    "published_event_ids": ["event-1"],
                                    "direct_interaction_event_ids": [],
                                    "attention": ["private-attention-body"],
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,ABCDEF"},
                    },
                ],
            },
        ],
        "max_tokens": 512,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "viewer_response", "schema": {}},
        },
    }

    summary = build_openai_request_summary(payload)
    serialized = json.dumps(summary.model_dump(mode="json"), ensure_ascii=False)

    assert summary.schema_name == "viewer_response"
    assert summary.max_output_tokens == 512
    assert "漂亮的一枪" in serialized
    assert "memory-1" in serialized
    assert "private memory body" not in serialized
    assert "secret-frame-ref" not in serialized
    assert "ABCDEF" not in serialized
    assert "top-secret" not in serialized
    assert "namespaced-secret" not in serialized
    assert "camel-secret" not in serialized
    assert "namespaced-inline" not in serialized
    assert "camel-inline" not in serialized
    assert "private-attention-body" not in serialized
    assert "Return the required JSON" not in serialized
    assert summary.input_preview["instruction"]["kind"] == "instruction_ref"
    assert summary.redacted_fields
    assert_redacted_artifact(summary)


def test_lifecycle_records_sent_received_and_parsed_output() -> None:
    sink = RecordingSink()
    lifecycle = AiCallLifecycle(
        sink=sink,
        role=AiCallRole.VIEWER,
        correlation_id="generation-1",
        provider="openai_compatible",
        model_id="viewer-model",
        endpoint="https://user:pass@example.com/v1/chat/completions?secret=yes",
        scope=AiCallScope(
            session_id="session-1",
            generation_request_id="generation-1",
            viewer_instance_id="viewer-1",
        ),
    )
    request = build_openai_request_summary(
        {
            "model": "viewer-model",
            "messages": [
                {"role": "system", "content": "Return JSON"},
                {"role": "user", "content": '{"event_id":"event-1"}'},
            ],
            "max_tokens": 32,
        }
    )
    lifecycle.sent(request)
    response = httpx.Response(
        200,
        headers={"x-request-id": "provider-1"},
        json={
            "id": "completion-1",
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
            },
        },
    )
    lifecycle.received(build_http_response_summary(response, include_model_output=True))
    lifecycle.succeeded({"action": "barrage", "text": "稳"})

    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.SENT,
        AiCallStatus.RECEIVED,
        AiCallStatus.SUCCEEDED,
    ]
    final = sink.traces[-1]
    assert final.endpoint == "https://example.com/v1/chat/completions"
    assert final.response is not None
    assert final.response.provider_request_id == "provider-1"
    assert final.response.total_tokens == 15
    assert final.response.model_output == "{}"
    assert final.response.parsed_output == {"action": "barrage", "text": "稳"}
    assert [event.stage for event in final.timeline][-2:] == ["parsed", "completed"]


def _viewer_request() -> ViewerGenerationRequest:
    assessment = SceneAssessment(
        assessment_id="assessment-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        salience=1,
        novelty=1,
        emotional_intensity=0,
        replyable_event_ids=["event-1"],
        evidence_event_ids=["event-1"],
        maximum_responses=1,
        created_at_ms=1,
        expires_at_ms=10_000,
    )
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="观众",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    return ViewerGenerationRequest(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        generation_request_id="generation-1",
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
        username="viewer-1",
        display_name="观众",
        persona=persona,
        persona_revision=1,
        presence_revision=1,
        moderation_revision=1,
        behavior_revision=1,
        scene_assessment=assessment,
        instance_variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="game",
            silence_tendency=0.5,
        ),
        mode_context={},
        visual_input_mode=ViewerVisualInputMode.TEXT_ONLY,
        input_event_ids=["event-1"],
        viewer_private_state=ViewerPrivateState(),
        room_memory_slice=RoomMemorySlice(room_id="room-1", memory_revision=0),
        deadline_at_ms=time.time_ns() // 1_000_000 + 60_000,
    )


@pytest.mark.asyncio
async def test_viewer_failure_does_not_record_unparsed_model_output() -> None:
    invalid_output = json.dumps(
        {
            "action": "barrage",
            "intent": "react_to_host",
            "target": None,
            "text": "哈喽",
            "reaction_type": "comment",
            "evidence_refs": ["event-1"],
        },
        ensure_ascii=False,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert '"source":"event","event_id":"allowed-event-id"' in payload["messages"][
            0
        ]["content"]
        assert '"source":"frame","frame_index":0' in payload["messages"][0]["content"]
        assert 'never bare IDs or numbers' in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": invalid_output},
                    }
                ]
            },
        )

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            OpenAICompatibleViewerRuntimeConfig(
                base_url="https://example.com/v1",
                provider=ProviderRuntimeSpec(
                    provider_profile_id="profile-1",
                    viewer_model="viewer",
                    memory_model="memory",
                    visual_summary_model="visual",
                ),
                api_key="test-key",
            ),
            client=client,
            ai_call_sink=sink,
        )
        with pytest.raises(ViewerRuntimeProtocolError, match="evidence_refs.0:model_type"):
            await provider.generate(_viewer_request())
        await provider.aclose()

    final = sink.traces[-1]
    assert final.status is AiCallStatus.FAILED
    assert final.response is not None
    assert final.response.model_output is None
    assert final.response.parsed_output is None
    assert_redacted_artifact(final)


@pytest.mark.asyncio
async def test_role_provider_records_blocked_visual_summary_call() -> None:
    sink = RecordingSink()
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://example.com/v1",
            provider=ProviderRuntimeSpec(
                provider_profile_id="profile-1",
                viewer_model="viewer",
                memory_model="memory",
                visual_summary_model="visual",
            ),
            api_key=None,
        ),
        ai_call_sink=sink,
    )
    frame_bundle = FrameBundle(
        bundle_id="bundle-1",
        settings=FrameBundleSettings(frame_bundle_size=1),
        frames=[
            FrameBundleItem(
                frame_id="frame-1",
                frame_index=0,
                captured_at_ms=100,
                width=1280,
                height=720,
                encoding="jpeg",
                content_hash="a" * 64,
                data_ref="frame-ref-1",
            )
        ],
    )
    wave = ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        created_at_ms=100,
        deadline_at_ms=1_000,
        triggers=[ObservationTrigger.SCREEN_CHANGE],
        frame_bundle=frame_bundle,
    )

    try:
        with pytest.raises(ViewerRuntimeProviderBlockedError):
            await provider.summarize(wave, frame_bundle, runtime={})
    finally:
        await provider.aclose()

    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.BLOCKED,
    ]
    assert sink.traces[-1].role is AiCallRole.VISUAL_SUMMARY
    assert sink.traces[-1].observation_id == "observation-1"


@pytest.mark.asyncio
async def test_history_summary_uses_lifecycle_and_returns_parsed_summary() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"summary": "主播完成了残局翻盘"},
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            OpenAICompatibleViewerRuntimeConfig(
                base_url="https://example.com/v1",
                provider=ProviderRuntimeSpec(
                    provider_profile_id="profile-1",
                    viewer_model="viewer",
                    memory_model="memory",
                    visual_summary_model="visual",
                ),
                api_key="secret",
            ),
            client=client,
            ai_call_sink=sink,
        )
        summary = await provider.summarize_history(
            session_id="session-1",
            audience_epoch=1,
            existing_summary=None,
            older_history="主播进入残局并完成翻盘",
        )
        await provider.aclose()

    assert summary == "主播完成了残局翻盘"
    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.SENT,
        AiCallStatus.RECEIVED,
        AiCallStatus.SUCCEEDED,
    ]
    assert sink.traces[-1].role is AiCallRole.HISTORY_SUMMARY
    assert sink.traces[-1].session_id == "session-1"


@pytest.mark.asyncio
async def test_memory_extractor_records_request_and_parsed_candidates() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "Use this shape: {\"candidates\"" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            headers={"x-request-id": "memory-provider-1"},
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "memory_type": "shared_experience",
                                            "content": "主播完成了残局翻盘",
                                            "evidence_event_ids": ["event-1"],
                                            "tags": ["残局"],
                                            "importance": 0.8,
                                            "confidence": 0.9,
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 12,
                    "total_tokens": 32,
                },
            },
        )

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = OpenAICompatibleMemoryExtractor(
            OpenAICompatibleViewerRuntimeConfig(
                base_url="https://example.com/v1",
                provider=ProviderRuntimeSpec(
                    provider_profile_id="profile-1",
                    viewer_model="viewer",
                    memory_model="memory",
                    visual_summary_model="visual",
                ),
                api_key="secret",
            ),
            client=client,
            ai_call_sink=sink,
        )
        candidates = await extractor.extract(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            events=[
                MemoryEvidence(
                    event_id="event-1",
                    room_id="room-1",
                    source_type="barrage",
                    occurred_at_ms=100,
                    summary="主播完成了残局翻盘",
                )
            ],
            current_revision=3,
        )
        await extractor.aclose()

    assert len(candidates) == 1
    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.SENT,
        AiCallStatus.RECEIVED,
        AiCallStatus.SUCCEEDED,
    ]
    final = sink.traces[-1]
    assert final.role is AiCallRole.MEMORY
    assert final.response is not None
    assert final.response.parsed_output == {
        "candidate_count": 1,
        "candidates": [
            {
                "memory_type": "shared_experience",
                "evidence_event_ids": ["event-1"],
                "importance": 0.8,
                "confidence": 0.9,
                "content_ref": {
                    "chars": len("主播完成了残局翻盘"),
                    "sha256": hashlib.sha256(
                        "主播完成了残局翻盘".encode()
                    ).hexdigest(),
                },
            }
        ]
    }
    assert "主播完成了残局翻盘" not in json.dumps(
        final.response.parsed_output,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_memory_extractor_preserves_retryable_http_failure_details() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = OpenAICompatibleMemoryExtractor(
            OpenAICompatibleViewerRuntimeConfig(
                base_url="https://example.com/v1",
                provider=ProviderRuntimeSpec(
                    provider_profile_id="profile-1",
                    viewer_model="viewer",
                    memory_model="memory",
                    visual_summary_model="visual",
                ),
                api_key="secret",
            ),
            client=client,
            ai_call_sink=sink,
        )
        with pytest.raises(ViewerRuntimeProviderError, match="HTTP 503"):
            await extractor.extract(
                room_id="room-1",
                session_id="session-1",
                audience_epoch=1,
                events=[],
                current_revision=3,
            )
        await extractor.aclose()

    final = sink.traces[-1]
    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.SENT,
        AiCallStatus.RECEIVED,
        AiCallStatus.FAILED,
    ]
    assert final.status is AiCallStatus.FAILED
    assert final.response is not None
    assert final.response.http_status == 503
    assert final.error is not None
    assert final.error.http_status == 503
    assert final.error.retryable is True


@pytest.mark.asyncio
async def test_stepfun_asr_records_audio_summary_stream_and_final_text() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.stepfun.com/v1/audio/asr/sse"
        return httpx.Response(
            200,
            headers={"x-request-id": "asr-provider-1"},
            text=(
                'data: {"type":"transcript.text.delta","delta":"你"}\n\n'
                'data: {"type":"transcript.text.done","text":"你好"}\n\n'
            ),
        )

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = StepFunAsrProvider(
            StepFunAsrConfig(api_key="secret"),
            client=client,
            ai_call_sink=sink,
        )
        results = [
            item
            async for item in provider._transcribe(
                _AudioSegment(
                    session_id="session-1",
                    started_at_ms=100,
                    ended_at_ms=400,
                    sample_rate=16_000,
                    channels=1,
                    sample_width_bits=16,
                    pcm=b"\x00\x01" * 160,
                )
            )
        ]

    assert [item.text for item in results] == ["你", "你好"]
    assert results[0].utterance_id == results[1].utterance_id
    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.SENT,
        AiCallStatus.RECEIVED,
        AiCallStatus.STREAMING,
        AiCallStatus.SUCCEEDED,
    ]
    final = sink.traces[-1]
    serialized = json.dumps(final.model_dump(mode="json"), ensure_ascii=False)
    assert "你好" in serialized
    assert "asr-provider-1" in serialized
    assert "audio_ref" in serialized
    assert "AAE" not in serialized
    assert final.request is not None
    assert final.request.wire_bytes is not None
    assert final.request.wire_bytes > len(b"\x00\x01" * 160)
    assert final.request.wire_sha256 != hashlib.sha256(
        b"\x00\x01" * 160
    ).hexdigest()
    assert final.utterance_id == results[-1].utterance_id
    assert_redacted_artifact(final)


@pytest.mark.asyncio
async def test_stepfun_asr_records_received_http_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = StepFunAsrProvider(
            StepFunAsrConfig(api_key="secret"),
            client=client,
            ai_call_sink=sink,
        )
        with pytest.raises(StepFunAsrError, match="HTTP 429"):
            _ = [
                item
                async for item in provider._transcribe(
                    _AudioSegment(
                        session_id="session-1",
                        started_at_ms=100,
                        ended_at_ms=400,
                        sample_rate=16_000,
                        channels=1,
                        sample_width_bits=16,
                        pcm=b"\x00\x01" * 160,
                    )
                )
            ]

    assert [trace.status for trace in sink.traces] == [
        AiCallStatus.PREPARING,
        AiCallStatus.SENT,
        AiCallStatus.RECEIVED,
        AiCallStatus.FAILED,
    ]
    final = sink.traces[-1]
    assert final.response is not None
    assert final.response.http_status == 429
    assert final.error is not None
    assert final.error.http_status == 429
    assert final.error.retryable is True


@pytest.mark.asyncio
async def test_stepfun_asr_retries_one_rate_limit_before_emitting_results() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                request=request,
            )
        return httpx.Response(
            200,
            request=request,
            text='data: {"type":"transcript.text.done","text":"重试成功"}\n\n',
        )

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = StepFunAsrProvider(
            StepFunAsrConfig(
                api_key="secret",
                max_retries=1,
                retry_backoff_seconds=0,
            ),
            client=client,
            ai_call_sink=sink,
        )
        results = [
            item
            async for item in provider._transcribe_with_retry(
                _AudioSegment(
                    session_id="session-1",
                    started_at_ms=100,
                    ended_at_ms=400,
                    sample_rate=16_000,
                    channels=1,
                    sample_width_bits=16,
                    pcm=b"\x00\x01" * 160,
                )
            )
        ]

    terminal = [
        trace
        for trace in sink.traces
        if trace.status in {AiCallStatus.FAILED, AiCallStatus.SUCCEEDED}
    ]
    assert attempts == 2
    assert [item.text for item in results] == ["重试成功"]
    assert [trace.status for trace in terminal] == [
        AiCallStatus.FAILED,
        AiCallStatus.SUCCEEDED,
    ]
    assert terminal[0].utterance_id == terminal[1].utterance_id
    assert terminal[0].call_id != terminal[1].call_id


@pytest.mark.asyncio
async def test_stepfun_asr_retries_transport_failure_before_emitting_results() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection dropped", request=request)
        return httpx.Response(
            200,
            request=request,
            text='data: {"type":"transcript.text.done","text":"连接恢复"}\n\n',
        )

    sink = RecordingSink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = StepFunAsrProvider(
            StepFunAsrConfig(
                api_key="secret",
                max_retries=1,
                retry_backoff_seconds=0,
            ),
            client=client,
            ai_call_sink=sink,
        )
        results = [
            item
            async for item in provider._transcribe_with_retry(
                _AudioSegment(
                    session_id="session-1",
                    started_at_ms=100,
                    ended_at_ms=400,
                    sample_rate=16_000,
                    channels=1,
                    sample_width_bits=16,
                    pcm=b"\x00\x01" * 160,
                )
            )
        ]

    terminal = [
        trace
        for trace in sink.traces
        if trace.status in {AiCallStatus.FAILED, AiCallStatus.SUCCEEDED}
    ]
    assert attempts == 2
    assert [item.text for item in results] == ["连接恢复"]
    assert [trace.status for trace in terminal] == [
        AiCallStatus.FAILED,
        AiCallStatus.SUCCEEDED,
    ]
    assert terminal[0].utterance_id == terminal[1].utterance_id
    assert terminal[0].call_id != terminal[1].call_id


@pytest.mark.asyncio
async def test_stepfun_asr_stops_after_the_configured_rate_limit_retry() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "0"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = StepFunAsrProvider(
            StepFunAsrConfig(
                api_key="secret",
                max_retries=1,
                retry_backoff_seconds=0,
            ),
            client=client,
        )
        with pytest.raises(StepFunAsrError, match="HTTP 429") as raised:
            _ = [
                item
                async for item in provider._transcribe_with_retry(
                    _AudioSegment(
                        session_id="session-1",
                        started_at_ms=100,
                        ended_at_ms=400,
                        sample_rate=16_000,
                        channels=1,
                        sample_width_bits=16,
                        pcm=b"\x00\x01" * 160,
                    )
                )
            ]

    assert attempts == 2
    assert raised.value.retry_after_seconds == 0
    assert raised.value.utterance_id is not None


@pytest.mark.asyncio
async def test_stepfun_asr_does_not_retry_after_streaming_partial_text() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            request=request,
            text=(
                'data: {"type":"transcript.text.delta","delta":"半"}\n\n'
                "data: not-json\n\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = StepFunAsrProvider(
            StepFunAsrConfig(
                api_key="secret",
                max_retries=1,
                retry_backoff_seconds=0,
            ),
            client=client,
        )
        stream = provider._transcribe_with_retry(
            _AudioSegment(
                session_id="session-1",
                started_at_ms=100,
                ended_at_ms=400,
                sample_rate=16_000,
                channels=1,
                sample_width_bits=16,
                pcm=b"\x00\x01" * 160,
            )
        )
        partial = await anext(stream)
        with pytest.raises(StepFunAsrError, match="invalid SSE JSON"):
            await anext(stream)

    assert partial.text == "半"
    assert not partial.final
    assert attempts == 1
