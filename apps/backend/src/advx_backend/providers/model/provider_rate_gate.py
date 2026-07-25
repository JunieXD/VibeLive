from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderRatePolicy:
    max_in_flight: int = 32
    min_start_interval_seconds: float = 0
    fallback_429_backoff_seconds: float = 5.0
    max_429_backoff_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_in_flight, int)
            or isinstance(self.max_in_flight, bool)
            or self.max_in_flight < 1
        ):
            raise ValueError("max_in_flight must be at least one")
        self._validate_seconds(
            self.min_start_interval_seconds,
            name="min_start_interval_seconds",
            allow_zero=True,
        )
        self._validate_seconds(
            self.fallback_429_backoff_seconds,
            name="fallback_429_backoff_seconds",
        )
        self._validate_seconds(
            self.max_429_backoff_seconds,
            name="max_429_backoff_seconds",
        )
        if self.max_429_backoff_seconds < self.fallback_429_backoff_seconds:
            raise ValueError(
                "max_429_backoff_seconds must not be less than the fallback"
            )

    @staticmethod
    def _validate_seconds(
        value: float,
        *,
        name: str,
        allow_zero: bool = False,
    ) -> None:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{name} must be a finite {qualifier} number")
        valid_boundary = value >= 0 if allow_zero else value > 0
        if not valid_boundary:
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{name} must be a finite {qualifier} number")


class ProviderRateGate:
    """Share concurrency, pacing, and 429 cooldown across model roles."""

    def __init__(
        self,
        policy: ProviderRatePolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.policy = policy or ProviderRatePolicy()
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._condition = asyncio.Condition()
        self._waiters: deque[object] = deque()
        self._active = 0
        self._next_start_at = 0.0
        self._cooldown_until = 0.0
        self._rate_limit_streak = 0
        self._rate_limit_generation = 0

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[int]:
        generation = await self._acquire()
        try:
            yield generation
        finally:
            await self._release()

    async def defer_for_rate_limit(
        self,
        retry_after_seconds: float | None,
    ) -> float:
        async with self._condition:
            self._rate_limit_streak = min(self._rate_limit_streak + 1, 16)
            fallback = min(
                self.policy.fallback_429_backoff_seconds
                * (2 ** (self._rate_limit_streak - 1)),
                self.policy.max_429_backoff_seconds,
            )
            retry_after = self._valid_retry_after(retry_after_seconds)
            delay = retry_after if retry_after is not None else fallback
            self._cooldown_until = max(
                self._cooldown_until,
                self._monotonic() + delay,
            )
            self._next_start_at = max(
                self._next_start_at,
                self._cooldown_until,
            )
            self._rate_limit_generation += 1
            self._condition.notify_all()
            return delay

    async def record_success(self, observed_generation: int) -> None:
        async with self._condition:
            if (
                observed_generation == self._rate_limit_generation
                and self._monotonic() >= self._cooldown_until
            ):
                self._rate_limit_streak = 0

    async def _acquire(self) -> int:
        ticket = object()
        async with self._condition:
            self._waiters.append(ticket)
            self._condition.notify_all()
        try:
            while True:
                delay: float | None = None
                async with self._condition:
                    is_head = bool(self._waiters) and self._waiters[0] is ticket
                    if is_head and self._active < self.policy.max_in_flight:
                        now = self._monotonic()
                        ready_at = max(self._next_start_at, self._cooldown_until)
                        delay = ready_at - now
                        if delay <= 0:
                            self._waiters.popleft()
                            self._active += 1
                            self._next_start_at = (
                                max(now, self._next_start_at)
                                + self.policy.min_start_interval_seconds
                            )
                            generation = self._rate_limit_generation
                            self._condition.notify_all()
                            return generation
                    if delay is None:
                        await self._condition.wait()
                        continue
                await self._sleeper(delay)
        except BaseException:
            async with self._condition:
                try:
                    self._waiters.remove(ticket)
                except ValueError:
                    pass
                self._condition.notify_all()
            raise

    async def _release(self) -> None:
        async with self._condition:
            if self._active < 1:
                raise RuntimeError("Provider rate gate lease was not active")
            self._active -= 1
            self._condition.notify_all()

    @staticmethod
    def _valid_retry_after(value: float | None) -> float | None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            return float(value)
        return None


__all__ = ["ProviderRateGate", "ProviderRatePolicy"]
