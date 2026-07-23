import json

import httpx
import pytest

from advx_backend.providers.asr import (
    AudioChunk,
    StepFunAsrConfig,
    StepFunAsrError,
    StepFunAsrProvider,
)


def audio_chunk(*, sample_rate: int = 16_000) -> AudioChunk:
    return AudioChunk(
        session_id="session-1",
        started_at_ms=1_000,
        ended_at_ms=1_500,
        sample_rate=sample_rate,
        channels=1,
        sample_width_bits=16,
        pcm=b"\x01\x02",
    )


@pytest.mark.asyncio
async def test_stepfun_asr_maps_sse_events() -> None:
    captured_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"type":"transcript.text.delta","delta":"你好",'
                '"start_time":0,"end_time":400}\n\n'
                'data: {"type":"transcript.text.done","text":"你好世界"}\n\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = StepFunAsrProvider(StepFunAsrConfig(api_key="secret"), client=client)
    await provider.start()
    await provider.push_audio(audio_chunk())
    await provider.commit()

    results = provider.results()
    partial = await anext(results)
    final = await anext(results)

    assert partial.text == "你好"
    assert partial.final is False
    assert partial.started_at_ms == 1_000
    assert partial.ended_at_ms == 1_400
    assert final.text == "你好世界"
    assert final.final is True
    assert captured_request["audio"] == {
        "data": "AQI=",
        "input": {
            "transcription": {
                "model": "stepaudio-2.5-asr",
                "language": "zh",
                "enable_itn": True,
                "enable_timestamp": True,
            },
            "format": {
                "type": "pcm",
                "codec": "pcm_s16le",
                "rate": 16_000,
                "bits": 16,
                "channel": 1,
            },
        },
    }

    await results.aclose()
    await provider.stop()
    await client.aclose()


@pytest.mark.asyncio
async def test_stepfun_asr_surfaces_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"type":"error","message":"quota exceeded"}\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = StepFunAsrProvider(StepFunAsrConfig(api_key="secret"), client=client)
    await provider.start()
    await provider.push_audio(audio_chunk())
    await provider.commit()

    with pytest.raises(StepFunAsrError, match="quota exceeded"):
        await anext(provider.results())

    await provider.stop()
    await client.aclose()


@pytest.mark.asyncio
async def test_stepfun_asr_rejects_stream_without_final_transcript() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"type":"transcript.text.delta","delta":"半截"}\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = StepFunAsrProvider(StepFunAsrConfig(api_key="secret"), client=client)
    await provider.start()
    await provider.push_audio(audio_chunk())
    await provider.commit()

    results = provider.results()
    partial = await anext(results)
    assert partial.text == "半截"
    with pytest.raises(StepFunAsrError, match="without a final transcript"):
        await anext(results)

    await provider.stop()
    await client.aclose()


@pytest.mark.asyncio
async def test_stepfun_asr_rejects_unsupported_audio_format() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    provider = StepFunAsrProvider(StepFunAsrConfig(api_key="secret"), client=client)
    await provider.start()

    with pytest.raises(ValueError, match="16000 Hz"):
        await provider.push_audio(audio_chunk(sample_rate=48_000))

    await provider.stop()
    await client.aclose()


def test_stepfun_asr_config_does_not_reveal_api_key() -> None:
    assert "secret" not in repr(StepFunAsrConfig(api_key="secret"))
