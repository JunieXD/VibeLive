import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleTimeoutError,
)
from advx_backend.providers.model.viewer_runtime import (
    ViewerRuntimeProtocolError,
    ViewerRuntimeProviderError,
)

ROOT = Path(__file__).parents[2]
RUNNER = (
    ROOT / "apps" / "backend" / "scripts" / "viewer_runtime_live_stepfun.py"
)
VERIFIER = ROOT / "scripts" / "verify_viewer_runtime_live_stepfun.py"


def _runner_module() -> object:
    spec = importlib.util.spec_from_file_location("viewer_runtime_live_stepfun", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _verifier_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "verify_viewer_runtime_live_stepfun", VERIFIER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_evidence(module: object) -> dict[str, object]:
    frame_hash = "a" * 64
    event_ref = "e" * 16
    viewer_1 = "1" * 16
    viewer_2 = "2" * 16
    mappings = [
        {"viewer": viewer_1, "persona": "instigator"},
        {"viewer": viewer_2, "persona": "fun_seeker"},
    ]

    def scenario(
        scenario_id: str,
        checks: dict[str, bool],
        **extra: object,
    ) -> dict[str, object]:
        return {
            "scenario_id": scenario_id,
            "status": "passed",
            "input": {
                "kind": "scripted",
                "sha256": "b" * 64,
                "event_refs": [event_ref],
            },
            "director": {
                "status": "passed",
                "selected_viewers": [viewer_1],
                "evidence_event_refs": [event_ref],
            },
            "viewer_personas": mappings,
            "responses": [
                {
                    "request": "3" * 16,
                    "viewer": viewer_1,
                    "status": "published",
                    "reaction": "comment",
                    "evidence_event_refs": [event_ref],
                }
            ],
            "oracle": {"passed": True, "checks": checks},
            **extra,
        }

    scenarios = [
        scenario(
            "normal_silence_no_fabrication",
            {"silence_allowed": True, "no_fabricated_kill": True},
        ),
        scenario(
            "highlight_multi_kill",
            {"responses_reference_current_wave": True},
        ),
        scenario(
            "obvious_mistake",
            {"responses_reference_current_wave": True},
        ),
        scenario(
            "final_voice_structured_mention",
            {"structured_mention_selected": True},
            mentioned_viewer=viewer_1,
        ),
        scenario(
            "user_text_response",
            {"user_text_response_observed": True},
        ),
        scenario(
            "6657_persona_allocation_call_ratio",
            {"allocation_ratio_proved": True, "actual_call_ratio_proved": True},
            metrics={
                "high_weight_allocated": 6,
                "low_weight_allocated": 2,
                "high_weight_calls": 4,
                "low_weight_calls": 1,
            },
        ),
        scenario(
            "cross_session_mode_memory_injection",
            {"memory_prompt_injection_proved": True},
            memory={
                "memory_id": "4" * 16,
                "cross_session": True,
                "cross_mode": True,
                "injected_into_real_viewer_request": True,
            },
        ),
        scenario(
            "meme_candidate_lifecycle",
            {
                "candidate_never_overlayed": True,
                "undo_survived_restart": True,
            },
            meme_lifecycle={
                "candidate_id": "5" * 16,
                "auto_ingested": True,
                "overlay_event_count": 0,
                "undo_accepted": True,
                "absent_after_restart": True,
            },
        ),
    ]
    claims = {name: True for name in module.REQUIRED_CLAIMS}
    return {
        "artifact_version": 2,
        "redacted": True,
        "status": "passed",
        "claims": claims,
        "not_proven": [],
        "capability_checks": [{"capability": "image_input", "status": "passed"}],
        "provider": {"model": "step-3.7-flash"},
        "call_identity": {
            "items": [
                {
                    "observation": "o" * 16,
                    "request": "request-1",
                    "viewer": "viewer-1",
                    "epoch": 2,
                    "status": "published",
                },
                {
                    "observation": "o" * 16,
                    "request": "request-2",
                    "viewer": "viewer-2",
                    "epoch": 2,
                    "status": "silence",
                },
            ],
            "provider_calls": [
                {
                    "role": "decide",
                    "observation": "o" * 16,
                    "epoch": 2,
                    "status": "passed",
                },
                {
                    "role": "generate",
                    "observation": "o" * 16,
                    "request": "request-1",
                    "viewer": "viewer-1",
                    "epoch": 2,
                    "status": "passed",
                },
                {
                    "role": "generate",
                    "observation": "o" * 16,
                    "request": "request-2",
                    "viewer": "viewer-2",
                    "epoch": 2,
                    "status": "passed",
                },
            ],
        },
        "independent_live_wave": {
            "observation": "o" * 16,
            "epoch": 2,
            "count": 2,
            "requests": ["request-1", "request-2"],
            "viewers": ["viewer-1", "viewer-2"],
        },
        "scenarios": scenarios,
        "real_game_smoke": {
            "capability": "image_input",
            "status": "passed",
            "model_id": "step-3.7-flash",
            "frame": {
                "source_kind": "reviewed_real_game_capture",
                "sha256": frame_hash,
                "bytes": 1,
                "width": 1920,
                "height": 1080,
                "mime_type": "image/jpeg",
            },
            "provenance": {
                "status": "verified",
                "reviewer_attestation": True,
                "content_kind": "real_game_capture",
                "frame_sha256": frame_hash,
                "width": 1920,
                "height": 1080,
            },
            "production_provider_calls": [
                {
                    "role": "generate",
                    "request": "request-1",
                    "frame_sha256": [frame_hash],
                    "frame_refs": ["frame-hash"],
                    "status": "passed",
                }
            ],
        },
    }


def test_live_runner_requires_explicit_network_opt_in() -> None:
    module = _runner_module()
    with pytest.raises(module.LiveStepFunBlocked):
        module.require_live_opt_in({"STEPFUN_API_KEY": "not-used"})


@pytest.mark.asyncio
async def test_live_voice_target_resolver_emits_structured_viewer_target() -> None:
    module = _runner_module()
    resolver = module.LiveVoiceTargetResolver(target_viewer_id="viewer-1")
    segment = module.TranscriptSegment(
        session_id="session-1",
        text="请点名观众",
        started_at_ms=1,
        ended_at_ms=2,
        final=True,
    )

    resolution = await resolver.resolve(segment)

    assert resolution.resolver_id == "live-stepfun-structured-target-v1"
    assert resolution.target_viewer_id == "viewer-1"
    assert resolution.ambiguous is False
    assert resolver.calls == 1


def test_live_error_evidence_redacts_retryable_provider_cause() -> None:
    module = _runner_module()
    upstream = OpenAICompatibleTimeoutError("sensitive prompt and local path")
    error = ViewerRuntimeProviderError(
        "sensitive prompt and local path",
        retryable=True,
    )
    error.__cause__ = upstream

    evidence = module._safe_provider_error_evidence(error)

    assert evidence == {
        "error_type": "ViewerRuntimeProviderError",
        "status_code": None,
        "retryable": True,
        "blocked_reason": "retryable_upstream_failure",
        "sanitized_cause_chain": [
            {
                "error_type": "OpenAICompatibleTimeoutError",
                "status_code": None,
            }
        ],
    }
    assert "sensitive" not in json.dumps(evidence)


def test_live_error_evidence_records_bounded_length_failure() -> None:
    module = _runner_module()
    error = ViewerRuntimeProtocolError(
        "raw response must not be persisted",
        finish_reason="length",
        token_budget=4_096,
    )

    evidence = module._safe_provider_error_evidence(error)

    assert evidence["finish_reason"] == "length"
    assert evidence["token_budget"] == 4_096
    assert evidence["blocked_reason"] == "output_token_budget_exhausted"
    assert "raw response" not in json.dumps(evidence)


def test_external_frame_metadata_is_only_a_candidate_until_provider_binding(
    tmp_path: Path,
) -> None:
    module = _runner_module()
    frame = tmp_path / "private-user-frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0synthetic-test-only")

    body, mime_type, evidence = module._load_frame(frame)

    assert body.startswith(b"\xff\xd8\xff")
    assert mime_type == "image/jpeg"
    assert evidence["source_kind"] == "external_candidate"
    assert evidence["sha256"] == module.hashlib.sha256(body).hexdigest()
    assert evidence["bytes"] == len(body)
    assert evidence["mime_type"] == "image/jpeg"
    assert evidence["provenance_status"] == "missing_review_manifest"
    assert str(frame) not in json.dumps(evidence)


def test_reviewed_real_game_frame_requires_hash_dimensions_and_human_manifest(
    tmp_path: Path,
) -> None:
    module = _runner_module()
    frame = Path(
        r"D:\Steam\userdata\1741832827\760\remote\730\screenshots"
        r"\20260714225936_1.jpg"
    )
    if not frame.is_file():
        pytest.skip("reviewed Steam screenshot is not present on this machine")
    manifest = (
        ROOT / "tests" / "e2e" / "cs2_real_game_frame_review_manifest.json"
    )

    _, _, evidence = module._load_frame(frame, manifest)

    assert evidence["source_kind"] == "external_candidate"
    assert evidence["sha256"] == (
        "5f56bdf990f8a947a8bb34a611d3abe31b1f0248ee5aec87a0c6650625aaddc7"
    )
    assert evidence["width"] == 2560
    assert evidence["height"] == 1600
    assert evidence["provenance_status"] == "verified"
    assert evidence["provenance"]["source"]["app_id"] == 730
    assert str(frame) not in json.dumps(evidence)

    bad_manifest = tmp_path / "bad-review.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["frames"][0]["height"] = 1599
    bad_manifest.write_text(json.dumps(payload), encoding="utf-8")
    _, _, rejected = module._load_frame(frame, bad_manifest)
    assert rejected["provenance_status"] == "missing_review_manifest"
    assert rejected["provenance"] is None


def _live_trace(
    *,
    observation: str,
    request: str,
    viewer: str,
    epoch: int = 2,
    status: str = "published",
) -> SimpleNamespace:
    return SimpleNamespace(
        audience_epoch=epoch,
        observation_id=observation,
        trace_id=request,
        viewer_instance_id=viewer,
        response_status=SimpleNamespace(value=status),
    )


def _live_call(
    module: object,
    *,
    observation: str,
    request: str | None = None,
    viewer: str | None = None,
    epoch: int = 2,
    role: str = "generate",
    status: str = "passed",
) -> dict[str, object]:
    return {
        "role": role,
        "observation": module._hash_id(observation),
        "request": module._hash_id(request) if request is not None else None,
        "viewer": module._hash_id(viewer) if viewer is not None else None,
        "epoch": epoch,
        "status": status,
    }


def test_scenario_evidence_hashes_inputs_and_preserves_viewer_persona_identity() -> None:
    module = _runner_module()
    event = SimpleNamespace(event_id="event-1")
    viewer = SimpleNamespace(viewer_instance_id="viewer-1", persona_id="instigator")
    trace = SimpleNamespace(
        public_context_event_ids=["event-1"],
        director_decision=SimpleNamespace(
            selected_viewer_ids=["viewer-1"],
            evidence_event_ids=["event-1"],
        ),
        viewer_instance_id="viewer-1",
        trace_id="request-1",
        response_status=SimpleNamespace(value="published"),
    )

    scenario = module._scenario_from_runtime(
        scenario_id="user_text_response",
        input_kind="user_text",
        input_sha256="a" * 64,
        input_events=[event],
        traces=[trace],
        viewers=[viewer],
        checks={"user_text_response_observed": True},
    )

    assert scenario["status"] == "passed"
    assert scenario["input"]["event_refs"] == [module._hash_id("event-1")]
    assert scenario["director"]["selected_viewers"] == [
        module._hash_id("viewer-1")
    ]
    assert scenario["viewer_personas"] == [
        {"viewer": module._hash_id("viewer-1"), "persona": "instigator"}
    ]
    assert scenario["responses"][0]["evidence_event_refs"] == [
        module._hash_id("event-1")
    ]
    assert "event-1" not in json.dumps(scenario)


def test_verifier_accepts_reviewed_frame_scenario_with_frame_only_evidence(
    tmp_path: Path,
) -> None:
    module = _verifier_module()
    evidence = _valid_evidence(module)
    scenario = next(
        item
        for item in evidence["scenarios"]
        if item["scenario_id"] == "highlight_multi_kill"
    )
    scenario["input"]["event_refs"] = []
    scenario["input"]["frame_refs"] = ["c" * 64]
    scenario["director"]["evidence_event_refs"] = []
    scenario["responses"][0]["evidence_event_refs"] = []
    scenario["responses"][0]["evidence_frame_refs"] = ["c" * 64]
    path = tmp_path / "frame-only.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert module._validate_evidence(path) is True


@pytest.mark.parametrize("case", ["duplicate", "unmatched"])
def test_independent_live_wave_rejects_duplicate_or_unmatched_calls(case: str) -> None:
    module = _runner_module()
    if case == "duplicate":
        traces = [
            _live_trace(observation="wave-1", request="request-1", viewer="viewer-1"),
            _live_trace(observation="wave-1", request="request-1", viewer="viewer-1"),
        ]
        calls = [
            _live_call(
                module,
                observation="wave-1",
                request="request-1",
                viewer="viewer-1",
            )
        ]
    else:
        traces = [
            _live_trace(observation="wave-1", request="request-1", viewer="viewer-1"),
            _live_trace(observation="wave-1", request="request-2", viewer="viewer-2"),
        ]
        calls = [
            _live_call(
                module,
                observation="wave-1",
                request="request-1",
                viewer="viewer-1",
            )
        ]
    calls.append(
        _live_call(
            module,
            role="decide",
            observation="wave-1",
        )
    )

    assert module._independent_live_wave(traces, calls, audience_epoch=2) is None


@pytest.mark.parametrize("case", ["failed", "different_observation", "different_epoch"])
def test_independent_live_wave_requires_matching_successful_director_call(
    case: str,
) -> None:
    module = _runner_module()
    traces = [
        _live_trace(observation="wave-1", request="request-1", viewer="viewer-1"),
        _live_trace(observation="wave-1", request="request-2", viewer="viewer-2"),
    ]
    calls = [
        _live_call(
            module,
            observation="wave-1",
            request="request-1",
            viewer="viewer-1",
        ),
        _live_call(
            module,
            observation="wave-1",
            request="request-2",
            viewer="viewer-2",
        ),
        _live_call(
            module,
            role="decide",
            observation="wave-2" if case == "different_observation" else "wave-1",
            epoch=3 if case == "different_epoch" else 2,
            status="failed" if case == "failed" else "passed",
        ),
    ]

    assert module._independent_live_wave(traces, calls, audience_epoch=2) is None


def test_independent_live_wave_accepts_two_distinct_correlated_calls() -> None:
    module = _runner_module()
    traces = [
        _live_trace(observation="wave-1", request="request-1", viewer="viewer-1"),
        _live_trace(
            observation="wave-1",
            request="request-2",
            viewer="viewer-2",
            status="silence",
        ),
    ]
    calls = [
        _live_call(
            module,
            observation="wave-1",
            request="request-1",
            viewer="viewer-1",
        ),
        _live_call(
            module,
            observation="wave-1",
            request="request-2",
            viewer="viewer-2",
        ),
        _live_call(
            module,
            role="decide",
            observation="wave-1",
        ),
    ]

    proof = module._independent_live_wave(traces, calls, audience_epoch=2)

    assert proof is not None
    assert proof["count"] == 2
    assert len(proof["requests"]) == 2
    assert len(proof["viewers"]) == 2


def test_live_response_cap_preserves_hamilton_pool_semantics() -> None:
    module = _runner_module()
    from advx_backend.application.viewer_pool_service import ViewerPoolService
    from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec

    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "cs2" / "viewer_runtime_recorded.json").read_text(
            encoding="utf-8"
        )
    )
    original = CanonicalRuntimeSpec.model_validate(
        fixture["bundle"]["canonical_runtime_spec"]
    )
    capped = module._cap_live_response_ranges(original)
    personas = {persona.persona_id: persona for persona in original.personas}

    for before, after in zip(original.modes, capped.modes, strict=True):
        assert after.viewer_count == before.viewer_count
        assert after.persona_weights == before.persona_weights
        assert after.normal_response_range.maximum == min(
            before.normal_response_range.maximum, 2
        )
        assert after.highlight_response_range.maximum == min(
            before.highlight_response_range.maximum, 2
        )
        assert ViewerPoolService._allocate(after, personas) == ViewerPoolService._allocate(
            before, personas
        )


def test_live_specs_use_strict_director_without_changing_pool_semantics() -> None:
    module = _runner_module()

    initial, hot = module._load_specs()

    for spec in (initial, hot):
        assert spec.settings.director_failure_mode.value == "strict"
        source = json.loads(
            (
                ROOT / "tests" / "fixtures" / "cs2" / "viewer_runtime_recorded.json"
            ).read_text(encoding="utf-8")
        )
        source_key = (
            "initial_canonical_runtime_spec"
            if spec.config_revision == 1
            else "bundle"
        )
        raw = source[source_key]
        if source_key == "bundle":
            raw = raw["canonical_runtime_spec"]
        assert [mode.target_concurrent_viewers for mode in spec.modes] == [
            mode["target_concurrent_viewers"] for mode in raw["modes"]
        ]
        assert [mode.persona_weights for mode in spec.modes] == [
            mode["persona_weights"] for mode in raw["modes"]
        ]


def test_verifier_rejects_director_failure_with_fallback_viewer_calls(
    tmp_path: Path,
) -> None:
    module = _verifier_module()
    evidence = _valid_evidence(module)
    provider_calls = evidence["call_identity"]["provider_calls"]
    provider_calls[0]["status"] = "failed"
    path = tmp_path / "fallback-evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert module._validate_evidence(path) is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("observation", "x" * 16),
        ("epoch", 3),
        ("request", "wrong-request"),
        ("viewer", "wrong-viewer"),
    ],
)
def test_verifier_rejects_disconnected_independent_wave_trace(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    module = _verifier_module()
    evidence = _valid_evidence(module)
    evidence["call_identity"]["items"][0][field] = replacement
    path = tmp_path / f"disconnected-{field}.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert module._validate_evidence(path) is False


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("missing_scenario", None),
        ("scenario_oracle", False),
        ("scenario_event_ref", "not-a-hash"),
        ("real_game_source", "external_real_game"),
        ("review_attestation", False),
        ("review_dimensions", 720),
    ],
)
def test_verifier_rejects_incomplete_scenarios_or_untrusted_real_game_provenance(
    tmp_path: Path,
    mutation: str,
    value: object,
) -> None:
    module = _verifier_module()
    evidence = _valid_evidence(module)
    if mutation == "missing_scenario":
        evidence["scenarios"].pop()
    elif mutation == "scenario_oracle":
        evidence["scenarios"][0]["oracle"]["passed"] = value
    elif mutation == "scenario_event_ref":
        evidence["scenarios"][1]["input"]["event_refs"][0] = value
    elif mutation == "real_game_source":
        evidence["real_game_smoke"]["frame"]["source_kind"] = value
    elif mutation == "review_attestation":
        evidence["real_game_smoke"]["provenance"]["reviewer_attestation"] = value
    else:
        evidence["real_game_smoke"]["provenance"]["height"] = value
    path = tmp_path / f"{mutation}.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert module._validate_evidence(path) is False


def test_verifier_defaults_to_read_only_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _verifier_module()
    monkeypatch.setattr(sys, "argv", [str(VERIFIER)])
    monkeypatch.setattr(module, "_validate_evidence", lambda: True)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("default verifier launched subprocess"),
    )

    assert module.main() == 0


def test_live_verifier_atomically_preserves_blocked_runner_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _verifier_module()
    output = tmp_path / "evidence.json"
    output.write_text('{"last_known_good":true}', encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    payload = _valid_evidence(module)
    payload["status"] = "blocked"

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        Path(command[-1]).write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._run_live() == 2
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_live_verifier_replaces_invalid_runner_output_with_failure_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _verifier_module()
    output = tmp_path / "evidence.json"
    output.write_text('{"last_known_good":true}', encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        Path(command[-1]).write_text('{"status":"blocked"}', encoding="utf-8")
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._run_live() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["blocked"]["error_type"] == "artifact_persistence_failed"
    assert payload["blocked"]["reason"] == "runner_artifact_missing_or_invalid"


def test_live_verifier_persists_redacted_failure_when_runner_cannot_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _verifier_module()
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(module, "OUTPUT", output)

    def fake_run(*_: object, **__: object) -> SimpleNamespace:
        raise OSError("sensitive local path")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._run_live() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["blocked"]["reason"] == "runner_process_failed"
    assert payload["blocked"]["runner_error_type"] == "OSError"
    assert "sensitive local path" not in json.dumps(payload)


def test_live_verifier_atomically_replaces_only_valid_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _verifier_module()
    output = tmp_path / "evidence.json"
    output.write_text('{"last_known_good":true}', encoding="utf-8")
    expected = _valid_evidence(module)
    monkeypatch.setattr(module, "OUTPUT", output)

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        Path(command[-1]).write_text(json.dumps(expected), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._run_live() == 0
    assert json.loads(output.read_text(encoding="utf-8")) == expected


def test_checked_in_live_evidence_is_redacted_when_present() -> None:
    path = ROOT / "tests" / "e2e" / "cs2_viewer_runtime_live_stepfun_evidence.json"
    if not path.exists():
        pytest.skip("credentialed live evidence has not been generated")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    assert evidence["artifact_version"] == 2
    assert evidence["redacted"] is True
    assert evidence["status"] in {"partial", "blocked", "passed"}
    assert isinstance(evidence["scenarios"], list)
    assert {
        item["scenario_id"] for item in evidence["scenarios"]
    } == _verifier_module().REQUIRED_SCENARIOS
    for forbidden in ("api_key", "authorization", "bearer ", "raw_audio", "base64"):
        assert forbidden not in serialized
