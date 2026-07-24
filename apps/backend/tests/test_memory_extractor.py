import asyncio
import json

import httpx
import pytest

from advx_backend.application.memory_extractor import OpenAICompatibleMemoryExtractor
from advx_backend.application.ports.memory import MemoryEvidence
from advx_backend.contracts.viewer_runtime import ProviderRuntimeSpec
from advx_backend.domain.memory import RoomMemoryType
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
    ViewerRuntimeProtocolError,
    ViewerRuntimeProviderBlockedError,
)


def config(*, api_key: str | None = "secret") -> OpenAICompatibleViewerRuntimeConfig:
    return OpenAICompatibleViewerRuntimeConfig(
        base_url="https://models.example/v1",
        provider=ProviderRuntimeSpec(
            provider_profile_id="active-profile",
            director_model="director-model",
            viewer_model="viewer-model",
            memory_model="memory-model",
            visual_summary_model="vision-model",
        ),
        api_key=api_key,
    )


def evidence(
    event_id: str = "event-1",
    *,
    source_type: str = "user_text",
) -> MemoryEvidence:
    return MemoryEvidence(
        event_id=event_id,
        room_id="room-1",
        source_type=source_type,
        occurred_at_ms=1_000,
        summary="The user says they prefer defensive play.",
    )


def completion(candidates: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps({"candidates": candidates})}}]},
    )


async def extract(
    extractor: OpenAICompatibleMemoryExtractor,
    *,
    events: tuple[MemoryEvidence, ...] = (evidence(),),
):
    return await extractor.extract(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=2,
        events=events,
        current_revision=4,
    )


@pytest.mark.asyncio
async def test_memory_role_returns_verified_deterministic_candidates() -> None:
    captured_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return completion(
            [
                {
                    "memory_type": "user_preference",
                    "content": "The user prefers defensive play.",
                    "evidence_event_ids": ["event-1"],
                    "tags": ["playstyle"],
                    "importance": 0.7,
                    "confidence": 0.8,
                }
            ]
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = OpenAICompatibleMemoryExtractor(config(), client=client)

    first = await extract(extractor)
    second = await extract(extractor)

    assert first == second
    assert len(first) == 1
    candidate = first[0]
    assert candidate.memory_type is RoomMemoryType.USER_PREFERENCE
    assert candidate.room_id == "room-1"
    assert candidate.session_id == "session-1"
    assert candidate.audience_epoch == 2
    assert candidate.base_revision == 4
    assert candidate.evidence_event_ids == ("event-1",)
    assert candidate.origin == "extracted"
    assert captured_payload["model"] == "memory-model"
    assert captured_payload["stream"] is False
    assert captured_payload["response_format"]["json_schema"]["strict"] is True
    context = json.loads(captured_payload["messages"][1]["content"])
    assert context["public_events"][0]["event_id"] == "event-1"

    await extractor.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_zero_memory_candidates_is_valid() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: completion([])))
    extractor = OpenAICompatibleMemoryExtractor(config(), client=client)

    assert await extract(extractor) == ()

    await extractor.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_candidate_cannot_reference_non_public_evidence() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: completion(
                [
                    {
                        "memory_type": "real_world_fact",
                        "content": "An unsupported fact.",
                        "evidence_event_ids": ["private-event"],
                        "tags": [],
                        "importance": 0.5,
                        "confidence": 0.5,
                    }
                ]
            )
        )
    )
    extractor = OpenAICompatibleMemoryExtractor(config(), client=client)

    with pytest.raises(
        ViewerRuntimeProtocolError,
        match="non-public event",
    ):
        await extract(extractor)

    await extractor.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_extraction_uses_its_own_single_concurrency_slot() -> None:
    active = 0
    maximum_active = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return completion([])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = OpenAICompatibleMemoryExtractor(
        config(),
        client=client,
        max_concurrency=1,
    )

    await asyncio.gather(extract(extractor), extract(extractor))

    assert maximum_active == 1

    await extractor.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_extraction_without_credentials_is_blocked_before_network() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = OpenAICompatibleMemoryExtractor(config(api_key=None), client=client)

    with pytest.raises(
        ViewerRuntimeProviderBlockedError,
        match="credentials are not configured",
    ):
        await extract(extractor)

    assert requests == 0

    await extractor.aclose()
    await client.aclose()
