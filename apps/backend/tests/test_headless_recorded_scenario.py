import json
import subprocess
import sys
from pathlib import Path

import pytest

from advx_backend.application.headless_harness import EXIT_OK, HeadlessHarness
from advx_backend.contracts.replay import (
    RecordedProviderOutput,
    ReplayBundle,
    ReplayEvent,
    ReplayRequest,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange


def _bundle() -> ReplayBundle:
    persona = PersonaTemplate(
        persona_id="scenario-persona",
        document_version=1,
        revision=1,
        content_hash="a" * 64,
        display_name="Scenario Viewer",
        role="commentator",
        silence_bias=0,
        burst_bias=1,
        repetition_bias=0,
        cooldown_ms=1,
    )
    mode = ModeDefinition(
        mode_id="scenario-mode",
        namespace_id="scenario-memes",
        revision=1,
        viewer_count=1,
        persona_ids=[persona.persona_id],
        persona_weights={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=1, maximum=1),
        highlight_response_range=ResponseRange(minimum=1, maximum=1),
    )
    spec = CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="scenario-room",
            display_name="Scenario Room",
            created_at_ms=1_000,
            updated_at_ms=1_000,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="recorded",
            director_model="recorded-director",
            viewer_model="recorded-viewer",
            memory_model="recorded-memory",
            visual_summary_model="recorded-visual",
        ),
    )
    roles = ("director", "viewer", "memory", "visual_summary", "asr")
    outputs = {
        "director": {"reason_codes": ["recorded"]},
        "viewer": {
            "action": "barrage",
            "text": "recorded viewer response",
            "reaction_type": "reply",
        },
        "memory": {
            "candidates": [
                {
                    "memory_type": "shared_experience",
                    "content": "the room completed the recorded scenario",
                    "tags": ["scenario"],
                    "importance": 0.7,
                    "confidence": 1,
                }
            ]
        },
        "visual_summary": {"summary": "a deterministic synthetic frame"},
        "asr": {
            "text": "recorded final transcript",
            "final": True,
            "started_at_ms": 1_010,
            "ended_at_ms": 1_020,
        },
    }
    counts = {"director": 3, "viewer": 1, "memory": 1}
    return ReplayBundle(
        bundle_id="headless-recorded-scenario",
        created_at_ms=1_000,
        seed=42,
        virtual_clock_start_ms=1_000,
        config_hash=spec.config_hash(),
        canonical_runtime_spec=spec,
        events=[
            ReplayEvent(
                sequence=index,
                event_type=f"{role}.completed",
                occurred_at_ms=1_000 + index,
                payload={
                    "generation_request_ids": [
                        f"recorded-{role}-{call_index}"
                        for call_index in range(1, counts.get(role, 1) + 1)
                    ]
                },
            )
            for index, role in enumerate(roles, start=1)
        ],
        recorded_provider_outputs=[
            RecordedProviderOutput(
                generation_request_id=f"recorded-{role}-{call_index}",
                provider_role=role,
                output=(
                    {
                        **outputs[role],
                        "text": f"recorded viewer response {call_index}",
                    }
                    if role == "viewer"
                    else outputs[role]
                ),
            )
            for role in roles
            for call_index in range(1, counts.get(role, 1) + 1)
        ],
    )


@pytest.mark.asyncio
async def test_scenario_uses_production_graph_and_covers_runtime_lifecycle(
    tmp_path: Path,
) -> None:
    code, response = await HeadlessHarness(data_directory=tmp_path).execute(
        {
            "command": "scenario",
            "request": ReplayRequest(bundle=_bundle()).model_dump(mode="json"),
        }
    )

    assert code == EXIT_OK
    result = response["result"]
    assert result["production_graph"]["runtime_identity"] is True
    assert result["production_graph"]["sqlite_started"] is True
    assert result["production_graph"]["capability_probe_calls"] >= 1
    assert result["session"]["initial_revision"] == 1
    assert result["session"]["applied_revision"] == 2
    assert result["session"]["stopped_state"] == "idle"
    assert result["ingest"] == {
        "text": "text",
        "voice": "audio",
        "frame": "frame",
        "final_asr_delivered": True,
    }
    assert result["dispatch"]["director_calls"] >= 3
    assert result["dispatch"]["viewer_calls"] == 1
    assert result["dispatch"]["visual_summary_calls"] >= 1
    assert result["observed"]["memory_count"] >= 1
    assert (
        result["observed"]["meme_count"]
        + result["observed"]["pending_meme_count"]
        >= 1
    )
    assert result["observed"]["trace_count"] >= 1
    assert result["replay"]["deterministic_proof"] is True
    assert result["replay"]["recorded_evidence"]["director_decisions"]
    assert result["replay"]["recorded_evidence"]["selected_viewer_ids"]
    assert result["replay"]["recorded_evidence"]["barrages"]
    assert result["replay"]["recorded_evidence"]["memories"]
    assert result["replay"]["recorded_evidence"]["traces"]
    assert result["external_transport_call_count"] == 0
    trace_path = Path(result["observed"]["trace_artifact"])
    assert trace_path.is_file()
    assert json.loads(trace_path.read_text(encoding="utf-8"))["redacted"] is True


def test_scenario_cli_cleans_its_isolated_directory_on_success() -> None:
    script = Path(__file__).parents[1] / "scripts" / "viewer_runtime_headless.py"
    payload = {
        "command": "scenario",
        "request": ReplayRequest(bundle=_bundle()).model_dump(mode="json"),
    }
    completed = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == EXIT_OK, completed.stdout
    assert completed.stderr == ""
    response = json.loads(completed.stdout)
    assert response["metadata"]["temporary_directory_cleaned"] is True
    assert not Path(response["metadata"]["data_directory"]).exists()
