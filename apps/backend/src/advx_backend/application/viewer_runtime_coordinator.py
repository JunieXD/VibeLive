from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from advx_backend.application.observation_wave_builder import select_frame_bundle
from advx_backend.application.ports.session import Clock
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_behavior_service import ViewerBehaviorService
from advx_backend.application.viewer_runtime import (
    ViewerDispatchSummary,
    ViewerRuntime,
)
from advx_backend.contracts.debug import ObservationWaveStatus
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ViewerRuntimeTelemetry,
)
from advx_backend.domain.crowd_decision import CrowdDecision, DecisionSource
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.memory import RoomMemorySlice, RoomWorkingMemory
from advx_backend.domain.observation import FrameRef, Observation
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.room import RoomEvent, RoomEventSource
from advx_backend.domain.scene_assessment import SceneAssessment

logger = logging.getLogger(__name__)


class ViewerPopulationController(Protocol):
    async def reconcile_population(
        self,
        session_id: str,
        *,
        observation_id: str | None = None,
    ) -> None: ...


class FrameMetadataResolver(Protocol):
    async def resolve(
        self,
        *,
        session_id: str,
        frame: FrameRef,
    ) -> FrameMetadata | None: ...


class RoomMemorySliceReader(Protocol):
    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice: ...


class WaveVisualSummarizer(Protocol):
    async def summarize(
        self,
        *,
        wave: ObservationWave,
        frame_bundle: FrameBundle,
        runtime: FrozenWaveRuntime,
    ) -> str: ...


class ConversationHistorySummarizer(Protocol):
    async def summarize_history(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        existing_summary: str | None,
        older_history: str,
    ) -> str: ...


class MemeCandidateSink(Protocol):
    async def commit_candidate(self, candidate: MemeCandidate) -> object: ...


class WaveMemoryExtractionSink(Protocol):
    async def extract_after_wave(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
        runtime: FrozenWaveRuntime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class FrameMetadata:
    width: int
    height: int
    encoding: str
    content_hash: str
    change_score: float = 0.0

    def is_complete(self) -> bool:
        return (
            self.width > 0
            and self.height > 0
            and bool(self.encoding)
            and len(self.content_hash) == 64
            and all(character in "0123456789abcdef" for character in self.content_hash)
            and 0 <= self.change_score <= 1
        )


@dataclass(frozen=True, slots=True)
class FrozenWaveRuntime:
    """The runtime and public-context view captured at one wave boundary."""

    canonical_runtime_spec: CanonicalRuntimeSpec
    public_context: tuple[RoomEvent, ...]
    public_context_event_ids: tuple[str, ...]
    user_context: tuple[tuple[str, str], ...]
    working_memory: RoomWorkingMemory
    room_memory_slice: RoomMemorySlice
    conversation_history_summary: str | None = None
    scene_assessment: SceneAssessment | None = None

    @property
    def settings(self) -> object:
        return self.canonical_runtime_spec.settings


@dataclass(frozen=True, slots=True)
class ViewerCoordinatorResult:
    """ReactionScheduler-compatible result with Viewer v2 dispatch evidence."""

    published_events: tuple[object, ...] = ()
    validations: tuple[object, ...] = ()
    wave: ObservationWave | None = None
    decision: CrowdDecision | None = None
    dispatch: ViewerDispatchSummary = ViewerDispatchSummary()
    runtime_missing: bool = False
    visual_failed: bool = False
    memory_failed: bool = False
    meme_failed: bool = False
    skipped: bool = False
    semantic_duplicate: bool = False


@dataclass(slots=True)
class _WavePolicyState:
    audience_epoch: int
    consecutive_ambient_waves: int = 0
    last_ambient_at_ms: int | None = None
    last_screen_at_ms: int | None = None
    semantic_inputs: OrderedDict[str, tuple[int, int]] = field(
        default_factory=OrderedDict
    )


@dataclass(slots=True)
class _ConversationHistoryState:
    summary: str | None = None
    covered_event_ids: set[str] = field(default_factory=set)


class ViewerRuntimeCoordinator:
    """Adapt legacy Observations to one frozen Viewer v2 reaction wave."""

    def __init__(
        self,
        *,
        runtime_state: RuntimeStateStore,
        viewer_runtime: ViewerRuntime,
        viewer_behavior: ViewerBehaviorService | None = None,
        population_controller: ViewerPopulationController | None = None,
        frame_metadata: FrameMetadataResolver | None = None,
        memory_reader: RoomMemorySliceReader | None = None,
        visual_summarizer: WaveVisualSummarizer | None = None,
        history_summarizer: ConversationHistorySummarizer | None = None,
        meme_sink: MemeCandidateSink | None = None,
        memory_extraction_sink: WaveMemoryExtractionSink | None = None,
        memory_slice_limit: int = 32,
        semantic_dedup_ttl_ms: int = 30_000,
        max_semantic_inputs_per_session: int = 256,
        background_task_timeout_ms: int = 2_000,
        clock: Clock | None = None,
    ) -> None:
        if memory_slice_limit < 1:
            raise ValueError("memory_slice_limit must be at least one")
        if semantic_dedup_ttl_ms < 1:
            raise ValueError("semantic_dedup_ttl_ms must be at least one")
        if max_semantic_inputs_per_session < 1:
            raise ValueError("max_semantic_inputs_per_session must be at least one")
        if background_task_timeout_ms < 1:
            raise ValueError("background_task_timeout_ms must be at least one")
        self._runtime_state = runtime_state
        self._viewer_runtime = viewer_runtime
        self._viewer_behavior = viewer_behavior or ViewerBehaviorService()
        self._population_controller = population_controller
        self._frame_metadata = frame_metadata
        self._memory_reader = memory_reader
        self._visual_summarizer = visual_summarizer
        self._history_summarizer = history_summarizer
        self._meme_sink = meme_sink
        self._memory_extraction_sink = memory_extraction_sink
        self._memory_slice_limit = memory_slice_limit
        self._semantic_dedup_ttl_ms = semantic_dedup_ttl_ms
        self._max_semantic_inputs_per_session = max_semantic_inputs_per_session
        self._background_task_timeout_ms = background_task_timeout_ms
        self._clock = clock or getattr(viewer_runtime, "_clock", None)
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._background_task_scopes: dict[asyncio.Task[None], tuple[str, int]] = {}
        self._policy_state: dict[str, _WavePolicyState] = {}
        self._conversation_history: dict[str, _ConversationHistoryState] = {}
        self._telemetry: dict[str, ViewerRuntimeTelemetry] = {}

    async def react(self, observation: Observation) -> ViewerCoordinatorResult:
        try:
            committed = await self._runtime_state.snapshot(observation.session_id)
        except KeyError:
            return ViewerCoordinatorResult(runtime_missing=True)
        if self._population_controller is not None:
            try:
                await self._population_controller.reconcile_population(
                    observation.session_id,
                    observation_id=observation.observation_id,
                )
                committed = await self._runtime_state.snapshot(observation.session_id)
            except Exception:
                logger.exception("Viewer population reconciliation failed")
        await self._cancel_stale_background_tasks(
            observation.session_id,
            committed.audience_epoch,
        )

        core_wave = await self._build_wave(observation, committed)
        core_wave, duplicate, proposed_policy = self._admit_wave(core_wave, committed)
        if core_wave is None:
            return ViewerCoordinatorResult(
                skipped=True,
                semantic_duplicate=duplicate,
            )
        if not committed.accepting_results:
            self._record_observation_trace(
                wave=core_wave,
                runtime=committed.spec,
                status=ObservationWaveStatus.SKIPPED,
                failure_reason="runtime_not_accepting_results",
            )
            return ViewerCoordinatorResult(wave=core_wave)

        memory_slice = await self._read_memory_slice(core_wave)
        if memory_slice is None:
            self._record_observation_trace(
                wave=core_wave,
                runtime=committed.spec,
                status=ObservationWaveStatus.FAILED,
                failure_reason="memory_slice_unavailable",
            )
            return ViewerCoordinatorResult(
                wave=core_wave,
                memory_failed=True,
            )
        public_context, history_summary = await self._compact_history(
            observation,
            committed,
        )
        runtime = self._freeze_runtime(
            committed.spec,
            observation,
            memory_slice,
            public_context=public_context,
            history_summary=history_summary,
        )
        wave = await self._prepare_visual_wave(core_wave, runtime)
        if wave is None:
            self._record_observation_trace(
                wave=core_wave,
                runtime=runtime,
                status=ObservationWaveStatus.FAILED,
                failure_reason="visual_preparation_failed",
            )
            return ViewerCoordinatorResult(wave=core_wave, visual_failed=True)
        retained_frames = await self._retain_wave_frames(wave)
        if retained_frames is None:
            # A capture failure must not suppress text or ASR reactions.
            wave = replace(
                wave,
                visual_input_mode=ViewerVisualInputMode.TEXT_ONLY,
                frame_bundle=None,
                shared_visual_summary=None,
            )
            retained_frames = ()
        try:
            return await self._react_with_prepared_wave(
                wave=wave,
                runtime=runtime,
                committed=committed,
                proposed_policy=proposed_policy,
            )
        finally:
            await self._release_wave_frames(wave, retained_frames)

    async def _react_with_prepared_wave(
        self,
        *,
        wave: ObservationWave,
        runtime: object,
        committed: CommittedRuntime,
        proposed_policy: _WavePolicyState | None,
    ) -> ViewerCoordinatorResult:
        assessment = self._independent_assessment(wave, committed)
        decision = self._decide_speakers(
            wave=wave,
            committed=committed,
        )
        runtime = replace(runtime, scene_assessment=assessment)

        self._record_observation_trace(
            wave=wave,
            runtime=runtime,
            status=(
                ObservationWaveStatus.COMPLETED
                if decision.selected_viewer_ids
                else ObservationWaveStatus.EMPTY
            ),
            decision=decision,
        )
        dispatch = await self._viewer_runtime.dispatch(
            wave=wave,
            decision=decision,
            pool=committed.pool,
            runtime=runtime,
        )
        if proposed_policy is not None and self._dispatch_commits_admission(
            decision,
            dispatch,
        ):
            self._policy_state[wave.session_id] = proposed_policy
        self._record_telemetry(
            session_id=wave.session_id,
            dispatch=dispatch,
        )
        if self._allows_wave_side_effects(decision, dispatch):
            self._schedule_memory_extraction(
                wave=wave,
                decision=decision,
                dispatch=dispatch,
                runtime=runtime,
            )
        return ViewerCoordinatorResult(
            wave=wave,
            decision=decision,
            dispatch=dispatch,
            meme_failed=False,
        )

    async def _retain_wave_frames(
        self,
        wave: ObservationWave,
    ) -> tuple[FrameRef, ...] | None:
        if wave.visual_input_mode is not ViewerVisualInputMode.DIRECT_FRAMES:
            return ()
        if wave.frame_bundle is None:
            return None
        retain = getattr(self._frame_metadata, "retain", None)
        if not callable(retain):
            return None
        frames = tuple(
            FrameRef(
                frame_id=item.frame_id,
                created_at_ms=item.captured_at_ms,
                mime_type=item.encoding,
                data_ref=item.data_ref,
            )
            for item in wave.frame_bundle.frames
        )
        try:
            retained = await retain(session_id=wave.session_id, frames=frames)
        except Exception as error:
            logger.warning(
                "Could not retain direct frames for observation %s: %s",
                wave.observation_id,
                error,
            )
            return None
        return frames if retained else None

    async def _release_wave_frames(
        self,
        wave: ObservationWave,
        frames: tuple[FrameRef, ...],
    ) -> None:
        if not frames:
            return
        release = getattr(self._frame_metadata, "release", None)
        if not callable(release):
            return
        try:
            await release(session_id=wave.session_id, frames=frames)
        except Exception as error:
            logger.warning(
                "Could not release direct frames for observation %s: %s",
                wave.observation_id,
                error,
            )

    def _decide_speakers(
        self,
        *,
        wave: ObservationWave,
        committed: CommittedRuntime,
    ) -> CrowdDecision:
        eligible = [
            viewer.viewer_instance_id
            for viewer in committed.pool.viewers
            if viewer.is_active() and not viewer.is_muted(wave.created_at_ms)
        ]
        if ObservationTrigger.AMBIENT_TICK in wave.triggers:
            by_id = {viewer.viewer_instance_id: viewer for viewer in committed.pool.viewers}
            selected = sorted(
                eligible,
                key=lambda viewer_id: (
                    by_id[viewer_id].private_state.last_spoke_at_ms is not None,
                    by_id[viewer_id].private_state.last_spoke_at_ms or 0,
                    viewer_id,
                ),
            )[:2]
        else:
            selected = eligible
        return CrowdDecision(
            decision_id=f"autonomous-{wave.observation_id}",
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            selected_viewer_ids=selected,
            reason_codes=["per_viewer_independent_decision"],
            evidence_event_ids=list(wave.trigger_event_ids),
            evidence_frame_indexes=list(
                range(0 if wave.frame_bundle is None else len(wave.frame_bundle.frames))
            ),
            decision_source=DecisionSource.AUTONOMOUS,
            created_at_ms=wave.created_at_ms,
            expires_at_ms=wave.deadline_at_ms,
        )

    @staticmethod
    def _independent_assessment(
        wave: ObservationWave,
        committed: CommittedRuntime,
    ) -> SceneAssessment:
        return SceneAssessment(
            assessment_id=f"autonomous-{wave.observation_id}",
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            salience=1.0,
            novelty=1.0,
            emotional_intensity=0.0,
            topics=[],
            emotional_tone=[],
            replyable_event_ids=list(wave.event_ids),
            evidence_event_ids=list(wave.trigger_event_ids),
            evidence_frame_indexes=list(
                range(0 if wave.frame_bundle is None else len(wave.frame_bundle.frames))
            ),
            suggested_reaction_types=[],
            maximum_responses=sum(
                viewer.is_active() and not viewer.is_muted(wave.created_at_ms)
                for viewer in committed.pool.viewers
            ),
            reason_codes=["per_viewer_independent_decision"],
            decision_source=DecisionSource.AUTONOMOUS,
            created_at_ms=wave.created_at_ms,
            expires_at_ms=wave.deadline_at_ms,
        )

    @staticmethod
    def _resolved_persona(
        runtime: FrozenWaveRuntime,
        persona_id: str,
    ) -> PersonaTemplate:
        spec = runtime.canonical_runtime_spec
        persona = next(
            (item for item in spec.personas if item.persona_id == persona_id),
            None,
        )
        if persona is None:
            raise ValueError("Viewer references an unavailable PersonaTemplate")
        mode = next(
            item for item in spec.modes if item.mode_id == spec.active_mode_id
        )
        override = mode.persona_overrides.get(persona_id)
        if override is None:
            return persona
        return persona.model_copy(update=override.model_dump(exclude_none=True))

    def _record_observation_trace(
        self,
        *,
        wave: ObservationWave,
        runtime: object | None,
        status: ObservationWaveStatus,
        decision: CrowdDecision | None = None,
        failure_reason: str | None = None,
    ) -> None:
        recorder = getattr(self._viewer_runtime, "record_observation_trace", None)
        if callable(recorder):
            recorder(
                wave=wave,
                runtime=runtime,
                status=status,
                decision=decision,
                failure_reason=failure_reason,
            )

    async def wait_for_background_tasks(self) -> None:
        await self._drain_background_tasks(tuple(self._background_tasks))

    async def start_session(self, session_id: str) -> None:
        self._policy_state.pop(session_id, None)
        self._conversation_history.pop(session_id, None)
        self._telemetry.pop(session_id, None)

    async def stop_session(self, session_id: str) -> None:
        tasks = tuple(
            task
            for task, scope in self._background_task_scopes.items()
            if scope[0] == session_id
        )
        await self._drain_background_tasks(tasks, cancel=True)
        self._policy_state.pop(session_id, None)
        self._conversation_history.pop(session_id, None)
        self._telemetry.pop(session_id, None)

    def telemetry_snapshot(self, session_id: str) -> ViewerRuntimeTelemetry:
        return self._telemetry.get(session_id, ViewerRuntimeTelemetry())

    async def _build_wave(
        self,
        observation: Observation,
        committed: CommittedRuntime,
    ) -> ObservationWave:
        settings = committed.spec.settings
        frames = await self._resolve_frames(
            session_id=observation.session_id,
            frames=observation.frames,
        )
        selected = select_frame_bundle(
            frames=frames,
            settings=settings.frame_bundle,
            now_ms=observation.created_at_ms,
        )
        selected = tuple(
            item.model_copy(update={"frame_index": index})
            for index, item in enumerate(selected)
        )
        frame_bundle = (
            FrameBundle(
                bundle_id=self._bundle_id(observation.observation_id),
                settings=settings.frame_bundle,
                frames=list(selected),
            )
            if selected
            else None
        )
        event_ids = list(dict.fromkeys(event.event_id for event in observation.room_events))
        trigger_event_ids = list(observation.trigger_event_ids)
        trigger_frame_ids = list(self._trigger_frame_ids(observation))
        delta_events = self._delta_events(observation)
        (
            event_target_viewer_id,
            event_target_persona_id,
            event_target_ambiguous,
        ) = self._targets_from_events(delta_events)
        target_ambiguous = observation.target_ambiguous or event_target_ambiguous
        target_viewer_id = (
            None
            if target_ambiguous
            else observation.target_viewer_id or event_target_viewer_id
        )
        target_persona_id = (
            None
            if target_ambiguous
            else observation.target_persona_id or event_target_persona_id
        )
        return ObservationWave(
            room_id=committed.spec.room.room_id,
            session_id=observation.session_id,
            audience_epoch=committed.audience_epoch,
            observation_id=observation.observation_id,
            created_at_ms=observation.created_at_ms,
            deadline_at_ms=(
                observation.created_at_ms + settings.viewer_request_ttl_ms
            ),
            triggers=self._triggers(observation),
            event_ids=event_ids,
            trigger_event_ids=trigger_event_ids,
            trigger_frame_ids=trigger_frame_ids,
            trigger_screen_change_score=max(
                (
                    frame.change_score
                    for frame in frames
                    if frame.frame_id in trigger_frame_ids
                ),
                default=0.0,
            ),
            frame_bundle=frame_bundle,
            target_viewer_id=target_viewer_id,
            target_persona_id=target_persona_id,
            target_ambiguous=target_ambiguous,
            input_revision=max(
                (self._event_revision(event) for event in delta_events),
                default=0,
            ),
            semantic_input_hash=self._input_fingerprint(
                observation,
                frames,
                trigger_frame_ids=tuple(trigger_frame_ids),
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
            ),
        )

    def _admit_wave(
        self,
        wave: ObservationWave,
        committed: CommittedRuntime,
    ) -> tuple[ObservationWave | None, bool, _WavePolicyState | None]:
        if not hasattr(committed.spec, "settings"):
            return wave, False, None
        current = self._policy_state.get(wave.session_id)
        if current is None or current.audience_epoch != wave.audience_epoch:
            current = _WavePolicyState(audience_epoch=wave.audience_epoch)
        state = _WavePolicyState(
            audience_epoch=current.audience_epoch,
            consecutive_ambient_waves=current.consecutive_ambient_waves,
            last_ambient_at_ms=current.last_ambient_at_ms,
            last_screen_at_ms=current.last_screen_at_ms,
            semantic_inputs=OrderedDict(current.semantic_inputs),
        )

        has_real_input = any(
            trigger in {ObservationTrigger.USER_TEXT, ObservationTrigger.FINAL_VOICE}
            for trigger in wave.triggers
        )
        if has_real_input:
            state.consecutive_ambient_waves = 0
            state.last_ambient_at_ms = None
        elif ObservationTrigger.AMBIENT_TICK not in wave.triggers:
            return None, False, None
        elif not self._ambient_allowed(committed, wave, state):
            return None, False, None
        else:
            state.consecutive_ambient_waves += 1
            state.last_ambient_at_ms = wave.created_at_ms

        return wave, False, state

    @staticmethod
    def _ambient_allowed(
        committed: CommittedRuntime,
        wave: ObservationWave,
        state: _WavePolicyState,
    ) -> bool:
        spec = committed.spec
        mode = next(mode for mode in spec.modes if mode.mode_id == spec.active_mode_id)
        if mode.ambience.value != "continuous":
            return False
        settings = spec.settings
        if state.consecutive_ambient_waves >= settings.max_consecutive_ambient_waves:
            return False
        return (
            state.last_ambient_at_ms is None
            or wave.created_at_ms
            >= state.last_ambient_at_ms + settings.ambient_tick_cooldown_ms
        )

    @staticmethod
    def _semantic_fingerprint(wave: ObservationWave) -> str:
        if (
            wave.semantic_input_hash is not None
            and ObservationTrigger.AMBIENT_TICK not in wave.triggers
        ):
            return wave.semantic_input_hash
        parts = [
            ",".join(sorted(trigger.value for trigger in wave.triggers)),
            wave.target_viewer_id or "",
            wave.target_persona_id or "",
            ",".join(wave.trigger_event_ids),
            str(wave.created_at_ms),
            ",".join(
                frame.content_hash
                for frame in (wave.frame_bundle.frames if wave.frame_bundle else ())
            ),
        ]
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _input_fingerprint(
        observation: Observation,
        frames: tuple[FrameBundleItem, ...],
        *,
        trigger_frame_ids: tuple[str, ...],
        target_viewer_id: str | None,
        target_persona_id: str | None,
    ) -> str:
        delta_events = ViewerRuntimeCoordinator._delta_events(observation)
        latest_user_event = next(
            (
                event
                for event in reversed(delta_events)
                if event.source_type
                in {RoomEventSource.USER_TEXT, RoomEventSource.USER_VOICE}
            ),
            None,
        )
        trigger_frames = [
            frame for frame in frames if frame.frame_id in trigger_frame_ids
        ]
        latest_frame_hash = trigger_frames[-1].content_hash if trigger_frames else ""
        parts = [
            "" if latest_user_event is None else latest_user_event.source_type.value,
            "" if latest_user_event is None else (latest_user_event.text or "").strip(),
            (
                ""
                if latest_user_event is None
                else str(latest_user_event.payload.get("revision", ""))
            ),
            latest_frame_hash,
            target_viewer_id or "",
            target_persona_id or "",
        ]
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _targets_from_events(
        events: tuple[RoomEvent, ...],
    ) -> tuple[str | None, str | None, bool]:
        targets: set[tuple[str, str]] = set()
        for event in events:
            if event.source_type not in {
                RoomEventSource.USER_TEXT,
                RoomEventSource.USER_VOICE,
            }:
                continue
            viewer = event.payload.get("target_viewer_id")
            persona = event.payload.get("target_persona_id")
            if isinstance(viewer, str) and viewer:
                targets.add(("viewer", viewer))
            if isinstance(persona, str) and persona:
                targets.add(("persona", persona))
        if len(targets) != 1:
            return None, None, len(targets) > 1
        kind, target = next(iter(targets))
        return (
            (target, None, False)
            if kind == "viewer"
            else (None, target, False)
        )

    @staticmethod
    def _delta_events(observation: Observation) -> tuple[RoomEvent, ...]:
        if not observation.trigger_event_ids:
            return ()
        trigger_ids = set(observation.trigger_event_ids)
        return tuple(
            event for event in observation.room_events if event.event_id in trigger_ids
        )

    @staticmethod
    def _event_revision(event: RoomEvent) -> int:
        revision = event.payload.get("revision")
        return (
            revision
            if isinstance(revision, int) and not isinstance(revision, bool)
            else event.sequence
        )

    @staticmethod
    def _trigger_frame_ids(observation: Observation) -> tuple[str, ...]:
        return observation.trigger_frame_ids

    def _record_telemetry(
        self,
        *,
        session_id: str,
        dispatch: ViewerDispatchSummary,
    ) -> None:
        current = self.telemetry_snapshot(session_id)
        self._telemetry[session_id] = current.model_copy(
            update={
                "selected": current.selected + getattr(dispatch, "selected", 0),
                "queued": current.queued + getattr(dispatch, "queued", 0),
                "dispatched": current.dispatched + getattr(dispatch, "dispatched", 0),
                "completed": current.completed + getattr(dispatch, "completed", 0),
                "silence": current.silence + dispatch.silenced,
                "published": current.published + dispatch.published,
                "rejected": current.rejected + dispatch.rejected,
                "expired": current.expired + dispatch.expired,
                "failed": current.failed + dispatch.failed,
                "stale": current.stale + dispatch.stale,
                "cancelled": current.cancelled + dispatch.cancelled,
                "superseded": current.superseded + dispatch.superseded,
                "retry": current.retry + dispatch.retry,
            }
        )

    @staticmethod
    def _dispatch_commits_admission(
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
    ) -> bool:
        if not decision.selected_viewer_ids:
            return True
        if dispatch.published > 0 or dispatch.silenced > 0:
            return True
        rejected_terminals = (
            dispatch.failed
            + dispatch.rejected
            + dispatch.expired
            + dispatch.stale
            + dispatch.cancelled
            + dispatch.superseded
        )
        return dispatch.completed > 0 and rejected_terminals == 0

    async def _resolve_frames(
        self,
        *,
        session_id: str,
        frames: tuple[FrameRef, ...],
    ) -> tuple[FrameBundleItem, ...]:
        if self._frame_metadata is None:
            return ()
        resolved: list[FrameBundleItem] = []
        for frame in frames:
            try:
                metadata = await self._frame_metadata.resolve(
                    session_id=session_id,
                    frame=frame,
                )
            except Exception:
                metadata = None
            if metadata is None or not metadata.is_complete():
                continue
            resolved.append(
                FrameBundleItem(
                    frame_id=frame.frame_id,
                    frame_index=0,
                    captured_at_ms=frame.created_at_ms,
                    width=metadata.width,
                    height=metadata.height,
                    encoding=metadata.encoding,
                    content_hash=metadata.content_hash,
                    data_ref=frame.data_ref,
                    change_score=metadata.change_score,
                )
            )
        resolved.sort(key=lambda item: (item.captured_at_ms, item.frame_id))
        return tuple(
            item.model_copy(update={"frame_index": index})
            for index, item in enumerate(resolved)
        )

    async def _read_memory_slice(
        self,
        wave: ObservationWave,
    ) -> RoomMemorySlice | None:
        if self._memory_reader is None:
            return RoomMemorySlice(room_id=wave.room_id, memory_revision=0)
        try:
            memory_slice = await self._memory_reader.read_slice(
                room_id=wave.room_id,
                event_ids=tuple(wave.event_ids),
                limit=self._memory_slice_limit,
            )
        except Exception:
            return None
        if memory_slice.room_id != wave.room_id:
            return None
        return memory_slice

    async def _prepare_visual_wave(
        self,
        wave: ObservationWave,
        runtime: FrozenWaveRuntime,
    ) -> ObservationWave | None:
        mode = runtime.settings.viewer_visual_input_mode
        if mode is ViewerVisualInputMode.DIRECT_FRAMES:
            # Text and final ASR input must remain speakable while capture is
            # still warming up or a frame cannot be decoded.
            if wave.frame_bundle is not None:
                return wave
            return wave.model_copy(
                update={
                    "visual_input_mode": ViewerVisualInputMode.TEXT_ONLY,
                    "frame_bundle": None,
                    "shared_visual_summary": None,
                }
            )
        if mode is ViewerVisualInputMode.TEXT_ONLY:
            return wave.model_copy(
                update={
                    "visual_input_mode": ViewerVisualInputMode.TEXT_ONLY,
                    "frame_bundle": None,
                    "shared_visual_summary": None,
                }
            )
        if wave.frame_bundle is None or self._visual_summarizer is None:
            return wave.model_copy(
                update={
                    "visual_input_mode": ViewerVisualInputMode.TEXT_ONLY,
                    "frame_bundle": None,
                    "shared_visual_summary": None,
                }
            )
        try:
            summary = await self._visual_summarizer.summarize(
                wave=wave,
                frame_bundle=wave.frame_bundle,
                runtime=runtime,
            )
        except Exception:
            return None
        summary = summary.strip()
        if not summary:
            return None
        return wave.model_copy(
            update={
                "visual_input_mode": ViewerVisualInputMode.SHARED_SUMMARY,
                "frame_bundle": None,
                "shared_visual_summary": summary,
            }
        )

    @staticmethod
    def _freeze_runtime(
        spec: CanonicalRuntimeSpec,
        observation: Observation,
        memory_slice: RoomMemorySlice,
        *,
        public_context: tuple[RoomEvent, ...],
        history_summary: str | None,
    ) -> FrozenWaveRuntime:
        event_ids = tuple(dict.fromkeys(event.event_id for event in public_context))
        return FrozenWaveRuntime(
            canonical_runtime_spec=spec,
            public_context=public_context,
            public_context_event_ids=event_ids,
            user_context=tuple(sorted(observation.user_context.items())),
            working_memory=RoomWorkingMemory(
                room_id=spec.room.room_id,
                session_id=observation.session_id,
                revision=max(
                    (event.sequence for event in public_context),
                    default=0,
                ),
                event_ids=list(event_ids),
                updated_at_ms=observation.created_at_ms,
            ),
            room_memory_slice=memory_slice,
            conversation_history_summary=history_summary,
        )

    async def _compact_history(
        self,
        observation: Observation,
        committed: CommittedRuntime,
    ) -> tuple[tuple[RoomEvent, ...], str | None]:
        events = tuple(observation.room_events)
        rendered = self._render_history(events)
        if len(rendered) <= 24_000:
            self._conversation_history.pop(observation.session_id, None)
            return events, None

        state = self._conversation_history.setdefault(
            observation.session_id,
            _ConversationHistoryState(),
        )
        recent = [event for event in events if event.event_id not in state.covered_event_ids]
        if len(self._render_history(tuple(recent))) > 18_000:
            older, newer = self._split_history(tuple(recent))
            if older:
                try:
                    state.summary = await self._summarize_history(
                        session_id=observation.session_id,
                        audience_epoch=committed.audience_epoch,
                        existing_summary=state.summary,
                        older_history=self._render_history(older),
                    )
                except Exception as error:
                    logger.warning(
                        "Could not summarize conversation history for session %s: %s",
                        observation.session_id,
                        error,
                    )
                # Each wave calls the summary model at most once. On failure,
                # retain the latest events and let a future wave compact again.
                state.covered_event_ids.update(event.event_id for event in older)
                recent = list(newer)
        return tuple(recent), state.summary

    async def _summarize_history(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        existing_summary: str | None,
        older_history: str,
    ) -> str:
        if self._history_summarizer is not None:
            summary = await self._history_summarizer.summarize_history(
                session_id=session_id,
                audience_epoch=audience_epoch,
                existing_summary=existing_summary,
                older_history=older_history,
            )
            summary = summary.strip()
            if summary:
                return summary[:6_000]
        # Tests and offline adapters may not install a history model. Preserve
        # the most recent part of the compacted history rather than dropping it.
        fallback = "\n".join(part for part in (existing_summary, older_history) if part)
        return fallback[-6_000:]

    @staticmethod
    def _split_history(
        events: tuple[RoomEvent, ...],
    ) -> tuple[tuple[RoomEvent, ...], tuple[RoomEvent, ...]]:
        if len(events) < 2:
            return (), events
        total = len(ViewerRuntimeCoordinator._render_history(events))
        threshold = max(1, total // 2)
        used = 0
        for index, event in enumerate(events[:-1], start=1):
            used += len(ViewerRuntimeCoordinator._render_history((event,)))
            if used >= threshold:
                return events[:index], events[index:]
        midpoint = len(events) // 2
        return events[:midpoint], events[midpoint:]

    @staticmethod
    def _render_history(events: tuple[RoomEvent, ...]) -> str:
        rows: list[str] = []
        for event in events:
            text = (event.text or "").strip()
            if not text:
                continue
            payload = event.payload
            display_name = payload.get("display_name")
            source = (
                display_name
                if isinstance(display_name, str) and display_name
                else event.source_id or event.source_type.value
            )
            target = payload.get("target_viewer_id")
            target_text = f" -> {target}" if isinstance(target, str) and target else ""
            rows.append(f"[{event.sequence}][{source}{target_text}] {text}")
        return "\n".join(rows)

    def _schedule_memory_extraction(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
        runtime: FrozenWaveRuntime,
    ) -> None:
        if self._memory_extraction_sink is None:
            return
        task = asyncio.create_task(
            self._extract_memory(
                wave=wave,
                decision=decision,
                dispatch=dispatch,
                runtime=runtime,
            ),
            name=f"viewer-memory:{wave.session_id}:{wave.observation_id}",
        )
        self._background_tasks.add(task)
        self._background_task_scopes[task] = (
            wave.session_id,
            wave.audience_epoch,
        )
        task.add_done_callback(self._background_task_done)

    def _background_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        self._background_task_scopes.pop(task, None)

    async def _cancel_stale_background_tasks(
        self,
        session_id: str,
        audience_epoch: int,
    ) -> None:
        tasks = tuple(
            task
            for task, scope in self._background_task_scopes.items()
            if scope[0] == session_id and scope[1] != audience_epoch
        )
        await self._drain_background_tasks(tasks, cancel=True)

    async def _drain_background_tasks(
        self,
        tasks: tuple[asyncio.Task[None], ...],
        *,
        cancel: bool = False,
    ) -> None:
        if not tasks:
            return
        if cancel:
            for task in tasks:
                task.cancel()
        done, pending = await asyncio.wait(
            tasks,
            timeout=self._background_task_timeout_ms / 1_000,
        )
        if pending:
            for task in pending:
                task.cancel()
            cancelled, resistant = await asyncio.wait(
                pending,
                timeout=self._background_task_timeout_ms / 1_000,
            )
            if cancelled:
                await asyncio.gather(*cancelled, return_exceptions=True)
            for task in resistant:
                scope = self._background_task_scopes.pop(task, None)
                self._background_tasks.discard(task)
                logger.warning(
                    "detaching cancellation-resistant viewer background task",
                    extra={
                        "task_name": task.get_name(),
                        "session_id": None if scope is None else scope[0],
                        "audience_epoch": None if scope is None else scope[1],
                    },
                )
        if done:
            await asyncio.gather(*done, return_exceptions=True)

    async def _commit_meme_candidate(
        self,
        *,
        wave: ObservationWave,
        candidate: MemeCandidate | None,
    ) -> bool:
        if candidate is None or self._meme_sink is None:
            return False

        async def commit() -> bool:
            if self._wave_expired(wave):
                return False
            try:
                await self._meme_sink.commit_candidate(candidate)
            except Exception:
                return True
            return False

        accepted, failed = await self._runtime_state.execute_if_accepting(
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            operation=commit,
        )
        return bool(failed) if accepted else False

    async def _extract_memory(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
        runtime: FrozenWaveRuntime,
    ) -> None:
        await asyncio.sleep(0)
        if self._wave_expired(wave) or not await self._runtime_state.accepts(
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
        ):
            return
        fenced_extract = getattr(
            self._memory_extraction_sink,
            "extract_after_wave_fenced",
            None,
        )
        try:
            if callable(fenced_extract):
                await fenced_extract(
                    wave=wave,
                    decision=decision,
                    dispatch=dispatch,
                    runtime=runtime,
                    commit_effect=lambda operation: self._commit_memory_effect(
                        wave,
                        operation,
                    ),
                )
                return
            await self._memory_extraction_sink.extract_after_wave(
                wave=wave,
                decision=decision,
                dispatch=dispatch,
                runtime=runtime,
            )
        except Exception as error:
            logger.exception(
                "viewer memory extraction failed",
                extra={
                    "session_id": wave.session_id,
                    "observation_id": wave.observation_id,
                    "exception_type": type(error).__name__,
                },
            )

    async def _commit_memory_effect(
        self,
        wave: ObservationWave,
        operation: Callable[[], Awaitable[object]],
    ) -> tuple[bool, object | None]:
        if self._wave_expired(wave):
            return False, None

        async def commit() -> tuple[bool, object | None]:
            if self._wave_expired(wave):
                return False, None
            return True, await operation()

        accepted, result = await self._runtime_state.execute_if_accepting(
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            operation=commit,
        )
        if not accepted or result is None:
            return False, None
        return result

    @staticmethod
    def _allows_wave_side_effects(
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
    ) -> bool:
        if not decision.selected_viewer_ids:
            return True
        return dispatch.published > 0 or dispatch.silenced > 0

    def _wave_expired(self, wave: ObservationWave) -> bool:
        return self._clock is not None and self._clock.now_ms() >= wave.deadline_at_ms

    @staticmethod
    def _bundle_id(observation_id: str) -> str:
        candidate = f"bundle-{observation_id}"
        if len(candidate) <= 128:
            return candidate
        digest = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()
        return f"bundle-{digest}"

    @staticmethod
    def _triggers(observation: Observation) -> list[ObservationTrigger]:
        triggers: list[ObservationTrigger] = []
        delta_events = ViewerRuntimeCoordinator._delta_events(observation)
        sources = {event.source_type for event in delta_events}
        if RoomEventSource.USER_TEXT in sources:
            triggers.append(ObservationTrigger.USER_TEXT)
        if RoomEventSource.USER_VOICE in sources:
            triggers.append(ObservationTrigger.FINAL_VOICE)
        if observation.user_context.get("ambient") == "true":
            triggers.append(ObservationTrigger.AMBIENT_TICK)
        return triggers
