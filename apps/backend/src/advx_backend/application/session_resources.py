from advx_backend.application.barrage_pipeline import BarragePipeline
from advx_backend.application.context_builder import ContextBuilder


class SessionResources:
    """Keep bounded in-memory pipeline state aligned with SessionService."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        barrage_pipeline: BarragePipeline,
    ) -> None:
        self._context_builder = context_builder
        self._barrage_pipeline = barrage_pipeline

    async def start_session(self, session_id: str) -> None:
        await self._context_builder.start_session(session_id)

    async def stop_session(self, session_id: str) -> None:
        try:
            await self._context_builder.stop_session(session_id)
        finally:
            self._barrage_pipeline.clear_session(session_id)
