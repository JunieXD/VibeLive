import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "e2e" / "cs2_viewer_runtime_live_stepfun_evidence.json"
RUNNER = ROOT / "apps" / "backend" / "scripts" / "viewer_runtime_live_stepfun.py"
RUN_LIVE_ENV = "ADVX_RUN_LIVE_STEPFUN"
RUNNER_OPT_IN_ENV = "ADVX_RUN_STEPFUN_LIVE"
REQUIRED_CLAIMS = {
    "capabilities_passed",
    "credentialed_provider_proof",
    "external_real_game_smoke_passed",
    "frame_text_final_asr_observed",
    "independent_live_viewer_responses_succeeded",
    "live_director_response_succeeded",
    "mode_meme_persistence_observed",
    "production_graph",
    "room_events_recovered",
    "room_memory_shared_cross_session_mode",
    "same_logical_session_new_epoch",
    "viewer_trace_identity_proved",
    "weight_6657_changed_pool",
}
REQUIRED_SCENARIOS = {
    "normal_silence_no_fabrication",
    "highlight_multi_kill",
    "obvious_mistake",
    "final_voice_structured_mention",
    "user_text_response",
    "6657_persona_allocation_call_ratio",
    "cross_session_mode_memory_injection",
    "meme_candidate_lifecycle",
}
FORBIDDEN_TEXT = ("api_key", "authorization", "bearer ", "raw_audio", "base64")


def _strict_independent_wave_proved(evidence: dict[str, object]) -> bool:
    independent_wave = evidence.get("independent_live_wave")
    if not isinstance(independent_wave, dict):
        return False
    observation = independent_wave.get("observation")
    epoch = independent_wave.get("epoch")
    requests = independent_wave.get("requests")
    viewers = independent_wave.get("viewers")
    count = independent_wave.get("count")
    if (
        not isinstance(observation, str)
        or len(observation) != 16
        or not isinstance(epoch, int)
        or epoch < 1
        or not isinstance(requests, list)
        or not all(isinstance(item, str) for item in requests)
        or not isinstance(viewers, list)
        or not all(isinstance(item, str) for item in viewers)
        or not isinstance(count, int)
        or count < 2
        or len(set(requests)) < 2
        or len(set(viewers)) < 2
    ):
        return False

    call_identity = evidence.get("call_identity")
    if not isinstance(call_identity, dict):
        return False
    provider_calls = call_identity.get("provider_calls")
    traces = call_identity.get("items")
    if not isinstance(provider_calls, list) or not isinstance(traces, list):
        return False
    same_wave_calls = [
        item
        for item in provider_calls
        if isinstance(item, dict)
        and item.get("observation") == observation
        and item.get("epoch") == epoch
    ]
    if not any(
        item.get("role") == "decide" and item.get("status") == "passed"
        for item in same_wave_calls
    ):
        return False
    generate_pairs = {
        (item.get("request"), item.get("viewer"))
        for item in same_wave_calls
        if item.get("role") == "generate"
        and item.get("status") == "passed"
        and isinstance(item.get("request"), str)
        and isinstance(item.get("viewer"), str)
    }
    trace_pairs = {
        (item.get("request"), item.get("viewer"))
        for item in traces
        if isinstance(item, dict)
        and item.get("observation") == observation
        and item.get("epoch") == epoch
        and item.get("status") in {"published", "silence"}
        and isinstance(item.get("request"), str)
        and isinstance(item.get("viewer"), str)
    }
    return (
        len(generate_pairs) >= 2
        and len(generate_pairs) == count
        and trace_pairs == generate_pairs
        and {request for request, _ in generate_pairs} == set(requests)
        and {viewer for _, viewer in generate_pairs} == set(viewers)
    )


def _hash(value: object, *, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _scenario_contract_proved(scenario: object) -> bool:
    if not isinstance(scenario, dict) or scenario.get("status") != "passed":
        return False
    input_evidence = scenario.get("input")
    director = scenario.get("director")
    mappings = scenario.get("viewer_personas")
    responses = scenario.get("responses")
    oracle = scenario.get("oracle")
    if (
        not isinstance(input_evidence, dict)
        or not _hash(input_evidence.get("sha256"), length=64)
        or not isinstance(input_evidence.get("event_refs"), list)
        or not all(_hash(item, length=16) for item in input_evidence["event_refs"])
        or not isinstance(input_evidence.get("frame_refs", []), list)
        or not all(
            _hash(item, length=64) for item in input_evidence.get("frame_refs", [])
        )
        or not isinstance(director, dict)
        or director.get("status") not in {"passed", "silence"}
        or not isinstance(director.get("selected_viewers"), list)
        or not isinstance(director.get("evidence_event_refs"), list)
        or not all(
            _hash(item, length=16) for item in director["evidence_event_refs"]
        )
        or not set(director["evidence_event_refs"]).issubset(
            input_evidence["event_refs"]
        )
        or not isinstance(mappings, list)
        or not all(
            isinstance(item, dict)
            and _hash(item.get("viewer"), length=16)
            and isinstance(item.get("persona"), str)
            and bool(item["persona"])
            for item in mappings
        )
        or not isinstance(responses, list)
        or not all(
            isinstance(item, dict)
            and _hash(item.get("request"), length=16)
            and _hash(item.get("viewer"), length=16)
            and item.get("status") in {"published", "silence"}
            and isinstance(item.get("reaction"), str)
            and isinstance(item.get("evidence_event_refs"), list)
            and all(
                _hash(event_ref, length=16)
                for event_ref in item["evidence_event_refs"]
            )
            and set(item["evidence_event_refs"]).issubset(
                input_evidence["event_refs"]
            )
            and isinstance(item.get("evidence_frame_refs", []), list)
            and all(
                _hash(frame_ref, length=64)
                for frame_ref in item.get("evidence_frame_refs", [])
            )
            and set(item.get("evidence_frame_refs", [])).issubset(
                input_evidence.get("frame_refs", [])
            )
            for item in responses
        )
        or not isinstance(oracle, dict)
        or oracle.get("passed") is not True
        or not isinstance(oracle.get("checks"), dict)
        or not oracle["checks"]
        or not all(value is True for value in oracle["checks"].values())
    ):
        return False
    selected = director["selected_viewers"]
    mapped_viewers = {item["viewer"] for item in mappings}
    response_viewers = {item["viewer"] for item in responses}
    if not all(_hash(item, length=16) for item in selected):
        return False
    if not set(selected).issubset(mapped_viewers) or not response_viewers.issubset(
        mapped_viewers
    ):
        return False

    scenario_id = scenario.get("scenario_id")
    checks = oracle["checks"]
    if scenario_id == "normal_silence_no_fabrication":
        return checks.get("silence_allowed") is True and checks.get(
            "no_fabricated_kill"
        ) is True
    if scenario_id in {"highlight_multi_kill", "obvious_mistake"}:
        return (
            bool(
                input_evidence["event_refs"]
                or input_evidence.get("frame_refs", [])
            )
            and checks.get("responses_reference_current_wave") is True
        )
    if scenario_id == "final_voice_structured_mention":
        target = scenario.get("mentioned_viewer")
        return (
            _hash(target, length=16)
            and target in selected
            and checks.get("structured_mention_selected") is True
        )
    if scenario_id == "user_text_response":
        return bool(responses) and checks.get("user_text_response_observed") is True
    if scenario_id == "6657_persona_allocation_call_ratio":
        metrics = scenario.get("metrics")
        return (
            isinstance(metrics, dict)
            and isinstance(metrics.get("high_weight_allocated"), int)
            and isinstance(metrics.get("low_weight_allocated"), int)
            and isinstance(metrics.get("high_weight_calls"), int)
            and isinstance(metrics.get("low_weight_calls"), int)
            and metrics["high_weight_allocated"] > metrics["low_weight_allocated"]
            and metrics["high_weight_calls"] > metrics["low_weight_calls"]
            and checks.get("allocation_ratio_proved") is True
            and checks.get("actual_call_ratio_proved") is True
        )
    if scenario_id == "cross_session_mode_memory_injection":
        memory = scenario.get("memory")
        return (
            isinstance(memory, dict)
            and _hash(memory.get("memory_id"), length=16)
            and memory.get("cross_session") is True
            and memory.get("cross_mode") is True
            and memory.get("injected_into_real_viewer_request") is True
            and checks.get("memory_prompt_injection_proved") is True
        )
    if scenario_id == "meme_candidate_lifecycle":
        lifecycle = scenario.get("meme_lifecycle")
        return (
            isinstance(lifecycle, dict)
            and _hash(lifecycle.get("candidate_id"), length=16)
            and lifecycle.get("auto_ingested") is True
            and lifecycle.get("overlay_event_count") == 0
            and lifecycle.get("undo_accepted") is True
            and lifecycle.get("absent_after_restart") is True
            and checks.get("candidate_never_overlayed") is True
            and checks.get("undo_survived_restart") is True
        )
    return False


def _real_game_provenance_proved(
    evidence: dict[str, object],
    trace_requests: set[object],
) -> bool:
    smoke = evidence.get("real_game_smoke")
    if not isinstance(smoke, dict):
        return False
    frame = smoke.get("frame")
    provenance = smoke.get("provenance")
    calls = smoke.get("production_provider_calls")
    if (
        smoke.get("status") != "passed"
        or smoke.get("capability") != "image_input"
        or smoke.get("model_id") != evidence.get("provider", {}).get("model")
        or not isinstance(frame, dict)
        or frame.get("source_kind") != "reviewed_real_game_capture"
        or not _hash(frame.get("sha256"), length=64)
        or not isinstance(frame.get("bytes"), int)
        or frame["bytes"] <= 0
        or not isinstance(frame.get("width"), int)
        or frame["width"] <= 0
        or not isinstance(frame.get("height"), int)
        or frame["height"] <= 0
        or frame.get("mime_type") not in {"image/jpeg", "image/png", "image/webp"}
        or not isinstance(provenance, dict)
        or provenance.get("status") != "verified"
        or provenance.get("reviewer_attestation") is not True
        or provenance.get("content_kind") != "real_game_capture"
        or provenance.get("frame_sha256") != frame["sha256"]
        or provenance.get("width") != frame["width"]
        or provenance.get("height") != frame["height"]
        or not isinstance(calls, list)
        or not calls
    ):
        return False
    return all(
        isinstance(call, dict)
        and call.get("role") == "generate"
        and call.get("status") == "passed"
        and call.get("request") in trace_requests
        and frame["sha256"] in call.get("frame_sha256", [])
        and bool(call.get("frame_refs"))
        for call in calls
    )


def _validate_evidence(path: Path = OUTPUT) -> bool:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    claims = evidence.get("claims")
    scenarios = evidence.get("scenarios")
    trace_requests = {
        item.get("request")
        for item in evidence.get("call_identity", {}).get("items", [])
        if item.get("status") in {"published", "silence"}
    }
    return (
        evidence.get("artifact_version") == 2
        and evidence.get("redacted") is True
        and evidence.get("status") == "passed"
        and isinstance(claims, dict)
        and REQUIRED_CLAIMS.issubset(claims)
        and all(claims[name] is True for name in REQUIRED_CLAIMS)
        and evidence.get("not_proven") == []
        and bool(evidence.get("capability_checks"))
        and all(
            item.get("status") == "passed"
            for item in evidence["capability_checks"]
        )
        and _strict_independent_wave_proved(evidence)
        and isinstance(scenarios, list)
        and {item.get("scenario_id") for item in scenarios if isinstance(item, dict)}
        == REQUIRED_SCENARIOS
        and len(scenarios) == len(REQUIRED_SCENARIOS)
        and all(_scenario_contract_proved(item) for item in scenarios)
        and _real_game_provenance_proved(evidence, trace_requests)
        and not any(forbidden in serialized for forbidden in FORBIDDEN_TEXT)
    )


def _run_live() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        prefix=f"{OUTPUT.stem}-",
        suffix=".tmp",
        dir=OUTPUT.parent,
        delete=False,
    ) as handle:
        temporary_output = Path(handle.name)
    environment = os.environ.copy()
    environment[RUNNER_OPT_IN_ENV] = "1"
    try:
        try:
            completed = subprocess.run(
                [sys.executable, str(RUNNER), "--output", str(temporary_output)],
                cwd=ROOT,
                env=environment,
                check=False,
            )
        except Exception as error:
            _write_artifact_persistence_failure(
                temporary_output,
                reason="runner_process_failed",
                error_type=type(error).__name__,
            )
            temporary_output.replace(OUTPUT)
            return 2
        if not _is_runner_artifact(temporary_output):
            _write_artifact_persistence_failure(
                temporary_output,
                reason="runner_artifact_missing_or_invalid",
                runner_exit_code=completed.returncode,
            )
        temporary_output.replace(OUTPUT)
        if completed.returncode != 0:
            return completed.returncode
        return 0 if _validate_evidence(OUTPUT) else 2
    finally:
        temporary_output.unlink(missing_ok=True)


def _is_runner_artifact(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("artifact_version") == 2
        and payload.get("redacted") is True
        and payload.get("status") in {"blocked", "passed"}
        and isinstance(payload.get("scenarios"), list)
    )


def _write_artifact_persistence_failure(
    path: Path,
    *,
    reason: str,
    runner_exit_code: int | None = None,
    error_type: str | None = None,
) -> None:
    scenarios = [
        {
            "scenario_id": scenario_id,
            "status": "blocked",
            "input": {
                "kind": "not_recoverable",
                "sha256": hashlib.sha256(scenario_id.encode("utf-8")).hexdigest(),
                "event_refs": [],
            },
            "director": {
                "status": "blocked",
                "selected_viewers": [],
                "evidence_event_refs": [],
            },
            "viewer_personas": [],
            "responses": [],
            "oracle": {
                "passed": False,
                "checks": {},
                "blocked_reason": "artifact_persistence_failed",
            },
        }
        for scenario_id in sorted(REQUIRED_SCENARIOS)
    ]
    payload = {
        "artifact_version": 2,
        "redacted": True,
        "status": "blocked",
        "artifact_provenance": {
            "source": "live_verifier_fallback",
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "run_started_at_utc": None,
            "run_completed_at_utc": None,
            "complete": False,
        },
        "blocked": {
            "stage": "artifact_persistence",
            "error_type": "artifact_persistence_failed",
            "reason": reason,
            "runner_exit_code": runner_exit_code,
            "runner_error_type": error_type,
        },
        "claims": {},
        "not_proven": sorted(REQUIRED_CLAIMS),
        "scenarios": scenarios,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate StepFun live evidence, or explicitly regenerate it."
    )
    parser.add_argument("--run-live", action="store_true")
    args = parser.parse_args()
    if args.run_live or os.environ.get(RUN_LIVE_ENV) == "1":
        return _run_live()
    return 0 if _validate_evidence() else 2


if __name__ == "__main__":
    raise SystemExit(main())
