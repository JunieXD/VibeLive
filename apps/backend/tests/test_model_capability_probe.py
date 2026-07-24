import json

import httpx
import pytest

from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    default_reasoning_options,
)
from advx_backend.providers.model.viewer_runtime import (
    _VIEWER_BARRAGE_JSON_EXAMPLE,
    _VIEWER_SILENCE_JSON_EXAMPLE,
    OpenAICompatibleViewerRuntimeConfig,
    OpenAICompatibleViewerRuntimeProvider,
)


def provider_with(client: httpx.AsyncClient) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://models.example/v1",
            model="vision-model",
            api_key="test-key",
        ),
        client=client,
    )


@pytest.mark.asyncio
async def test_role_payload_uses_json_examples_and_stepfun_low_reasoning() -> None:
    async with httpx.AsyncClient() as client:
        provider = OpenAICompatibleViewerRuntimeProvider(
            OpenAICompatibleViewerRuntimeConfig(
                base_url="https://api.stepfun.com/step_plan/v1",
                provider=ProviderRuntimeSpec(
                    provider_profile_id="default",
                    viewer_model="step-3.7-flash",
                    memory_model="step-3.7-flash",
                    visual_summary_model="step-3.7-flash",
                ),
                api_key="test-key",
            ),
            client=client,
        )
        payload = provider._json_payload(
            model_id="step-3.7-flash",
            system_prompt='Return exactly one JSON object. Use this shape: {"summary":"text"}',
            content="{}",
        )
        await provider.aclose()

    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "low"
    assert payload["messages"] == [
        {
            "role": "system",
            "content": 'Return exactly one JSON object. Use this shape: {"summary":"text"}',
        },
        {"role": "user", "content": "{}"},
    ]


def test_stepfun_flash_defaults_to_low_reasoning_effort() -> None:
    assert default_reasoning_options(
        "https://api.stepfun.com/step_plan/v1",
        "step-3.7-flash",
    ) == {"reasoning_effort": "low"}
    assert default_reasoning_options(
        "https://api.stepfun.com/step_plan/v1",
        "step-router-v1",
    ) == {}


def test_viewer_json_examples_satisfy_the_runtime_contract() -> None:
    barrage = ViewerGenerationResponse.model_validate(json.loads(_VIEWER_BARRAGE_JSON_EXAMPLE))
    silence = ViewerGenerationResponse.model_validate(json.loads(_VIEWER_SILENCE_JSON_EXAMPLE))

    assert barrage.action is ViewerAction.BARRAGE
    assert barrage.target is None
    assert barrage.text == "这波漂亮"
    assert silence.action is ViewerAction.SILENCE
    assert silence.target is None
    assert silence.text is None
    assert default_reasoning_options(
        "https://models.example/v1",
        "step-3.7-flash",
    ) == {}


@pytest.mark.asyncio
async def test_image_probe_allows_the_production_output_budget() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4_096
        assert isinstance(payload["messages"][0]["content"], list)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][0]["content"][0]["text"] == (
            'Return exactly this JSON object and no Markdown or prose: {"ok":true}.'
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"ok":true}',
                            "reasoning": "The image was accepted before the JSON response.",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = provider_with(client)

    check = await provider._probe_chat(
        capability="image_input",
        model_id="vision-model",
        include_image=True,
    )

    assert check.status.value == "passed"
    assert check.error_code is None
    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_probe_falls_back_when_json_mode_is_explicitly_unsupported() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if "response_format" in payload:
            return httpx.Response(
                400,
                json={"error": {"message": "response_format json_object is unsupported"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = provider_with(client)

    check = await provider._probe_chat(capability="viewer_json_output", model_id="vision-model")

    assert check.status.value == "passed"
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]
    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_probe_reports_output_token_exhaustion_without_reading_reasoning() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning": "untrusted upstream reasoning",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = provider_with(client)

    check = await provider._probe_chat(
        capability="image_input",
        model_id="vision-model",
        include_image=True,
    )

    assert check.status.value == "failed"
    assert check.error_code == "output_token_limit"
    await provider.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_capability_probe_only_checks_active_model_roles() -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "viewer-model"}]})
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = provider_with(client)

    result = await provider.probe_capabilities(
        role_models={
            "viewer": "viewer-model",
            "memory": "memory-model",
            "visual_summary": "vision-model",
        }
    )

    assert result.status.value == "passed"
    assert "director_json_output" not in {
        check.capability for check in result.checks
    }
    assert requested_models.count("viewer-model") == 3
    assert requested_models.count("memory-model") == 1
    assert requested_models.count("vision-model") == 1
    await provider.aclose()
    await client.aclose()
