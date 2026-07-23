import pytest

from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.domain.session import SessionState, SessionStatus


def session_status(revision: int) -> SessionStatus:
    return SessionStatus(
        session_id="session-1",
        state=SessionState.RUNNING,
        started_at_ms=1,
        updated_at_ms=revision,
        revision=revision,
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
