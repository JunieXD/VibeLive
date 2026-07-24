import asyncio
from typing import TypeVar

from advx_backend.contracts.audience import ViewerPresenceEvent
from advx_backend.domain.barrage import BarrageEvent
from advx_backend.domain.session import SessionStatus

T = TypeVar("T")


class RealtimeBroker:
    """Bounded in-process fan-out for realtime status subscribers."""

    def __init__(self, *, subscriber_capacity: int = 16) -> None:
        if subscriber_capacity < 1:
            raise ValueError("subscriber_capacity must be at least one")
        self._subscriber_capacity = subscriber_capacity
        self._subscribers: set[asyncio.Queue[SessionStatus]] = set()
        self._barrage_subscribers: set[asyncio.Queue[BarrageEvent]] = set()
        self._viewer_subscribers: set[asyncio.Queue[ViewerPresenceEvent]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[SessionStatus]:
        queue: asyncio.Queue[SessionStatus] = asyncio.Queue(maxsize=self._subscriber_capacity)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[SessionStatus]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def subscribe_barrages(self) -> asyncio.Queue[BarrageEvent]:
        queue: asyncio.Queue[BarrageEvent] = asyncio.Queue(maxsize=self._subscriber_capacity)
        async with self._lock:
            self._barrage_subscribers.add(queue)
        return queue

    async def unsubscribe_barrages(self, queue: asyncio.Queue[BarrageEvent]) -> None:
        async with self._lock:
            self._barrage_subscribers.discard(queue)

    async def publish_session_status(self, status: SessionStatus) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)

        for queue in subscribers:
            self._put_latest(queue, status)

    async def publish_barrage(self, event: BarrageEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._barrage_subscribers)

        for queue in subscribers:
            self._put_latest(queue, event)

    async def subscribe_viewers(self) -> asyncio.Queue[ViewerPresenceEvent]:
        queue: asyncio.Queue[ViewerPresenceEvent] = asyncio.Queue(
            maxsize=self._subscriber_capacity
        )
        async with self._lock:
            self._viewer_subscribers.add(queue)
        return queue

    async def unsubscribe_viewers(
        self,
        queue: asyncio.Queue[ViewerPresenceEvent],
    ) -> None:
        async with self._lock:
            self._viewer_subscribers.discard(queue)

    async def publish_viewer_event(self, event: ViewerPresenceEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._viewer_subscribers)
        for queue in subscribers:
            self._put_latest(queue, event)

    @staticmethod
    def _put_latest(queue: asyncio.Queue[T], item: T) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(item)
