import asyncio

from advx_backend.domain.session import SessionStatus


class RealtimeBroker:
    """Bounded in-process fan-out for realtime status subscribers."""

    def __init__(self, *, subscriber_capacity: int = 16) -> None:
        if subscriber_capacity < 1:
            raise ValueError("subscriber_capacity must be at least one")
        self._subscriber_capacity = subscriber_capacity
        self._subscribers: set[asyncio.Queue[SessionStatus]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[SessionStatus]:
        queue: asyncio.Queue[SessionStatus] = asyncio.Queue(maxsize=self._subscriber_capacity)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[SessionStatus]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish_session_status(self, status: SessionStatus) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)

        for queue in subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(status)
