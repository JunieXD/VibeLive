import json
from pathlib import Path
from types import SimpleNamespace

from advx_backend.application.director_service import DirectorService
from advx_backend.application.headless_harness import EXIT_OK, HeadlessHarness
from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.contracts.replay import ReplayBundle, ReplayRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.observation_wave import (
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.scene_assessment import SceneAssessment


class FixedClock:
    def now_ms(self) -> int:
        return 1_250


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"fake-viewer-call-{self.value}"


class NeverLiveProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def replay(self, bundle: ReplayBundle) -> None:
        del bundle
        self.calls += 1
        raise AssertionError("recorded replay must not call a live provider")


class FakeDirector:
    def __init__(self, preferred_viewer_ids: list[str]) -> None:
        self.calls: list[object] = []
        self.preferred_viewer_ids = preferred_viewer_ids

    async def decide(self, request: object) -> SceneAssessment:
        self.calls.append(request)
        return SceneAssessment(
            assessment_id="assessment-cs2-1",
            room_id=request.wave.room_id,
            session_id=request.wave.session_id,
            audience_epoch=request.wave.audience_epoch,
            observation_id=request.wave.observation_id,
            salience=1.0,
            novelty=1.0,
            emotional_intensity=1.0,
            topics=["cs2", "highlight"],
            emotional_tone=["excited"],
            replyable_event_ids=["cs2-event-1"],
            evidence_event_ids=["cs2-event-1"],
            maximum_responses=min(2, request.maximum),
            created_at_ms=1_250,
            expires_at_ms=2_000,
        )


class FixedBudget:
    def maximum(self, **context: object) -> int:
        del context
        return 2


class ForbiddenFallback:
    def decide(self, **context: object) -> CrowdDecision:
        del context
        raise AssertionError("deterministic Director fixture must not use fallback")


class FakeViewer:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        action = ViewerAction.BARRAGE if len(self.requests) == 1 else ViewerAction.SILENCE
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=action,
            text="clean synthetic highlight" if action is ViewerAction.BARRAGE else None,
            reaction_type="highlight" if action is ViewerAction.BARRAGE else "silence",
            evidence_refs=[
                EvidenceRef(source=EvidenceSource.EVENT, event_id="cs2-event-1")
            ],
        )


class AcceptingPipeline:
    def validate(self, *, request: object, response: object) -> object:
        del request
        return SimpleNamespace(accepted=True, event=response, rejection_reason=None)


class AcceptingFence:
    async def accepts(self, **scope: object) -> bool:
        del scope
        return True


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)

    async def append_published_barrage(self, event: object) -> None:
        self.events.append(event)


def _counts(pool: object) -> dict[str, int]:
    persona_ids = [viewer.persona_id for viewer in pool.viewers]
    return {
        persona_id: persona_ids.count(persona_id)
        for persona_id in sorted(set(persona_ids))
    }


def _pool_identity(pool: object) -> list[dict[str, object]]:
    return [
        {
            "viewer_instance_id": viewer.viewer_instance_id,
            "persona_id": viewer.persona_id,
            "ordinal": viewer.ordinal,
            "display_name": viewer.display_name,
        }
        for viewer in pool.viewers
    ]


async def collect_evidence(
    fixture_path: Path,
    *,
    data_directory: Path,
) -> dict[str, object]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    bundle = ReplayBundle.model_validate(fixture["bundle"])
    live_provider = NeverLiveProvider()
    harness = HeadlessHarness(
        data_directory=data_directory,
        live_provider=live_provider,
    )
    exit_code, replay = await harness.execute(
        {
            "command": "replay",
            "request": ReplayRequest(bundle=bundle).model_dump(mode="json"),
        }
    )
    if exit_code != EXIT_OK:
        raise AssertionError(replay)

    initial_spec = CanonicalRuntimeSpec.model_validate(
        fixture["initial_canonical_runtime_spec"]
    )
    updated_spec = bundle.canonical_runtime_spec
    pool_service = ViewerPoolService(id_generator=SequenceIds())
    initial_pool = pool_service.create_pool(
        room_id=updated_spec.room.room_id,
        session_id="cs2-session-1",
        audience_epoch=1,
        session_seed="cs2-seed-6657",
        spec=initial_spec,
    )
    reconciliation = pool_service.reconcile(
        current=initial_pool,
        next_epoch=2,
        spec=updated_spec,
    )
    updated_pool = reconciliation.snapshot

    wave = ObservationWave(
        room_id=updated_spec.room.room_id,
        session_id="cs2-session-1",
        audience_epoch=2,
        observation_id="cs2-wave-1",
        created_at_ms=1_200,
        deadline_at_ms=2_000,
        triggers=[ObservationTrigger.SCREEN_CHANGE],
        event_ids=["cs2-event-1"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="Synthetic CS2 highlight marker.",
    )
    hot_instigator_ids = [
        viewer.viewer_instance_id
        for viewer in updated_pool.viewers
        if viewer.persona_id == "instigator"
    ]
    director_provider = FakeDirector(hot_instigator_ids)
    director = DirectorService(
        provider=director_provider,
        budget_policy=FixedBudget(),
        fallback=ForbiddenFallback(),
        clock=FixedClock(),
    )
    outcome = await director.decide(
        wave=wave,
        pool=updated_pool,
        runtime=updated_spec,
    )
    viewer_provider = FakeViewer()
    sink = RecordingSink()
    runtime = ViewerRuntime(
        provider=viewer_provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=AcceptingFence(),
        publisher=sink,
        room_service=sink,
        clock=FixedClock(),
        id_generator=SequenceIds(),
        max_in_flight=2,
    )
    await runtime.start_session("cs2-session-1")
    selected_ids = hot_instigator_ids[: outcome.assessment.maximum_responses]
    decision = CrowdDecision(
        decision_id=outcome.assessment.assessment_id,
        room_id=wave.room_id,
        session_id=wave.session_id,
        audience_epoch=wave.audience_epoch,
        observation_id=wave.observation_id,
        selected_viewer_ids=selected_ids,
        reason_codes=["recorded_per_viewer_behavior"],
        evidence_event_ids=list(outcome.assessment.evidence_event_ids),
        created_at_ms=outcome.assessment.created_at_ms,
        expires_at_ms=outcome.assessment.expires_at_ms,
    )
    runtime_context = SimpleNamespace(
        canonical_runtime_spec=updated_spec,
        settings=updated_spec.settings,
        scene_assessment=outcome.assessment,
    )
    summary = await runtime.dispatch(
        wave=wave,
        decision=decision,
        pool=updated_pool,
        runtime=runtime_context,
    )

    selected_by_id = {
        viewer.viewer_instance_id: viewer for viewer in updated_pool.viewers
    }
    request_viewer_ids = [request.viewer_instance_id for request in viewer_provider.requests]
    request_ids = [request.generation_request_id for request in viewer_provider.requests]
    selected_identity = [
        {
            "viewer_instance_id": viewer_id,
            "persona_id": selected_by_id[viewer_id].persona_id,
            "display_name": selected_by_id[viewer_id].display_name,
        }
        for viewer_id in selected_ids
    ]
    request_identity = [
        {
            "viewer_instance_id": request.viewer_instance_id,
            "persona_id": request.mode_context["_viewer_persona_id"],
            "display_name": request.mode_context["_viewer_display_name"],
            "generation_request_id": request.generation_request_id,
        }
        for request in viewer_provider.requests
    ]
    initial_counts = _counts(initial_pool)
    updated_counts = _counts(updated_pool)
    expected_initial_counts = {
        "abstract_radio": 3,
        "cheat_suspector": 1,
        "clip_alarm": 1,
        "fun_seeker": 3,
        "grudge_keeper": 2,
        "hardmouth_antifan": 3,
        "instigator": 3,
        "jinx_machine": 2,
        "meme_archivist": 3,
        "parrot_unit": 2,
        "praise_then_bite": 1,
        "reaction_qmark": 3,
        "room_historian": 1,
    }
    expected_updated_counts = expected_initial_counts
    active_mode = next(
        mode for mode in updated_spec.modes if mode.mode_id == updated_spec.active_mode_id
    )
    return {
        "artifact_version": 2,
        "proof_scope": fixture["proof_scope"],
        "fixture_bundle_id": bundle.bundle_id,
        "desktop_source": fixture["desktop_source"],
        "canonical_hash": {
            "fixture": bundle.config_hash,
            "backend": updated_spec.config_hash(),
            "matches": bundle.config_hash == updated_spec.config_hash(),
        },
        "replay": {
            "exit_code": exit_code,
            "deterministic_proof": replay["result"]["deterministic_proof"],
            "credentialed_provider_proof": replay["result"][
                "credentialed_provider_proof"
            ],
            "live_provider_calls": live_provider.calls,
            "external_transport_call_count": replay["result"][
                "external_transport_call_count"
            ],
            "event_count": replay["result"]["event_count"],
        },
        "hot_update": {
            "initial_counts": initial_counts,
            "updated_counts": updated_counts,
            "initial_pool": _pool_identity(initial_pool),
            "updated_pool": _pool_identity(updated_pool),
            "retained_viewer_ids": list(reconciliation.retained_viewer_ids),
            "reset_viewer_ids": list(reconciliation.reset_viewer_ids),
            "added_viewer_ids": list(reconciliation.added_viewer_ids),
            "removed_viewer_ids": list(reconciliation.removed_viewer_ids),
        },
        "call_identity": {
            "director_calls": len(director_provider.calls),
            "selected_viewer_ids": selected_ids,
            "request_viewer_ids": request_viewer_ids,
            "selected_identity": selected_identity,
            "request_identity": request_identity,
            "generation_request_ids": request_ids,
            "unique_generation_request_ids": len(set(request_ids)),
            "published": summary.published,
            "silenced": summary.silenced,
        },
        "claims": {
            "desktop_room_6657_primary_personas_are_real_preset": (
                fixture["desktop_source"]["primary_persona_ids"]
                == active_mode.persona_ids[:9]
            ),
            "canonical_hash_matches_backend": bundle.config_hash
            == updated_spec.config_hash(),
            "initial_hamilton_allocation_is_exact": initial_counts
            == expected_initial_counts,
            "updated_hamilton_allocation_is_exact": updated_counts
            == expected_updated_counts,
            "hot_update_reconciliation_is_exact": (
                len(reconciliation.retained_viewer_ids) == 28
                and not reconciliation.added_viewer_ids
                and not reconciliation.removed_viewer_ids
                and not reconciliation.reset_viewer_ids
            ),
            "one_director_call": len(director_provider.calls) == 1,
            "hot_update_instigator_calls_visible": (
                bool(request_identity)
                and all(
                    item["persona_id"] == "instigator"
                    for item in request_identity
                )
            ),
            "selected_and_request_identity_match": (
                [
                    (item["viewer_instance_id"], item["persona_id"], item["display_name"])
                    for item in selected_identity
                ]
                == [
                    (item["viewer_instance_id"], item["persona_id"], item["display_name"])
                    for item in request_identity
                ]
            ),
            "one_independent_call_per_selected_viewer": (
                request_viewer_ids == selected_ids
                and len(set(request_ids)) == len(selected_ids)
            ),
            "recorded_replay_used_no_external_provider": (
                live_provider.calls == 0
                and replay["result"]["external_transport_call_count"] == 0
            ),
        },
        "not_proven": ["electron_ui", "credentialed_live_provider"],
    }
