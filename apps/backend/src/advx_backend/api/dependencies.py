from typing import Annotated

from fastapi import Depends, Header, HTTPException
from fastapi import status as http_status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.infrastructure.security.local_token import local_token_matches

_BEARER = HTTPBearer(auto_error=False)


class LocalTokenGuard:
    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    async def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_BEARER),
        ],
    ) -> None:
        token = credentials.credentials if credentials is not None else None
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not local_token_matches(self._expected_token, token)
        ):
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "invalid_local_token",
                    "message": "A valid local bearer token is required.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )


class ProtocolVersionGuard:
    def __init__(self, supported_version: int) -> None:
        self._supported_version = supported_version

    async def __call__(
        self,
        version: Annotated[
            str | None,
            Header(alias=PROTOCOL_VERSION_HEADER),
        ] = None,
    ) -> None:
        if version != str(self._supported_version):
            raise HTTPException(
                status_code=http_status.HTTP_426_UPGRADE_REQUIRED,
                detail={
                    "code": "protocol_version_mismatch",
                    "message": "The requested protocol version is not supported.",
                    "supported_version": self._supported_version,
                },
                headers={PROTOCOL_VERSION_HEADER: str(self._supported_version)},
            )
