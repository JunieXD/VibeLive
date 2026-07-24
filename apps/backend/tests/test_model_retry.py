import httpx
import pytest

from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProvider,
)
from advx_backend.providers.model.viewer_runtime import ViewerRuntimeProviderError


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
