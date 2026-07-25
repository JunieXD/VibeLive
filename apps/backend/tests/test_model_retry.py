import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerGenerationRequest,
)
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import ViewerVisualInputMode
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstanceVariant, ViewerPrivateState
from advx_backend.providers.model.provider_rate_gate import ProviderRateGate
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
    OpenAICompatibleViewerRuntimeProvider,
    ViewerRuntimeProtocolError,
    ViewerRuntimeProviderError,
)


class _RecordingRateGate(ProviderRateGate):
    def __init__(self) -> None:
        self.deferred: list[float | None] = []
        self.leases = 0

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[int]:
        self.leases += 1
        yield 0

    async def defer_for_rate_limit(self, retry_after_seconds: float | None) -> float:
        self.deferred.append(retry_after_seconds)
        return retry_after_seconds or 0

    async def record_success(self, observed_generation: int) -> None:
        del observed_generation


def _viewer_request() -> ViewerGenerationRequest:
    return ViewerGenerationRequest(
        room_id="room",
        session_id="session",
        audience_epoch=1,
        observation_id="observation",
        generation_request_id="request",
        viewer_instance_id="viewer",
        viewer_sequence=1,
        username="viewer",
        display_name="Viewer",
        persona=PersonaTemplate(
            persona_id="persona",
            document_version=1,
            revision=1,
            content_hash="1" * 64,
            display_name="Viewer",
            role="viewer",
            silence_bias=0.2,
            burst_bias=0.2,
            repetition_bias=0.2,
            cooldown_ms=0,
        ),
        persona_revision=1,
        presence_revision=1,
        moderation_revision=1,
        behavior_revision=1,
        scene_assessment=SceneAssessment(
            assessment_id="assessment",
            room_id="room",
            session_id="session",
            audience_epoch=1,
            observation_id="observation",
            salience=1,
            novelty=1,
            emotional_intensity=0,
            replyable_event_ids=[],
            maximum_responses=1,
            created_at_ms=1,
            expires_at_ms=10_000,
        ),
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
        viewer_private_state=ViewerPrivateState(),
        room_memory_slice=RoomMemorySlice(room_id="room", memory_revision=0),
        deadline_at_ms=time.time_ns() // 1_000_000 + 60_000,
    )


def _runtime_config() -> OpenAICompatibleViewerRuntimeConfig:
    return OpenAICompatibleViewerRuntimeConfig(
        base_url="https://models.example/v1",
        provider=ProviderRuntimeSpec(
            provider_profile_id="profile",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
        api_key="test-key",
    )


def _viewer_completion(target: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "generation_request_id": "model-request",
                            "viewer_instance_id": "model-viewer",
                            "viewer_sequence": 99,
                            "action": "barrage",
                            "intent": "react_to_scene",
                            "target": target,
                            "texts": ["看到了"],
                            "reaction_type": "reaction",
                            "evidence_refs": [],
                        }
                    )
                },
            }
        ]
    }


def _viewer_completion_with_output(output: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(output)},
            }
        ]
    }


@pytest.mark.asyncio
async def test_viewer_429_updates_the_shared_provider_rate_gate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "1.25"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gate = _RecordingRateGate()
    provider = OpenAICompatibleViewerRuntimeProvider(
        _runtime_config(),
        client=client,
        rate_gate=gate,
    )

    try:
        with pytest.raises(ViewerRuntimeProviderError) as raised:
            await provider.generate(_viewer_request())
    finally:
        await provider.aclose()
        await client.aclose()

    assert raised.value.status_code == 429
    assert raised.value.retryable
    assert raised.value.retry_after_seconds == 1.25
    assert gate.leases == 1
    assert gate.deferred == [1.25]


@pytest.mark.parametrize(
    "invalid_output",
    [
        {
            "action": "barrage",
            "intent": "follow_consensus",
            "target": None,
            "texts": ["确实"],
            "reaction_type": "comment",
            "evidence_refs": [],
        },
        {
            "action": "barrage",
            "intent": "react_to_scene",
            "target": None,
            "texts": ["一", "二", "三", "四"],
            "reaction_type": "comment",
            "evidence_refs": [],
        },
    ],
    ids=["invalid-intent", "too-many-texts"],
)
@pytest.mark.asyncio
async def test_viewer_protocol_violation_repairs_once(
    invalid_output: dict[str, object],
) -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=_viewer_completion_with_output(invalid_output),
                request=request,
            )
        return httpx.Response(
            200,
            json=_viewer_completion(
                {
                    "kind": "scene",
                    "viewer_instance_id": None,
                    "event_id": None,
                }
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            _runtime_config(),
            client=client,
            rate_gate=_RecordingRateGate(),
        )
        result = await provider.generate(_viewer_request())
        await provider.aclose()

    assert result.texts == ["看到了"]
    assert len(requests) == 2
    assert "Validation codes:" in requests[1]["messages"][-1]["content"]
    assert json.dumps(invalid_output) not in requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_viewer_accepts_a_bounded_barrage_batch() -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_viewer_completion_with_output(
                {
                    "action": "barrage",
                    "intent": "react_to_scene",
                    "target": None,
                    "texts": ["第一句", "第二句"],
                    "reaction_type": "comment",
                    "evidence_refs": [],
                }
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            _runtime_config(),
            client=client,
            rate_gate=_RecordingRateGate(),
        )
        result = await provider.generate(_viewer_request())
        await provider.aclose()

    assert result.texts == ["第一句", "第二句"]
    assert "texts must be a JSON array" in captured[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_viewer_protocol_violation_rejects_after_one_failed_repair() -> None:
    calls = 0
    invalid_output = {
        "action": "barrage",
        "intent": "follow_consensus",
        "target": None,
        "texts": ["确实"],
        "reaction_type": "comment",
        "evidence_refs": [],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_viewer_completion_with_output(invalid_output),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            _runtime_config(),
            client=client,
            rate_gate=_RecordingRateGate(),
        )
        with pytest.raises(ViewerRuntimeProtocolError, match="intent:enum"):
            await provider.generate(_viewer_request())
        await provider.aclose()

    assert calls == 2

@pytest.mark.asyncio
async def test_viewer_protocol_violation_skips_repair_when_deadline_is_too_short() -> None:
    calls = 0
    request = _viewer_request().model_copy(update={"deadline_at_ms": 5_999})

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_viewer_completion_with_output(
                {
                    "action": "barrage",
                    "intent": "follow_consensus",
                    "target": None,
                    "texts": ["确实"],
                    "reaction_type": "comment",
                    "evidence_refs": [],
                }
            ),
            request=http_request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            _runtime_config(),
            client=client,
            rate_gate=_RecordingRateGate(),
            clock_ms=lambda: 0,
        )
        with pytest.raises(ViewerRuntimeProtocolError, match="intent:enum"):
            await provider.generate(request)
        await provider.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_viewer_transport_retry_and_repair_share_two_call_budget() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary failure", request=request)
        return httpx.Response(
            200,
            json=_viewer_completion_with_output(
                {
                    "action": "barrage",
                    "intent": "follow_consensus",
                    "target": None,
                    "texts": ["确实"],
                    "reaction_type": "comment",
                    "evidence_refs": [],
                }
            ),
            request=request,
        )

    request = _viewer_request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            _runtime_config(),
            client=client,
            rate_gate=_RecordingRateGate(),
        )
        with pytest.raises(ViewerRuntimeProviderError):
            await provider.generate(request)
        with pytest.raises(ViewerRuntimeProtocolError, match="intent:enum"):
            await provider.generate(request)
        await provider.aclose()

    assert calls == 2
