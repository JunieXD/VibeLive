from typing import Protocol

from advx_backend.domain.barrage import BarrageEvent


class BarragePublisher(Protocol):
    async def publish_barrage(self, event: BarrageEvent) -> None: ...
