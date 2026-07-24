import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from advx_backend.application.runtime_config_service import RuntimeApplyError
from advx_backend.application.runtime_provider import RuntimeProviderController
from advx_backend.application.runtime_session_service import (
    RuntimeSessionConflictError,
    RuntimeSessionRecoveryError,
    RuntimeSessionService,
)
from advx_backend.application.runtime_state import RuntimeStateStore
from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.contracts.configuration import (
    ProviderConfigurationRequest,
    RuntimeModelProviderCandidate,
)
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
    RuntimeApplyRequest,
    RuntimeRollbackRequest,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.infrastructure.persistence.sqlite import DatabaseConfig, SQLiteDatabase
from advx_backend.infrastructure.persistence.sqlite.models import (
    SessionRecordRow,
    SessionRuntimeRevisionRow,
)


class IncrementingClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now_ms(self) -> int:
        self.value += 1
        return self.value


class SequenceIdGenerator:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active.start()
    try:
        yield active
    finally:
        await active.close()


def runtime_spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash=f"{1:064x}",
        display_name="Persona",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=1,
        viewer_count=1,
        persona_ids=[persona.persona_id],
        persona_weights={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def create_service(database: SQLiteDatabase) -> RuntimeSessionService:
    return RuntimeSessionService(
        session_factory=database.session_factory,
        viewer_pool=ViewerPoolService(
            id_generator=SequenceIdGenerator("viewer"),
        ),
        clock=IncrementingClock(),
        id_generator=SequenceIdGenerator("session"),
        app_version="test",
    )


class RecordingProbe:
    def __init__(self) -> None:
        self.full = 0
        self.candidates: list[RuntimeModelProviderCandidate] = []
        self.candidate_error: Exception | None = None

    async def probe(self, spec: CanonicalRuntimeSpec) -> None:
        del spec
        self.full += 1

    async def probe_candidate(
        self,
        spec: CanonicalRuntimeSpec,
        candidate: RuntimeModelProviderCandidate,
    ) -> None:
        if self.candidate_error is not None:
            raise self.candidate_error
        assert candidate.role_models() == {
            "director": spec.provider.director_model,
            "viewer": spec.provider.viewer_model,
            "memory": spec.provider.memory_model,
            "visual_summary": spec.provider.visual_summary_model,
        }
        self.candidates.append(candidate)


class ClosableProvider:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def provider_configuration(name: str) -> ProviderConfigurationRequest:
    return ProviderConfigurationRequest(
        provider_profile_id=name,
        model_base_url=f"https://{name}.example/v1",
        model_name=name,
        director_model=f"{name}-director",
        viewer_model=f"{name}-viewer",
        memory_model=f"{name}-memory",
        visual_summary_model=f"{name}-visual",
        model_api_key=f"{name}-secret",
        asr_api_key="stable-asr-secret",
    )


def provider_candidate(name: str) -> RuntimeModelProviderCandidate:
    return RuntimeModelProviderCandidate(
        provider_profile_id=name,
        model_base_url=f"https://{name}.example/v1",
        model_name=name,
        director_model=f"{name}-director",
        viewer_model=f"{name}-viewer",
        memory_model=f"{name}-memory",
        visual_summary_model=f"{name}-visual",
        model_api_key=f"{name}-secret",
    )


def provider_runtime_spec(name: str) -> ProviderRuntimeSpec:
    return ProviderRuntimeSpec(
        provider_profile_id=name,
        director_model=f"{name}-director",
        viewer_model=f"{name}-viewer",
        memory_model=f"{name}-memory",
        visual_summary_model=f"{name}-visual",
    )


def hot_swap_service(
    database: SQLiteDatabase,
) -> tuple[
    RuntimeSessionService,
    RuntimeStateStore,
    RuntimeProviderController,
    RecordingProbe,
]:
    state = RuntimeStateStore()
    probe = RecordingProbe()
    controller = RuntimeProviderController(
        frame_resolver=object(),  # type: ignore[arg-type]
        configuration_committer=lambda _: None,
    )
    controller.install_initial(
        provider_configuration("old"),
        viewer_provider=ClosableProvider(),  # type: ignore[arg-type]
        memory_extractor=ClosableProvider(),  # type: ignore[arg-type]
    )
    service = RuntimeSessionService(
        session_factory=database.session_factory,
        viewer_pool=ViewerPoolService(id_generator=SequenceIdGenerator("viewer")),
        clock=IncrementingClock(),
        id_generator=SequenceIdGenerator("session"),
        capability_probe=probe,
        runtime_state=state,
        provider_controller=controller,
        app_version="test",
    )
    return service, state, controller, probe


async def start_runtime(service: RuntimeSessionService):
    spec = runtime_spec()
    return await service.start(
        RuntimeSessionStartRequest(
            client_request_id="request-1",
            canonical_runtime_spec=spec,
            client_config_hash=spec.config_hash(),
        )
    )


async def advance_runtime_to_revision_two(
    service: RuntimeSessionService,
):
    started = await start_runtime(service)
    next_spec = runtime_spec().model_copy(update={"config_revision": 2})
    applied = await service.apply(
        started.session_id,
        RuntimeApplyRequest(
            apply_id="apply-revision-2",
            base_revision=1,
            canonical_runtime_spec=next_spec,
            client_config_hash=next_spec.config_hash(),
        ),
    )
    return started, applied


@pytest.mark.asyncio
async def test_start_snapshot_reports_committed_apply_id_and_added_viewers(
    database: SQLiteDatabase,
) -> None:
    service = create_service(database)

    snapshot = await start_runtime(service)

    assert snapshot.apply_id == "start:request-1"
    assert snapshot.diff.added_viewer_ids == [
        viewer.viewer_instance_id for viewer in snapshot.viewers
    ]


@pytest.mark.asyncio
async def test_completed_runtime_session_cannot_be_recovered_without_side_effects(
    database: SQLiteDatabase,
) -> None:
    service = create_service(database)
    started = await start_runtime(service)
    async with database.session_factory() as session:
        await session.execute(
            update(SessionRecordRow)
            .where(SessionRecordRow.session_id == started.session_id)
            .values(state="stopped", ended_at_ms=2_000, outcome="completed")
        )
        await session.commit()

    with pytest.raises(
        RuntimeSessionRecoveryError,
        match="only an interrupted runtime Session can be recovered",
    ):
        await service.recover(started.session_id)

    async with database.session_factory() as session:
        record = await session.get(SessionRecordRow, started.session_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(SessionRuntimeRevisionRow)
            .where(SessionRuntimeRevisionRow.session_id == started.session_id)
        )
    assert record is not None
    assert (record.state, record.ended_at_ms, record.outcome) == (
        "stopped",
        2_000,
        "completed",
    )
    assert revision_count == 1


@pytest.mark.asyncio
async def test_interrupted_recovery_reopens_record_and_advances_epoch(
    database: SQLiteDatabase,
) -> None:
    service = create_service(database)
    started = await start_runtime(service)
    async with database.session_factory() as session:
        await session.execute(
            update(SessionRecordRow)
            .where(SessionRecordRow.session_id == started.session_id)
            .values(state="stopped", ended_at_ms=2_000, outcome="interrupted")
        )
        await session.commit()

    recovered = await service.recover(started.session_id)

    async with database.session_factory() as session:
        record = await session.get(SessionRecordRow, started.session_id)
    assert recovered.audience_epoch == started.audience_epoch + 1
    assert recovered.recovered is True
    assert record is not None
    assert (record.state, record.ended_at_ms, record.outcome) == (
        "running",
        None,
        None,
    )


@pytest.mark.asyncio
async def test_interrupted_recovery_commits_new_apply_id_and_diff_summary(
    database: SQLiteDatabase,
) -> None:
    service = create_service(database)
    started = await start_runtime(service)
    async with database.session_factory() as session:
        await session.execute(
            update(SessionRecordRow)
            .where(SessionRecordRow.session_id == started.session_id)
            .values(state="stopped", ended_at_ms=2_000, outcome="interrupted")
        )
        await session.commit()

    recovered = await service.recover(started.session_id)
    current = await service.current(started.session_id)

    assert recovered.apply_id == "recover:session-2"
    assert recovered.diff.retained_viewer_ids == [
        viewer.viewer_instance_id for viewer in recovered.viewers
    ]
    assert current.apply_id == recovered.apply_id
    assert current.diff == recovered.diff


@pytest.mark.asyncio
async def test_recovery_rejects_crash_orphan_and_uses_next_storage_revision(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await database.start()
    initial = runtime_spec()
    try:
        started = await create_service(database).start(
            RuntimeSessionStartRequest(
                client_request_id="start-before-crash",
                canonical_runtime_spec=initial,
                client_config_hash=initial.config_hash(),
            )
        )
        async with database.session_factory() as session:
            session.add(
                SessionRuntimeRevisionRow(
                    session_id=started.session_id,
                    revision=2,
                    apply_id="apply-crashed",
                    base_revision=1,
                    config_hash=initial.config_hash(),
                    status="pending",
                    canonical_spec_json=initial.canonical_json(),
                    diff_summary_json="{}",
                    created_at_ms=2_000,
                    updated_at_ms=2_000,
                )
            )
            await session.execute(
                update(SessionRecordRow)
                .where(SessionRecordRow.session_id == started.session_id)
                .values(
                    state="stopped",
                    ended_at_ms=2_001,
                    outcome="interrupted",
                )
            )
            await session.commit()
    finally:
        await database.close()

    restarted = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await restarted.start()
    try:
        recovered = await create_service(restarted).recover(started.session_id)
        async with restarted.session_factory() as session:
            revisions = list(
                await session.scalars(
                    select(SessionRuntimeRevisionRow)
                    .where(
                        SessionRuntimeRevisionRow.session_id == started.session_id
                    )
                    .order_by(SessionRuntimeRevisionRow.revision)
                )
            )

        assert recovered.canonical_runtime_spec == initial
        assert recovered.audience_epoch == started.audience_epoch + 1
        assert [(row.revision, row.status) for row in revisions] == [
            (1, "committed"),
            (2, "rejected"),
            (3, "committed"),
        ]
        assert revisions[2].base_revision == 1
        assert revisions[2].config_hash == initial.config_hash()
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_model_provider_hot_swap_commits_generation_with_epoch_without_asr_probe(
    database: SQLiteDatabase,
) -> None:
    service, state, controller, probe = hot_swap_service(database)
    initial_spec = runtime_spec().model_copy(
        update={"provider": provider_runtime_spec("old")}
    )
    started = await service.start(
        RuntimeSessionStartRequest(
            client_request_id="start-hot-swap",
            canonical_runtime_spec=initial_spec,
            client_config_hash=initial_spec.config_hash(),
        )
    )
    next_spec = initial_spec.model_copy(
        update={
            "config_revision": 2,
            "provider": provider_runtime_spec("next"),
        }
    )

    applied = await service.apply(
        started.session_id,
        RuntimeApplyRequest(
            apply_id="apply-provider-next",
            base_revision=1,
            canonical_runtime_spec=next_spec,
            client_config_hash=next_spec.config_hash(),
            provider_candidate=provider_candidate("next"),
        ),
    )

    committed = await state.snapshot(started.session_id)
    assert applied.audience_epoch == 2
    assert committed.audience_epoch == 2
    assert committed.provider_generation is controller._active
    assert committed.provider_generation is not None
    assert committed.provider_generation.provider_spec == next_spec.provider
    assert probe.full == 1
    assert len(probe.candidates) == 1
    assert "asr_api_key" not in probe.candidates[0].model_dump()


@pytest.mark.asyncio
async def test_failed_provider_swap_persistence_retires_candidate_and_keeps_old_generation(
    database: SQLiteDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, controller, _ = hot_swap_service(database)
    initial_spec = runtime_spec().model_copy(
        update={"provider": provider_runtime_spec("old")}
    )
    started = await service.start(
        RuntimeSessionStartRequest(
            client_request_id="start-failed-swap",
            canonical_runtime_spec=initial_spec,
            client_config_hash=initial_spec.config_hash(),
        )
    )
    old_generation = controller._active
    built = []
    original_build = controller.build

    def recording_build(*args, **kwargs):
        generation = original_build(*args, **kwargs)
        built.append(generation)
        return generation

    monkeypatch.setattr(controller, "build", recording_build)

    async def fail_persistence(**_: object) -> None:
        raise RuntimeError("persistence unavailable")

    monkeypatch.setattr(service, "_commit_reconciliation", fail_persistence)
    next_spec = initial_spec.model_copy(
        update={
            "config_revision": 2,
            "provider": provider_runtime_spec("next"),
        }
    )

    with pytest.raises(RuntimeError, match="persistence unavailable"):
        await service.apply(
            started.session_id,
            RuntimeApplyRequest(
                apply_id="apply-provider-fails",
                base_revision=1,
                canonical_runtime_spec=next_spec,
                client_config_hash=next_spec.config_hash(),
                provider_candidate=provider_candidate("next"),
            ),
        )

    committed = await state.snapshot(started.session_id)
    assert committed.audience_epoch == 1
    assert committed.provider_generation is old_generation
    assert controller._active is old_generation
    assert len(built) == 1
    assert built[0].retired is True
    assert built[0].closed is True


@pytest.mark.asyncio
async def test_failed_provider_candidate_probe_keeps_old_generation(
    database: SQLiteDatabase,
) -> None:
    service, state, controller, probe = hot_swap_service(database)
    initial_spec = runtime_spec().model_copy(
        update={"provider": provider_runtime_spec("old")}
    )
    started = await service.start(
        RuntimeSessionStartRequest(
            client_request_id="start-failed-probe",
            canonical_runtime_spec=initial_spec,
            client_config_hash=initial_spec.config_hash(),
        )
    )
    old_generation = controller._active
    probe.candidate_error = RuntimeError("redacted candidate probe failure")
    next_spec = initial_spec.model_copy(
        update={
            "config_revision": 2,
            "provider": provider_runtime_spec("next"),
        }
    )

    with pytest.raises(RuntimeApplyError, match="redacted candidate probe failure"):
        await service.apply(
            started.session_id,
            RuntimeApplyRequest(
                apply_id="apply-provider-probe-fails",
                base_revision=1,
                canonical_runtime_spec=next_spec,
                client_config_hash=next_spec.config_hash(),
                provider_candidate=provider_candidate("next"),
            ),
        )

    committed = await state.snapshot(started.session_id)
    assert committed.audience_epoch == 1
    assert committed.provider_generation is old_generation
    assert controller._active is old_generation


@pytest.mark.asyncio
async def test_successful_rollback_retry_is_idempotent_and_rejects_changed_payload(
    database: SQLiteDatabase,
) -> None:
    service = create_service(database)
    _, applied = await advance_runtime_to_revision_two(service)
    request = RuntimeRollbackRequest(
        apply_id="rollback-to-1",
        base_revision=2,
        target_revision=1,
    )

    first = await service.rollback(applied.session_id, request)
    retried = await service.rollback(applied.session_id, request)

    assert retried == first
    assert retried.audience_epoch == 3
    async with database.session_factory() as session:
        revision_count = await session.scalar(
            select(func.count())
            .select_from(SessionRuntimeRevisionRow)
            .where(SessionRuntimeRevisionRow.session_id == applied.session_id)
        )
    assert revision_count == 3

    with pytest.raises(
        RuntimeSessionConflictError,
        match="different rollback content",
    ):
        await service.rollback(
            applied.session_id,
            request.model_copy(update={"base_revision": 3}),
        )


@pytest.mark.asyncio
async def test_concurrent_successful_rollback_retries_commit_once(
    database: SQLiteDatabase,
) -> None:
    service = create_service(database)
    _, applied = await advance_runtime_to_revision_two(service)
    request = RuntimeRollbackRequest(
        apply_id="rollback-concurrent",
        base_revision=2,
        target_revision=1,
    )

    first, second = await asyncio.gather(
        service.rollback(applied.session_id, request),
        service.rollback(applied.session_id, request),
    )

    assert first == second
    assert first.audience_epoch == 3
    async with database.session_factory() as session:
        revision_count = await session.scalar(
            select(func.count())
            .select_from(SessionRuntimeRevisionRow)
            .where(SessionRuntimeRevisionRow.session_id == applied.session_id)
        )
    assert revision_count == 3


@pytest.mark.asyncio
async def test_provider_rollback_retry_does_not_repeat_probe_or_generation_swap(
    database: SQLiteDatabase,
) -> None:
    service, state, controller, probe = hot_swap_service(database)
    initial_spec = runtime_spec().model_copy(
        update={"provider": provider_runtime_spec("old")}
    )
    started = await service.start(
        RuntimeSessionStartRequest(
            client_request_id="start-provider-rollback",
            canonical_runtime_spec=initial_spec,
            client_config_hash=initial_spec.config_hash(),
        )
    )
    next_spec = initial_spec.model_copy(
        update={
            "config_revision": 2,
            "provider": provider_runtime_spec("next"),
        }
    )
    await service.apply(
        started.session_id,
        RuntimeApplyRequest(
            apply_id="apply-provider-before-rollback",
            base_revision=1,
            canonical_runtime_spec=next_spec,
            client_config_hash=next_spec.config_hash(),
            provider_candidate=provider_candidate("next"),
        ),
    )
    request = RuntimeRollbackRequest(
        apply_id="rollback-provider-to-old",
        base_revision=2,
        target_revision=1,
        provider_candidate=provider_candidate("old"),
    )

    first = await service.rollback(started.session_id, request)
    committed_generation = controller._active
    retried = await service.rollback(started.session_id, request)

    assert retried == first
    assert retried.audience_epoch == 3
    assert controller._active is committed_generation
    assert (await state.snapshot(started.session_id)).provider_generation is (
        committed_generation
    )
    assert len(probe.candidates) == 2

    changed_candidate = provider_candidate("old").model_copy(
        update={"model_api_key": "different-secret"}
    )
    with pytest.raises(
        RuntimeSessionConflictError,
        match="different rollback content",
    ):
        await service.rollback(
            started.session_id,
            request.model_copy(update={"provider_candidate": changed_candidate}),
        )
    assert controller._active is committed_generation
    assert len(probe.candidates) == 2
