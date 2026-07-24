from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.room_event_persistence import persisted_room_event
from advx_backend.application.room_service import RoomService
from advx_backend.application.runtime_state import RuntimeStateStore
from advx_backend.contracts.viewer_runtime import EvidenceSource, ViewerBarrageEvent
from advx_backend.domain.barrage import (
    BarrageEvent,
    BarrageEvidenceRef,
    BarrageEvidenceSource,
)
from advx_backend.domain.room import RoomEvent, RoomEventSource
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    SQLiteRoomEventRepository,
)


def to_barrage_event(event: ViewerBarrageEvent) -> BarrageEvent:
    return BarrageEvent(
        barrage_id=event.barrage_id,
        room_id=event.room_id,
        session_id=event.session_id,
        audience_epoch=event.audience_epoch,
        observation_id=event.observation_id,
        generation_request_id=event.generation_request_id,
        viewer_instance_id=event.viewer_instance_id,
        persona_id=event.persona_id,
        display_name=event.display_name,
        viewer_sequence=event.viewer_sequence,
        reaction_type=event.reaction_type,
        evidence_refs=tuple(
            BarrageEvidenceRef(
                source=(
                    BarrageEvidenceSource.EVENT
                    if item.source is EvidenceSource.EVENT
                    else BarrageEvidenceSource.FRAME
                ),
                event_id=item.event_id,
                frame_index=item.frame_index,
            )
            for item in event.evidence_refs
        ),
        text=event.text,
        created_at_ms=event.created_at_ms,
        expires_at_ms=event.expires_at_ms,
    )


class RealtimeViewerBarragePublisher:
    def __init__(self, broker: RealtimeBroker) -> None:
        self._broker = broker

    async def publish(self, event: ViewerBarrageEvent) -> None:
        await self._broker.publish_barrage(to_barrage_event(event))


class PersistentViewerRoomWriter:
    """Atomically expose and persist one accepted Viewer barrage."""

    def __init__(
        self,
        *,
        room_service: RoomService,
        runtime_state: RuntimeStateStore,
        session_factory: async_sessionmaker[AsyncSession],
        repository_factory: Callable[[AsyncSession], SQLiteRoomEventRepository] = (
            SQLiteRoomEventRepository
        ),
    ) -> None:
        self._room_service = room_service
        del runtime_state
        self._session_factory = session_factory
        self._repository_factory = repository_factory

    async def append_published_barrage(self, event: ViewerBarrageEvent) -> None:
        content = event.model_dump(mode="json")

        async def persist(room_event: RoomEvent) -> None:
            persisted = persisted_room_event(
                room_event,
                room_id=event.room_id,
                audience_epoch=event.audience_epoch,
            )
            async with self._session_factory() as session:
                await self._repository_factory(session).append(persisted)
                await session.commit()

        async def append() -> None:
            await self._room_service.append_event_after(
                event.session_id,
                source_type=RoomEventSource.AUDIENCE_BARRAGE,
                source_id=event.barrage_id,
                text=event.text,
                payload=_room_payload(content),
                persist=persist,
            )

        await append()


def _room_payload(content: dict[str, Any]) -> dict[str, object]:
    return {
        "barrage_id": content["barrage_id"],
        "audience_epoch": content["audience_epoch"],
        "observation_id": content["observation_id"],
        "generation_request_id": content["generation_request_id"],
        "viewer_instance_id": content["viewer_instance_id"],
        "persona_id": content["persona_id"],
        "display_name": content["display_name"],
        "viewer_sequence": content["viewer_sequence"],
        "reaction_type": content["reaction_type"],
        "evidence_refs": content["evidence_refs"],
        "expires_at_ms": content["expires_at_ms"],
    }
