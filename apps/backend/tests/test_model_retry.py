import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from advx_backend.application.memory_extractor import OpenAICompatibleMemoryExtractor
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerGenerationRequest,
)
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import ViewerVisualInputMode
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstanceVariant, ViewerPrivateState
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProvider,
)
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
                            "text": "看到了",
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
async def test_http_429_exposes_retry_after_delta_seconds() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2.5"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://models.example/v1",
            model="viewer",
            api_key="test-key",
        ),
        client=client,
    )

    with pytest.raises(OpenAICompatibleHttpError) as raised:
        await provider._send("POST", "https://models.example/v1/chat/completions", payload={})

    assert raised.value.status_code == 429
    assert raised.value.retry_after_seconds == 2.5
    await client.aclose()


def test_viewer_retry_delay_prefers_retry_after() -> None:
    rate_limit = ViewerRuntimeProviderError(
        "OpenAI-compatible provider returned HTTP 429",
        status_code=429,
        retryable=True,
        retry_after_seconds=1.25,
    )

    assert ViewerRuntime._retry_delay_seconds(rate_limit) == 1.25
    assert ViewerRuntime._retry_delay_seconds(Exception("transient")) == 0.5


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


@pytest.mark.asyncio
async def test_viewer_target_prompt_matches_contract_and_empty_placeholders_normalize() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system_prompt = payload["messages"][0]["content"]

        assert payload["response_format"] == {"type": "json_object"}
        assert "viewer_instance_id and event_id must both be null" in system_prompt
        assert "For a viewer target, provide viewer_instance_id only" in system_prompt
        assert "for an event target, provide event_id only" in system_prompt
        assert "never an empty string, for every absent target ID" in system_prompt
        assert '"target":null,"text":"这波漂亮"' in system_prompt
        return httpx.Response(
            200,
            json=_viewer_completion(
                {
                    "kind": "host",
                    "viewer_instance_id": "",
                    "event_id": "",
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

    assert result.target is not None
    assert result.target.kind == "host"
    assert result.target.viewer_instance_id is None
    assert result.target.event_id is None


@pytest.mark.asyncio
async def test_viewer_target_canonicalizer_does_not_invent_required_event_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_viewer_completion(
                {
                    "kind": "event",
                    "viewer_instance_id": "",
                    "event_id": "",
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
        with pytest.raises(
            ViewerRuntimeProtocolError,
            match="target:value_error",
        ):
            await provider.generate(_viewer_request())
        await provider.aclose()


@pytest.mark.parametrize(
    "invalid_output",
    [
        {
            "action": "barrage",
            "intent": "follow_consensus",
            "target": None,
            "text": "确实",
            "reaction_type": "comment",
            "evidence_refs": [],
        },
        {
            "action": "barrage",
            "intent": "react_to_scene",
            "target": "scene",
            "text": "看到了",
            "reaction_type": "comment",
            "evidence_refs": [],
        },
        {
            "action": "barrage",
            "intent": "react_to_scene",
            "target": {"viewer_instance_id": None, "event_id": None},
            "text": "看到了",
            "reaction_type": "comment",
            "evidence_refs": [],
        },
        {
            "action": "barrage",
            "intent": "react_to_scene",
            "target": {
                "kind": "scene",
                "viewer_instance_id": None,
                "event_id": None,
                "source": "frame",
            },
            "text": "看到了",
            "reaction_type": "comment",
            "evidence_refs": [],
        },
    ],
    ids=[
        "invalid-intent",
        "string-target",
        "missing-target-kind",
        "extra-target-source",
    ],
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

    assert result.text == "看到了"
    assert len(requests) == 2
    assert "Validation codes:" in requests[1]["messages"][-1]["content"]
    assert json.dumps(invalid_output) not in requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_viewer_protocol_violation_rejects_after_one_failed_repair() -> None:
    calls = 0
    invalid_output = {
        "action": "barrage",
        "intent": "follow_consensus",
        "target": None,
        "text": "确实",
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
                    "text": "确实",
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
                    "text": "确实",
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


@pytest.mark.asyncio
async def test_memory_429_updates_the_shared_provider_rate_gate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "2.5"},
            request=request,
        )

    gate = _RecordingRateGate()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = OpenAICompatibleMemoryExtractor(
            _runtime_config(),
            client=client,
            rate_gate=gate,
        )
        with pytest.raises(ViewerRuntimeProviderError) as raised:
            await extractor.extract(
                room_id="room",
                session_id="session",
                audience_epoch=1,
                events=[],
                current_revision=0,
            )
        await extractor.aclose()

    assert raised.value.status_code == 429
    assert raised.value.retry_after_seconds == 2.5
    assert gate.leases == 1
    assert gate.deferred == [2.5]


@pytest.mark.asyncio
async def test_history_summary_429_updates_the_shared_provider_rate_gate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "3"},
            request=request,
        )

    gate = _RecordingRateGate()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            _runtime_config(),
            client=client,
            rate_gate=gate,
        )
        with pytest.raises(ViewerRuntimeProviderError) as raised:
            await provider.summarize_history(
                session_id="session",
                audience_epoch=1,
                existing_summary=None,
                older_history="需要压缩的公开历史",
            )
        await provider.aclose()

    assert raised.value.status_code == 429
    assert raised.value.retry_after_seconds == 3
    assert gate.leases == 1
    assert gate.deferred == [3]
