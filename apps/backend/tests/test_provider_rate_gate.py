import asyncio

import pytest

from advx_backend.application.runtime_provider import RuntimeProviderController
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.viewer_runtime import ProviderRuntimeSpec
from advx_backend.providers.model.provider_rate_gate import (
    ProviderRateGate,
    ProviderRatePolicy,
)


class _FakeClock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _ControlledSleeper:
    def __init__(self, clock: _FakeClock, expected_calls: int) -> None:
        self._clock = clock
        self._expected_calls = expected_calls
        self.calls: list[float] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        target = self._clock.now + delay
        self.calls.append(delay)
        if len(self.calls) >= self._expected_calls:
            self.started.set()
        await self.release.wait()
        self._clock.now = max(self._clock.now, target)


class _AdvancingSleeper:
    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        target = self._clock.now + delay
        self.calls.append(delay)
        await asyncio.sleep(0)
        self._clock.now = max(self._clock.now, target)


def _policy(
    *,
    max_in_flight: int = 4,
    min_start_interval_seconds: float = 0,
) -> ProviderRatePolicy:
    return ProviderRatePolicy(
        max_in_flight=max_in_flight,
        min_start_interval_seconds=min_start_interval_seconds,
        fallback_429_backoff_seconds=5,
        max_429_backoff_seconds=60,
    )


def test_default_provider_rate_policy() -> None:
    policy = ProviderRatePolicy()

    assert policy.max_in_flight == 32
    assert policy.min_start_interval_seconds == 0


@pytest.mark.asyncio
async def test_twenty_eight_requests_complete_with_four_provider_calls_in_flight() -> None:
    gate = ProviderRateGate(_policy(max_in_flight=4))
    release = asyncio.Event()
    four_entered = asyncio.Event()
    active = 0
    max_active = 0
    entered = 0

    async def hold_lease() -> None:
        nonlocal active, entered, max_active
        async with gate.lease():
            active += 1
            entered += 1
            max_active = max(max_active, active)
            if entered == 4:
                four_entered.set()
            await release.wait()
            active -= 1

    tasks = [asyncio.create_task(hold_lease()) for _ in range(28)]
    await asyncio.wait_for(four_entered.wait(), timeout=1)
    await asyncio.sleep(0)

    assert entered == 4
    assert max_active == 4

    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert entered == 28
    assert max_active == 4
    assert active == 0


@pytest.mark.asyncio
async def test_one_rate_limit_defers_all_new_provider_leases() -> None:
    clock = _FakeClock()
    sleeper = _ControlledSleeper(clock, expected_calls=1)
    gate = ProviderRateGate(
        _policy(max_in_flight=2),
        monotonic=clock,
        sleeper=sleeper,
    )
    entered = 0

    delay = await gate.defer_for_rate_limit(2.5)

    async def take_lease() -> None:
        nonlocal entered
        async with gate.lease():
            entered += 1

    tasks = [asyncio.create_task(take_lease()) for _ in range(2)]
    await asyncio.wait_for(sleeper.started.wait(), timeout=1)

    assert delay == 2.5
    assert sleeper.calls == [2.5]
    assert entered == 0

    sleeper.release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert entered == 2


@pytest.mark.asyncio
async def test_provider_calls_start_at_the_configured_interval() -> None:
    clock = _FakeClock()
    sleeper = _AdvancingSleeper(clock)
    gate = ProviderRateGate(
        _policy(max_in_flight=4, min_start_interval_seconds=1),
        monotonic=clock,
        sleeper=sleeper,
    )
    started_at: list[float] = []

    async def take_lease() -> None:
        async with gate.lease():
            started_at.append(clock.now)

    await asyncio.wait_for(
        asyncio.gather(*(take_lease() for _ in range(3))),
        timeout=1,
    )

    assert started_at == [10, 11, 12]
    assert sleeper.calls == [1, 1]


@pytest.mark.asyncio
async def test_cancelled_cooldown_waiter_does_not_leak_provider_capacity() -> None:
    clock = _FakeClock()
    sleeper = _ControlledSleeper(clock, expected_calls=1)
    gate = ProviderRateGate(
        _policy(max_in_flight=1),
        monotonic=clock,
        sleeper=sleeper,
    )

    await gate.defer_for_rate_limit(3)

    async def wait_for_lease() -> None:
        async with gate.lease():
            raise AssertionError("cancelled waiter must not enter the provider")

    waiting = asyncio.create_task(wait_for_lease())
    await asyncio.wait_for(sleeper.started.wait(), timeout=1)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    clock.now += 3
    entered = False
    async with gate.lease():
        entered = True

    assert entered


@pytest.mark.asyncio
async def test_rate_gate_releases_capacity_when_an_active_call_is_cancelled() -> None:
    gate = ProviderRateGate(_policy(max_in_flight=1))
    entered = asyncio.Event()
    hold = asyncio.Event()

    async def active_call() -> None:
        async with gate.lease():
            entered.set()
            await hold.wait()

    task = asyncio.create_task(active_call())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with gate.lease():
        pass


@pytest.mark.asyncio
async def test_provider_generations_share_the_controller_rate_gate() -> None:
    request = ProviderConfigurationRequest(
        provider_profile_id="profile",
        model_base_url="https://models.example/v1",
        model_name="model",
        model_api_key="model-key",
        asr_api_key="asr-key",
    )
    spec = ProviderRuntimeSpec(
        provider_profile_id="profile",
        viewer_model="model",
        memory_model="model",
        visual_summary_model="model",
    )
    controller = RuntimeProviderController(
        frame_resolver=object(),
        configuration_committer=lambda _: None,
    )

    first = controller.build(spec, request)
    second = controller.build(spec, request)
    try:
        shared_gate = getattr(first.viewer_provider, "_rate_gate")
        assert shared_gate is getattr(first.memory_extractor, "_rate_gate")
        assert shared_gate is getattr(second.viewer_provider, "_rate_gate")
        assert shared_gate is getattr(second.memory_extractor, "_rate_gate")
    finally:
        await first.retire()
        await second.retire()
