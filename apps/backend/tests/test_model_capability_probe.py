import json

import httpx
import pytest

from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
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
async def test_image_probe_allows_the_production_output_budget() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4_096
        assert isinstance(payload["messages"][0]["content"], list)
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
