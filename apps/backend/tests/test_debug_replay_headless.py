import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from advx_backend.application.debug_service import DebugService
from advx_backend.application.headless_harness import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_UNSAFE_ARTIFACT,
    HeadlessHarness,
)
from advx_backend.application.replay_service import ReplayService
from advx_backend.contracts.debug import (
    DirectorBudgetTrace,
    PromptManifest,
    ProviderTrace,
    TraceQuery,
    TraceResponseStatus,
    ValidationTrace,
    ViewerRequestTrace,
)
from advx_backend.contracts.replay import (
    RecordedProviderOutput,
    ReplayBundle,
    ReplayEvent,
    ReplayMode,
    ReplayRequest,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
    ViewerRuntimeTelemetry,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.viewer import ViewerInstanceVariant
from advx_backend.infrastructure.logging.trace_store import (
    TraceStore,
    UnsafeTraceArtifactError,
    assert_redacted_artifact,
)


def runtime_spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash="a" * 64,
        display_name="Viewer",
        role="commentator",
        silence_bias=0.2,
        burst_bias=0.4,
        repetition_bias=0.1,
        cooldown_ms=500,
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1-memes",
        revision=1,
        viewer_count=1,
        persona_ids=["persona-1"],
        persona_weights={"persona-1": 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=1, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=100,
            updated_at_ms=100,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="vision",
        ),
    )


def trace(number: int = 1) -> ViewerRequestTrace:
    variant = ViewerInstanceVariant(
        expression_length=0.5,
        skepticism=0.5,
        encouragement=0.5,
        meme_affinity=0.5,
        focus="gameplay",
        silence_tendency=0.2,
    )
    decision = CrowdDecision(
        decision_id=f"decision-{number}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=f"observation-{number}",
        selected_viewer_ids=["viewer-1"],
        created_at_ms=100,
        expires_at_ms=200,
    )
    return ViewerRequestTrace(
        trace_id=f"trace-{number}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        config_hash="b" * 64,
        observation_id=f"observation-{number}",
        director_budget=DirectorBudgetTrace(
            minimum=0,
            maximum=1,
            available_viewer_ids=["viewer-1"],
        ),
        director_decision=decision,
        viewer_instance_id="viewer-1",
        viewer_sequence=number,
        persona_revision=1,
        instance_variant=variant,
        memory=RoomMemorySlice(room_id="room-1", memory_revision=0),
        prompt_manifest=PromptManifest(
            template_id="viewer-v1",
            template_revision=1,
            input_hash="c" * 64,
            sections=["public-context"],
        ),
        provider=ProviderTrace(
            provider_role="viewer",
            model_id="model-1",
            queued_at_ms=100,
            completed_at_ms=110,
        ),
        response_status=TraceResponseStatus.COMPLETED,
        validation=ValidationTrace(accepted=True),
    )


def bundle(*, unsafe_output: bool = False) -> ReplayBundle:
    spec = runtime_spec()
    viewer_output = {"action": "barrage", "text": "recorded viewer response"}
    if unsafe_output:
        viewer_output = {
            "action": "silence",
            "text": "sk-this-must-never-be-exported",
        }
    outputs = {
        "director": {"reason_codes": ["recorded"]},
        "viewer": viewer_output,
        "memory": {
            "candidates": [
                {
                    "memory_type": "shared_experience",
                    "content": "recorded replay memory",
                    "tags": ["recorded"],
                    "importance": 0.5,
                    "confidence": 1,
                }
            ]
        },
        "visual_summary": {"summary": "recorded replay frame"},
        "asr": {
            "text": "recorded replay transcript",
            "final": True,
            "started_at_ms": 1_010,
            "ended_at_ms": 1_020,
        },
    }
    roles = ("director", "viewer", "memory", "visual_summary", "asr")
    counts = {"director": 3, "viewer": 1, "memory": 1}
    return ReplayBundle(
        bundle_id="bundle-1",
        created_at_ms=100,
        seed=42,
        virtual_clock_start_ms=1_000,
        config_hash=spec.config_hash(),
        canonical_runtime_spec=spec,
        events=[
            ReplayEvent(
                sequence=index,
                event_type=f"{role}.completed",
                occurred_at_ms=1_100 + index,
                payload={
                    "generation_request_ids": [
                        f"request-{role}-{call_index}"
                        for call_index in range(1, counts.get(role, 1) + 1)
                    ]
                },
            )
            for index, role in enumerate(roles, start=1)
        ],
        recorded_provider_outputs=[
            RecordedProviderOutput(
                generation_request_id=f"request-{role}-{call_index}",
                provider_role=role,
                output=(
                    {
                        **outputs[role],
                        "text": f"recorded viewer response {call_index}",
                    }
                    if role == "viewer" and not unsafe_output
                    else outputs[role]
                ),
            )
            for role in roles
            for call_index in range(1, counts.get(role, 1) + 1)
        ],
    )


def test_trace_store_is_bounded_queryable_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    store = TraceStore(max_items=2, path=path)
    store.append(trace(1))
    store.append(trace(2))
    store.append(trace(3))

    first = store.query(TraceQuery(limit=1))
    second = store.query(TraceQuery(limit=1, cursor=first.next_cursor))
    assert [item.trace_id for item in first.items + second.items] == ["trace-2", "trace-3"]
    assert second.next_cursor is None

    restored = TraceStore(max_items=2, path=path)
    assert [item.trace_id for item in restored.query().items] == ["trace-2", "trace-3"]
    exported = tmp_path / "export.json"
    assert restored.export(exported) == 2
    assert "sk-" not in exported.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_debug_service_composes_runtime_trace_and_agent_telemetry(
    tmp_path: Path,
) -> None:
    request_trace = trace()
    store = TraceStore(max_items=10, path=tmp_path / "traces.jsonl")
    store.append(request_trace)
    spec = runtime_spec()
    pool = {
        "room_id": "room-1",
        "session_id": "session-1",
        "audience_epoch": 1,
        "mode_id": "mode-1",
        "session_seed": "redacted",
        "viewers": [],
    }

    class State:
        async def debug_snapshot(self, session_id: str) -> object:
            return SimpleNamespace(
                session_id=session_id,
                spec=spec,
                audience_epoch=1,
                pool=pool,
                accepting_results=True,
            )

    class Agent:
        def telemetry_snapshot(self, session_id: str) -> ViewerRuntimeTelemetry:
            assert session_id == "session-1"
            return ViewerRuntimeTelemetry(selected=2, queued=2, completed=1)

    snapshot = await DebugService(
        store,
        runtime_state=State(),
        runtime_agent=Agent(),
    ).runtime_snapshot("session-1")

    assert snapshot.telemetry == ViewerRuntimeTelemetry(
        selected=2,
        queued=2,
        completed=1,
    )
    assert snapshot.director_budgets == [request_trace.director_budget]
    assert snapshot.history[0]["trace_id"] == "trace-1"
    assert snapshot.queue is not None
    assert snapshot.queue.depth is None
    assert snapshot.queue.capacity == spec.settings.viewer_queue_capacity
    assert snapshot.unavailable == ["queue.depth"]


def test_strict_redaction_rejects_secrets_prompts_raw_media_and_provider_payloads() -> None:
    unsafe_values = [
        {"api_key": "value"},
        {"prompt": "full prompt"},
        {"image": "base64 bytes"},
        {"output": "Bearer abcdefghijklmnop"},
        {"provider_raw_response": {"message": "raw"}},
    ]
    for value in unsafe_values:
        with pytest.raises(UnsafeTraceArtifactError):
            assert_redacted_artifact(value)


class FailingIfCalledProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def replay(self, replay_bundle: ReplayBundle) -> None:
        del replay_bundle
        self.calls += 1


@pytest.mark.asyncio
async def test_recorded_replay_never_calls_external_provider() -> None:
    provider = FailingIfCalledProvider()
    result = await ReplayService(live_provider=provider).replay(
        ReplayRequest(bundle=bundle())
    )

    assert provider.calls == 0
    assert result.deterministic_proof is True
    assert result.credentialed_provider_proof is False
    assert result.completed_at_ms == 1_105
    assert result.replay_digest is not None
    assert result.recorded_evidence is not None
    assert result.recorded_evidence.external_transport_call_count == 0


@pytest.mark.asyncio
async def test_recorded_replay_runs_production_graph_deterministically() -> None:
    service = ReplayService()
    request = ReplayRequest(bundle=bundle())

    first = await service.replay(request)
    second = await service.replay(request)

    assert first.replay_digest == second.replay_digest
    assert first.recorded_evidence == second.recorded_evidence
    evidence = first.recorded_evidence
    assert evidence is not None
    assert evidence.director_decisions
    assert evidence.selected_viewer_ids
    assert len(evidence.barrages) == 1
    assert [item["text"] for item in evidence.barrages] == [
        "recorded viewer response 1",
    ]
    assert evidence.memories
    assert evidence.traces
    consumed_viewers = [
        item
        for item in evidence.consumed_provider_outputs
        if item.provider_role == "viewer"
    ]
    assert [item.generation_request_id for item in consumed_viewers] == [
        "request-viewer-1",
    ]
    for role in {
        item.provider_role for item in evidence.consumed_provider_outputs
    }:
        role_indexes = [
            item.call_index
            for item in evidence.consumed_provider_outputs
            if item.provider_role == role
        ]
        assert role_indexes[0] == 1
        assert role_indexes == list(range(1, len(role_indexes) + 1))
    assert len({item.runtime_request_id for item in consumed_viewers}) == 1
    assert first.external_transport_call_count == 0


@pytest.mark.asyncio
async def test_recorded_replay_rejects_tampered_or_missing_outputs() -> None:
    original = bundle()
    outputs = list(original.recorded_provider_outputs)
    viewer_index = next(
        index
        for index, output in enumerate(outputs)
        if output.provider_role == "viewer"
        and output.generation_request_id == "request-viewer-1"
    )
    outputs[viewer_index] = outputs[viewer_index].model_copy(
        update={"generation_request_id": "request-viewer-tampered"}
    )
    tampered = original.model_copy(
        update={
            "recorded_provider_outputs": outputs,
            "recorded_outputs_digest": None,
        }
    )
    object.__setattr__(
        tampered,
        "recorded_outputs_digest",
        tampered.compute_recorded_outputs_digest(),
    )
    missing = original.model_copy(
        update={
            "recorded_provider_outputs": [
                output
                for output in original.recorded_provider_outputs
                if output.generation_request_id != "request-viewer-1"
            ],
            "recorded_outputs_digest": None,
        }
    )
    object.__setattr__(
        missing,
        "recorded_outputs_digest",
        missing.compute_recorded_outputs_digest(),
    )

    for replay_bundle in (tampered, missing):
        with pytest.raises(ValueError, match="do not match replay events"):
            await ReplayService().replay(ReplayRequest(bundle=replay_bundle))


def test_replay_bundle_requires_redacted_role_outputs() -> None:
    payload = bundle().model_dump(mode="json")
    payload["redacted"] = False
    with pytest.raises(ValidationError):
        ReplayBundle.model_validate(payload)

    payload = bundle().model_dump(mode="json")
    payload["recorded_provider_outputs"] = []
    with pytest.raises(ValidationError):
        ReplayBundle.model_validate(payload)


@pytest.mark.asyncio
async def test_live_replay_rejects_provider_without_verified_provenance() -> None:
    provider = FailingIfCalledProvider()
    request = ReplayRequest(
        mode=ReplayMode.LIVE,
        bundle=bundle(),
        allow_external_provider_calls=True,
    )
    with pytest.raises(RuntimeError, match="verified provenance"):
        await ReplayService(live_provider=provider).replay(request)

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_headless_harness_has_stable_codes_and_isolation_metadata(tmp_path: Path) -> None:
    harness = HeadlessHarness(data_directory=tmp_path)
    exit_code, response = await harness.execute(
        {
            "command": "replay",
            "request": ReplayRequest(bundle=bundle()).model_dump(mode="json"),
        }
    )
    assert exit_code == EXIT_OK
    assert response["metadata"] == {
        "seed": 42,
        "virtual_clock_start_ms": 1_000,
        "data_directory": str(tmp_path.resolve()),
        "isolated_data_directory": True,
        "sqlite_path": str((tmp_path / "advx.sqlite3").resolve()),
        "port": 0,
        "token_scope": "headless",
        "room_id": "room-1",
    }

    invalid_code, invalid = await harness.execute({"command": "unknown"})
    assert invalid_code == EXIT_INVALID_INPUT
    assert invalid["error"]["code"] == "invalid_input"

    unsafe_code, unsafe = await harness.execute(
        {
            "command": "replay",
            "request": ReplayRequest(bundle=bundle(unsafe_output=True)).model_dump(
                mode="json"
            ),
        }
    )
    assert unsafe_code == EXIT_UNSAFE_ARTIFACT
    assert unsafe["error"]["code"] == "unsafe_artifact"


@pytest.mark.asyncio
async def test_headless_replay_injects_seed_clock_and_isolated_run_directories(
    tmp_path: Path,
) -> None:
    observations: list[tuple[int, int, Path, Path, int, str, str]] = []

    async def runner(
        replay_bundle: ReplayBundle,
        data_directory: Path,
        execution: object,
    ) -> object:
        observations.append(
            (
                execution.random.randint(0, 1_000_000),
                execution.clock.now_ms(),
                data_directory,
                execution.sqlite_path,
                execution.port,
                execution.local_token,
                execution.room_id,
            )
        )
        return {
            "director_decisions": [],
            "selected_viewer_ids": [],
            "barrages": [],
            "memories": [],
            "traces": [],
            "consumed_provider_roles": [],
            "consumed_provider_outputs": [],
            "external_transport_call_count": 0,
        }

    harness = HeadlessHarness(data_directory=tmp_path, recorded_runner=runner)
    payload = {
        "command": "replay",
        "request": ReplayRequest(bundle=bundle()).model_dump(mode="json"),
    }
    first_code, first = await harness.execute(payload)
    second_code, second = await harness.execute(payload)

    assert first_code == second_code == EXIT_OK
    assert observations[0][0:2] == observations[2][0:2]
    assert observations[1][0:2] == observations[3][0:2]
    assert observations[0][1] == 1_000
    assert observations[0][2] != observations[1][2]
    assert observations[2][2] != observations[3][2]
    assert all(
        path.parent == tmp_path.resolve() / "replay"
        for _, _, path, _, _, _, _ in observations
    )
    assert all(
        sqlite_path == path / "advx.sqlite3"
        for _, _, path, sqlite_path, _, _, _ in observations
    )
    assert {port for _, _, _, _, port, _, _ in observations} == {0}
    assert len({token for _, _, _, _, _, token, _ in observations}) == 4
    assert {room_id for _, _, _, _, _, _, room_id in observations} == {"room-1"}
    assert first["result"]["replay_digest"] == second["result"]["replay_digest"]


def test_headless_cli_accepts_json_stdin_and_emits_only_json() -> None:
    script = Path(__file__).parents[1] / "scripts" / "viewer_runtime_headless.py"
    payload = {
        "command": "replay",
        "request": ReplayRequest(bundle=bundle()).model_dump(mode="json"),
    }
    completed = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == EXIT_OK
    assert completed.stderr == ""
    response = json.loads(completed.stdout)
    assert response["ok"] is True
    assert response["result"]["deterministic_proof"] is True
