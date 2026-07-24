from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from advx_backend.api.dependencies import LocalTokenGuard, ProtocolVersionGuard
from advx_backend.bootstrap import BackendRuntime, ProviderPipelineAlreadyConfiguredError
from advx_backend.contracts.configuration import (
    ProviderCapabilityCheck,
    ProviderCapabilityProbeResult,
    ProviderConfigurationRequest,
    ProviderConfigurationStatus,
    ProviderModelDiscovery,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.domain.session import SessionState
from advx_backend.providers.model.base import CapabilityProbeStatus
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
)


def create_configuration_router(
    *,
    runtime: BackendRuntime,
    local_token: str,
) -> APIRouter:
    router = APIRouter(
        prefix="/configuration",
        tags=["configuration"],
        dependencies=[
            Depends(LocalTokenGuard(local_token)),
            Depends(ProtocolVersionGuard(PROTOCOL_VERSION)),
        ],
    )

    @router.get("/providers", response_model=ProviderConfigurationStatus)
    async def provider_status() -> ProviderConfigurationStatus:
        return _status(runtime, runtime.provider_configuration)

    @router.get("/providers/models", response_model=ProviderModelDiscovery)
    async def discover_provider_models() -> ProviderModelDiscovery:
        request = _active_request(runtime, runtime.provider_configuration)
        provider = _provider(request, request.viewer_model or request.model_name)
        try:
            model_ids = await provider.discover_models()
        except OpenAICompatibleProviderError as error:
            raise _provider_http_exception(error) from error
        finally:
            await provider.aclose()
        return ProviderModelDiscovery(
            provider_profile_id=request.provider_profile_id,
            model_ids=list(model_ids),
        )

    @router.post("/providers/probe", response_model=ProviderCapabilityProbeResult)
    async def probe_provider_capabilities(
        request: ProviderConfigurationRequest | None = None,
    ) -> ProviderCapabilityProbeResult:
        probe_request = (
            _active_request(runtime, runtime.provider_configuration)
            if request is None
            else request
        )
        provider = _provider(probe_request, probe_request.viewer_model or probe_request.model_name)
        try:
            result = await provider.probe_capabilities(
                role_models=probe_request.role_models(),
            )
        finally:
            await provider.aclose()
        checks = [
            ProviderCapabilityCheck(
                capability=check.capability,
                status=check.status.value,
                model_id=check.model_id,
                error_code=check.error_code,
                http_status=check.http_status,
            )
            for check in result.checks
        ]
        checks.append(
            ProviderCapabilityCheck(
                capability="asr_adapter",
                status=CapabilityProbeStatus.SKIPPED.value,
                model_id=probe_request.asr_model,
                error_code="requires_final_audio",
            )
        )
        return ProviderCapabilityProbeResult(
            provider_profile_id=probe_request.provider_profile_id,
            status=result.status.value,
            discovered_model_ids=list(result.discovered_model_ids),
            checks=checks,
        )

    @router.put("/providers", response_model=ProviderConfigurationStatus)
    async def configure_providers(
        request: ProviderConfigurationRequest,
    ) -> ProviderConfigurationStatus:
        session = await runtime.session_service.status()
        if session.state is not SessionState.IDLE:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail={
                    "code": "session_active",
                    "message": "Providers can only be configured while no Session is active.",
                },
            )
        if (
            runtime.provider_configuration is not None
            and runtime.provider_configuration != request
        ):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail={
                    "code": "providers_already_configured",
                    "message": (
                        "Different providers are already configured; "
                        "restart the backend to replace them."
                    ),
                },
            )
        try:
            runtime.configure_provider_profile(request)
        except ProviderPipelineAlreadyConfiguredError as error:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail={
                    "code": "providers_already_configured",
                    "message": (
                        "Different providers are already configured; "
                        "restart the backend to replace them."
                    ),
                },
            ) from error
        return _status(runtime, runtime.provider_configuration)

    return router


def _status(
    runtime: BackendRuntime,
    active_request: ProviderConfigurationRequest | None,
) -> ProviderConfigurationStatus:
    config = runtime.external_provider_config
    if config is None:
        return ProviderConfigurationStatus(configured=False)
    request = active_request
    role_models = (
        request.role_models()
        if request is not None
        else {
            "viewer": config.model_name,
            "memory": config.model_name,
            "visual_summary": config.model_name,
        }
    )
    return ProviderConfigurationStatus(
        configured=True,
        provider_profile_id=request.provider_profile_id if request is not None else "default",
        model_base_url=request.model_base_url if request is not None else config.model_base_url,
        model_name=request.model_name if request is not None else config.model_name,
        viewer_model=role_models["viewer"],
        memory_model=role_models["memory"],
        visual_summary_model=role_models["visual_summary"],
        asr_base_url=config.asr_base_url,
        asr_model=config.asr_model,
    )


def _active_request(
    runtime: BackendRuntime,
    active_request: ProviderConfigurationRequest | None,
) -> ProviderConfigurationRequest:
    if active_request is not None:
        return active_request
    config = runtime.external_provider_config
    if config is None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={
                "code": "providers_not_configured",
                "message": "Configure providers before using the active provider profile.",
            },
        )
    return ProviderConfigurationRequest(
        provider_profile_id="default",
        model_base_url=config.model_base_url,
        model_name=config.model_name,
        model_api_key=config.model_api_key,
        asr_base_url=config.asr_base_url,
        asr_model=config.asr_model,
        asr_api_key=config.asr_api_key,
    )


def _provider(
    request: ProviderConfigurationRequest,
    model_id: str,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=request.model_base_url,
            model=model_id,
            api_key=request.model_api_key,
        )
    )


def _provider_http_exception(error: OpenAICompatibleProviderError) -> HTTPException:
    check = OpenAICompatibleProvider.normalize_probe_error("model_discovery", None, error)
    return HTTPException(
        status_code=http_status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": check.error_code,
            "provider_status": check.status.value,
            "upstream_http_status": check.http_status,
        },
    )
