from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from advx_backend.api.dependencies import LocalTokenGuard, ProtocolVersionGuard
from advx_backend.bootstrap import (
    BackendRuntime,
    ExternalProviderConfig,
    ProviderPipelineAlreadyConfiguredError,
)
from advx_backend.contracts.configuration import (
    ProviderConfigurationRequest,
    ProviderConfigurationStatus,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.domain.session import SessionState


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
        return _status(runtime)

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
        try:
            runtime.configure_external_provider_pipeline(
                ExternalProviderConfig(
                    model_base_url=request.model_base_url,
                    model_name=request.model_name,
                    model_api_key=request.model_api_key,
                    asr_api_key=request.asr_api_key,
                )
            )
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
        return _status(runtime)

    return router


def _status(runtime: BackendRuntime) -> ProviderConfigurationStatus:
    config = runtime.external_provider_config
    if config is None:
        return ProviderConfigurationStatus(configured=False)
    return ProviderConfigurationStatus(
        configured=True,
        model_base_url=config.model_base_url,
        model_name=config.model_name,
        asr_model=config.asr_model,
    )
