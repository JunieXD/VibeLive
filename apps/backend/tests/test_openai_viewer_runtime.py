import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest

from advx_backend.application.director_service import DirectorRequest
from advx_backend.application.ports.ingest import ResolvedFrame
from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerGenerationRequest,
)
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.viewer import ViewerInstanceVariant, ViewerPrivateState
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
    OpenAICompatibleViewerRuntimeProvider,
    ViewerRuntimeProtocolError,
    ViewerRuntimeProviderBlockedError,
    ViewerRuntimeProviderError,
)


def provider_spec() -> ProviderRuntimeSpec:
    return ProviderRuntimeSpec(
        provider_profile_id="active-profile",
        director_model="director-model",
        viewer_model="viewer-model",
        memory_model="memory-model",
        visual_summary_model="vision-model",
    )


def viewer_request(
    *,
    request_id: str = "request-1",
    viewer_id: str = "viewer-1",
    sequence: int = 1,
) -> ViewerGenerationRequest:
    return ViewerGenerationRequest(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        generation_request_id=request_id,
        viewer_instance_id=viewer_id,
        viewer_sequence=sequence,
        persona_revision=1,
        instance_variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.2,
            encouragement=0.8,
            meme_affinity=0.4,
            focus="gameplay",
            silence_tendency=0.1,
        ),
        mode_context={"mode": "default"},
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="A player lands a difficult shot.",
        input_event_ids=["event-1"],
        public_context_event_ids=["event-1"],
        viewer_private_state=ViewerPrivateState(),
        room_memory_slice=RoomMemorySlice(
            room_id="room-1",
            memory_revision=0,
        ),
        deadline_at_ms=2_000,
    )


def director_request() -> DirectorRequest:
    return DirectorRequest(
        wave=ObservationWave(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            observation_id="observation-1",
            created_at_ms=1_000,
            deadline_at_ms=2_000,
            triggers=[ObservationTrigger.USER_TEXT],
            event_ids=["event-1"],
        ),
        viewer_ids=("viewer-1", "viewer-2"),
        maximum=1,
        runtime={"config_revision": 1},
    )


def completion(output: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(output)}}]},
    )


def director_frame_index_schemas(
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    response_format = payload["response_format"]
    assert isinstance(response_format, dict)
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    schema = json_schema["schema"]
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    decision_indexes = properties["evidence_frame_indexes"]
    assert isinstance(decision_indexes, dict)
    meme_candidate = properties["meme_candidate"]
    assert isinstance(meme_candidate, dict)
    alternatives = meme_candidate["anyOf"]
    assert isinstance(alternatives, list)
    candidate_schema = alternatives[1]
    assert isinstance(candidate_schema, dict)
    candidate_properties = candidate_schema["properties"]
    assert isinstance(candidate_properties, dict)
    meme_indexes = candidate_properties["evidence_frame_indexes"]
    assert isinstance(meme_indexes, dict)
    return decision_indexes, meme_indexes


@pytest.mark.asyncio
async def test_director_and_viewer_use_their_role_models_and_strict_contracts() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload["model"] == "director-model":
            return completion(
                {
                    "decision_id": "decision-1",
                    "room_id": "room-1",
                    "session_id": "session-1",
                    "audience_epoch": 1,
                    "observation_id": "observation-1",
                    "selected_viewer_ids": ["viewer-1"],
                    "reason_codes": ["direct_mention"],
                    "evidence_event_ids": ["event-1"],
                    "evidence_frame_indexes": [],
                    "decision_source": "director",
                    "created_at_ms": 1_000,
                    "expires_at_ms": 2_000,
                }
            )
        return completion(
            {
                "generation_request_id": "request-1",
                "viewer_instance_id": "viewer-1",
                "viewer_sequence": 1,
                "action": "barrage",
                "text": "Nice shot!",
                "reaction_type": "highlight",
                "evidence_refs": [{"source": "event", "event_id": "event-1", "frame_index": None}],
            }
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    decision = await provider.decide(director_request())
    reaction = await provider.generate(viewer_request())

    assert decision.decision.selected_viewer_ids == ["viewer-1"]
    assert reaction.text == "Nice shot!"
    assert [payload["model"] for payload in payloads] == [
        "director-model",
        "viewer-model",
    ]
    for payload in payloads:
        assert payload["stream"] is False
        assert payload["n"] == 1
        assert payload["max_tokens"] == 4_096
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_director_schema_constrains_frame_indexes_for_each_wave() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return completion(
            {
                "decision_id": "decision-1",
                "room_id": "room-1",
                "session_id": "session-1",
                "audience_epoch": 1,
                "observation_id": "observation-1",
                "selected_viewer_ids": [],
                "reason_codes": [],
                "evidence_event_ids": [],
                "evidence_frame_indexes": [],
                "decision_source": "director",
                "created_at_ms": 1_000,
                "expires_at_ms": 2_000,
                "meme_candidate": None,
            }
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )
    base = director_request()
    bundle = FrameBundle(
        bundle_id="bundle-1",
        settings=FrameBundleSettings(frame_bundle_size=2),
        frames=[
            FrameBundleItem(
                frame_id=f"frame-{index}",
                frame_index=index,
                captured_at_ms=1_000 + index,
                width=640,
                height=360,
                encoding="png",
                content_hash=str(index + 1) * 64,
                data_ref=f"memory://frame-{index}",
            )
            for index in range(2)
        ],
    )
    framed_request = DirectorRequest(
        wave=base.wave.model_copy(update={"frame_bundle": bundle}),
        viewer_ids=base.viewer_ids,
        maximum=base.maximum,
        runtime=base.runtime,
    )

    await provider.decide(base)
    await provider.decide(framed_request)

    zero_frame_schemas = director_frame_index_schemas(payloads[0])
    framed_schemas = director_frame_index_schemas(payloads[1])
    for frame_indexes in zero_frame_schemas:
        assert frame_indexes["maxItems"] == 0
        assert frame_indexes["items"] == {"type": "integer", "minimum": 0}
    for frame_indexes in framed_schemas:
        assert frame_indexes["maxItems"] == 32
        assert frame_indexes["items"] == {
            "type": "integer",
            "minimum": 0,
            "maximum": 1,
        }

    zero_response_format = payloads[0]["response_format"]
    assert isinstance(zero_response_format, dict)
    zero_json_schema = zero_response_format["json_schema"]
    assert isinstance(zero_json_schema, dict)
    assert zero_json_schema["strict"] is True
    zero_schema = zero_json_schema["schema"]
    assert isinstance(zero_schema, dict)
    assert zero_schema["additionalProperties"] is False

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_director_serializes_frozen_dataclass_runtime_context() -> None:
    @dataclass(frozen=True)
    class RuntimeContext:
        config_revision: int
        labels: tuple[str, ...]

    captured_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return completion(
            {
                "decision_id": "decision-1",
                "room_id": "room-1",
                "session_id": "session-1",
                "audience_epoch": 1,
                "observation_id": "observation-1",
                "selected_viewer_ids": [],
                "reason_codes": [],
                "evidence_event_ids": [],
                "evidence_frame_indexes": [],
                "decision_source": "director",
                "created_at_ms": 1_000,
                "expires_at_ms": 2_000,
            }
        )

    request = director_request()
    request = DirectorRequest(
        wave=request.wave,
        viewer_ids=request.viewer_ids,
        maximum=request.maximum,
        runtime=RuntimeContext(config_revision=1, labels=("live",)),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    await provider.decide(request)

    content = json.loads(captured_payload["messages"][1]["content"])
    assert content["runtime"] == {"config_revision": 1, "labels": ["live"]}

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_director_builds_meme_candidate_with_trusted_runtime_scope() -> None:
    @dataclass(frozen=True)
    class Mode:
        mode_id: str
        namespace_id: str

    @dataclass(frozen=True)
    class RuntimeSpec:
        active_mode_id: str
        modes: tuple[Mode, ...]

    @dataclass(frozen=True)
    class RuntimeContext:
        canonical_runtime_spec: RuntimeSpec
        public_context: tuple[object, ...]

    @dataclass(frozen=True)
    class Event:
        event_id: str
        text: str

    def handler(_: httpx.Request) -> httpx.Response:
        return completion(
            {
                "decision_id": "decision-1",
                "room_id": "untrusted-room",
                "session_id": "untrusted-session",
                "audience_epoch": 99,
                "observation_id": "untrusted-observation",
                "selected_viewer_ids": [],
                "reason_codes": ["repeated_phrase"],
                "evidence_event_ids": ["event-1"],
                "evidence_frame_indexes": [],
                "decision_source": "director",
                "created_at_ms": 0,
                "expires_at_ms": 1,
                "meme_candidate": {
                    "text": "沙二关键翻盘",
                    "evidence_event_ids": [],
                    "evidence_frame_indexes": [],
                },
            }
        )

    base = director_request()
    request = DirectorRequest(
        wave=base.wave,
        viewer_ids=base.viewer_ids,
        maximum=base.maximum,
        runtime=RuntimeContext(
            canonical_runtime_spec=RuntimeSpec(
                active_mode_id="mode-1",
                modes=(Mode(mode_id="mode-1", namespace_id="namespace-1"),),
            ),
            public_context=(
                Event(
                    event_id="event-1",
                    text="沙二关键翻盘，沙二关键翻盘，沙二关键翻盘",
                ),
            ),
        ),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    outcome = await provider.decide(request)

    assert outcome.decision.room_id == "room-1"
    assert outcome.meme_candidate is not None
    assert outcome.meme_candidate.room_id == "room-1"
    assert outcome.meme_candidate.session_id == "session-1"
    assert outcome.meme_candidate.audience_epoch == 1
    assert outcome.meme_candidate.namespace_id == "namespace-1"
    assert outcome.meme_candidate.evidence_event_ids == ["event-1"]
    with pytest.raises(
        ViewerRuntimeProtocolError,
        match="referenced an unknown event",
    ):
        provider._meme_candidate(
            request,
            {
                "text": "沙二关键翻盘",
                "evidence_event_ids": ["unknown-event"],
                "evidence_frame_indexes": [],
            },
        )

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_each_viewer_is_an_independent_concurrent_request() -> None:
    active = 0
    maximum_active = 0
    request_ids: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        request_id = context["generation_request_id"]
        viewer_id = context["viewer_instance_id"]
        sequence = context["viewer_sequence"]
        request_ids.append(request_id)
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return completion(
            {
                "generation_request_id": request_id,
                "viewer_instance_id": viewer_id,
                "viewer_sequence": sequence,
                "action": "silence",
                "text": None,
                "reaction_type": "none",
                "evidence_refs": [],
            }
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    first, second = await asyncio.gather(
        provider.generate(viewer_request(request_id="request-1", viewer_id="viewer-1")),
        provider.generate(
            viewer_request(
                request_id="request-2",
                viewer_id="viewer-2",
                sequence=2,
            )
        ),
    )

    assert {first.generation_request_id, second.generation_request_id} == {
        "request-1",
        "request-2",
    }
    assert request_ids == ["request-1", "request-2"]
    assert maximum_active == 2

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_viewer_binds_mismatched_upstream_correlation_to_local_request() -> None:
    secret_body = "upstream-secret-body"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: completion(
                {
                    "generation_request_id": "wrong-request",
                    "viewer_instance_id": "viewer-1",
                    "viewer_sequence": 1,
                    "action": "barrage",
                    "text": secret_body,
                    "reaction_type": "highlight",
                    "evidence_refs": [],
                }
            )
        )
    )
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    response = await provider.generate(viewer_request())

    assert response.generation_request_id == "request-1"
    assert response.viewer_instance_id == "viewer-1"
    assert response.viewer_sequence == 1

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_validation_error_reports_only_safe_field_codes() -> None:
    secret_body = "upstream-secret-body"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: completion(
                {
                    "generation_request_id": "request-1",
                    "viewer_instance_id": "viewer-1",
                    "viewer_sequence": 1,
                    "action": "barrage",
                    "text": secret_body,
                    "reaction_type": "",
                    "evidence_refs": [],
                }
            )
        )
    )
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    with pytest.raises(ViewerRuntimeProtocolError) as caught:
        await provider.generate(viewer_request())

    message = str(caught.value)
    assert "reaction_type:string_too_short" in message
    assert secret_body not in message
    assert secret_body not in repr(caught.value)

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_viewer_canonicalizes_mutually_exclusive_evidence_fields() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: completion(
                {
                    "generation_request_id": "request-1",
                    "viewer_instance_id": "viewer-1",
                    "viewer_sequence": 1,
                    "action": "barrage",
                    "text": "Nice shot!",
                    "reaction_type": "highlight",
                    "evidence_refs": [
                        {
                            "source": "event",
                            "event_id": "event-1",
                            "frame_index": 0,
                        }
                    ],
                }
            )
        )
    )
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    response = await provider.generate(viewer_request())

    assert response.evidence_refs[0].event_id == "event-1"
    assert response.evidence_refs[0].frame_index is None

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_credentials_is_explicitly_blocked_without_network() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
        ),
        client=client,
    )

    with pytest.raises(
        ViewerRuntimeProviderBlockedError,
        match="credentials are not configured",
    ):
        await provider.generate(viewer_request())

    assert requests == 0

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("http_429", 429),
        ("timeout", None),
        ("transport", None),
    ],
)
async def test_viewer_provider_preserves_retryable_upstream_metadata(
    failure: str,
    expected_status: int | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "http_429":
            return httpx.Response(429, request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("unreachable", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    with pytest.raises(ViewerRuntimeProviderError) as caught:
        await provider.generate(viewer_request())

    assert caught.value.retryable is True
    assert caught.value.status_code == expected_status
    assert caught.value.__cause__ is not None

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_viewer_provider_reports_bounded_length_failure_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": ""},
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    with pytest.raises(ViewerRuntimeProtocolError) as caught:
        await provider.generate(viewer_request())

    assert caught.value.finish_reason == "length"
    assert caught.value.token_budget == 4_096
    assert caught.value.retryable is False

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_visual_summary_uses_visual_model_and_resolved_frame_once() -> None:
    bundle = FrameBundle(
        bundle_id="bundle-1",
        settings=FrameBundleSettings(frame_bundle_size=1),
        frames=[
            FrameBundleItem(
                frame_id="frame-1",
                frame_index=0,
                captured_at_ms=1_000,
                width=640,
                height=360,
                encoding="png",
                content_hash="a" * 64,
                data_ref="memory://private-frame-reference",
            )
        ],
    )
    wave = ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        created_at_ms=1_000,
        deadline_at_ms=2_000,
        triggers=[ObservationTrigger.SCREEN_CHANGE],
        frame_bundle=bundle,
    )
    captured_payload: dict[str, object] = {}

    class Resolver:
        async def resolve(self, *, session_id: str, frame) -> ResolvedFrame:
            assert session_id == "session-1"
            assert frame.frame_id == "frame-1"
            return ResolvedFrame(
                session_id=session_id,
                frame_id=frame.frame_id,
                input_id="input-1",
                captured_at_ms=1_000,
                mime_type="image/png",
                body=b"\x00",
            )

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return completion({"summary": "The scene changes to a scoreboard."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
        frame_resolver=Resolver(),
    )

    summary = await provider.summarize(wave, bundle, {"config_revision": 1})

    assert summary == "The scene changes to a scoreboard."
    assert captured_payload["model"] == "vision-model"
    messages = captured_payload["messages"]
    content = messages[1]["content"]
    assert isinstance(content, list)
    assert "private-frame-reference" not in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AA=="},
    }

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_visual_summary_without_resolvable_frames_is_blocked() -> None:
    bundle = FrameBundle(
        bundle_id="bundle-1",
        settings=FrameBundleSettings(frame_bundle_size=1),
        frames=[
            FrameBundleItem(
                frame_id="frame-1",
                frame_index=0,
                captured_at_ms=1_000,
                width=640,
                height=360,
                encoding="png",
                content_hash="a" * 64,
                data_ref="memory://frame-1",
            )
        ],
    )
    wave = ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        created_at_ms=1_000,
        deadline_at_ms=2_000,
        triggers=[ObservationTrigger.SCREEN_CHANGE],
        frame_bundle=bundle,
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("must not call upstream"))
    )
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    with pytest.raises(
        ViewerRuntimeProviderBlockedError,
        match="resolvable FrameBundle",
    ):
        await provider.summarize(wave, bundle, {"config_revision": 1})

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_direct_frame_viewer_request_without_resolved_images_is_blocked() -> None:
    bundle = FrameBundle(
        bundle_id="bundle-1",
        settings=FrameBundleSettings(frame_bundle_size=1),
        frames=[
            FrameBundleItem(
                frame_id="frame-1",
                frame_index=0,
                captured_at_ms=1_000,
                width=640,
                height=360,
                encoding="png",
                content_hash="a" * 64,
                data_ref="memory://frame-1",
            )
        ],
    )
    values = viewer_request().model_dump()
    values.update(
        {
            "visual_input_mode": ViewerVisualInputMode.DIRECT_FRAMES,
            "frame_bundle": bundle,
            "shared_visual_summary": None,
        }
    )
    request = ViewerGenerationRequest.model_validate(values)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("must not call upstream"))
    )
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=provider_spec(),
            api_key="secret",
        ),
        client=client,
    )

    with pytest.raises(
        ViewerRuntimeProviderBlockedError,
        match="direct_frames requires resolvable frames",
    ):
        await provider.generate(request)

    await provider.aclose()
    await client.aclose()
