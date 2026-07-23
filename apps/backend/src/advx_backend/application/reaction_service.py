from dataclasses import dataclass

from advx_backend.application.barrage_pipeline import BarragePipeline
from advx_backend.application.generation_service import GenerationService
from advx_backend.application.ports.barrage import BarragePublisher
from advx_backend.application.ports.generation import SessionTaskScope
from advx_backend.application.room_service import RoomService, RoomSessionNotActiveError
from advx_backend.domain.barrage import (
    BarrageEvent,
    BarrageValidationResult,
    BarrageValidationScope,
)
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEventSource


@dataclass(frozen=True, slots=True)
class ReactionResult:
    published_events: tuple[BarrageEvent, ...]
    validations: tuple[BarrageValidationResult, ...]


class ReactionService:
    """Connect generation, local barrage validation, Room and realtime output."""

    def __init__(
        self,
        *,
        generation_service: GenerationService,
        barrage_pipeline: BarragePipeline,
        room_service: RoomService,
        session_tasks: SessionTaskScope,
        publisher: BarragePublisher,
    ) -> None:
        self._generation_service = generation_service
        self._barrage_pipeline = barrage_pipeline
        self._room_service = room_service
        self._session_tasks = session_tasks
        self._publisher = publisher

    async def react(self, observation: Observation) -> ReactionResult:
        task = await self._session_tasks.start_task(
            observation.session_id,
            lambda: self._react(observation),
            name=f"reaction:{observation.session_id}:{observation.observation_id}",
        )
        return await task

    async def _react(self, observation: Observation) -> ReactionResult:
        outputs = await self._generation_service.generate_outputs(observation)
        validations: list[BarrageValidationResult] = []
        published: list[BarrageEvent] = []

        for output in outputs:
            if not await self._session_tasks.accepts_results(observation.session_id):
                break
            work_item = output.work_item
            validation = self._barrage_pipeline.process(
                scope=BarrageValidationScope(
                    session_id=work_item.session_id,
                    observation_id=work_item.observation_id,
                    request_id=work_item.request_id,
                ),
                request=work_item.request,
                result=output.result,
            )
            validations.append(validation)

            for event in validation.events:
                if not await self._session_tasks.accepts_results(event.session_id):
                    break
                try:
                    await self._room_service.append_event(
                        event.session_id,
                        source_type=RoomEventSource.AUDIENCE_BARRAGE,
                        source_id=event.audience_id,
                        text=event.text,
                        payload={
                            "barrage_id": event.barrage_id,
                            "observation_id": event.observation_id,
                            "request_id": event.request_id,
                            "expires_at_ms": event.expires_at_ms,
                        },
                    )
                except RoomSessionNotActiveError:
                    break
                if not await self._session_tasks.accepts_results(event.session_id):
                    break
                await self._publisher.publish_barrage(event)
                published.append(event)

        return ReactionResult(
            published_events=tuple(published),
            validations=tuple(validations),
        )
