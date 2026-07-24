import pytest

from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
    RuntimeApplyRequest,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange


class SequenceIdGenerator:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"viewer-{self.value}"


class FixedClock:
    def now_ms(self) -> int:
        return 1_000


def persona(persona_id: str, *, revision: int = 1, enabled: bool = True) -> PersonaTemplate:
    return PersonaTemplate(
        persona_id=persona_id,
        document_version=1,
        revision=revision,
        content_hash=f"{revision:064x}",
        display_name=persona_id,
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
        enabled=enabled,
    )


def mode(
    weights: list[tuple[str, float]],
    *,
    viewer_count: int,
    revision: int = 1,
    mode_id: str = "mode-1",
) -> ModeDefinition:
    persona_ids = [persona_id for persona_id, _ in weights]
    return ModeDefinition(
        mode_id=mode_id,
        namespace_id=f"{mode_id}-namespace",
        revision=revision,
        viewer_count=viewer_count,
        persona_ids=persona_ids,
        persona_weights=dict(weights),
        normal_response_range=ResponseRange(minimum=0, maximum=viewer_count),
        highlight_response_range=ResponseRange(minimum=0, maximum=viewer_count),
    )


def allocate(
    service: object,
    definition: ModeDefinition,
    templates: list[PersonaTemplate],
    *,
    seed: str = "seed-1",
    epoch: int = 1,
):
    spec = runtime_spec(definition, templates)
    return service.create_pool(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        session_seed=seed,
        spec=spec,
    )


def runtime_spec(
    definition: ModeDefinition,
    templates: list[PersonaTemplate],
    *,
    revision: int | None = None,
) -> CanonicalRuntimeSpec:
    return CanonicalRuntimeSpec(
        config_revision=revision or definition.revision,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id=definition.mode_id,
        personas=templates,
        modes=[definition],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def test_hamilton_allocation_uses_mode_order_to_break_equal_remainders() -> None:
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    service = ViewerPoolService(id_generator=SequenceIdGenerator())
    definition = mode([("first", 1), ("second", 1), ("third", 1)], viewer_count=5)

    viewers = allocate(
        service,
        definition,
        [persona("first"), persona("second"), persona("third")],
    )

    counts = {
        persona_id: sum(viewer.persona_id == persona_id for viewer in viewers.viewers)
        for persona_id in definition.persona_ids
    }
    assert counts == {"first": 2, "second": 2, "third": 1}


def test_hamilton_allocation_filters_disabled_unknown_and_nonpositive_personas() -> None:
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    service = ViewerPoolService(id_generator=SequenceIdGenerator())
    definition = mode(
        [("enabled", 1), ("disabled", 100), ("zero", 0)],
        viewer_count=4,
    )

    viewers = allocate(
        service,
        definition,
        [persona("enabled"), persona("disabled", enabled=False), persona("zero")],
    )

    assert len(viewers.viewers) == 4
    assert {viewer.persona_id for viewer in viewers.viewers} == {"enabled"}


def test_aliases_and_variants_are_stable_for_the_same_seed() -> None:
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    service = ViewerPoolService(id_generator=SequenceIdGenerator())
    definition = mode([("persona-a", 1)], viewer_count=3)
    templates = [persona("persona-a")]

    first = allocate(service, definition, templates)
    second = allocate(service, definition, templates)

    assert [
        (viewer.viewer_instance_id, viewer.display_name, viewer.variant)
        for viewer in first.viewers
    ] == [
        (viewer.viewer_instance_id, viewer.display_name, viewer.variant)
        for viewer in second.viewers
    ]
    assert len({viewer.display_name for viewer in first.viewers}) == 3


def test_variants_change_when_the_session_seed_changes() -> None:
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    service = ViewerPoolService(id_generator=SequenceIdGenerator())
    definition = mode([("persona-a", 1)], viewer_count=2)
    templates = [persona("persona-a")]

    first = allocate(service, definition, templates, seed="seed-1")
    second = allocate(service, definition, templates, seed="seed-2")

    assert [viewer.variant for viewer in first.viewers] != [
        viewer.variant for viewer in second.viewers
    ]


class InMemoryRuntimeRepository:
    def __init__(self, active_spec: object, active_epoch: int = 4) -> None:
        self.active_spec = active_spec
        self.active_epoch = active_epoch
        self.pending: list[object] = []
        self.commits: list[tuple[object, int]] = []
        self.rollbacks: list[str] = []

    async def stage(self, session_id: str, request: RuntimeApplyRequest) -> None:
        del session_id
        self.pending.append(request)

    async def commit(self, spec: object, audience_epoch: int) -> None:
        self.active_spec = spec
        self.active_epoch = audience_epoch
        self.commits.append((spec, audience_epoch))

    async def rollback(self, apply_id: str) -> None:
        self.rollbacks.append(apply_id)


class ConfigurableProbe:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.specs: list[object] = []

    async def probe(self, spec: object) -> None:
        self.specs.append(spec)
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_failed_apply_rolls_back_without_advancing_epoch_or_active_spec() -> None:
    from advx_backend.application.runtime_config_service import (
        RuntimeApplyError,
        RuntimeConfigService,
    )
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    old_spec = runtime_spec(mode([("old", 1)], viewer_count=1), [persona("old")])
    new_spec = runtime_spec(
        mode([("new", 1)], viewer_count=1, revision=2),
        [persona("new")],
    )
    repository = InMemoryRuntimeRepository(old_spec)
    service = RuntimeConfigService(
        repository=repository,
        viewer_pool=ViewerPoolService(id_generator=SequenceIdGenerator()),
        capability_probe=ConfigurableProbe(error=RuntimeError("unsupported model")),
        clock=FixedClock(),
    )
    request = RuntimeApplyRequest(
        apply_id="apply-new",
        base_revision=1,
        canonical_runtime_spec=new_spec,
        client_config_hash=new_spec.config_hash(),
    )

    with pytest.raises(RuntimeApplyError, match="unsupported model"):
        await service.prepare_apply("session-1", request)

    assert repository.active_spec is old_spec
    assert repository.active_epoch == 4
    assert repository.commits == []
    assert repository.rollbacks == ["apply-new"]


@pytest.mark.asyncio
async def test_successful_apply_commits_spec_and_epoch_together() -> None:
    from advx_backend.application.runtime_config_service import RuntimeConfigService
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    old_spec = runtime_spec(mode([("old", 1)], viewer_count=1), [persona("old")])
    new_spec = runtime_spec(
        mode([("new", 1)], viewer_count=1, revision=2),
        [persona("new")],
    )
    repository = InMemoryRuntimeRepository(old_spec)
    service = RuntimeConfigService(
        repository=repository,
        viewer_pool=ViewerPoolService(id_generator=SequenceIdGenerator()),
        capability_probe=ConfigurableProbe(),
        clock=FixedClock(),
    )
    request = RuntimeApplyRequest(
        apply_id="apply-new",
        base_revision=1,
        canonical_runtime_spec=new_spec,
        client_config_hash=new_spec.config_hash(),
    )

    await service.prepare_apply("session-1", request)
    applied = await service.commit_at_wave_boundary("session-1", "apply-new")

    assert applied.audience_epoch == 5
    assert repository.commits == [(new_spec, 5)]
    assert repository.active_spec is new_spec


def test_selective_reconciliation_retains_only_unchanged_viewer_state() -> None:
    from advx_backend.application.viewer_pool_service import ViewerPoolService

    service = ViewerPoolService(id_generator=SequenceIdGenerator())
    old_mode = mode([("stable", 1), ("changed", 1)], viewer_count=2)
    old_personas = [persona("stable"), persona("changed")]
    previous = allocate(service, old_mode, old_personas, epoch=4)
    viewers = list(previous.viewers)
    viewers[0] = viewers[0].model_copy(
        update={
            "private_state": viewers[0].private_state.model_copy(
                update={"published_event_ids": ["event-1"]}
            )
        }
    )
    viewers[1] = viewers[1].model_copy(
        update={
            "private_state": viewers[1].private_state.model_copy(
                update={"published_event_ids": ["event-2"]}
            )
        }
    )
    previous = previous.model_copy(update={"viewers": viewers})
    new_mode = mode([("stable", 1), ("changed", 1)], viewer_count=2, revision=2)

    reconciled = service.reconcile(
        current=previous,
        next_epoch=5,
        spec=runtime_spec(
            new_mode,
            [persona("stable"), persona("changed", revision=2)],
        ),
    )

    by_persona = {viewer.persona_id: viewer for viewer in reconciled.snapshot.viewers}
    assert by_persona["stable"].private_state.published_event_ids == ["event-1"]
    assert by_persona["changed"].private_state.published_event_ids == []
    assert reconciled.retained_viewer_ids == (by_persona["stable"].viewer_instance_id,)
