import time
import uuid


class SystemClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000


class UuidIdGenerator:
    def new_id(self) -> str:
        return str(uuid.uuid4())
