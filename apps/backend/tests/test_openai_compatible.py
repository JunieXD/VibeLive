import asyncio
import json

import httpx
import pytest

from advx_backend.application.ports.ingest import ResolvedFrame
from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.generation import (
    AudienceContext,
    FrameRef,
    GenerationRequest,
    Observation,
)
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleClosedError,
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProtocolError,
    OpenAICompatibleProvider,
    OpenAICompatibleTimeoutError,
)


def make_request(
    *,
    request_id: str = "request-1",
    audience_id: str = "audience-1",
    frames: list[FrameRef] | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        request_id=request_id,
        observation=Observation(
            session_id="session-1",
            observation_id="observation-1",
            created_at_ms=1_000,
            frames=frames or [],
            user_context={"language": "zh"},
        ),
        audiences=[
            AudienceContext(
                member=AudienceMember(
                    audience_id=audience_id,
                    display_name="Audience",
                    personality={"energy": "calm"},
                ),
            )
        ],
    )


def completion_response(
    candidates: list[dict[str, str]],
    *,
    echoed_request_id: str | None = None,
) -> httpx.Response:
    output: dict[str, object] = {"candidates": candidates}
    if echoed_request_id is not None:
        output["request_id"] = echoed_request_id
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(output)}}]},
    )


@pytest.mark.asyncio
async def test_generate_builds_structured_chat_completion_and_binds_local_request_id() -> None:
    captured_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        captured_payload.update(json.loads(request.content))
        return completion_response(
            [{"audience_id": "audience-1", "text": "hello"}],
            echoed_request_id="untrusted-request-id",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1/",
            model="test-model",
            api_key="secret",
        ),
        client=client,
    )

    result = await provider.generate(make_request())

    assert result.request_id == "request-1"
    assert [(candidate.audience_id, candidate.text) for candidate in result.candidates] == [
        ("audience-1", "hello")
    ]
    assert captured_payload["model"] == "test-model"
    assert captured_payload["stream"] is False
    assert captured_payload["n"] == 1
    response_format = captured_payload["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"] == {
        "name": "barrage_candidates",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["candidates"],
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["audience_id", "text"],
                        "properties": {
                            "audience_id": {"type": "string", "minLength": 1},
                            "text": {"type": "string", "minLength": 1, "maxLength": 200},
                        },
                    },
                },
            },
        },
    }
    messages = captured_payload["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    assert isinstance(user_message["content"], str)
    prompt_context = json.loads(user_message["content"])
    assert prompt_context["observation"] == {
        "session_id": "session-1",
        "observation_id": "observation-1",
        "created_at_ms": 1_000,
        "room_events": [],
        "user_context": {"language": "zh"},
    }
    assert "request_id" not in prompt_context

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_health_uses_configured_model_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/models/test-model"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        ),
        client=client,
    )

    assert await provider.health() is True

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_health_returns_false_for_an_upstream_error() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        ),
        client=client,
    )

    assert await provider.health() is False

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_errors_are_normalized_without_response_or_credential_details() -> None:
    secret = "secret-token"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": f"invalid {secret}"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key=secret,
        ),
        client=client,
    )

    with pytest.raises(OpenAICompatibleHttpError) as caught:
        await provider.generate(make_request())

    assert caught.value.status_code == 429
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_json_and_malformed_candidates_are_protocol_errors() -> None:
    responses = iter(
        [
            httpx.Response(200, content=b"not json"),
            completion_response([{"audience_id": "audience-1", "text": ""}]),
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        ),
        client=client,
    )

    with pytest.raises(OpenAICompatibleProtocolError, match="invalid response JSON"):
        await provider.generate(make_request(request_id="request-invalid-json"))
    with pytest.raises(OpenAICompatibleProtocolError, match="candidate text"):
        await provider.generate(make_request(request_id="request-invalid-candidate"))

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_is_normalized_without_leaking_credentials() -> None:
    secret = "secret-token"

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(secret, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key=secret,
            request_timeout_seconds=0.01,
        ),
        client=client,
    )

    with pytest.raises(OpenAICompatibleTimeoutError) as caught:
        await provider.generate(make_request())

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_frame_resolver_adds_image_parts_without_sending_data_references() -> None:
    class RecordingFrameResolver:
        def __init__(self) -> None:
            self.frame_ids: list[str] = []

        async def resolve(
            self,
            *,
            session_id: str,
            frame: FrameRef,
        ) -> ResolvedFrame | None:
            self.frame_ids.append(frame.frame_id)
            return ResolvedFrame(
                session_id=session_id,
                frame_id=frame.frame_id,
                input_id="frame-input-1",
                captured_at_ms=frame.created_at_ms,
                mime_type=frame.mime_type,
                body=b"\x00",
            )

    resolver = RecordingFrameResolver()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        content = messages[1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert "memory://frame-1" not in content[0]["text"]
        assert content[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AA=="},
        }
        return completion_response([{"audience_id": "audience-1", "text": "seen"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        ),
        client=client,
        frame_resolver=resolver,
    )

    result = await provider.generate(
        make_request(
            frames=[
                FrameRef(
                    frame_id="frame-1",
                    created_at_ms=1_000,
                    mime_type="image/png",
                    data_ref="memory://frame-1",
                )
            ]
        )
    )

    assert resolver.frame_ids == ["frame-1"]
    assert result.candidates[0].text == "seen"

    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_only_interrupts_the_matching_concurrent_request() -> None:
    started = {"audience-cancel": asyncio.Event(), "audience-keep": asyncio.Event()}
    release_keep = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        prompt = json.loads(messages[1]["content"])
        audience_id = prompt["audiences"][0]["member"]["audience_id"]
        started[audience_id].set()
        if audience_id == "audience-cancel":
            await asyncio.Event().wait()
        else:
            await release_keep.wait()
        return completion_response([{"audience_id": audience_id, "text": "complete"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        ),
        client=client,
    )
    cancelled = asyncio.create_task(
        provider.generate(make_request(request_id="request-cancel", audience_id="audience-cancel"))
    )
    kept = asyncio.create_task(
        provider.generate(make_request(request_id="request-keep", audience_id="audience-keep"))
    )

    await asyncio.wait_for(
        asyncio.gather(started["audience-cancel"].wait(), started["audience-keep"].wait()),
        timeout=1,
    )
    await provider.cancel("request-cancel")
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    release_keep.set()
    result = await kept
    assert result.request_id == "request-keep"
    assert result.candidates[0].audience_id == "audience-keep"

    await provider.cancel("missing-request")
    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_aclose_is_idempotent_and_closes_an_owned_client() -> None:
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        )
    )
    client = provider._client

    await provider.aclose()
    await provider.close()

    assert client.is_closed is True
    assert await provider.health() is False
    with pytest.raises(OpenAICompatibleClosedError):
        await provider.generate(make_request())


def test_config_repr_hides_api_key() -> None:
    assert "secret" not in repr(
        OpenAICompatibleConfig(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="secret",
        )
    )
