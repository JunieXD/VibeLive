import logging

from advx_backend.application.barrage_pipeline import BarragePipeline
from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.ports.session import SessionResource

logger = logging.getLogger(__name__)


class SessionResources:
    """Keep bounded in-memory pipeline state aligned with SessionService."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        barrage_pipeline: BarragePipeline,
        resources: tuple[SessionResource, ...] = (),
    ) -> None:
        self._resources: list[SessionResource] = [context_builder, *resources]
        self._barrage_pipeline = barrage_pipeline
        self._active_session_id: str | None = None

    def add_resource(self, resource: SessionResource) -> None:
        if self._active_session_id is not None:
            raise RuntimeError("session resources cannot be changed while a session is active")
        if resource not in self._resources:
            self._resources.append(resource)

    async def start_session(self, session_id: str) -> None:
        if self._active_session_id is not None:
            if self._active_session_id == session_id:
                return
            raise RuntimeError(f"session resources already own {self._active_session_id}")

        started: list[SessionResource] = []
        try:
            for resource in self._resources:
                await resource.start_session(session_id)
                started.append(resource)
        except BaseException:
            try:
                await self._stop_resources(session_id, tuple(reversed(started)))
            except BaseException:
                logger.warning(
                    "failed to roll back session resources after startup failure",
                    extra={"session_id": session_id},
                )
            raise
        self._active_session_id = session_id

    async def stop_session(self, session_id: str) -> None:
        try:
            if self._active_session_id == session_id:
                self._active_session_id = None
                await self._stop_resources(session_id, tuple(reversed(self._resources)))
        finally:
            self._barrage_pipeline.clear_session(session_id)

    @staticmethod
    async def _stop_resources(
        session_id: str,
        resources: tuple[SessionResource, ...],
    ) -> None:
        first_error: BaseException | None = None
        for resource in resources:
            try:
                await resource.stop_session(session_id)
            except BaseException as error:
                if first_error is None:
                    first_error = error
                logger.warning(
                    "failed to stop a session resource",
                    extra={
                        "session_id": session_id,
                        "resource_type": type(resource).__name__,
                        "error_type": type(error).__name__,
                    },
                )
        if first_error is not None:
            raise first_error
