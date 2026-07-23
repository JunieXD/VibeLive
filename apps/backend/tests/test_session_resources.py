import pytest

from advx_backend.application.session_resources import SessionResources


class RecordingResource:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    async def start_session(self, session_id: str) -> None:
        self.events.append(f"start:{self.name}:{session_id}")
        if self.fail_start:
            raise RuntimeError(f"start failed: {self.name}")

    async def stop_session(self, session_id: str) -> None:
        self.events.append(f"stop:{self.name}:{session_id}")
        if self.fail_stop:
            raise RuntimeError(f"stop failed: {self.name}")


class RecordingBarragePipeline:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def clear_session(self, session_id: str) -> None:
        self.events.append(f"clear:barrage:{session_id}")


def resources(
    context: RecordingResource,
    barrage: RecordingBarragePipeline,
    *additional: RecordingResource,
) -> SessionResources:
    return SessionResources(
        context_builder=context,  # type: ignore[arg-type]
        barrage_pipeline=barrage,  # type: ignore[arg-type]
        resources=additional,
    )


@pytest.mark.asyncio
async def test_session_resources_start_in_order_and_stop_in_reverse() -> None:
    events: list[str] = []
    context = RecordingResource("context", events)
    audience = RecordingResource("audience", events)
    ingest = RecordingResource("ingest", events)
    coordinator = resources(context, RecordingBarragePipeline(events), audience, ingest)

    await coordinator.start_session("session-1")
    await coordinator.stop_session("session-1")

    assert events == [
        "start:context:session-1",
        "start:audience:session-1",
        "start:ingest:session-1",
        "stop:ingest:session-1",
        "stop:audience:session-1",
        "stop:context:session-1",
        "clear:barrage:session-1",
    ]


@pytest.mark.asyncio
async def test_session_resources_roll_back_started_resources_after_partial_start() -> None:
    events: list[str] = []
    context = RecordingResource("context", events)
    audience = RecordingResource("audience", events)
    ingest = RecordingResource("ingest", events, fail_start=True)
    coordinator = resources(context, RecordingBarragePipeline(events), audience, ingest)

    with pytest.raises(RuntimeError, match="start failed: ingest"):
        await coordinator.start_session("session-1")

    assert events == [
        "start:context:session-1",
        "start:audience:session-1",
        "start:ingest:session-1",
        "stop:audience:session-1",
        "stop:context:session-1",
    ]


@pytest.mark.asyncio
async def test_session_resources_continue_cleanup_after_stop_failure() -> None:
    events: list[str] = []
    context = RecordingResource("context", events)
    audience = RecordingResource("audience", events, fail_stop=True)
    ingest = RecordingResource("ingest", events)
    coordinator = resources(context, RecordingBarragePipeline(events), audience, ingest)
    await coordinator.start_session("session-1")
    events.clear()

    with pytest.raises(RuntimeError, match="stop failed: audience"):
        await coordinator.stop_session("session-1")

    assert events == [
        "stop:ingest:session-1",
        "stop:audience:session-1",
        "stop:context:session-1",
        "clear:barrage:session-1",
    ]


@pytest.mark.asyncio
async def test_session_resources_preserve_start_error_when_rollback_fails() -> None:
    events: list[str] = []
    context = RecordingResource("context", events, fail_stop=True)
    audience = RecordingResource("audience", events, fail_start=True)
    coordinator = resources(context, RecordingBarragePipeline(events), audience)

    with pytest.raises(RuntimeError, match="start failed: audience"):
        await coordinator.start_session("session-1")

    assert events == [
        "start:context:session-1",
        "start:audience:session-1",
        "stop:context:session-1",
    ]
