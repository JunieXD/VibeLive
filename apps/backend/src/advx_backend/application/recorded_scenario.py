from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from advx_backend.application.ports.asr import AudioChunk, AudioSource, TranscriptSegment
from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    TextInput,
)
from advx_backend.application.ports.memory import MemoryEvidence, RoomMemoryCandidate
from advx_backend.application.replay_service import ReplayService
from advx_backend.bootstrap import BackendRuntime, build_runtime
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.debug import TraceQuery
from advx_backend.contracts.replay import (
    RecordedOutputConsumption,
    RecordedProviderOutput,
    RecordedReplayEvidence,
    ReplayBundle,
    ReplayRequest,
)
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    RuntimeApplyRequest,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.memory import RoomMemoryType
from advx_backend.domain.observation_wave import ViewerVisualInputMode
from advx_backend.providers.model.viewer_runtime import ViewerRuntimeProtocolError

_PNG_FRAME = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x01@\x00\x00\x00\xc8"
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


class RecordedScenarioError(RuntimeError):
    def __init__(self, artifact_path: Path) -> None:
        self.artifact_path = artifact_path
        super().__init__("recorded scenario failed")


@dataclass(frozen=True)
class RecordedRuntimeFixture:
    runtime: BackendRuntime
    app: Any
    capability_probe: _PassingCapabilityProbe
    viewer_provider: _RecordedViewerProvider
    memory_extractor: _RecordedMemoryExtractor
    asr_provider: _RecordedAsrProvider
    asr_providers: Mapping[AudioSource, _RecordedAsrProvider]
    output_ledger: _RecordedOutputLedger

    @property
    def external_transport_call_count(self) -> int:
        return sum(provider.external_calls for provider in self.asr_providers.values())


class _VirtualClock:
    def __init__(self, value: int) -> None:
        self._value = value

    def now_ms(self) -> int:
        self._value += 1
        return self._value


class _SequenceIds:
    def __init__(self) -> None:
        self._value = 0

    def new_id(self) -> str:
        self._value += 1
        return f"scenario-{self._value:04d}"


class _PassingCapabilityProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def probe(self, spec: object) -> None:
        del spec
        self.calls += 1


class _RecordedOutputLedger:
    def __init__(self, bundle: ReplayBundle) -> None:
        outputs = {
            (item.provider_role, item.generation_request_id): item
            for item in bundle.recorded_provider_outputs
        }
        referenced: list[tuple[str, str]] = []
        for event in bundle.events:
            role = event.event_type.partition(".")[0]
            raw_ids = event.payload.get("generation_request_ids")
            raw_id = event.payload.get("generation_request_id")
            if isinstance(raw_ids, list):
                referenced.extend(
                    (role, item) for item in raw_ids if isinstance(item, str)
                )
            elif isinstance(raw_id, str):
                referenced.append((role, raw_id))
        if len(referenced) != len(set(referenced)):
            raise ValueError("recorded output identity is referenced more than once")
        referenced_set = set(referenced)
        output_set = set(outputs)
        if referenced_set != output_set:
            missing = referenced_set - output_set
            detail = "missing" if missing else "unknown"
            raise ValueError(f"{detail} recorded output identity")

        self._queues: dict[str, list[RecordedProviderOutput]] = {}
        for key in referenced:
            self._queues.setdefault(key[0], []).append(outputs[key])
        self._consumed: list[RecordedOutputConsumption] = []

    def consume(
        self,
        role: str,
        *,
        runtime_request_id: str | None = None,
    ) -> dict[str, Any]:
        queue = self._queues.get(role)
        if not queue:
            raise ValueError(f"recorded runtime has no remaining {role} output")
        item = queue.pop(0)
        call_index = (
            sum(
                consumed.provider_role == item.provider_role
                for consumed in self._consumed
            )
            + 1
        )
        self._consumed.append(
            RecordedOutputConsumption(
                provider_role=item.provider_role,
                generation_request_id=item.generation_request_id,
                call_index=call_index,
                runtime_request_id=runtime_request_id,
            )
        )
        return dict(item.output)

    def assert_complete(self) -> None:
        if any(self._queues.values()):
            raise ValueError("recorded runtime did not consume every output identity")

    @property
    def consumptions(self) -> list[RecordedOutputConsumption]:
        return list(self._consumed)


class _RecordedViewerProvider:
    def __init__(self, ledger: _RecordedOutputLedger) -> None:
        self._ledger = ledger
        self.viewer_calls = 0
        self.visual_calls = 0
        self.history_calls = 0

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.viewer_calls += 1
        viewer = self._ledger.consume(
            "viewer",
            runtime_request_id=request.generation_request_id,
        )
        action = ViewerAction(viewer.get("action", ViewerAction.BARRAGE))
        raw_texts = viewer.get("texts")
        legacy_text = viewer.get("text")
        if action is ViewerAction.BARRAGE and isinstance(raw_texts, list):
            texts = raw_texts
        elif action is ViewerAction.BARRAGE and isinstance(legacy_text, str):
            texts = [legacy_text]
        elif action is ViewerAction.BARRAGE:
            texts = ["recorded viewer response"]
        else:
            texts = None
        if action is ViewerAction.SILENCE:
            texts = None
        evidence: list[EvidenceRef] = []
        if request.input_event_ids:
            evidence.append(
                EvidenceRef(
                    source=EvidenceSource.EVENT,
                    event_id=request.input_event_ids[0],
                )
            )
        elif request.frame_bundle is not None:
            evidence.append(EvidenceRef(source=EvidenceSource.FRAME, frame_index=0))
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=action,
            texts=texts,
            reaction_type=str(viewer.get("reaction_type", "recorded")),
            evidence_refs=evidence,
        )

    async def summarize(
        self,
        wave: object,
        frame_bundle: object,
        runtime: object,
    ) -> str:
        del wave, frame_bundle, runtime
        self.visual_calls += 1
        visual = self._ledger.consume("visual_summary")
        summary = visual.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ViewerRuntimeProtocolError("recorded visual summary is blank")
        return summary.strip()

    async def summarize_history(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        existing_summary: str | None,
        older_history: str,
    ) -> str:
        del session_id, audience_epoch, existing_summary, older_history
        self.history_calls += 1
        history = self._ledger.consume("history_summary")
        summary = history.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ViewerRuntimeProtocolError("recorded history summary is blank")
        return summary.strip()

    async def aclose(self) -> None:
        return None


class _RecordedMemoryExtractor:
    def __init__(self, ledger: _RecordedOutputLedger) -> None:
        self._ledger = ledger
        self.calls = 0

    async def extract(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        events: Sequence[MemoryEvidence],
        current_revision: int,
    ) -> tuple[RoomMemoryCandidate, ...]:
        self.calls += 1
        output = self._ledger.consume("memory")
        if not events:
            return ()
        raw_candidates = output.get("candidates")
        if raw_candidates == []:
            return ()
        raw = (
            raw_candidates[0]
            if isinstance(raw_candidates, list) and raw_candidates
            else {}
        )
        evidence_ids = tuple(item.event_id for item in events[:1])
        digest = hashlib.sha256(
            f"{room_id}:{evidence_ids[0]}".encode()
        ).hexdigest()[:16]
        memory_type = RoomMemoryType(
            raw.get("memory_type", RoomMemoryType.SHARED_EXPERIENCE)
        )
        return (
            RoomMemoryCandidate(
                candidate_id=f"recorded-memory-candidate-{digest}",
                room_id=room_id,
                session_id=session_id,
                audience_epoch=audience_epoch,
                idempotency_key=f"recorded-memory-{digest}",
                base_revision=current_revision,
                memory_id=f"recorded-memory-{digest}",
                memory_type=memory_type,
                content=str(raw.get("content", "recorded shared experience")),
                evidence_event_ids=evidence_ids,
                tags=tuple(raw.get("tags", ["recorded"])),
                importance=float(raw.get("importance", 0.5)),
                confidence=float(raw.get("confidence", 1.0)),
            ),
        )

    async def aclose(self) -> None:
        return None


class _RecordedAsrProvider:
    def __init__(
        self,
        ledger: _RecordedOutputLedger,
        *,
        source: AudioSource,
        final_delivered: asyncio.Event,
    ) -> None:
        self._ledger = ledger
        self._source = source
        self._queue: asyncio.Queue[TranscriptSegment | None] = asyncio.Queue()
        self._session_id: str | None = None
        self._pushed: AudioChunk | None = None
        self.final_delivered = final_delivered
        self.external_calls = 0

    async def start(self) -> None:
        return None

    async def push_audio(self, chunk: AudioChunk) -> None:
        if chunk.source is not self._source:
            raise ValueError("recorded ASR received the wrong audio source")
        self._session_id = chunk.session_id
        self._pushed = chunk

    async def commit(self, source: AudioSource = AudioSource.MICROPHONE) -> None:
        if source is not self._source:
            raise ValueError("recorded ASR commit source does not match provider source")
        if self._session_id is None or self._pushed is None:
            raise RuntimeError("recorded ASR has no pending audio")
        output = self._ledger.consume("asr")
        await self._queue.put(
            TranscriptSegment(
                session_id=self._session_id,
                source=self._pushed.source,
                text=str(output.get("text", "recorded final transcript")),
                started_at_ms=int(
                    output.get("started_at_ms", self._pushed.started_at_ms)
                ),
                ended_at_ms=int(
                    output.get("ended_at_ms", self._pushed.ended_at_ms)
                ),
                final=True,
            )
        )

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item
            if item.final:
                self.final_delivered.set()

    async def stop(self) -> None:
        await self._queue.put(None)


def _install_determinism(
    runtime: BackendRuntime,
    *,
    clock: _VirtualClock,
    ids: _SequenceIds,
) -> None:
    runtime.clock = clock  # type: ignore[assignment]
    runtime.id_generator = ids  # type: ignore[assignment]
    for component in (
        runtime.session_service,
        runtime.room_service,
        runtime.context_builder,
        runtime.audience_service,
        runtime.generation_trigger,
        runtime.barrage_pipeline,
        runtime.shared_brain_service,
        runtime.runtime_session_service,
    ):
        if hasattr(component, "_clock"):
            component._clock = clock
        if hasattr(component, "_id_generator"):
            component._id_generator = ids
    runtime.frame_store._id_generator = ids
    runtime.runtime_session_service._viewer_pool._id_generator = ids


def _provider_request(bundle: ReplayBundle) -> ProviderConfigurationRequest:
    provider = bundle.canonical_runtime_spec.provider
    return ProviderConfigurationRequest(
        provider_profile_id=provider.provider_profile_id,
        model_base_url="https://recorded.invalid/v1",
        model_name=provider.viewer_model,
        viewer_model=provider.viewer_model,
        memory_model=provider.memory_model,
        visual_summary_model=provider.visual_summary_model,
        model_api_key="recorded-no-network",
        asr_api_key="recorded-no-network",
    )


def build_recorded_runtime_fixture(
    *,
    data_directory: Path,
    local_token: str,
    bundle: ReplayBundle,
) -> RecordedRuntimeFixture:
    """Build the production FastAPI graph with deterministic, no-network adapters."""

    random.seed(bundle.seed)
    data_directory.mkdir(parents=True, exist_ok=True)
    probe = _PassingCapabilityProbe()
    runtime = build_runtime(
        local_token=local_token,
        data_directory=data_directory,
        runtime_capability_probe=probe,
    )
    _install_determinism(
        runtime,
        clock=_VirtualClock(bundle.virtual_clock_start_ms),
        ids=_SequenceIds(),
    )
    ledger = _RecordedOutputLedger(bundle)
    viewer = _RecordedViewerProvider(ledger)
    memory = _RecordedMemoryExtractor(ledger)
    final_delivered = asyncio.Event()
    asr_providers = {
        source: _RecordedAsrProvider(
            ledger,
            source=source,
            final_delivered=final_delivered,
        )
        for source in AudioSource
    }
    asr = asr_providers[AudioSource.MICROPHONE]
    runtime.configure_recorded_runtime_pipeline(
        request=_provider_request(bundle),
        viewer_provider=viewer,
        memory_extractor=memory,
        asr_providers=asr_providers,
    )

    from advx_backend.main import create_app

    return RecordedRuntimeFixture(
        runtime=runtime,
        app=create_app(runtime=runtime),
        capability_probe=probe,
        viewer_provider=viewer,
        memory_extractor=memory,
        asr_provider=asr,
        asr_providers=asr_providers,
        output_ledger=ledger,
    )


async def _run_recorded_runtime_once(
    *,
    data_directory: Path,
    request: ReplayRequest,
) -> dict[str, Any]:
    if request.allow_external_provider_calls:
        raise ValueError("recorded scenario forbids external Provider calls")
    bundle = request.bundle
    random.seed(bundle.seed)
    data_directory.mkdir(parents=True, exist_ok=True)
    stage = "build"
    runtime: BackendRuntime | None = None
    session_id: str | None = None
    stopped = False
    try:
        fixture = build_recorded_runtime_fixture(
            bundle=bundle,
            local_token="recorded-headless-token",
            data_directory=data_directory,
        )
        runtime = fixture.runtime
        probe = fixture.capability_probe
        viewer = fixture.viewer_provider
        memory = fixture.memory_extractor
        asr = fixture.asr_provider
        clock = runtime.clock

        stage = "fastapi_startup"
        app = fixture.app
        async with app.router.lifespan_context(app):
            stage = "session_start"
            start = await runtime.runtime_session_service.start(
                RuntimeSessionStartRequest(
                    client_request_id=f"scenario:{bundle.bundle_id}",
                    canonical_runtime_spec=bundle.canonical_runtime_spec,
                    client_config_hash=bundle.config_hash,
                )
            )
            session_id = start.session_id
            assert runtime.ingest_service is not None

            stage = "text"
            text_receipt = await runtime.ingest_service.submit_text(
                TextInput(
                    session_id=session_id,
                    input_id="scenario-text",
                    created_at_ms=clock.now_ms(),
                    text="deterministic user text",
                )
            )
            assert runtime.reaction_scheduler is not None
            await runtime.reaction_scheduler.wait_for_idle(session_id)
            assert runtime.viewer_runtime_coordinator is not None
            await runtime.viewer_runtime_coordinator.wait_for_background_tasks()

            stage = "voice"
            audio_receipt = await runtime.ingest_service.submit_audio(
                AudioInput(
                    session_id=session_id,
                    input_id="scenario-audio",
                    captured_at_ms=clock.now_ms(),
                    format="audio/pcm;rate=16000;channels=1;format=s16le",
                    body=b"\x00\x00" * 160,
                )
            )
            await runtime.ingest_service.commit_audio(
                AudioCommit(
                    session_id=session_id,
                    input_id="scenario-audio",
                    committed_at_ms=clock.now_ms(),
                )
            )
            await asyncio.wait_for(asr.final_delivered.wait(), timeout=2)
            await runtime.reaction_scheduler.wait_for_idle(session_id)
            await runtime.viewer_runtime_coordinator.wait_for_background_tasks()

            stage = "hot_apply"
            next_spec = bundle.canonical_runtime_spec.model_copy(
                update={
                    "config_revision": bundle.canonical_runtime_spec.config_revision + 1,
                    "settings": bundle.canonical_runtime_spec.settings.model_copy(
                        update={
                            "viewer_visual_input_mode": ViewerVisualInputMode.SHARED_SUMMARY
                        }
                    ),
                }
            )
            applied = await runtime.runtime_session_service.apply(
                session_id,
                RuntimeApplyRequest(
                    apply_id="scenario-hot-apply",
                    base_revision=bundle.canonical_runtime_spec.config_revision,
                    canonical_runtime_spec=next_spec,
                    client_config_hash=next_spec.config_hash(),
                ),
            )

            stage = "frame"
            frame_receipt = await runtime.ingest_service.submit_frame(
                FrameInput(
                    session_id=session_id,
                    input_id="scenario-frame",
                        captured_at_ms=clock.now_ms(),
                        mime_type="image/png",
                        body=_PNG_FRAME,
                        change_score=1.0,
                    )
            )
            await runtime.reaction_scheduler.wait_for_idle(session_id)
            assert runtime.viewer_runtime_coordinator is not None
            await runtime.viewer_runtime_coordinator.wait_for_background_tasks()

            stage = "observe"
            current = applied
            room_events = await runtime.room_service.read_events(session_id)
            memories = await runtime.shared_brain_service.list_memories(
                bundle.canonical_runtime_spec.room.room_id
            )
            mode = next(
                item
                for item in next_spec.modes
                if item.mode_id == next_spec.active_mode_id
            )
            memes = await runtime.shared_brain_service.list_memes(mode.namespace_id)
            pending_memes = await runtime.shared_brain_service.list_pending_candidates(
                mode.namespace_id
            )
            traces = runtime.debug_service.query(TraceQuery(limit=1_000))
            trace_path = data_directory / "artifacts" / "viewer-traces.json"
            trace_artifact = runtime.debug_service.export_artifact(
                TraceQuery(limit=1_000)
            )
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(
                json.dumps(
                    trace_artifact,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            fixture.output_ledger.assert_complete()
            consumptions = fixture.output_ledger.consumptions
            runtime_evidence = RecordedReplayEvidence(
                decisions=[
                    item.decision.model_dump(mode="json")
                    for item in traces.items
                ],
                selected_viewer_ids=[
                    item.viewer_instance_id for item in traces.items
                ],
                barrages=[
                    {
                        "event_id": event.event_id,
                        "session_id": event.session_id,
                        "source_type": event.source_type.value,
                        "source_id": event.source_id,
                        "created_at_ms": event.created_at_ms,
                        "text": event.text,
                        "payload": _json_value(event.payload),
                    }
                    for event in room_events
                    if event.source_type.value == "audience_barrage"
                ],
                memories=[
                    item.model_dump(mode="json") for item in memories
                ],
                traces=[
                    item.model_dump(mode="json") for item in traces.items
                ],
                consumed_provider_roles=list(
                    dict.fromkeys(
                        consumption.provider_role for consumption in consumptions
                    )
                ),
                consumed_provider_outputs=consumptions,
                external_transport_call_count=fixture.external_transport_call_count,
            )
            expected_identities = {
                (item.provider_role, item.generation_request_id)
                for item in bundle.recorded_provider_outputs
            }
            consumed_identities = {
                (item.provider_role, item.generation_request_id)
                for item in runtime_evidence.consumed_provider_outputs
            }
            if consumed_identities != expected_identities:
                raise ValueError(
                    "recorded runtime did not consume every Provider output identity"
                )

            stage = "stop"
            stop_status = await runtime.session_service.stop(session_id)
            stopped = True

            return {
                "production_graph": {
                    "fastapi_title": app.title,
                    "runtime_identity": app.state.runtime is runtime,
                    "sqlite_started": runtime.database.started,
                    "sqlite_filename": runtime.database.path.name,
                    "capability_probe_calls": probe.calls,
                },
                "session": {
                    "session_id": session_id,
                    "initial_revision": start.config_revision,
                    "applied_revision": applied.config_revision,
                    "audience_epoch": current.audience_epoch,
                    "viewer_count": len(current.viewers),
                    "stopped_state": stop_status.state.value,
                },
                "ingest": {
                    "text": text_receipt.input_kind.value,
                    "voice": audio_receipt.input_kind.value,
                    "frame": frame_receipt.input_kind.value,
                    "final_asr_delivered": asr.final_delivered.is_set(),
                },
                "dispatch": {
                    "viewer_calls": viewer.viewer_calls,
                    "visual_summary_calls": viewer.visual_calls,
                    "memory_extractor_calls": memory.calls,
                },
                "observed": {
                    "room_event_sources": [
                        event.source_type.value for event in room_events
                    ],
                    "memory_count": len(memories),
                    "meme_count": len(memes),
                    "pending_meme_count": len(pending_memes),
                    "trace_count": len(traces.items),
                    "trace_artifact": str(trace_path),
                    "trace_redacted": True,
                },
                "runtime_evidence": runtime_evidence.model_dump(mode="json"),
                "external_transport_call_count": asr.external_calls,
                "seed": bundle.seed,
                "virtual_clock_start_ms": bundle.virtual_clock_start_ms,
            }
    except Exception as error:
        artifact = data_directory / "failure-artifact.json"
        artifact.write_text(
            json.dumps(
                {
                    "redacted": True,
                    "stage": stage,
                    "error_code": "scenario_failed",
                    "error_type": type(error).__name__,
                    "session_started": session_id is not None,
                    "session_stopped": stopped,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        raise RecordedScenarioError(artifact) from error


async def execute_recorded_runtime(
    bundle: ReplayBundle,
    data_directory: Path,
) -> RecordedReplayEvidence:
    report = await _run_recorded_runtime_once(
        data_directory=data_directory,
        request=ReplayRequest(bundle=bundle),
    )
    return RecordedReplayEvidence.model_validate(report["runtime_evidence"])


async def run_recorded_scenario(
    *,
    data_directory: Path,
    request: ReplayRequest,
) -> dict[str, Any]:
    report = await _run_recorded_runtime_once(
        data_directory=data_directory,
        request=request,
    )
    first = RecordedReplayEvidence.model_validate(report["runtime_evidence"])
    calls = 0

    async def recorded_runner(
        bundle: ReplayBundle,
        run_directory: Path,
    ) -> RecordedReplayEvidence:
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        return await execute_recorded_runtime(bundle, run_directory)

    replay = await ReplayService(
        recorded_runner=recorded_runner,
        recorded_data_directory=data_directory / "proof",
    ).replay(request)
    report["replay"] = replay.model_dump(mode="json")
    return report
