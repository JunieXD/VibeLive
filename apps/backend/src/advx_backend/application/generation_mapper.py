from collections.abc import Mapping
from typing import Any

from advx_backend.contracts.events import RoomEvent as ContractRoomEvent
from advx_backend.contracts.generation import (
    FrameRef as ContractFrameRef,
)
from advx_backend.contracts.generation import (
    Observation as ContractObservation,
)
from advx_backend.domain.observation import Observation as DomainObservation


def to_generation_observation(
    observation: DomainObservation | ContractObservation,
) -> ContractObservation:
    if isinstance(observation, ContractObservation):
        return observation

    return ContractObservation(
        session_id=observation.session_id,
        observation_id=observation.observation_id,
        created_at_ms=observation.created_at_ms,
        frames=[
            ContractFrameRef(
                frame_id=frame.frame_id,
                created_at_ms=frame.created_at_ms,
                mime_type=frame.mime_type,
                data_ref=frame.data_ref,
            )
            for frame in observation.frames
        ],
        room_events=[
            ContractRoomEvent(
                event_id=event.event_id,
                session_id=event.session_id,
                source_type=event.source_type.value,
                source_id=event.source_id,
                created_at_ms=event.created_at_ms,
                text=event.text,
                payload=_thaw_json(event.payload),
            )
            for event in observation.room_events
        ],
        user_context=dict(observation.user_context),
    )


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
