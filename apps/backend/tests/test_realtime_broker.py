import pytest

from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.domain.barrage import (
    BarrageEvent,
    BarrageEvidenceRef,
    BarrageEvidenceSource,
)
from advx_backend.domain.session import SessionState, SessionStatus


def session_status(revision: int) -> SessionStatus:
    return SessionStatus(
        session_id="session-1",
        state=SessionState.RUNNING,
        started_at_ms=1,
        updated_at_ms=revision,
        revision=revision,
    )


def barrage_event(index: int) -> BarrageEvent:
    return BarrageEvent(
        barrage_id=f"barrage-{index}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        generation_request_id=f"request-{index}",
        viewer_instance_id="viewer-1",
        persona_id="persona-1",
        display_name="Viewer One",
        viewer_sequence=index,
        reaction_type="reply",
        evidence_refs=(
            BarrageEvidenceRef(
                source=BarrageEvidenceSource.EVENT,
                event_id="event-1",
            ),
        ),
        text=f"message {index}",
        created_at_ms=index,
        expires_at_ms=index + 100,
    )


@pytest.mark.asyncio
async def test_realtime_broker_drops_oldest_status_when_subscriber_is_slow() -> None:
    broker = RealtimeBroker(subscriber_capacity=2)
    subscription = await broker.subscribe()

    await broker.publish_session_status(session_status(1))
    await broker.publish_session_status(session_status(2))
    await broker.publish_session_status(session_status(3))

    assert (await subscription.get()).revision == 2
    assert (await subscription.get()).revision == 3

    await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_realtime_broker_bounds_barrage_stream_independently() -> None:
    broker = RealtimeBroker(subscriber_capacity=2)
    status_subscription = await broker.subscribe()
    barrage_subscription = await broker.subscribe_barrages()

    await broker.publish_session_status(session_status(1))
    await broker.publish_barrage(barrage_event(1))
    await broker.publish_barrage(barrage_event(2))
    await broker.publish_barrage(barrage_event(3))

    assert (await status_subscription.get()).revision == 1
    assert (await barrage_subscription.get()).barrage_id == "barrage-2"
    assert (await barrage_subscription.get()).barrage_id == "barrage-3"

    await broker.unsubscribe(status_subscription)
    await broker.unsubscribe_barrages(barrage_subscription)
