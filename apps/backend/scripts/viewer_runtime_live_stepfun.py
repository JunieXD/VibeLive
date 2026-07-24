from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import traceback
import wave
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn

from pydantic import ValidationError

from advx_backend.application.ports.asr import (
    AudioChunk,
    TranscriptSegment,
    TranscriptTargetResolution,
)
from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    TextInput,
)
from advx_backend.application.ports.memory import RoomMemoryCandidate
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.debug import TraceQuery
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import DirectorFailureMode, RuntimeApplyRequest
from advx_backend.domain.memory import RoomMemoryType
from advx_backend.domain.observation import FrameRef
from advx_backend.domain.observation_wave import ViewerVisualInputMode
from advx_backend.domain.room import RoomEventSource
from advx_backend.main import create_app
from advx_backend.providers.asr import StepFunAsrConfig, StepFunAsrProvider
from advx_backend.providers.model.base import CapabilityProbeStatus
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from advx_backend.providers.model.viewer_runtime import ViewerRuntimeProtocolError

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "cs2" / "viewer_runtime_recorded.json"
FRAME_PATH = ROOT / "tests" / "fixtures" / "cs2" / "live-stepfun-frame.png"
FRAME_REVIEW_MANIFEST_PATH = (
    ROOT / "tests" / "e2e" / "cs2_real_game_frame_review_manifest.json"
)
BASE_URL = "https://api.stepfun.com/step_plan/v1"
MODEL = "step-3.7-flash"
ASR_MODEL = "stepaudio-2.5-asr"
LIVE_OPT_IN = "ADVX_RUN_STEPFUN_LIVE"
API_KEY_ENV = "STEPFUN_API_KEY"
WAV_ENV = "ADVX_STEPFUN_LIVE_WAV"
FRAME_ENV = "ADVX_STEPFUN_LIVE_FRAME"
NORMAL_FRAME_ENV = "ADVX_STEPFUN_LIVE_NORMAL_FRAME"
HIGHLIGHT_FRAME_ENV = "ADVX_STEPFUN_LIVE_HIGHLIGHT_FRAME"
MISTAKE_FRAME_ENV = "ADVX_STEPFUN_LIVE_MISTAKE_FRAME"
FRAME_REVIEW_MANIFEST_ENV = "ADVX_STEPFUN_LIVE_FRAME_REVIEW_MANIFEST"
SCENARIO_IDS = (
    "normal_silence_no_fabrication",
    "highlight_multi_kill",
    "obvious_mistake",
    "final_voice_structured_mention",
    "user_text_response",
    "6657_persona_allocation_call_ratio",
    "cross_session_mode_memory_injection",
    "meme_candidate_lifecycle",
)


class LiveStepFunBlocked(RuntimeError):
    pass


@dataclass(slots=True)
class LiveVoiceTargetResolver:
    target_viewer_id: str | None = None
    calls: int = 0
    resolver_id: str = "live-stepfun-structured-target-v1"

    async def resolve(
        self,
        segment: TranscriptSegment,
    ) -> TranscriptTargetResolution:
        del segment
        self.calls += 1
        return TranscriptTargetResolution(
            resolver_id=self.resolver_id,
            target_viewer_id=self.target_viewer_id,
            ambiguous=self.target_viewer_id is None,
        )


@dataclass(slots=True)
class LiveCapabilityProbe:
    configuration: ProviderConfigurationRequest
    pcm: bytes
    checks: list[dict[str, object]]
    verified: bool = False

    async def probe(self, spec: object) -> None:
        del spec
        if self.verified:
            return
        result = None
        for attempt in range(3):
            provider = OpenAICompatibleProvider(
                OpenAICompatibleConfig(
                    base_url=self.configuration.model_base_url,
                    model=MODEL,
                    api_key=self.configuration.model_api_key,
                )
            )
            try:
                result = await provider.probe_capabilities(
                    role_models=self.configuration.role_models()
                )
            finally:
                await provider.aclose()
            if result.status is CapabilityProbeStatus.PASSED:
                break
            if attempt < 2:
                await asyncio.sleep(2**attempt)
        assert result is not None
        self.checks = [
            {
                "capability": check.capability,
                "status": check.status.value,
                "model_id": check.model_id,
                "error_code": check.error_code,
                "http_status": check.http_status,
            }
            for check in result.checks
        ]
        if result.status is not CapabilityProbeStatus.PASSED:
            raise LiveStepFunBlocked("model capability probe did not pass")

        asr = StepFunAsrProvider(
            StepFunAsrConfig(
                api_key=self.configuration.asr_api_key,
                base_url=BASE_URL,
                model=ASR_MODEL,
            )
        )
        final = False
        await asr.start()
        try:
            await asr.push_audio(
                AudioChunk(
                    session_id="live-capability-probe",
                    started_at_ms=0,
                    ended_at_ms=max(1, len(self.pcm) // 32),
                    sample_rate=16_000,
                    channels=1,
                    sample_width_bits=16,
                    pcm=self.pcm,
                )
            )
            await asr.commit()
            async for segment in asr.results():
                if segment.final and segment.text.strip():
                    final = True
                    break
        finally:
            await asr.stop()
        self.checks.append(
            {
                "capability": "asr_final_audio",
                "status": "passed" if final else "failed",
                "model_id": ASR_MODEL,
                "error_code": None if final else "missing_final_transcript",
                "http_status": None,
            }
        )
        if not final:
            raise LiveStepFunBlocked("ASR capability probe did not return a final transcript")
        self.verified = True


class ProviderCallObserver:
    def __init__(self, router: object, frame_resolver: object) -> None:
        self.calls: list[dict[str, object]] = []
        self._frame_resolver = frame_resolver
        self._install(router, "decide")
        self._install(router, "generate")
        self._install(router, "summarize")
        self._install(router, "extract")

    def _install(self, router: object, role: str) -> None:
        original = getattr(router, role)

        async def observed(*args: object, **kwargs: object) -> object:
            request = args[0] if args else kwargs
            wave = getattr(request, "wave", request)
            identity = getattr(request, "generation_request_id", None)
            viewer_id = getattr(request, "viewer_instance_id", None)
            epoch = getattr(wave, "audience_epoch", kwargs.get("audience_epoch"))
            item: dict[str, object] = {
                "role": role,
                "epoch": epoch if isinstance(epoch, int) else None,
                "observation": (
                    _hash_id(wave.observation_id)
                    if isinstance(getattr(wave, "observation_id", None), str)
                    else None
                ),
                "request": _hash_id(identity) if isinstance(identity, str) else None,
                "viewer": _hash_id(viewer_id) if isinstance(viewer_id, str) else None,
                "status": "started",
            }
            bundle = getattr(request, "frame_bundle", None)
            if bundle is None:
                bundle = getattr(wave, "frame_bundle", None)
            frame_hashes: list[str] = []
            frame_refs: list[str] = []
            if bundle is not None:
                session_id = getattr(wave, "session_id", None)
                if not isinstance(session_id, str):
                    session_id = getattr(request, "session_id", None)
                if isinstance(session_id, str):
                    for frame in bundle.frames:
                        resolved = await self._frame_resolver.resolve(
                            session_id=session_id,
                            frame=FrameRef(
                                frame_id=frame.frame_id,
                                created_at_ms=frame.captured_at_ms,
                                mime_type=frame.encoding,
                                data_ref=frame.data_ref,
                            ),
                        )
                        if resolved is not None:
                            frame_hashes.append(hashlib.sha256(resolved.body).hexdigest())
                            frame_refs.append(_hash_id(frame.frame_id))
            if frame_hashes:
                item["frame_sha256"] = frame_hashes
                item["frame_refs"] = frame_refs
            self.calls.append(item)
            try:
                result = await original(*args, **kwargs)
            except Exception as error:
                item["status"] = "failed"
                item.update(_safe_provider_error_evidence(error))
                raise
            item["status"] = "passed"
            return result

        setattr(router, role, observed)


def require_live_opt_in(environ: dict[str, str] | os._Environ[str]) -> str:
    if environ.get(LIVE_OPT_IN) != "1":
        raise LiveStepFunBlocked(f"set {LIVE_OPT_IN}=1 to allow credentialed network calls")
    key = environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise LiveStepFunBlocked(f"{API_KEY_ENV} is not configured")
    return key


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_provider_error_evidence(error: BaseException) -> dict[str, object]:
    evidence: dict[str, object] = {
        "error_type": type(error).__name__,
        "status_code": getattr(error, "status_code", None),
        "retryable": bool(getattr(error, "retryable", False)),
    }
    if isinstance(error, ViewerRuntimeProtocolError):
        evidence["finish_reason"] = error.finish_reason
        evidence["token_budget"] = error.token_budget
        evidence["blocked_reason"] = (
            "output_token_budget_exhausted"
            if error.finish_reason == "length"
            else "provider_protocol_violation"
        )
    elif evidence["retryable"]:
        evidence["blocked_reason"] = "retryable_upstream_failure"
    elif evidence["status_code"] is not None:
        evidence["blocked_reason"] = "upstream_http_failure"
    else:
        evidence["blocked_reason"] = "provider_failure"

    cause_chain: list[dict[str, object]] = []
    current = error.__cause__
    for _ in range(4):
        if current is None:
            break
        cause_chain.append(
            {
                "error_type": type(current).__name__,
                "status_code": getattr(current, "status_code", None),
            }
        )
        current = current.__cause__
    evidence["sanitized_cause_chain"] = cause_chain
    return evidence


def _safe_error_detail(error: BaseException) -> str | None:
    current: BaseException | None = error
    for _ in range(4):
        if isinstance(current, ValidationError):
            codes = {
                f"{'.'.join(str(item) for item in detail['loc'])}:{detail['type']}"
                for detail in current.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )
            }
            return ",".join(sorted(codes))[:512]
        if isinstance(current, LiveStepFunBlocked):
            return str(current)[:512]
        current = current.__cause__
        if current is None:
            break
    return None


def _safe_error_trace(error: BaseException) -> list[str]:
    return [
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)[-8:]
    ]


def _blocked_scenarios(
    reason: str,
    frame_evidence_by_scenario: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    frame_evidence_by_scenario = frame_evidence_by_scenario or {}
    return [
        {
            "scenario_id": scenario_id,
            "status": "blocked",
            "input": {
                "kind": (
                    "reviewed_real_game_frame"
                    if scenario_id in frame_evidence_by_scenario
                    else "not_executed"
                ),
                "sha256": frame_evidence_by_scenario.get(scenario_id, {}).get(
                    "sha256",
                    hashlib.sha256(scenario_id.encode("utf-8")).hexdigest(),
                ),
                "event_refs": [],
                "frame": frame_evidence_by_scenario.get(scenario_id),
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
                "blocked_reason": reason,
            },
        }
        for scenario_id in SCENARIO_IDS
    ]


def _counts(viewers: list[object]) -> dict[str, int]:
    persona_ids = [str(getattr(viewer, "persona_id")) for viewer in viewers]
    return {
        persona_id: persona_ids.count(persona_id)
        for persona_id in sorted(set(persona_ids))
    }


def _cap_live_response_ranges(spec: object, *, maximum: int = 2) -> object:
    modes = [
        mode.model_copy(
            update={
                "normal_response_range": mode.normal_response_range.model_copy(
                    update={
                        "minimum": min(mode.normal_response_range.minimum, maximum),
                        "maximum": min(mode.normal_response_range.maximum, maximum),
                    }
                ),
                "highlight_response_range": mode.highlight_response_range.model_copy(
                    update={
                        "minimum": min(mode.highlight_response_range.minimum, maximum),
                        "maximum": min(mode.highlight_response_range.maximum, maximum),
                    }
                ),
            }
        )
        for mode in spec.modes
    ]
    return spec.model_copy(update={"modes": modes})


def _independent_live_wave(
    traces: list[object],
    provider_calls: list[dict[str, object]],
    *,
    audience_epoch: int,
) -> dict[str, object] | None:
    successful_decisions = {
        (item.get("observation"), item.get("epoch"))
        for item in provider_calls
        if item.get("role") == "decide" and item.get("status") == "passed"
    }
    successful_calls = {
        (
            item.get("observation"),
            item.get("request"),
            item.get("viewer"),
            item.get("epoch"),
        )
        for item in provider_calls
        if item.get("role") == "generate" and item.get("status") == "passed"
    }
    correlated: dict[str, dict[tuple[str, str], None]] = {}
    for trace in traces:
        if trace.audience_epoch != audience_epoch:
            continue
        if trace.response_status.value not in {"published", "silence"}:
            continue
        observation = _hash_id(trace.observation_id)
        request = _hash_id(trace.trace_id)
        viewer = _hash_id(trace.viewer_instance_id)
        if (observation, request, viewer, audience_epoch) not in successful_calls:
            continue
        if (observation, audience_epoch) not in successful_decisions:
            continue
        correlated.setdefault(observation, {})[(request, viewer)] = None
    for observation, pairs in sorted(correlated.items()):
        requests = sorted({request for request, _ in pairs})
        viewers = sorted({viewer for _, viewer in pairs})
        if len(requests) >= 2 and len(viewers) >= 2:
            return {
                "observation": observation,
                "epoch": audience_epoch,
                "count": len(pairs),
                "requests": requests,
                "viewers": viewers,
            }
    return None


def _load_specs() -> tuple[object, object]:
    from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    initial_source = CanonicalRuntimeSpec.model_validate(
        fixture["initial_canonical_runtime_spec"]
    )
    hot_source = CanonicalRuntimeSpec.model_validate(
        fixture["bundle"]["canonical_runtime_spec"]
    )
    provider = hot_source.provider.model_copy(
        update={
            "provider_profile_id": "stepfun-live",
            "director_model": MODEL,
            "viewer_model": MODEL,
            "memory_model": MODEL,
            "visual_summary_model": MODEL,
        }
    )
    def live_spec(source: CanonicalRuntimeSpec) -> CanonicalRuntimeSpec:
        return source.model_copy(
            update={
                "provider": provider,
                "settings": source.settings.model_copy(
                    update={
                        "viewer_visual_input_mode": ViewerVisualInputMode.DIRECT_FRAMES,
                        "viewer_request_ttl_ms": 120_000,
                        "director_failure_mode": DirectorFailureMode.STRICT,
                    }
                ),
            }
        )

    initial = _cap_live_response_ranges(live_spec(initial_source)).model_copy(
        update={
            "config_revision": 1,
            "room": initial_source.room.model_copy(
                update={"updated_at_ms": initial_source.room.created_at_ms}
            ),
        }
    )
    hot = _cap_live_response_ranges(live_spec(hot_source)).model_copy(
        update={"config_revision": 2}
    )
    return initial, hot


def _read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        if (
            handle.getframerate() != 16_000
            or handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getcomptype() != "NONE"
        ):
            raise LiveStepFunBlocked("live WAV must be 16 kHz mono 16-bit PCM")
        return handle.readframes(handle.getnframes())


def _load_review_manifest(
    *,
    body: bytes,
    mime_type: str,
    review_manifest_path: Path | None,
) -> dict[str, object] | None:
    from advx_backend.application.frame_metadata import _image_metadata

    if review_manifest_path is None or not review_manifest_path.is_file():
        return None
    try:
        manifest = json.loads(review_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    dimensions = _image_metadata(body)
    frame_sha256 = hashlib.sha256(body).hexdigest()
    frames = manifest.get("frames") if isinstance(manifest, dict) else None
    reviewed_frame = next(
        (
            item
            for item in frames
            if isinstance(item, dict) and item.get("frame_sha256") == frame_sha256
        ),
        None,
    ) if isinstance(frames, list) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("manifest_version") != 1
        or manifest.get("content_kind") != "real_game_capture"
        or manifest.get("classification") != "human_reviewed"
        or manifest.get("reviewer_attestation") is not True
        or not isinstance(reviewed_frame, dict)
        or reviewed_frame.get("mime_type") != mime_type
        or reviewed_frame.get("bytes") != len(body)
        or dimensions is None
        or reviewed_frame.get("width") != dimensions[0]
        or reviewed_frame.get("height") != dimensions[1]
    ):
        return None
    source = manifest.get("source")
    if (
        not isinstance(source, dict)
        or source.get("platform") != "steam"
        or source.get("app_id") != 730
        or source.get("artifact_kind") != "user_screenshot"
    ):
        return None
    return {
        "status": "verified",
        "reviewer_attestation": True,
        "content_kind": "real_game_capture",
        "frame_sha256": frame_sha256,
        "width": dimensions[0],
        "height": dimensions[1],
        "scenario_category": reviewed_frame["scenario_category"],
        "source": {
            "platform": "steam",
            "app_id": 730,
            "artifact_kind": "user_screenshot",
        },
    }


def _load_frame(
    path: Path | None,
    review_manifest_path: Path | None = None,
) -> tuple[bytes, str, dict[str, object]]:
    selected = FRAME_PATH if path is None else path
    body = selected.read_bytes()
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type = "image/png"
    elif body.startswith(b"\xff\xd8\xff"):
        mime_type = "image/jpeg"
    elif len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        mime_type = "image/webp"
    else:
        raise LiveStepFunBlocked("live frame must be PNG, JPEG, or WebP")
    from advx_backend.application.frame_metadata import _image_metadata

    dimensions = _image_metadata(body)
    provenance = _load_review_manifest(
        body=body,
        mime_type=mime_type,
        review_manifest_path=review_manifest_path,
    )
    return (
        body,
        mime_type,
        {
            "source_kind": (
                "synthetic_repo_fixture" if path is None else "external_candidate"
            ),
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "mime_type": mime_type,
            "width": dimensions[0] if dimensions is not None else None,
            "height": dimensions[1] if dimensions is not None else None,
            "provenance_status": (
                "verified" if provenance is not None else "missing_review_manifest"
            ),
            "provenance": provenance,
        },
    )


def _generate_pcm(
    directory: Path,
    text: str = "这是一条用于直播观众运行时验证的中文语音。",
) -> bytes:
    configured = os.environ.get(WAV_ENV)
    if configured:
        return _read_pcm(Path(configured))
    if sys.platform != "win32":
        raise LiveStepFunBlocked(f"set {WAV_ENV} to a 16 kHz mono 16-bit PCM WAV")
    output = directory / "stepfun-live-speech.wav"
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
        "[System.Speech.AudioFormat.AudioChannel]::Mono);"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{str(output).replace(chr(39), chr(39) * 2)}',$f);"
        f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}');"
        "$s.Dispose()"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0 or not output.is_file():
        raise LiveStepFunBlocked("Windows speech synthesis could not create the live WAV")
    return _read_pcm(output)


def _scenario_from_runtime(
    *,
    scenario_id: str,
    input_kind: str,
    input_sha256: str,
    input_events: list[object],
    traces: list[object],
    viewers: list[object],
    checks: dict[str, bool],
    frame_sha256: str | None = None,
    room_events: list[object] | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    room_events = room_events or []
    barrage_by_id = {
        event.source_id: event
        for event in room_events
        if event.source_type is RoomEventSource.AUDIENCE_BARRAGE
        and event.source_id is not None
    }
    event_ids = {event.event_id for event in input_events}
    related = [
        trace
        for trace in traces
        if event_ids.intersection(trace.public_context_event_ids)
        or (
            frame_sha256 is not None
            and frame_sha256 in trace.frame_hashes
        )
    ]
    selected_ids = {
        viewer_id
        for trace in related
        for viewer_id in trace.director_decision.selected_viewer_ids
    }
    viewer_by_id = {viewer.viewer_instance_id: viewer for viewer in viewers}
    referenced_viewers = selected_ids | {
        trace.viewer_instance_id for trace in related
    }
    event_refs = {_hash_id(event_id): event_id for event_id in event_ids}
    scenario: dict[str, object] = {
        "scenario_id": scenario_id,
        "status": "passed" if all(checks.values()) else "blocked",
        "input": {
            "kind": input_kind,
            "sha256": input_sha256,
            "event_refs": sorted(event_refs),
            "frame_refs": [frame_sha256] if frame_sha256 is not None else [],
        },
        "director": {
            "status": (
                "passed"
                if selected_ids
                else "silence"
                if input_events or frame_sha256 is not None
                else "blocked"
            ),
            "selected_viewers": sorted(_hash_id(item) for item in selected_ids),
            "evidence_event_refs": sorted(
                {
                    _hash_id(event_id)
                    for trace in related
                    for event_id in trace.director_decision.evidence_event_ids
                    if event_id in event_ids
                }
            ),
        },
        "viewer_personas": [
            {
                "viewer": _hash_id(viewer_id),
                "persona": viewer_by_id[viewer_id].persona_id,
            }
            for viewer_id in sorted(referenced_viewers)
            if viewer_id in viewer_by_id
        ],
        "responses": [
            {
                "request": _hash_id(trace.trace_id),
                "viewer": _hash_id(trace.viewer_instance_id),
                "status": trace.response_status.value,
                "reaction": (
                    barrage_by_id[
                        getattr(
                            getattr(trace, "side_effects", None),
                            "published_barrage_id",
                            None,
                        )
                    ].payload.get(
                        "reaction_type",
                        trace.response_status.value,
                    )
                    if getattr(
                        getattr(trace, "side_effects", None),
                        "published_barrage_id",
                        None,
                    )
                    in barrage_by_id
                    else trace.response_status.value
                ),
                "evidence_event_refs": sorted(
                    _hash_id(event_id)
                    for event_id in trace.public_context_event_ids
                    if event_id in event_ids
                ),
                "evidence_frame_refs": (
                    [frame_sha256]
                    if frame_sha256 is not None
                    and frame_sha256 in trace.frame_hashes
                    else []
                ),
            }
            for trace in related
            if trace.response_status.value in {"published", "silence"}
        ],
        "oracle": {
            "passed": all(checks.values()),
            "checks": checks,
        },
    }
    if extra:
        scenario.update(extra)
    return scenario


async def _wait_for_voice(runtime: object, session_id: str) -> None:
    for _ in range(120):
        events = await runtime.room_service.read_events(session_id)
        if any(event.source_type is RoomEventSource.USER_VOICE for event in events):
            return
        await asyncio.sleep(0.25)
    raise TimeoutError("final ASR room event was not observed")


async def _wait_for_runtime(runtime: object, session_id: str) -> None:
    assert runtime.reaction_scheduler is not None
    await runtime.reaction_scheduler.wait_for_idle(session_id)
    assert runtime.viewer_runtime_coordinator is not None
    await runtime.viewer_runtime_coordinator.wait_for_background_tasks()


def _new_items(before: list[object], after: list[object]) -> list[object]:
    before_ids = {item.trace_id for item in before}
    return [item for item in after if item.trace_id not in before_ids]


def _new_events(before: list[object], after: list[object]) -> list[object]:
    before_ids = {item.event_id for item in before}
    return [item for item in after if item.event_id not in before_ids]


def _published_texts(events: list[object], traces: list[object]) -> list[str]:
    barrage_ids = {
        trace.side_effects.published_barrage_id
        for trace in traces
        if trace.side_effects.published_barrage_id is not None
    }
    return [
        event.text
        for event in events
        if event.source_type is RoomEventSource.AUDIENCE_BARRAGE
        and event.source_id in barrage_ids
        and isinstance(event.text, str)
    ]


def _contains_any(texts: list[str], terms: tuple[str, ...]) -> bool:
    normalized = "\n".join(texts).casefold()
    return any(term.casefold() in normalized for term in terms)


async def _abrupt_close(runtime: object, session_id: str) -> None:
    if runtime.ingest_service is not None:
        await runtime.ingest_service.stop_session(session_id)
    if runtime.viewer_runtime is not None:
        await runtime.viewer_runtime.stop_session(session_id)
    await runtime.provider_controller.aclose()
    await runtime.database.close()


async def run_live_stepfun(
    *,
    data_directory: Path,
    frame_path: Path | None = None,
    frame_review_manifest_path: Path | None = None,
    highlight_frame_path: Path | None = None,
    mistake_frame_path: Path | None = None,
) -> dict[str, object]:
    key = require_live_opt_in(os.environ)
    initial_spec, hot_spec = _load_specs()
    frame_body, frame_mime_type, frame_evidence = _load_frame(
        frame_path,
        frame_review_manifest_path,
    )
    loaded_frames: dict[str, tuple[bytes, str, dict[str, object]]] = {
        "normal_silence_no_fabrication": (
            frame_body,
            frame_mime_type,
            frame_evidence,
        )
    }
    scenario_frames: dict[str, dict[str, object]] = {}
    for scenario_id, scenario_path in (
        ("normal_silence_no_fabrication", frame_path),
        ("highlight_multi_kill", highlight_frame_path),
        ("obvious_mistake", mistake_frame_path),
    ):
        if scenario_path is None:
            continue
        body, mime_type, item = _load_frame(
            scenario_path,
            frame_review_manifest_path,
        )
        loaded_frames[scenario_id] = (body, mime_type, item)
        scenario_frames[scenario_id] = {
            key: value
            for key, value in item.items()
            if key not in {"provenance"}
        }
    with TemporaryDirectory(prefix="advx-stepfun-audio-") as audio_dir:
        pcm = _generate_pcm(Path(audio_dir))
        configuration = ProviderConfigurationRequest(
            provider_profile_id="stepfun-live",
            model_base_url=BASE_URL,
            model_name=MODEL,
            director_model=MODEL,
            viewer_model=MODEL,
            memory_model=MODEL,
            visual_summary_model=MODEL,
            model_api_key=key,
            asr_api_key=key,
        )
        probe = LiveCapabilityProbe(configuration=configuration, pcm=pcm, checks=[])
        runtime = build_runtime(
            local_token="live-stepfun-local-token",
            data_directory=data_directory,
            runtime_capability_probe=probe,
        )
        runtime.configure_provider_profile(configuration)
        voice_target_resolver = LiveVoiceTargetResolver()
        assert runtime.ingest_service is not None
        runtime.ingest_service.set_voice_target_resolver(voice_target_resolver)
        observer = ProviderCallObserver(runtime.provider_router, runtime.frame_store)
        app = create_app(runtime=runtime)
        first_app_runtime_identity = app.state.runtime is runtime
        stage = "startup"
        session_id = ""
        runtime2 = None
        try:
            await runtime.startup()
            stage = "session_start"
            started = await runtime.runtime_session_service.start(
                RuntimeSessionStartRequest(
                    client_request_id="live-stepfun-session-1",
                    canonical_runtime_spec=initial_spec,
                    client_config_hash=initial_spec.config_hash(),
                )
            )
            session_id = started.session_id
            assert runtime.ingest_service is not None

            now = runtime.clock.now_ms()
            live_scenarios: list[dict[str, object]] = []
            for scenario_id in (
                "normal_silence_no_fabrication",
                "highlight_multi_kill",
                "obvious_mistake",
            ):
                stage = scenario_id
                loaded = loaded_frames.get(scenario_id)
                if loaded is None:
                    continue
                scenario_body, scenario_mime, scenario_frame = loaded
                before_query = runtime.debug_service.query(TraceQuery(limit=1_000))
                before_traces = list(before_query.items)
                before_wave_ids = {wave.trace_id for wave in before_query.waves}
                await runtime.ingest_service.submit_frame(
                    FrameInput(
                        session_id=session_id,
                        input_id=f"live-frame-{scenario_id}",
                        captured_at_ms=runtime.clock.now_ms(),
                        mime_type=scenario_mime,
                        body=scenario_body,
                        change_score=1.0,
                    )
                )
                await _wait_for_runtime(runtime, session_id)
                after_query = runtime.debug_service.query(TraceQuery(limit=1_000))
                after_traces = list(after_query.items)
                related_waves = [
                    wave
                    for wave in after_query.waves
                    if wave.trace_id not in before_wave_ids
                    and scenario_frame["sha256"] in wave.frame_hashes
                ]
                related_traces = [
                    trace
                    for trace in _new_items(before_traces, after_traces)
                    if scenario_frame["sha256"] in trace.frame_hashes
                ]
                scenario_events = list(
                    await runtime.room_service.read_events(session_id)
                )
                texts = _published_texts(scenario_events, related_traces)
                checks = {
                    "responses_reference_current_wave": all(
                        scenario_frame["sha256"] in trace.frame_hashes
                        for trace in related_traces
                    )
                    and bool(related_traces),
                    "reviewed_frame_provenance": (
                        scenario_frame["provenance_status"] == "verified"
                        and scenario_frame["provenance"]["scenario_category"]
                        == scenario_id
                    ),
                    "frame_wave_observed": bool(related_waves),
                }
                if scenario_id == "normal_silence_no_fabrication":
                    checks = {
                        "silence_allowed": True,
                        "no_fabricated_kill": not _contains_any(
                            texts,
                            (
                                "击杀",
                                "杀了",
                                "双杀",
                                "三杀",
                                "四杀",
                                "multi-kill",
                                "multikill",
                            ),
                        ),
                        "reviewed_frame_provenance": checks[
                            "reviewed_frame_provenance"
                        ],
                        "frame_wave_observed": checks["frame_wave_observed"],
                    }
                elif scenario_id == "highlight_multi_kill":
                    checks["semantic_highlight_observed"] = _contains_any(
                        texts,
                        ("27", "四杀", "击杀", "最高", "mvp"),
                    )
                else:
                    checks["semantic_mistake_observed"] = _contains_any(
                        texts,
                        ("队友", "误伤", "攻击", "嘲讽"),
                    )
                live_scenarios.append(
                    _scenario_from_runtime(
                        scenario_id=scenario_id,
                        input_kind="reviewed_real_game_frame",
                        input_sha256=str(scenario_frame["sha256"]),
                        input_events=[],
                        traces=related_traces,
                        viewers=started.viewers,
                        checks=checks,
                        frame_sha256=str(scenario_frame["sha256"]),
                        room_events=scenario_events,
                    )
                )

            stage = "user_text_response"
            text_input = "请记住：这局在沙二发生了一次关键翻盘。"
            before_text_events = list(
                await runtime.room_service.read_events(session_id)
            )
            before_text_traces = list(
                runtime.debug_service.query(TraceQuery(limit=1_000)).items
            )
            await runtime.ingest_service.submit_text(
                TextInput(
                    session_id=session_id,
                    input_id="live-text-1",
                    created_at_ms=runtime.clock.now_ms(),
                    text=text_input,
                )
            )
            await _wait_for_runtime(runtime, session_id)
            text_events = list(await runtime.room_service.read_events(session_id))
            new_text_events = _new_events(before_text_events, text_events)
            text_traces = _new_items(
                before_text_traces,
                list(runtime.debug_service.query(TraceQuery(limit=1_000)).items),
            )
            live_scenarios.append(
                _scenario_from_runtime(
                    scenario_id="user_text_response",
                    input_kind="user_text",
                    input_sha256=hashlib.sha256(text_input.encode("utf-8")).hexdigest(),
                    input_events=[
                        event
                        for event in new_text_events
                        if event.source_type is RoomEventSource.USER_TEXT
                    ],
                    traces=text_traces,
                    viewers=started.viewers,
                    checks={
                        "user_text_response_observed": any(
                            trace.response_status.value
                            in {"published", "silence"}
                            for trace in text_traces
                        ),
                    },
                    room_events=text_events,
                )
            )
            stage = "final_voice_structured_mention"
            target_viewer = started.viewers[0]
            voice_target_resolver.target_viewer_id = (
                target_viewer.viewer_instance_id
            )
            mentioned_voice_text = (
                f"请 {target_viewer.display_name} 回应刚才的沙二画面。"
            )
            mention_pcm = _generate_pcm(Path(audio_dir), mentioned_voice_text)
            before_voice_events = list(
                await runtime.room_service.read_events(session_id)
            )
            before_voice_traces = list(
                runtime.debug_service.query(TraceQuery(limit=1_000)).items
            )
            await runtime.ingest_service.submit_audio(
                AudioInput(
                    session_id=session_id,
                    input_id="live-audio-1",
                    captured_at_ms=now + 2,
                    format="audio/pcm;rate=16000;channels=1;format=s16le",
                    body=mention_pcm,
                )
            )
            await runtime.ingest_service.commit_audio(
                AudioCommit(
                    session_id=session_id,
                    input_id="live-audio-1",
                    committed_at_ms=now + max(3, len(mention_pcm) // 32),
                )
            )
            await _wait_for_voice(runtime, session_id)
            events_after_asr = await runtime.room_service.read_events(session_id)
            final_asr_event_count = sum(
                event.source_type is RoomEventSource.USER_VOICE
                for event in events_after_asr
            )
            await _wait_for_runtime(runtime, session_id)
            voice_events = [
                event
                for event in _new_events(before_voice_events, list(events_after_asr))
                if event.source_type is RoomEventSource.USER_VOICE
            ]
            voice_traces = _new_items(
                before_voice_traces,
                list(runtime.debug_service.query(TraceQuery(limit=1_000)).items),
            )
            target_selected = any(
                target_viewer.viewer_instance_id
                in trace.director_decision.selected_viewer_ids
                for trace in voice_traces
            )
            structured_target_present = any(
                event.payload.get("target_viewer_id")
                == target_viewer.viewer_instance_id
                and event.payload.get("target_resolver_id")
                == voice_target_resolver.resolver_id
                and event.payload.get("target_ambiguous") is False
                for event in voice_events
            )
            target_forced = any(
                target_viewer.viewer_instance_id
                in trace.director_budget.forced_viewer_ids
                for trace in voice_traces
            )
            live_scenarios.append(
                _scenario_from_runtime(
                    scenario_id="final_voice_structured_mention",
                    input_kind="final_voice",
                    input_sha256=hashlib.sha256(mention_pcm).hexdigest(),
                    input_events=voice_events,
                    traces=voice_traces,
                    viewers=started.viewers,
                    checks={
                        "final_voice_event_observed": bool(voice_events),
                        "transcript_mentions_viewer": any(
                            target_viewer.display_name in (event.text or "")
                            for event in voice_events
                        ),
                        "structured_mention_selected": (
                            voice_target_resolver.calls > 0
                            and structured_target_present
                            and target_forced
                            and target_selected
                        ),
                    },
                    room_events=list(
                        await runtime.room_service.read_events(session_id)
                    ),
                    extra={
                        "mentioned_viewer": _hash_id(
                            target_viewer.viewer_instance_id
                        )
                    },
                )
            )

            stage = "hot_update_6657"
            before_hot_events = list(
                await runtime.room_service.read_events(session_id)
            )
            active_mode = next(
                mode for mode in hot_spec.modes if mode.mode_id == hot_spec.active_mode_id
            )
            namespace = active_mode.namespace_id
            meme_setting = await runtime.shared_brain_service.get_auto_ingest(namespace)
            if not meme_setting.enabled:
                await runtime.shared_brain_service.set_auto_ingest(
                    namespace,
                    enabled=True,
                    expected_revision=meme_setting.revision,
                )
            applied = await runtime.runtime_session_service.apply(
                session_id,
                RuntimeApplyRequest(
                    apply_id="live-stepfun-6657",
                    base_revision=1,
                    canonical_runtime_spec=hot_spec,
                    client_config_hash=hot_spec.config_hash(),
                ),
            )
            await runtime.ingest_service.submit_text(
                TextInput(
                    session_id=session_id,
                    input_id="live-text-after-hot",
                    created_at_ms=runtime.clock.now_ms(),
                    text=(
                        "观众配置已经热更新。请 Director 按新的独立观众身份响应。"
                    ),
                )
            )
            await _wait_for_runtime(runtime, session_id)
            traces_after_hot = runtime.debug_service.query(TraceQuery(limit=1_000)).items
            for attempt in range(2):
                if any(
                    trace.audience_epoch == applied.audience_epoch
                    for trace in traces_after_hot
                ):
                    break
                await runtime.ingest_service.submit_text(
                    TextInput(
                        session_id=session_id,
                        input_id=f"live-text-after-hot-proof-{attempt + 1}",
                        created_at_ms=runtime.clock.now_ms(),
                        text=(
                            "这是独立观众调用验证事件。请 Director 从当前可选观众中"
                            "选择两名最适合回应的观众。"
                        ),
                    )
                )
                await _wait_for_runtime(runtime, session_id)
                traces_after_hot = runtime.debug_service.query(
                    TraceQuery(limit=1_000)
                ).items
            hot_events = [
                event
                for event in _new_events(
                    before_hot_events,
                    list(await runtime.room_service.read_events(session_id)),
                )
                if event.source_type is RoomEventSource.USER_TEXT
            ]
            applied_by_id = {
                viewer.viewer_instance_id: viewer for viewer in applied.viewers
            }
            hot_traces = [
                trace
                for trace in traces_after_hot
                if trace.audience_epoch == applied.audience_epoch
                and trace.response_status.value in {"published", "silence"}
            ]
            call_counts: dict[str, int] = {}
            for trace in hot_traces:
                persona_id = applied_by_id[trace.viewer_instance_id].persona_id
                call_counts[persona_id] = call_counts.get(persona_id, 0) + 1
            low_persona = min(
                (
                    persona_id
                    for persona_id, weight in active_mode.persona_weights.items()
                    if weight > 0 and persona_id != "instigator"
                ),
                key=lambda persona_id: (
                    active_mode.persona_weights[persona_id],
                    persona_id,
                ),
            )
            high_allocated = _counts(applied.viewers).get("instigator", 0)
            low_allocated = _counts(applied.viewers).get(low_persona, 0)
            high_calls = call_counts.get("instigator", 0)
            low_calls = call_counts.get(low_persona, 0)
            hot_input_hash = hashlib.sha256(
                "\n".join(event.text or "" for event in hot_events).encode("utf-8")
            ).hexdigest()
            live_scenarios.append(
                _scenario_from_runtime(
                    scenario_id="6657_persona_allocation_call_ratio",
                    input_kind="hot_update_and_user_text",
                    input_sha256=hot_input_hash,
                    input_events=hot_events,
                    traces=hot_traces,
                    viewers=applied.viewers,
                    checks={
                        "allocation_ratio_proved": high_allocated > low_allocated,
                        "actual_call_ratio_proved": high_calls > low_calls,
                    },
                    room_events=list(
                        await runtime.room_service.read_events(session_id)
                    ),
                    extra={
                        "metrics": {
                            "high_weight_persona": "instigator",
                            "low_weight_persona": low_persona,
                            "high_weight_allocated": high_allocated,
                            "low_weight_allocated": low_allocated,
                            "high_weight_calls": high_calls,
                            "low_weight_calls": low_calls,
                        }
                    },
                )
            )
            memes_before = await runtime.shared_brain_service.list_memes(namespace)
            frame_expires_at_ms = (
                now + hot_spec.settings.frame_bundle.frame_window_ms + 1
            )
            frame_expiry_delay_ms = max(
                0,
                frame_expires_at_ms - runtime.clock.now_ms(),
            )
            if not memes_before and frame_expiry_delay_ms:
                await asyncio.sleep(frame_expiry_delay_ms / 1_000)
            for attempt in range(4):
                if memes_before:
                    break
                await runtime.ingest_service.submit_text(
                    TextInput(
                        session_id=session_id,
                        input_id=f"live-meme-proof-{attempt + 1}",
                        created_at_ms=runtime.clock.now_ms(),
                        text=(
                            "仅引用这条文字事件，不要引用画面：沙二关键翻盘 "
                            "沙二关键翻盘 沙二关键翻盘 沙二关键翻盘"
                        ),
                    )
                )
                await _wait_for_runtime(runtime, session_id)
                memes_before = await runtime.shared_brain_service.list_memes(namespace)
            meme_before_undo = memes_before[0] if memes_before else None
            meme_undo = None
            if meme_before_undo is not None:
                meme_undo = await runtime.shared_brain_service.undo_meme(
                    namespace,
                    meme_before_undo.meme_id,
                    expected_revision=meme_before_undo.revision,
                )
            before_crash_events = await runtime.room_service.read_events(session_id)
            memories_before = await runtime.shared_brain_service.list_memories(
                hot_spec.room.room_id
            )
            seed_event = next(
                event
                for event in before_crash_events
                if event.source_type is RoomEventSource.USER_TEXT
            )
            memory_seeded = False
            if not memories_before:
                memory_result = await runtime.shared_brain_service.commit_memory_candidate(
                    RoomMemoryCandidate(
                        candidate_id="live-e2e-memory-candidate",
                        room_id=hot_spec.room.room_id,
                        session_id=session_id,
                        audience_epoch=applied.audience_epoch,
                        idempotency_key="live-e2e-memory-candidate-v1",
                        base_revision=0,
                        memory_id="live-e2e-shared-experience",
                        memory_type=RoomMemoryType.SHARED_EXPERIENCE,
                        content="沙二发生过一次关键翻盘。",
                        evidence_event_ids=(seed_event.event_id,),
                        origin="live_e2e_persistence_proof",
                        importance=0.8,
                        confidence=1.0,
                    )
                )
                memory_seeded = memory_result.accepted
                memories_before = await runtime.shared_brain_service.list_memories(
                    hot_spec.room.room_id
                )
            traces_before = runtime.debug_service.query(TraceQuery(limit=1_000)).items
            hot_trace_ids = {
                trace.viewer_instance_id
                for trace in traces_before
                if trace.audience_epoch == applied.audience_epoch
            }

            stage = "crash"
            await _abrupt_close(runtime, session_id)
            runtime = None

            stage = "restart_recover"
            probe2 = LiveCapabilityProbe(
                configuration=configuration,
                pcm=pcm,
                checks=list(probe.checks),
                verified=probe.verified,
            )
            runtime2 = build_runtime(
                local_token="live-stepfun-local-token-2",
                data_directory=data_directory,
                runtime_capability_probe=probe2,
            )
            runtime2.configure_provider_profile(configuration)
            observer2 = ProviderCallObserver(
                runtime2.provider_router,
                runtime2.frame_store,
            )
            app2 = create_app(runtime=runtime2)
            await runtime2.startup()
            recovered = await runtime2.runtime_session_service.recover(session_id)
            assert runtime2.ingest_service is not None
            await runtime2.ingest_service.submit_text(
                TextInput(
                    session_id=session_id,
                    input_id="live-text-after-recover",
                    created_at_ms=runtime2.clock.now_ms(),
                    text="后端恢复后继续同一个逻辑会话。",
                )
            )
            await _wait_for_runtime(runtime2, session_id)
            recovered_events = await runtime2.room_service.read_events(session_id)
            recovered_event_ids = {event.event_id for event in recovered_events}
            recovered_matching_event_count = sum(
                event.event_id in recovered_event_ids for event in before_crash_events
            )
            memories_recovered = await runtime2.shared_brain_service.list_memories(
                hot_spec.room.room_id
            )
            memes_recovered = await runtime2.shared_brain_service.list_memes(namespace)
            active_memes_recovered = (
                await runtime2.shared_brain_service.list_active_memes(namespace)
            )
            candidate_trace = next(
                (
                    trace
                    for trace in traces_before
                    if trace.side_effects.meme_candidate is not None
                    and meme_before_undo is not None
                    and trace.side_effects.meme_candidate.candidate_id
                    == meme_before_undo.source_candidate_id
                ),
                None,
            )
            candidate = (
                candidate_trace.side_effects.meme_candidate
                if candidate_trace is not None
                else None
            )
            candidate_events = [
                event
                for event in before_crash_events
                if candidate is not None
                and event.event_id in candidate.evidence_event_ids
            ]
            candidate_overlay_event_count = sum(
                event.source_id == candidate.candidate_id
                or event.payload.get("candidate_id") == candidate.candidate_id
                or event.payload.get("meme_candidate_id") == candidate.candidate_id
                for event in before_crash_events
                if candidate is not None
                and event.source_type is RoomEventSource.AUDIENCE_BARRAGE
            )
            recovered_meme = next(
                (
                    item
                    for item in memes_recovered
                    if meme_before_undo is not None
                    and item.meme_id == meme_before_undo.meme_id
                ),
                None,
            )
            live_scenarios.append(
                _scenario_from_runtime(
                    scenario_id="meme_candidate_lifecycle",
                    input_kind="meme_candidate_evidence",
                    input_sha256=hashlib.sha256(
                        "\n".join(
                            event.event_id for event in candidate_events
                        ).encode("utf-8")
                    ).hexdigest(),
                    input_events=candidate_events,
                    traces=[candidate_trace] if candidate_trace is not None else [],
                    viewers=applied.viewers,
                    checks={
                        "candidate_never_overlayed": (
                            candidate_overlay_event_count == 0
                        ),
                        "undo_survived_restart": (
                            recovered_meme is not None
                            and recovered_meme.state.value == "revoked"
                            and all(
                                item.meme_id != recovered_meme.meme_id
                                for item in active_memes_recovered
                            )
                        ),
                    },
                    room_events=list(before_crash_events),
                    extra={
                        "meme_lifecycle": {
                            "candidate_id": (
                                _hash_id(candidate.candidate_id)
                                if candidate is not None
                                else ""
                            ),
                            "auto_ingested": (
                                candidate is not None
                                and meme_before_undo is not None
                                and meme_before_undo.source_candidate_id
                                == candidate.candidate_id
                            ),
                            "overlay_event_count": candidate_overlay_event_count,
                            "undo_accepted": (
                                meme_undo is not None
                                and meme_undo.state.value == "revoked"
                            ),
                            "absent_after_restart": (
                                recovered_meme is not None
                                and all(
                                    item.meme_id != recovered_meme.meme_id
                                    for item in active_memes_recovered
                                )
                            ),
                            "relisted_state": (
                                recovered_meme.state.value
                                if recovered_meme is not None
                                else None
                            ),
                        }
                    },
                )
            )
            await runtime2.session_service.stop(session_id)

            stage = "cross_session_mode"
            cross_mode = next(
                mode for mode in hot_spec.modes if mode.mode_id != hot_spec.active_mode_id
            )
            cross_spec = hot_spec.model_copy(
                update={
                    "config_revision": 3,
                    "active_mode_id": cross_mode.mode_id,
                }
            )
            second = await runtime2.runtime_session_service.start(
                RuntimeSessionStartRequest(
                    client_request_id="live-stepfun-session-2",
                    canonical_runtime_spec=cross_spec,
                    client_config_hash=cross_spec.config_hash(),
                )
            )
            memories_cross = await runtime2.shared_brain_service.list_memories(
                cross_spec.room.room_id
            )
            cross_text = "请结合直播间长期记忆回应这条跨模式会话消息。"
            before_cross_events = list(
                await runtime2.room_service.read_events(second.session_id)
            )
            await runtime2.ingest_service.submit_text(
                TextInput(
                    session_id=second.session_id,
                    input_id="live-cross-session-memory-request",
                    created_at_ms=runtime2.clock.now_ms(),
                    text=cross_text,
                    target_viewer_id=second.viewers[0].viewer_instance_id,
                )
            )
            await _wait_for_runtime(runtime2, second.session_id)
            cross_traces = list(
                runtime2.debug_service.query(
                    TraceQuery(session_id=second.session_id, limit=1_000)
                ).items
            )
            cross_events = [
                event
                for event in _new_events(
                    before_cross_events,
                    list(
                        await runtime2.room_service.read_events(second.session_id)
                    ),
                )
                if event.source_type is RoomEventSource.USER_TEXT
            ]
            expected_memory_ids = {item.memory_id for item in memories_cross}
            traces_with_memory = [
                trace
                for trace in cross_traces
                if expected_memory_ids.intersection(trace.memory.memory_ids)
            ]
            live_scenarios.append(
                _scenario_from_runtime(
                    scenario_id="cross_session_mode_memory_injection",
                    input_kind="cross_session_targeted_user_text",
                    input_sha256=hashlib.sha256(
                        cross_text.encode("utf-8")
                    ).hexdigest(),
                    input_events=cross_events,
                    traces=cross_traces,
                    viewers=second.viewers,
                    checks={
                        "memory_prompt_injection_proved": bool(traces_with_memory)
                        and any(
                            call["role"] == "generate"
                            and call["status"] == "passed"
                            and call["request"]
                            in {
                                _hash_id(trace.trace_id)
                                for trace in traces_with_memory
                            }
                            for call in observer2.calls
                        )
                    },
                    room_events=list(
                        await runtime2.room_service.read_events(second.session_id)
                    ),
                    extra={
                        "memory": {
                            "memory_id": (
                                _hash_id(next(iter(expected_memory_ids)))
                                if expected_memory_ids
                                else ""
                            ),
                            "cross_session": second.session_id != session_id,
                            "cross_mode": (
                                cross_spec.active_mode_id
                                != hot_spec.active_mode_id
                            ),
                            "injected_into_real_viewer_request": bool(
                                traces_with_memory
                            ),
                        }
                    },
                )
            )
            await runtime2.session_service.stop(second.session_id)

            trace_identity = [
                {
                    "request": _hash_id(trace.trace_id),
                    "viewer": _hash_id(trace.viewer_instance_id),
                    "observation": _hash_id(trace.observation_id),
                    "epoch": trace.audience_epoch,
                    "model": trace.provider.model_id,
                    "status": trace.response_status.value,
                }
                for trace in traces_before
            ]
            director_calls = [
                item for item in observer.calls if item["role"] == "decide"
            ]
            hot_viewer_traces = [
                trace
                for trace in traces_before
                if trace.audience_epoch == applied.audience_epoch
            ]
            independent_live_wave = _independent_live_wave(
                hot_viewer_traces,
                observer.calls,
                audience_epoch=applied.audience_epoch,
            )
            image_check = next(
                (
                    item
                    for item in probe.checks
                    if item["capability"] == "image_input"
                ),
                None,
            )
            trace_requests = {
                item["request"]
                for item in trace_identity
                if item["status"] in {"published", "silence"}
            }
            real_game_frame_evidence = frame_evidence
            bound_real_game_calls: list[dict[str, object]] = []
            for _, _, reviewed_frame in loaded_frames.values():
                matching_calls = [
                    item
                    for item in observer.calls
                    if item["role"] == "generate"
                    and item["status"] == "passed"
                    and reviewed_frame["sha256"] in item.get("frame_sha256", [])
                    and item["request"] in trace_requests
                ]
                if matching_calls:
                    real_game_frame_evidence = reviewed_frame
                    bound_real_game_calls = matching_calls
                    break
            real_game_smoke_passed = (
                real_game_frame_evidence["source_kind"] == "external_candidate"
                and real_game_frame_evidence["provenance_status"] == "verified"
                and bool(bound_real_game_calls)
            )
            credentialed_provider_proof = any(
                item["status"] == "passed"
                and item["role"] in {"decide", "generate", "extract"}
                for item in observer.calls
            )
            claims = {
                "production_graph": (
                    first_app_runtime_identity
                    and app2.state.runtime is runtime2
                    and runtime2.database.path.name == "advx.sqlite3"
                ),
                "capabilities_passed": (
                    bool(probe.checks)
                    and all(item["status"] == "passed" for item in probe.checks)
                ),
                "frame_text_final_asr_observed": (
                    final_asr_event_count > 0
                    and any(
                        event.source_type is RoomEventSource.USER_TEXT
                        for event in before_crash_events
                    )
                ),
                "viewer_trace_identity_proved": (
                    bool(hot_trace_ids)
                    and hot_trace_ids.issubset(
                        {viewer.viewer_instance_id for viewer in applied.viewers}
                    )
                ),
                "live_director_response_succeeded": (
                    bool(director_calls)
                    and any(item["status"] == "passed" for item in director_calls)
                ),
                "independent_live_viewer_responses_succeeded": (
                    independent_live_wave is not None
                ),
                "weight_6657_changed_pool": (
                    _counts(applied.viewers).get("instigator", 0)
                    > _counts(started.viewers).get("instigator", 0)
                ),
                "same_logical_session_new_epoch": (
                    recovered.session_id == session_id
                    and recovered.recovered
                    and recovered.audience_epoch > applied.audience_epoch
                ),
                "room_events_recovered": (
                    recovered_matching_event_count > 0
                    and any(
                        event.source_type is RoomEventSource.USER_TEXT
                        for event in recovered_events
                    )
                ),
                "room_memory_shared_cross_session_mode": (
                    bool(memories_recovered)
                    and [item.memory_id for item in memories_recovered]
                    == [item.memory_id for item in memories_cross]
                ),
                "mode_meme_persistence_observed": (
                    bool(memes_before)
                ),
                "external_real_game_smoke_passed": real_game_smoke_passed,
                "credentialed_provider_proof": credentialed_provider_proof,
            }
            scenarios_by_id = {
                item["scenario_id"]: item for item in live_scenarios
            }
            blocked_by_id = {
                item["scenario_id"]: item
                for item in _blocked_scenarios(
                    "the scenario did not execute",
                    scenario_frames,
                )
            }
            scenarios = [
                scenarios_by_id.get(scenario_id, blocked_by_id[scenario_id])
                for scenario_id in SCENARIO_IDS
            ]
            scenarios_passed = all(
                item["status"] == "passed" for item in scenarios
            )
            return {
                "artifact_version": 2,
                "redacted": True,
                "provider": {
                    "profile": "stepfun-live",
                    "model": MODEL,
                    "asr_model": ASR_MODEL,
                    "base_url": BASE_URL,
                },
                "status": (
                    "passed"
                    if all(claims.values()) and scenarios_passed
                    else "blocked"
                ),
                "scenarios": scenarios,
                "capability_checks": probe.checks,
                "production_graph": {
                    "fastapi_title": app.title,
                    "sqlite_filename": runtime2.database.path.name,
                    "restart_fastapi_title": app2.title,
                },
                "ingest": {
                    "event_sources": sorted(
                        {
                            event.source_type.value
                            for event in (*events_after_asr, *before_crash_events)
                        }
                    ),
                    "final_asr_event_count": final_asr_event_count,
                    "frame": frame_evidence,
                },
                "real_game_smoke": {
                    "capability": "image_input",
                    "status": "passed" if real_game_smoke_passed else "blocked",
                    "model_id": image_check["model_id"] if image_check else None,
                    "frame": {
                        **real_game_frame_evidence,
                        "source_kind": (
                            "reviewed_real_game_capture"
                            if real_game_smoke_passed
                            else frame_evidence["source_kind"]
                        ),
                        "provenance": None,
                    },
                    "provenance": real_game_frame_evidence["provenance"],
                    "production_provider_calls": [
                        {
                            "role": item["role"],
                            "request": item["request"],
                            "frame_sha256": item["frame_sha256"],
                            "frame_refs": item["frame_refs"],
                            "status": item["status"],
                        }
                        for item in bound_real_game_calls
                    ],
                },
                "hot_update": {
                    "initial_counts": _counts(started.viewers),
                    "updated_counts": _counts(applied.viewers),
                    "initial_epoch": started.audience_epoch,
                    "updated_epoch": applied.audience_epoch,
                    "added_count": len(applied.diff.added_viewer_ids),
                    "retained_count": len(applied.diff.retained_viewer_ids),
                    "removed_count": len(applied.diff.removed_viewer_ids),
                },
                "call_identity": {
                    "trace_count": len(trace_identity),
                    "items": trace_identity,
                    "hot_epoch_unique_viewers": len(hot_trace_ids),
                    "provider_calls": observer.calls,
                },
                "independent_live_wave": independent_live_wave,
                "shared_brain": {
                    "memory_count_before_crash": len(memories_before),
                    "memory_count_after_recovery": len(memories_recovered),
                    "memory_count_cross_session_mode": len(memories_cross),
                    "mode_meme_count": len(memes_before),
                    "memory_seeded_for_persistence_proof": memory_seeded,
                    "mode_meme_seeded_for_persistence_proof": False,
                    "mode_meme_observation": "passed" if memes_before else "blocked",
                },
                "recovery": {
                    "logical_session": _hash_id(session_id),
                    "recovered": recovered.recovered,
                    "epoch_before": applied.audience_epoch,
                    "epoch_after": recovered.audience_epoch,
                    "event_count_before": len(before_crash_events),
                    "event_count_after": len(recovered_events),
                    "matched_event_count": recovered_matching_event_count,
                    "recovered_event_sources": sorted(
                        {event.source_type.value for event in recovered_events}
                    ),
                    "second_session": _hash_id(second.session_id),
                    "second_mode": cross_mode.mode_id,
                },
                "claims": claims,
                "not_proven": [
                    name
                    for name, proven in {
                        "live_director_response": claims[
                            "live_director_response_succeeded"
                        ],
                        "independent_live_viewer_responses": claims[
                            "independent_live_viewer_responses_succeeded"
                        ],
                        "room_long_term_memory_cross_session_mode": claims[
                            "room_memory_shared_cross_session_mode"
                        ],
                        "live_director_mode_meme_candidate": bool(memes_before),
                        "external_real_game_provider_binding": real_game_smoke_passed,
                        "credentialed_provider_proof": credentialed_provider_proof,
                    }.items()
                    if not proven
                ]
                + [
                    f"scenario:{item['scenario_id']}"
                    for item in scenarios
                    if item["status"] != "passed"
                ],
            }
        except Exception as error:
            blocked: dict[str, object] = {
                "stage": stage,
                "error_type": type(error).__name__,
                "session_started": bool(session_id),
            }
            if detail := _safe_error_detail(error):
                blocked["validation_detail"] = detail
            blocked["trace"] = _safe_error_trace(error)
            return {
                "artifact_version": 2,
                "redacted": True,
                "status": "blocked",
                "scenarios": _blocked_scenarios(f"run_failed_at:{stage}"),
                "blocked": blocked,
            }
        finally:
            if runtime2 is not None:
                await runtime2.shutdown()
            elif runtime is not None:
                await runtime.shutdown()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the credentialed, redacted StepFun Viewer Runtime E2E."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the redacted JSON evidence.",
    )
    parser.add_argument(
        "--data-directory",
        type=Path,
        help="Optional isolated data directory. A temporary directory is the default.",
    )
    parser.add_argument(
        "--frame",
        "--normal-frame",
        dest="frame",
        type=Path,
        default=(
            Path(os.environ[NORMAL_FRAME_ENV])
            if os.environ.get(NORMAL_FRAME_ENV)
            else Path(os.environ[FRAME_ENV])
            if os.environ.get(FRAME_ENV)
            else None
        ),
        help="Optional external PNG/JPEG/WebP game frame. The path is never persisted.",
    )
    parser.add_argument(
        "--highlight-frame",
        type=Path,
        default=(
            Path(os.environ[HIGHLIGHT_FRAME_ENV])
            if os.environ.get(HIGHLIGHT_FRAME_ENV)
            else None
        ),
        help="Optional reviewed highlight frame. The path is never persisted.",
    )
    parser.add_argument(
        "--mistake-frame",
        type=Path,
        default=(
            Path(os.environ[MISTAKE_FRAME_ENV])
            if os.environ.get(MISTAKE_FRAME_ENV)
            else None
        ),
        help="Optional reviewed obvious-mistake frame. The path is never persisted.",
    )
    parser.add_argument(
        "--frame-review-manifest",
        type=Path,
        default=(
            Path(os.environ[FRAME_REVIEW_MANIFEST_ENV])
            if os.environ.get(FRAME_REVIEW_MANIFEST_ENV)
            else FRAME_REVIEW_MANIFEST_PATH
        ),
        help=(
            "Redacted human-review manifest bound to the frame hash and dimensions."
        ),
    )
    return parser.parse_args()


def _emit(result: dict[str, object], output: Path | None) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")


def main() -> NoReturn:
    args = _parse_args()
    try:
        if args.data_directory is not None:
            result = asyncio.run(
                run_live_stepfun(
                    data_directory=args.data_directory,
                    frame_path=args.frame,
                    frame_review_manifest_path=args.frame_review_manifest,
                    highlight_frame_path=args.highlight_frame,
                    mistake_frame_path=args.mistake_frame,
                )
            )
        else:
            with TemporaryDirectory(prefix="advx-stepfun-live-") as directory:
                result = asyncio.run(
                    run_live_stepfun(
                        data_directory=Path(directory),
                        frame_path=args.frame,
                        frame_review_manifest_path=args.frame_review_manifest,
                        highlight_frame_path=args.highlight_frame,
                        mistake_frame_path=args.mistake_frame,
                    )
                )
    except Exception as error:
        blocked: dict[str, object] = {
            "stage": "preflight",
            "error_type": type(error).__name__,
            "session_started": False,
        }
        if detail := _safe_error_detail(error):
            blocked["validation_detail"] = detail
        blocked["trace"] = _safe_error_trace(error)
        result = {
            "artifact_version": 2,
            "redacted": True,
            "status": "blocked",
            "scenarios": _blocked_scenarios("preflight_failed"),
            "blocked": blocked,
        }
    _emit(result, args.output)
    raise SystemExit(0 if result["status"] == "passed" else 2)


if __name__ == "__main__":
    main()
