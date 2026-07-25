from typing import Protocol

from advx_backend.contracts.viewer_runtime import (
    ViewerBarrageEvent,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
    WindowBatchGenerationRequest,
    WindowBatchGenerationResponse,
)


class ViewerProvider(Protocol):
    async def generate(self, request: ViewerGenerationRequest) -> ViewerGenerationResponse: ...

    async def generate_window_batch(
        self,
        request: WindowBatchGenerationRequest,
    ) -> WindowBatchGenerationResponse: ...


class ViewerSessionFence(Protocol):
    async def accepts(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        viewer_instance_id: str,
        viewer_sequence: int,
        deadline_at_ms: int,
    ) -> bool: ...


class ViewerSequenceClaimer(Protocol):
    async def claim_viewer_sequence(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        viewer_instance_id: str,
        viewer_sequence: int,
    ) -> bool: ...


class ViewerBarragePublisher(Protocol):
    async def publish(self, event: ViewerBarrageEvent) -> None: ...


class ViewerRoomWriter(Protocol):
    async def append_published_barrage(self, event: ViewerBarrageEvent) -> None: ...
