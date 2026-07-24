import pytest

from advx_backend.application.ports.asr import AudioChunk, TranscriptSegment
from advx_backend.application.runtime_capability_probe import (
    ProductionRuntimeCapabilityProbe,
    RuntimeCapabilityProbeBlockedError,
    RuntimeCapabilityProbeError,
    create_stepfun_final_audio_probe,
)
from advx_backend.contracts.configuration import (
    ProviderConfigurationRequest,
    RuntimeModelProviderCandidate,
)
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec
from advx_backend.providers.model.base import (
    CapabilityProbeCheck,
    CapabilityProbeResult,
    CapabilityProbeStatus,
)


def runtime_spec() -> CanonicalRuntimeSpec:
    return CanonicalRuntimeSpec.model_validate(
        {
            "protocol_version": 2,
            "audience_contract_version": 1,
            "config_revision": 1,
            "room": {
                "room_id": "room-1",
                "display_name": "Room",
                "revision": 1,
                "created_at_ms": 1,
                "updated_at_ms": 1,
            },
            "active_mode_id": "mode-1",
            "personas": [
                {
                    "persona_id": "persona-1",
                    "document_version": 1,
                    "revision": 1,
                    "content_hash": "1" * 64,
                    "display_name": "Viewer",
                    "role": "viewer",
                    "silence_bias": 0.2,
                    "burst_bias": 0.2,
                    "repetition_bias": 0.2,
                    "cooldown_ms": 0,
                }
            ],
            "modes": [
                {
                    "mode_id": "mode-1",
                    "namespace_id": "mode-1",
                    "revision": 1,
                    "viewer_count": 1,
                    "persona_ids": ["persona-1"],
                    "persona_weights": {"persona-1": 1},
                    "normal_response_range": {"minimum": 0, "maximum": 1},
                    "highlight_response_range": {"minimum": 0, "maximum": 1},
                }
            ],
            "provider": {
                "provider_profile_id": "provider-1",
                "director_model": "director",
                "viewer_model": "viewer",
                "memory_model": "memory",
                "visual_summary_model": "visual",
            },
        }
    )


def configuration(**updates: object) -> ProviderConfigurationRequest:
    values: dict[str, object] = {
        "provider_profile_id": "provider-1",
        "model_base_url": "https://models.example/v1",
        "model_name": "viewer",
        "director_model": "director",
        "viewer_model": "viewer",
        "memory_model": "memory",
        "visual_summary_model": "visual",
        "model_api_key": "private-model-key",
        "asr_api_key": "private-asr-key",
    }
    values.update(updates)
    return ProviderConfigurationRequest.model_validate(values)


def passed_result() -> CapabilityProbeResult:
    return CapabilityProbeResult(
        status=CapabilityProbeStatus.PASSED,
        discovered_model_ids=("director", "viewer", "memory", "visual"),
        checks=(
            CapabilityProbeCheck(
                capability="model_discovery",
                status=CapabilityProbeStatus.PASSED,
            ),
            CapabilityProbeCheck(
                capability="director_structured_output",
                status=CapabilityProbeStatus.PASSED,
                model_id="director",
            ),
            CapabilityProbeCheck(
                capability="viewer_structured_output",
                status=CapabilityProbeStatus.PASSED,
                model_id="viewer",
            ),
            CapabilityProbeCheck(
                capability="memory_structured_output",
                status=CapabilityProbeStatus.PASSED,
                model_id="memory",
            ),
            CapabilityProbeCheck(
                capability="image_input",
                status=CapabilityProbeStatus.PASSED,
                model_id="visual",
            ),
            CapabilityProbeCheck(
                capability="viewer_minimal_concurrency",
                status=CapabilityProbeStatus.PASSED,
                model_id="viewer",
            ),
        ),
    )


class FakeModelProvider:
    def __init__(self, result: CapabilityProbeResult) -> None:
        self.result = result
        self.role_models: dict[str, str] | None = None
        self.closed = False

    async def probe_capabilities(
        self,
        *,
        role_models: dict[str, str],
    ) -> CapabilityProbeResult:
        self.role_models = role_models
        return self.result

    async def aclose(self) -> None:
        self.closed = True


class FakeAsrProvider:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.chunks: list[AudioChunk] = []
        self.commits = 0

    async def start(self) -> None:
        self.started = True

    async def push_audio(self, chunk: AudioChunk) -> None:
        self.chunks.append(chunk)

    async def commit(self) -> None:
        self.commits += 1

    async def results(self):
        yield TranscriptSegment(
            session_id="capability-probe",
            text="",
            started_at_ms=0,
            ended_at_ms=1_000,
            final=True,
        )

    async def stop(self) -> None:
        self.stopped = True


async def passed_callback(
    spec: CanonicalRuntimeSpec,
    active_configuration: ProviderConfigurationRequest,
) -> CapabilityProbeCheck:
    assert spec.provider.provider_profile_id == active_configuration.provider_profile_id
    return CapabilityProbeCheck(
        capability="callback",
        status=CapabilityProbeStatus.PASSED,
    )


@pytest.mark.asyncio
async def test_stepfun_final_audio_probe_sends_bounded_pcm_and_waits_for_final() -> None:
    fake = FakeAsrProvider()
    captured_config = None

    def provider_factory(config):
        nonlocal captured_config
        captured_config = config
        return fake

    probe = create_stepfun_final_audio_probe(provider_factory=provider_factory)

    result = await probe(runtime_spec(), configuration())

    assert result == CapabilityProbeCheck(
        capability="asr_final_audio",
        status=CapabilityProbeStatus.PASSED,
    )
    assert captured_config.api_key == "private-asr-key"
    assert "private-asr-key" not in repr(captured_config)
    assert fake.started is True
    assert fake.stopped is True
    assert fake.commits == 1
    assert len(fake.chunks) == 1
    assert fake.chunks[0].sample_rate == 16_000
    assert fake.chunks[0].channels == 1
    assert fake.chunks[0].sample_width_bits == 16
    assert len(fake.chunks[0].pcm) == 32_000


@pytest.mark.asyncio
async def test_production_probe_runs_model_matrix_and_injected_final_asr() -> None:
    fake = FakeModelProvider(passed_result())
    captured_config = None

    def provider_factory(config):
        nonlocal captured_config
        captured_config = config
        return fake

    probe = ProductionRuntimeCapabilityProbe(
        configuration_provider=configuration,
        frame_probe=passed_callback,
        asr_probe=passed_callback,
        model_provider_factory=provider_factory,
    )

    await probe.probe(runtime_spec())

    assert captured_config.model == "viewer"
    assert "private-model-key" not in repr(captured_config)
    assert fake.role_models == {
        "director": "director",
        "viewer": "viewer",
        "memory": "memory",
        "visual_summary": "visual",
    }
    assert fake.closed is True


@pytest.mark.asyncio
async def test_model_candidate_probe_does_not_require_or_invoke_asr() -> None:
    fake = FakeModelProvider(passed_result())
    asr_calls = 0

    async def asr_probe(*_: object) -> CapabilityProbeCheck:
        nonlocal asr_calls
        asr_calls += 1
        raise AssertionError("ASR must not run for a model-only candidate")

    probe = ProductionRuntimeCapabilityProbe(
        configuration_provider=configuration,
        asr_probe=asr_probe,
        model_provider_factory=lambda _: fake,
    )
    candidate = RuntimeModelProviderCandidate.model_validate(
        configuration().model_dump(exclude={"asr_api_key"})
    )

    await probe.probe_candidate(runtime_spec(), candidate)

    assert asr_calls == 0
    assert fake.closed is True


@pytest.mark.asyncio
async def test_missing_configuration_and_credentials_are_blocked_without_network() -> None:
    provider_calls = 0

    def provider_factory(config):
        nonlocal provider_calls
        provider_calls += 1
        return FakeModelProvider(passed_result())

    missing = ProductionRuntimeCapabilityProbe(
        configuration_provider=lambda: None,
        model_provider_factory=provider_factory,
    )
    with pytest.raises(RuntimeCapabilityProbeBlockedError) as missing_error:
        await missing.probe(runtime_spec())

    no_credentials = configuration().model_copy(update={"model_api_key": ""})
    blank = ProductionRuntimeCapabilityProbe(
        configuration_provider=lambda: no_credentials,
        model_provider_factory=provider_factory,
    )
    with pytest.raises(RuntimeCapabilityProbeBlockedError) as credential_error:
        await blank.probe(runtime_spec())

    assert missing_error.value.status is CapabilityProbeStatus.BLOCKED
    assert credential_error.value.status is CapabilityProbeStatus.BLOCKED
    assert provider_calls == 0

    no_asr_credentials = configuration().model_copy(update={"asr_api_key": ""})
    no_asr = ProductionRuntimeCapabilityProbe(
        configuration_provider=lambda: no_asr_credentials,
        model_provider_factory=provider_factory,
    )
    with pytest.raises(RuntimeCapabilityProbeBlockedError) as asr_credential_error:
        await no_asr.probe(runtime_spec())

    assert asr_credential_error.value.status is CapabilityProbeStatus.BLOCKED
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_profile_and_all_role_models_must_match_before_network() -> None:
    provider_calls = 0

    def provider_factory(config):
        nonlocal provider_calls
        provider_calls += 1
        return FakeModelProvider(passed_result())

    mismatched = configuration(
        provider_profile_id="other-profile",
        director_model="other-director",
        viewer_model="other-viewer",
        memory_model="other-memory",
        visual_summary_model="other-visual",
    )
    probe = ProductionRuntimeCapabilityProbe(
        configuration_provider=lambda: mismatched,
        model_provider_factory=provider_factory,
    )

    with pytest.raises(RuntimeCapabilityProbeError) as caught:
        await probe.probe(runtime_spec())

    assert caught.value.status is CapabilityProbeStatus.FAILED
    assert {check.capability for check in caught.value.checks} == {
        "provider_profile_match",
        "director_model_match",
        "viewer_model_match",
        "memory_model_match",
        "visual_summary_model_match",
    }
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_missing_final_audio_fixture_blocks_even_when_models_pass() -> None:
    fake = FakeModelProvider(passed_result())
    probe = ProductionRuntimeCapabilityProbe(
        configuration_provider=configuration,
        model_provider_factory=lambda _: fake,
    )

    with pytest.raises(RuntimeCapabilityProbeBlockedError) as caught:
        await probe.probe(runtime_spec())

    assert str(caught.value) == (
        "runtime capability probe blocked: asr_final_audio:final_audio_fixture_required"
    )
    assert fake.closed is True


@pytest.mark.asyncio
async def test_upstream_failure_and_missing_models_are_redacted() -> None:
    secret = "raw-upstream-secret"
    result = CapabilityProbeResult(
        status=CapabilityProbeStatus.BLOCKED,
        discovered_model_ids=("viewer",),
        checks=(
            CapabilityProbeCheck(
                capability="model_discovery",
                status=CapabilityProbeStatus.PASSED,
            ),
            CapabilityProbeCheck(
                capability="director_structured_output",
                status=CapabilityProbeStatus.BLOCKED,
                model_id="director",
                error_code="upstream_http_error",
                http_status=403,
            ),
        ),
    )
    fake = FakeModelProvider(result)

    async def failing_asr(
        spec: CanonicalRuntimeSpec,
        active_configuration: ProviderConfigurationRequest,
    ) -> CapabilityProbeCheck:
        del spec, active_configuration
        raise RuntimeError(secret)

    probe = ProductionRuntimeCapabilityProbe(
        configuration_provider=configuration,
        asr_probe=failing_asr,
        model_provider_factory=lambda _: fake,
    )

    with pytest.raises(RuntimeCapabilityProbeBlockedError) as caught:
        await probe.probe(runtime_spec())

    assert "upstream_http_error" in str(caught.value)
    assert "model_not_discovered" in str(caught.value)
    assert "probe_callback_failed" in str(caught.value)
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert "private-model-key" not in repr(caught.value)
