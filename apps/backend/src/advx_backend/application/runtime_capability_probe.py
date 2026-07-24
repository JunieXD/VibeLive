from collections.abc import Awaitable, Callable
from typing import NoReturn, Protocol

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
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)

CapabilityCallback = Callable[
    [
        CanonicalRuntimeSpec,
        ProviderConfigurationRequest | RuntimeModelProviderCandidate,
    ],
    Awaitable[CapabilityProbeCheck],
]


class ModelCapabilityProvider(Protocol):
    async def probe_startup_capabilities(
        self,
        *,
        role_models: dict[str, str],
    ) -> CapabilityProbeResult: ...

    async def probe_capabilities(
        self,
        *,
        role_models: dict[str, str],
    ) -> CapabilityProbeResult: ...

    async def aclose(self) -> None: ...


ModelProviderFactory = Callable[[OpenAICompatibleConfig], ModelCapabilityProvider]


class RuntimeCapabilityProbeError(RuntimeError):
    """Redacted failure raised when a runtime spec cannot use the active provider."""

    def __init__(
        self,
        *,
        status: CapabilityProbeStatus,
        checks: tuple[CapabilityProbeCheck, ...],
    ) -> None:
        self.status = status
        self.checks = checks
        failures = ",".join(
            f"{check.capability}:{check.error_code or check.status.value}"
            for check in checks
            if check.status is not CapabilityProbeStatus.PASSED
        )
        super().__init__(f"runtime capability probe {status.value}: {failures}")


class RuntimeCapabilityProbeBlockedError(RuntimeCapabilityProbeError):
    """Raised when credentials, fixtures, or upstream availability block the probe."""


class ProductionRuntimeCapabilityProbe:
    """Validate a canonical runtime against the one active provider profile."""

    def __init__(
        self,
        *,
        configuration_provider: Callable[[], ProviderConfigurationRequest | None],
        frame_probe: CapabilityCallback | None = None,
        model_provider_factory: ModelProviderFactory | None = None,
    ) -> None:
        self._configuration_provider = configuration_provider
        self._frame_probe = frame_probe
        self._model_provider_factory = model_provider_factory or self._default_provider

    async def probe(self, spec: CanonicalRuntimeSpec) -> None:
        configuration = self._configuration_provider()
        if configuration is None:
            self._raise(
                CapabilityProbeCheck(
                    capability="active_provider_configuration",
                    status=CapabilityProbeStatus.BLOCKED,
                    error_code="provider_not_configured",
                )
            )
        assert configuration is not None
        await self._probe(spec, configuration)

    async def probe_candidate(
        self,
        spec: CanonicalRuntimeSpec,
        configuration: RuntimeModelProviderCandidate,
    ) -> None:
        """Probe a model-only candidate before applying its runtime configuration."""

        await self._probe(spec, configuration)

    async def _probe(
        self,
        spec: CanonicalRuntimeSpec,
        configuration: ProviderConfigurationRequest | RuntimeModelProviderCandidate,
    ) -> None:
        if not configuration.model_api_key.strip():
            self._raise(
                CapabilityProbeCheck(
                    capability="active_provider_credentials",
                    status=CapabilityProbeStatus.BLOCKED,
                    error_code="credentials_not_configured",
                )
            )
        expected_models = {
            "viewer": spec.provider.viewer_model,
            "memory": spec.provider.memory_model,
            "visual_summary": spec.provider.visual_summary_model,
        }
        configuration_models = configuration.role_models()
        mismatch_checks = self._configuration_mismatches(
            spec=spec,
            configuration=configuration,
            expected_models=expected_models,
            configuration_models=configuration_models,
        )
        if mismatch_checks:
            self._raise(*mismatch_checks)

        try:
            provider = self._model_provider_factory(
                OpenAICompatibleConfig(
                    base_url=configuration.model_base_url,
                    model=configuration.viewer_model or configuration.model_name,
                    api_key=configuration.model_api_key,
                )
            )
        except Exception:
            self._raise(
                CapabilityProbeCheck(
                    capability="model_provider_initialization",
                    status=CapabilityProbeStatus.FAILED,
                    error_code="invalid_provider_configuration",
                )
            )
        try:
            try:
                result = await provider.probe_startup_capabilities(role_models=expected_models)
            except Exception:
                self._raise(
                    CapabilityProbeCheck(
                        capability="model_capability_probe",
                        status=CapabilityProbeStatus.BLOCKED,
                        error_code="model_probe_failed",
                    )
                )
        finally:
            try:
                await provider.aclose()
            except Exception:
                self._raise(
                    CapabilityProbeCheck(
                        capability="model_provider_close",
                        status=CapabilityProbeStatus.BLOCKED,
                        error_code="provider_close_failed",
                    )
                )

        checks = list(result.checks)
        if result.status is not CapabilityProbeStatus.PASSED and all(
            check.status is CapabilityProbeStatus.PASSED for check in checks
        ):
            checks.append(
                CapabilityProbeCheck(
                    capability="model_capability_probe",
                    status=result.status,
                    error_code="model_probe_failed",
                )
            )
        if self._frame_probe is not None:
            checks.append(
                await self._run_callback(
                    "frame_fixture",
                    self._frame_probe,
                    spec,
                    configuration,
                )
            )
        failures = tuple(
            check
            for check in checks
            if check.status
            in {
                CapabilityProbeStatus.FAILED,
                CapabilityProbeStatus.BLOCKED,
            }
        )
        if failures:
            self._raise(*failures)

    @staticmethod
    async def _run_callback(
        capability: str,
        callback: CapabilityCallback,
        spec: CanonicalRuntimeSpec,
        configuration: ProviderConfigurationRequest | RuntimeModelProviderCandidate,
    ) -> CapabilityProbeCheck:
        try:
            result = await callback(spec, configuration)
            if not isinstance(result, CapabilityProbeCheck):
                raise TypeError
            return CapabilityProbeCheck(
                capability=capability,
                status=result.status,
                model_id=result.model_id,
                error_code=result.error_code,
                http_status=result.http_status,
            )
        except Exception:
            return CapabilityProbeCheck(
                capability=capability,
                status=CapabilityProbeStatus.BLOCKED,
                error_code="probe_callback_failed",
            )

    @staticmethod
    def _configuration_mismatches(
        *,
        spec: CanonicalRuntimeSpec,
        configuration: ProviderConfigurationRequest,
        expected_models: dict[str, str],
        configuration_models: dict[str, str],
    ) -> tuple[CapabilityProbeCheck, ...]:
        checks: list[CapabilityProbeCheck] = []
        if configuration.provider_profile_id != spec.provider.provider_profile_id:
            checks.append(
                CapabilityProbeCheck(
                    capability="provider_profile_match",
                    status=CapabilityProbeStatus.FAILED,
                    error_code="provider_profile_mismatch",
                )
            )
        for role, expected_model in expected_models.items():
            if configuration_models[role] != expected_model:
                checks.append(
                    CapabilityProbeCheck(
                        capability=f"{role}_model_match",
                        status=CapabilityProbeStatus.FAILED,
                        model_id=expected_model,
                        error_code="role_model_mismatch",
                    )
                )
        return tuple(checks)

    @staticmethod
    def _default_provider(config: OpenAICompatibleConfig) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(config)

    @staticmethod
    def _raise(*checks: CapabilityProbeCheck) -> NoReturn:
        status = (
            CapabilityProbeStatus.BLOCKED
            if any(check.status is CapabilityProbeStatus.BLOCKED for check in checks)
            else CapabilityProbeStatus.FAILED
        )
        error_type = (
            RuntimeCapabilityProbeBlockedError
            if status is CapabilityProbeStatus.BLOCKED
            else RuntimeCapabilityProbeError
        )
        raise error_type(status=status, checks=tuple(checks))


__all__ = [
    "CapabilityCallback",
    "ProductionRuntimeCapabilityProbe",
    "RuntimeCapabilityProbeBlockedError",
    "RuntimeCapabilityProbeError",
]
