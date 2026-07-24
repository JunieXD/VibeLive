import asyncio
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from advx_backend.application.frame_metadata import StoredFrameMetadataResolver
from advx_backend.application.frame_store import InMemoryFrameStore
from advx_backend.application.observation_wave_builder import select_frame_bundle
from advx_backend.application.ports.ingest import FrameInput, FrameStoreLimits
from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.room_service import RoomService
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_policies import (
    ActiveModeDirectorBudgetPolicy,
    DeterministicDirectorFallbackPolicy,
)
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.application.viewer_runtime_adapters import (
    PersistentViewerRoomWriter,
    RealtimeViewerBarragePublisher,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    EvidenceRef,
    EvidenceSource,
    ProviderRuntimeSpec,
    Room,
    ViewerAction,
    ViewerBarrageEvent,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.observation_wave import (
    FrameBundleItem,
    FrameBundleSettings,
    FrameSelectionStrategy,
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.viewer import ViewerInstance, ViewerInstanceVariant, ViewerPrivateState
from advx_backend.infrastructure.persistence.sqlite import DatabaseConfig, SQLiteDatabase
from advx_backend.infrastructure.persistence.sqlite.models import RoomEventRow
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    SQLiteRoomEventRepository,
    SQLiteRoomRepository,
    SQLiteSessionRuntimeRepository,
)


class FixedClock:
    def now_ms(self) -> int:
        return 120


class SequenceIds:
    def __init__(self) -> None:
        self.next = 1

    def new_id(self) -> str:
        value = f"id-{self.next}"
        self.next += 1
        return value


class ImmediateViewerProvider:
    async def generate(self, request: object) -> ViewerGenerationResponse:
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            text="Nice shot",
            reaction_type="comment",
            evidence_refs=[
                EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")
            ],
        )


class FixedBarragePipeline:
    def validate(self, **_: object) -> object:
        return SimpleNamespace(
            accepted=True,
            event=_barrage(),
            rejection_reason=None,
        )


class BlockingRealtimePublisher:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.entered.set()
        await self.release.wait()
        self.events.append(event)


class BlockingRoomEventRepository:
    def __init__(
        self,
        session: object,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._repository = SQLiteRoomEventRepository(session)
        self._entered = entered
        self._release = release

    async def append(self, event: object) -> None:
        self._entered.set()
        await self._release.wait()
        await self._repository.append(event)


def _persona() -> PersonaTemplate:
    return PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash="a" * 64,
        display_name="Viewer",
        role="viewer",
        silence_bias=0,
        burst_bias=0,
        repetition_bias=0,
        cooldown_ms=0,
    )


def _spec() -> CanonicalRuntimeSpec:
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=1,
        viewer_count=3,
        persona_ids=["persona-1"],
        persona_weights={"persona-1": 1},
        normal_response_range=ResponseRange(minimum=0, maximum=0),
        highlight_response_range=ResponseRange(minimum=1, maximum=3),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=0,
            updated_at_ms=1,
        ),
        active_mode_id=mode.mode_id,
        personas=[_persona()],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def _viewer(index: int, *, cooldown_until_ms: int | None = None) -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id=f"viewer-{index}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        persona_id="persona-1",
        persona_revision=1,
        ordinal=index,
        display_name=f"Viewer {index}",
        variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="game",
            silence_tendency=0.5,
        ),
        private_state=ViewerPrivateState(cooldown_until_ms=cooldown_until_ms),
        created_at_ms=0,
    )


def _pool() -> ViewerPoolSnapshot:
    return ViewerPoolSnapshot(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        mode_id="mode-1",
        session_seed="seed",
        viewers=[_viewer(1), _viewer(2), _viewer(3, cooldown_until_ms=500)],
    )


def _wave(
    observation_id: str = "observation-1",
    trigger: ObservationTrigger = ObservationTrigger.USER_TEXT,
) -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        created_at_ms=100,
        deadline_at_ms=500,
        triggers=[trigger],
        event_ids=["event-1"],
        trigger_event_ids=["event-1"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="Text-only user input.",
    )


def _barrage() -> ViewerBarrageEvent:
    return ViewerBarrageEvent(
        barrage_id="barrage-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        generation_request_id="request-1",
        viewer_instance_id="viewer-1",
        persona_id="persona-1",
        display_name="Viewer",
        viewer_sequence=1,
        reaction_type="comment",
        evidence_refs=[EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")],
        text="Nice shot",
        created_at_ms=110,
        expires_at_ms=500,
    )


def test_budget_uses_trigger_range_pool_and_cooldown_with_zero_legal() -> None:
    policy = ActiveModeDirectorBudgetPolicy()
    runtime = _spec()

    assert policy.maximum(wave=_wave(), pool=_pool(), runtime=runtime) == 2
    assert (
        policy.maximum(
            wave=_wave(trigger=ObservationTrigger.SCREEN_CHANGE),
            pool=_pool(),
            runtime=runtime,
        )
        == 0
    )


def test_fallback_is_deterministic_bounded_and_fresh_per_wave() -> None:
    policy = DeterministicDirectorFallbackPolicy()

    first = policy.decide(wave=_wave(), pool=_pool(), runtime=_spec(), maximum=9)
    repeated = policy.decide(wave=_wave(), pool=_pool(), runtime=_spec(), maximum=9)
    next_wave = policy.decide(
        wave=_wave("observation-2"), pool=_pool(), runtime=_spec(), maximum=1
    )
    quiet = policy.decide(wave=_wave(), pool=_pool(), runtime=_spec(), maximum=0)

    assert first == repeated
    assert len(first.selected_viewer_ids) == 2
    assert "viewer-3" not in first.selected_viewer_ids
    assert next_wave.decision_id != first.decision_id
    assert len(next_wave.selected_viewer_ids) == 1
    assert quiet.selected_viewer_ids == []


@pytest.mark.asyncio
async def test_realtime_publisher_converts_viewer_contract_to_domain_event() -> None:
    broker = RealtimeBroker()
    queue = await broker.subscribe_barrages()

    await RealtimeViewerBarragePublisher(broker).publish(_barrage())

    published = queue.get_nowait()
    assert published.barrage_id == "barrage-1"
    assert published.text == "Nice shot"
    assert published.evidence_refs[0].event_id == "event-1"


@pytest.mark.asyncio
async def test_room_writer_persists_canonical_event_before_exposing_it(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await database.start()
    try:
        await _seed_session(database)
        room_service = RoomService(
            clock=FixedClock(),
            id_generator=SequenceIds(),
            event_capacity=8,
            event_ttl_ms=10_000,
        )
        await room_service.start_session("session-1")
        state = RuntimeStateStore()
        await state.activate(
            CommittedRuntime(
                session_id="session-1",
                spec=_spec(),
                audience_epoch=1,
                pool=_pool(),
            )
        )
        assert await state.claim_viewer_sequence(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            viewer_instance_id="viewer-1",
            viewer_sequence=1,
        )
        writer = PersistentViewerRoomWriter(
            room_service=room_service,
            runtime_state=state,
            session_factory=database.session_factory,
        )

        await writer.append_published_barrage(_barrage())

        public = await room_service.read_events("session-1")
        assert len(public) == 1
        assert public[0].text == "Nice shot"
        async with database.session_factory() as session:
            row = await session.scalar(select(RoomEventRow))
        assert row is not None
        assert row.sequence == public[0].sequence
        content = json.loads(row.content_json)
        assert content["schema_version"] == 1
        assert content["room_id"] == "room-1"
        assert content["session_id"] == "session-1"
        assert content["audience_epoch"] == 1
        assert content["text"] == "Nice shot"
        assert content["payload"]["barrage_id"] == "barrage-1"
        assert row.content_hash == hashlib.sha256(row.content_json.encode()).hexdigest()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_room_writer_does_not_expose_event_when_persistence_fails(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await database.start()
    try:
        room_service = RoomService(
            clock=FixedClock(),
            id_generator=SequenceIds(),
            event_capacity=8,
            event_ttl_ms=10_000,
        )
        await room_service.start_session("session-1")
        state = RuntimeStateStore()
        await state.activate(
            CommittedRuntime(
                session_id="session-1",
                spec=_spec(),
                audience_epoch=1,
                pool=_pool(),
            )
        )
        assert await state.claim_viewer_sequence(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            viewer_instance_id="viewer-1",
            viewer_sequence=1,
        )
        writer = PersistentViewerRoomWriter(
            room_service=room_service,
            runtime_state=state,
            session_factory=database.session_factory,
        )

        with pytest.raises(Exception):
            await writer.append_published_barrage(_barrage())

        assert await room_service.read_events("session-1") == ()
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["stop", "replace"])
async def test_persistent_append_and_realtime_publish_precede_runtime_transition(
    tmp_path: Path,
    transition: str,
) -> None:
    database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await database.start()
    try:
        await _seed_session(database)
        room_service = RoomService(
            clock=FixedClock(),
            id_generator=SequenceIds(),
            event_capacity=8,
            event_ttl_ms=10_000,
        )
        await room_service.start_session("session-1")
        runtime_state = RuntimeStateStore()
        pool = _pool()
        await runtime_state.activate(
            CommittedRuntime(
                session_id="session-1",
                spec=_spec(),
                audience_epoch=1,
                pool=pool,
            )
        )
        publisher = BlockingRealtimePublisher()
        viewer_runtime = ViewerRuntime(
            provider=ImmediateViewerProvider(),
            barrage_pipeline=FixedBarragePipeline(),
            session_fence=runtime_state,
            publisher=publisher,
            room_service=PersistentViewerRoomWriter(
                room_service=room_service,
                runtime_state=runtime_state,
                session_factory=database.session_factory,
            ),
            clock=FixedClock(),
            id_generator=SequenceIds(),
            max_in_flight=1,
        )
        await viewer_runtime.start_session("session-1")
        wave = _wave()
        decision = CrowdDecision(
            decision_id="decision-1",
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            selected_viewer_ids=["viewer-1"],
            evidence_event_ids=["event-1"],
            created_at_ms=100,
            expires_at_ms=500,
        )
        dispatched = asyncio.create_task(
            viewer_runtime.dispatch(
                wave=wave,
                decision=decision,
                pool=pool,
                runtime=SimpleNamespace(),
            )
        )
        await asyncio.wait_for(publisher.entered.wait(), timeout=1)

        if transition == "stop":
            transitioning = asyncio.create_task(runtime_state.stop("session-1"))
        else:
            replacement_pool = pool.model_copy(
                update={
                    "audience_epoch": 2,
                    "viewers": [
                        item.model_copy(update={"audience_epoch": 2})
                        for item in pool.viewers
                    ],
                }
            )
            transitioning = asyncio.create_task(
                runtime_state.replace(
                    CommittedRuntime(
                        session_id="session-1",
                        spec=_spec(),
                        audience_epoch=2,
                        pool=replacement_pool,
                    )
                )
            )
        await asyncio.sleep(0)

        assert not transitioning.done()
        assert len(await room_service.read_events("session-1")) == 1
        assert publisher.events == []

        publisher.release.set()
        summary = await asyncio.wait_for(dispatched, timeout=1)
        await asyncio.wait_for(transitioning, timeout=1)
        published_after_transition = len(publisher.events)
        await asyncio.sleep(0)

        assert summary.published == 1
        assert [event.barrage_id for event in publisher.events] == ["barrage-1"]
        assert len(publisher.events) == published_after_transition
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_viewer_stop_waits_for_in_progress_persistent_publish(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await database.start()
    try:
        await _seed_session(database)
        room_service = RoomService(
            clock=FixedClock(),
            id_generator=SequenceIds(),
            event_capacity=8,
            event_ttl_ms=10_000,
        )
        await room_service.start_session("session-1")
        runtime_state = RuntimeStateStore()
        pool = _pool()
        await runtime_state.activate(
            CommittedRuntime(
                session_id="session-1",
                spec=_spec(),
                audience_epoch=1,
                pool=pool,
            )
        )
        repository_entered = asyncio.Event()
        release_repository = asyncio.Event()
        publisher = BlockingRealtimePublisher()
        publisher.release.set()
        viewer_runtime = ViewerRuntime(
            provider=ImmediateViewerProvider(),
            barrage_pipeline=FixedBarragePipeline(),
            session_fence=runtime_state,
            publisher=publisher,
            room_service=PersistentViewerRoomWriter(
                room_service=room_service,
                runtime_state=runtime_state,
                session_factory=database.session_factory,
                repository_factory=lambda session: BlockingRoomEventRepository(
                    session,
                    entered=repository_entered,
                    release=release_repository,
                ),
            ),
            clock=FixedClock(),
            id_generator=SequenceIds(),
            max_in_flight=1,
        )
        await viewer_runtime.start_session("session-1")
        wave = _wave()
        decision = CrowdDecision(
            decision_id="decision-stop-race",
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            selected_viewer_ids=["viewer-1"],
            evidence_event_ids=["event-1"],
            created_at_ms=100,
            expires_at_ms=500,
        )
        dispatched = asyncio.create_task(
            viewer_runtime.dispatch(
                wave=wave,
                decision=decision,
                pool=pool,
                runtime=SimpleNamespace(),
            )
        )
        await asyncio.wait_for(repository_entered.wait(), timeout=1)

        stopping = asyncio.create_task(viewer_runtime.stop_session("session-1"))
        await asyncio.sleep(0)
        stopped_while_blocked = stopping.done()
        release_repository.set()
        summary = await asyncio.wait_for(dispatched, timeout=1)
        await asyncio.wait_for(stopping, timeout=1)

        assert not stopped_while_blocked
        assert summary.published == 1
        assert len(await room_service.read_events("session-1")) == 1
        assert [event.barrage_id for event in publisher.events] == ["barrage-1"]
        async with database.session_factory() as session:
            rows = (await session.scalars(select(RoomEventRow))).all()
        assert len(rows) == 1
    finally:
        await database.close()


async def _seed_session(database: SQLiteDatabase) -> None:
    async with database.session_factory() as session:
        await SQLiteRoomRepository(session).get_or_create(
            "room-1", display_name="Room", now_ms=0
        )
        await SQLiteSessionRuntimeRepository(session).start(
            session_id="session-1",
            room_id="room-1",
            client_request_id="start-1",
            request_hash="hash",
            apply_id="apply-1",
            canonical_spec_json="{}",
            diff_summary_json="{}",
            app_version="test",
            now_ms=1,
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "body", "expected"),
    [
        (
            "image/png",
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 320, 200),
            (320, 200),
        ),
        (
            "image/jpeg",
            b"\xff\xd8\xff\xc0\x00\x11\x08\x00\xc8\x01\x40"
            + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00",
            (320, 200),
        ),
        (
            "image/webp",
            b"RIFF\x16\x00\x00\x00WEBPVP8X"
            + b"\x0a\x00\x00\x00\x00\x00\x00\x00\x3f\x01\x00\xc7\x00\x00",
            (320, 200),
        ),
    ],
)
async def test_frame_metadata_comes_from_real_bytes(
    mime_type: str,
    body: bytes,
    expected: tuple[int, int],
) -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=4,
            max_frame_bytes=1024,
            max_total_bytes=4096,
        ),
        id_generator=SequenceIds(),
    )
    await store.start_session("session-1")
    frame = await store.store(
        FrameInput(
            session_id="session-1",
            input_id=f"input-{mime_type}",
            captured_at_ms=100,
            mime_type=mime_type,
            body=body,
        )
    )

    metadata = await StoredFrameMetadataResolver(frame_store=store).resolve(
        session_id="session-1",
        frame=frame,
    )

    assert metadata is not None
    assert (metadata.width, metadata.height) == expected
    assert metadata.encoding == mime_type
    assert metadata.content_hash == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_frame_metadata_returns_none_for_invalid_or_mismatched_bytes() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=4,
            max_frame_bytes=1024,
            max_total_bytes=4096,
        ),
        id_generator=SequenceIds(),
    )
    await store.start_session("session-1")
    frame = await store.store(
        FrameInput(
            session_id="session-1",
            input_id="bad",
            captured_at_ms=100,
            mime_type="image/png",
            body=b"not an image",
        )
    )

    assert (
        await StoredFrameMetadataResolver(
            frame_store=store,
        ).resolve(
            session_id="session-1",
            frame=frame,
        )
        is None
    )


@pytest.mark.asyncio
async def test_real_frame_store_metadata_carries_encoded_frame_change_score() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=4,
            max_frame_bytes=1024,
            max_total_bytes=4096,
        ),
        id_generator=SequenceIds(),
    )
    await store.start_session("session-1")
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 320, 200)
    refs = []
    for index, payload in enumerate((b"stable", b"stable", b"changed-scene")):
        refs.append(
            await store.store(
                FrameInput(
                    session_id="session-1",
                    input_id=f"frame-{index}",
                    captured_at_ms=100 + index,
                    mime_type="image/png",
                    body=header + payload,
                )
            )
        )
    resolver = StoredFrameMetadataResolver(frame_store=store)

    metadata = [
        await resolver.resolve(session_id="session-1", frame=frame)
        for frame in refs
    ]

    assert [item.change_score for item in metadata if item is not None][:2] == [0.0, 0.0]
    assert metadata[2] is not None
    assert metadata[2].change_score > 0


@pytest.mark.asyncio
async def test_change_score_follows_ingest_order_not_frame_resolution_lru() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=4,
            max_frame_bytes=1024,
            max_total_bytes=4096,
        ),
        id_generator=SequenceIds(),
    )
    await store.start_session("session-1")
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 320, 200)
    first = await store.store(
        FrameInput("session-1", "first", 100, "image/png", header + b"aaaa")
    )
    second = await store.store(
        FrameInput("session-1", "second", 200, "image/png", header + b"bbbb")
    )
    await store.resolve(session_id="session-1", frame=first)
    third = await store.store(
        FrameInput("session-1", "third", 300, "image/png", header + b"bbbb")
    )

    metadata = await StoredFrameMetadataResolver(frame_store=store).resolve(
        session_id="session-1",
        frame=third,
    )

    assert second.frame_id != third.frame_id
    assert metadata is not None
    assert metadata.change_score == 0.0


@pytest.mark.asyncio
async def test_real_frame_store_data_refs_distinguish_all_frame_strategies() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=8,
            max_frame_bytes=1024,
            max_total_bytes=8192,
        ),
        id_generator=SequenceIds(),
    )
    await store.start_session("session-1")
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 320, 200)
    refs = []
    payloads = (b"aaaa", b"zzzz", b"bbbb", b"yyyy", b"cccc")
    visual_scores = (0.0, 0.01, 0.9, 0.02, 0.8)
    for index, (payload, change_score) in enumerate(zip(payloads, visual_scores, strict=True)):
        refs.append(
            await store.store(
                FrameInput(
                    session_id="session-1",
                    input_id=f"strategy-{index}",
                    captured_at_ms=(index + 1) * 100,
                    mime_type="image/png",
                    body=header + payload,
                    change_score=change_score,
                )
            )
        )
    resolver = StoredFrameMetadataResolver(frame_store=store)
    frames = []
    for index, frame in enumerate(refs):
        metadata = await resolver.resolve(session_id="session-1", frame=frame)
        assert metadata is not None
        frames.append(
            FrameBundleItem(
                frame_id=frame.frame_id,
                frame_index=index,
                captured_at_ms=frame.created_at_ms,
                width=metadata.width,
                height=metadata.height,
                encoding=metadata.encoding,
                content_hash=metadata.content_hash,
                data_ref=frame.data_ref,
                change_score=metadata.change_score,
            )
        )

    selections = {
        strategy: [
            item.frame_id
            for item in select_frame_bundle(
                frames=frames,
                settings=FrameBundleSettings(
                    frame_bundle_size=2,
                    frame_window_ms=1_000,
                    frame_selection_strategy=strategy,
                ),
                now_ms=500,
            )
        ]
        for strategy in FrameSelectionStrategy
    }

    assert selections[FrameSelectionStrategy.LATEST_N] == [
        refs[3].frame_id,
        refs[4].frame_id,
    ]
    assert selections[FrameSelectionStrategy.EVENLY_SPACED] == [
        refs[0].frame_id,
        refs[4].frame_id,
    ]
    assert selections[FrameSelectionStrategy.CHANGE_PEAKS] == [
        refs[2].frame_id,
        refs[4].frame_id,
    ]
