import json
from pathlib import Path
from types import SimpleNamespace

from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision, DecisionSource
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
    del data_directory
    bundle = fixture["bundle"]

    initial_spec = CanonicalRuntimeSpec.model_validate(
        fixture["initial_canonical_runtime_spec"]
    )
    updated_spec = CanonicalRuntimeSpec.model_validate(bundle["canonical_runtime_spec"])
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
        max_in_flight=len(updated_pool.viewers),
    )
    await runtime.start_session("cs2-session-1")
    selected_ids = [
        viewer.viewer_instance_id
        for viewer in updated_pool.viewers
        if viewer.is_active() and not viewer.is_muted(wave.created_at_ms)
    ]
    assessment = SceneAssessment(
        assessment_id="autonomous-cs2-1",
        room_id=wave.room_id,
        session_id=wave.session_id,
        audience_epoch=wave.audience_epoch,
        observation_id=wave.observation_id,
        salience=1.0,
        novelty=1.0,
        emotional_intensity=0.0,
        replyable_event_ids=["cs2-event-1"],
        evidence_event_ids=["cs2-event-1"],
        maximum_responses=len(selected_ids),
        created_at_ms=1_250,
        expires_at_ms=2_000,
    )
    decision = CrowdDecision(
        decision_id=assessment.assessment_id,
        room_id=wave.room_id,
        session_id=wave.session_id,
        audience_epoch=wave.audience_epoch,
        observation_id=wave.observation_id,
        selected_viewer_ids=selected_ids,
        reason_codes=["per_viewer_independent_decision"],
        evidence_event_ids=list(assessment.evidence_event_ids),
        decision_source=DecisionSource.AUTONOMOUS,
        created_at_ms=assessment.created_at_ms,
        expires_at_ms=assessment.expires_at_ms,
    )
    runtime_context = SimpleNamespace(
        canonical_runtime_spec=updated_spec,
        settings=updated_spec.settings,
        scene_assessment=assessment,
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
    expected_updated_counts = {
        **expected_initial_counts,
        "instigator": 4,
        "reaction_qmark": 2,
    }
    active_mode = next(
        mode for mode in updated_spec.modes if mode.mode_id == updated_spec.active_mode_id
    )
    return {
        "artifact_version": 4,
        "proof_scope": fixture["proof_scope"],
        "fixture_bundle_id": bundle["bundle_id"],
        "desktop_source": fixture["desktop_source"],
        "canonical_hash": {
            "fixture": bundle["config_hash"],
            "backend": updated_spec.config_hash(),
            "matches": bundle["config_hash"] == updated_spec.config_hash(),
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
                set(fixture["desktop_source"]["primary_persona_ids"])
                == {
                    persona_id
                    for persona_id, count in active_mode.persona_counts.items()
                    if count > 1
                }
            ),
            "canonical_hash_matches_backend": bundle["config_hash"]
            == updated_spec.config_hash(),
            "initial_persona_counts_are_exact": initial_counts
            == expected_initial_counts,
            "updated_persona_counts_are_exact": updated_counts
            == expected_updated_counts,
            "hot_update_reconciliation_is_exact": (
                len(reconciliation.retained_viewer_ids) == 27
                and not reconciliation.added_viewer_ids
                and not reconciliation.removed_viewer_ids
                and len(reconciliation.reset_viewer_ids) == 1
            ),
            "all_active_viewers_are_called": (
                set(request_viewer_ids) == set(selected_ids)
                and len(request_viewer_ids) == len(selected_ids)
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
        },
        "not_proven": ["electron_ui", "credentialed_live_provider"],
    }
