import asyncio
import logging
import math
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from advx_backend.application.ports.session import Clock, IdGenerator
from advx_backend.application.ports.viewer import (
    ViewerBarragePublisher,
    ViewerProvider,
    ViewerRoomWriter,
    ViewerSequenceClaimer,
    ViewerSessionFence,
)
from advx_backend.application.viewer_trace import (
    ViewerTraceSink,
    build_observation_wave_trace,
    build_viewer_request_trace,
)
from advx_backend.contracts.debug import (
    ObservationWaveStatus,
    TraceResponseStatus,
    ViewerOutputDelivery,
)
from advx_backend.contracts.viewer_runtime import (
    MAX_VIEWER_BARRAGE_TEXT_LENGTH,
    ViewerAction,
    ViewerBarrageEvent,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
    ViewerPublicEvent,
    ViewerRequestTriggerContext,
    WindowBatchGenerationRequest,
    WindowBatchGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import (
    UNBOUNDED_DEADLINE_AT_MS,
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.viewer import ViewerInstance, ViewerLifecycleState
from advx_backend.providers.model.viewer_runtime import (
    ViewerRuntimeProviderBlockedError,
    ViewerRuntimeProviderError,
)

logger = logging.getLogger(__name__)

_BARRAGE_BATCH_INTERVAL_SECONDS = 0.2


@dataclass(frozen=True, slots=True)
class ViewerDispatchSummary:
    selected: int = 0
    queued: int = 0
    dispatched: int = 0
    completed: int = 0
    retry: int = 0
    published: int = 0
    silenced: int = 0
    rejected: int = 0
    expired: int = 0
    failed: int = 0
    stale: int = 0
    cancelled: int = 0
    superseded: int = 0

    @property
    def silence(self) -> int:
        return self.silenced

    @classmethod
    def combine(
        cls,
        results: list["_DispatchResult"],
        *,
        selected: int,
        unmatched_stale: int = 0,
    ) -> "ViewerDispatchSummary":
        outcomes = [result.outcome for result in results]
        return cls(
            selected=selected,
            queued=sum(result.queued for result in results),
            dispatched=sum(result.dispatched for result in results),
            completed=sum(result.completed for result in results),
            retry=sum(result.retry for result in results),
            published=outcomes.count("published"),
            silenced=outcomes.count("silenced"),
            rejected=outcomes.count("rejected"),
            expired=outcomes.count("expired"),
            failed=outcomes.count("failed"),
            stale=outcomes.count("stale") + unmatched_stale,
            cancelled=outcomes.count("cancelled"),
            superseded=outcomes.count("superseded"),
        )


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    outcome: str
    queued: int
    dispatched: int
    completed: int
    retry: int


@dataclass(slots=True)
class _WorkItem:
    request: ViewerGenerationRequest
    viewer: ViewerInstance
    wave: ObservationWave
    decision: CrowdDecision
    available_viewer_ids: tuple[str, ...]
    runtime: object
    generation: int
    wave_generation: int
    priority: int
    future: asyncio.Future[_DispatchResult]
    queued_at_ms: int
    dispatched_at_ms: int | None = None
    completed_at_ms: int | None = None
    retry_count: int = 0
    traced: bool = False
    lane: "_RuntimeLane | None" = None
    slot_reserved: bool = False
    queued: bool = False
    was_queued: bool = False
    replacement: "_WorkItem | None" = None
    superseded_reason: str | None = None
    provider_task: asyncio.Task[object] | None = None
    output_scheduled: bool = False
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    invalidated: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class _WindowBatchWork:
    session_id: str
    audience_epoch: int
    observation_id: str
    priority: int
    wave_generation: int
    generation: int = 0
    items: list[_WorkItem] = field(default_factory=list)
    unmatched_stale: int = 0
    lane: "_RuntimeLane | None" = None
    slot_reserved: bool = False
    queued: bool = False
    was_queued: bool = False
    admitted: bool = False
    superseded_reason: str | None = None
    provider_dispatched_at_ms: int | None = None
    provider_task: asyncio.Task[object] | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    finished: bool = False
    output_delivery_detached: bool = False


@dataclass(slots=True)
class _ViewerMailbox:
    task: asyncio.Task[None] | None = None
    current: _WorkItem | None = None
    pending: _WorkItem | None = None


@dataclass(slots=True)
class _RuntimeLane:
    max_in_flight: int
    queue_capacity: int
    active: int = 0
    queued: int = 0
    eligible: deque[_WorkItem | _WindowBatchWork] = field(default_factory=deque)


@dataclass(slots=True)
class _OutputBatch:
    item: _WorkItem
    events: tuple[ViewerBarrageEvent, ...]
    ready_at_ms: int
    scheduled_at_ms: int
    published_events: list[ViewerBarrageEvent] = field(default_factory=list)
    published_at_ms: int | None = None
    realtime_delivery_failed: bool = False
    interruption_reason: str | None = None
    finished: bool = False


@dataclass(slots=True)
class _OutputPaceState:
    session_id: str
    queue: deque[_OutputBatch] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None
    active: _OutputBatch | None = None
    next_release_at_ms: int | None = None
    stopped: bool = False


@dataclass(slots=True)
class _RequestPaceState:
    next_start_at_ms: int | None = None
    turn: asyncio.Lock = field(default_factory=asyncio.Lock)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class _RequestContext:
    mode_context: dict[str, Any] = field(default_factory=dict)
    public_context_event_ids: list[str] = field(default_factory=list)
    public_context: list[ViewerPublicEvent] = field(default_factory=list)
    reply_context_event_ids: list[str] = field(default_factory=list)
    reply_context: list[ViewerPublicEvent] = field(default_factory=list)
    conversation_history_summary: str | None = None
    room_memory_slice: RoomMemorySlice | None = None


class _ViewerRequestExpired(TimeoutError):
    pass


class _ViewerRequestSuperseded(RuntimeError):
    pass


class _WindowBatchSuperseded(RuntimeError):
    pass


class ViewerBehaviorStateSink(Protocol):
    async def record_published(self, request: ViewerGenerationRequest, event: object) -> None: ...

    async def record_silence(self, request: ViewerGenerationRequest) -> None: ...


class ViewerRuntime:
    """Dispatch independent Viewer requests through bounded FIFO mailboxes."""

    def __init__(
        self,
        *,
        provider: ViewerProvider,
        barrage_pipeline: object,
        session_fence: ViewerSessionFence,
        publisher: ViewerBarragePublisher,
        room_service: ViewerRoomWriter,
        clock: Clock,
        id_generator: IdGenerator,
        max_in_flight: int,
        sequence_claimer: ViewerSequenceClaimer | None = None,
        trace_recorder: ViewerTraceSink | None = None,
        debug_service: ViewerTraceSink | None = None,
        behavior_state_sink: ViewerBehaviorStateSink | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be at least one")
        if trace_recorder is not None and debug_service is not None:
            raise ValueError("provide only one trace recorder")
        self._provider = provider
        self._barrage_pipeline = barrage_pipeline
        self._session_fence = session_fence
        if sequence_claimer is not None:
            self._sequence_claimer = sequence_claimer
        elif hasattr(session_fence, "claim_viewer_sequence"):
            self._sequence_claimer = cast(ViewerSequenceClaimer, session_fence)
        else:
            self._sequence_claimer = None
        self._publisher = publisher
        self._room_service = room_service
        self._clock = clock
        self._id_generator = id_generator
        self._trace_recorder = trace_recorder or debug_service
        self._default_max_in_flight = max_in_flight
        self._default_queue_capacity = 64
        self._behavior_state_sink = behavior_state_sink
        self._sleep = sleeper
        self._lanes: dict[tuple[object, ...], _RuntimeLane] = {}
        self._mailboxes: dict[str, _ViewerMailbox] = {}
        self._window_batches: dict[int, _WindowBatchWork] = {}
        self._output_states: dict[str, _OutputPaceState] = {}
        self._request_pace_states: dict[str, _RequestPaceState] = {}
        self._sequences: dict[str, int] = {}
        self._sequence_epochs: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None
        self._generation = 0
        self._wave_generation = 0
        self._wave_fences: dict[tuple[str, int], tuple[int, int, str]] = {}

    async def start_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        async with self._lock:
            if self._active_session_id not in (None, session_id):
                raise RuntimeError(f"viewer runtime already owns {self._active_session_id}")
            self._active_session_id = session_id

    async def stop_session(self, session_id: str) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            self._active_session_id = None
            self._generation += 1
            tasks = [
                mailbox.task
                for mailbox in self._mailboxes.values()
                if mailbox.task is not None
            ]
            output_state = self._output_states.pop(session_id, None)
            if output_state is not None:
                output_state.stopped = True
                for batch in tuple(output_state.queue):
                    output_state.queue.remove(batch)
                    self._cancel_queued_output_locked(
                        batch,
                        reason="session_stopped",
                    )
                if output_state.active is not None:
                    output_state.active.interruption_reason = "session_stopped"
                if output_state.worker is not None:
                    tasks.append(output_state.worker)
            for work in self._window_batches.values():
                work.superseded_reason = "session_stopped"
                work.cancelled.set()
                if work.queued:
                    self._discard_item_locked(work)
                    work.ready.set()
                if work.provider_task is not None:
                    work.provider_task.cancel()
                    work.provider_task.add_done_callback(self._consume_task_result)
            pending = list(
                {
                    id(item): item
                    for mailbox in self._mailboxes.values()
                    for item in (
                        (
                            mailbox.current
                            if mailbox.current is not None and mailbox.current.queued
                            else None
                        ),
                        mailbox.pending,
                    )
                    if item is not None
                }.values()
            )
            for item in pending:
                if not item.output_scheduled:
                    self._record_trace(
                        item,
                        status=TraceResponseStatus.CANCELLED,
                        accepted=False,
                        reason="session_stopped",
                        validation_codes=("cancelled",),
                    )
                    self._resolve(item, "cancelled")
            self._mailboxes.clear()
            self._window_batches.clear()
            self._output_states.clear()
            request_pace_state = self._request_pace_states.pop(session_id, None)
            if request_pace_state is not None:
                request_pace_state.stopped.set()
            self._lanes.clear()
            self._sequences.clear()
            self._sequence_epochs.clear()
            self._wave_fences.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_viewer(
        self,
        viewer_instance_id: str,
        *,
        reason: str = "viewer_state_changed",
    ) -> None:
        """Cancel all queued or in-flight work owned by one Viewer."""

        async with self._lock:
            mailbox = self._mailboxes.pop(viewer_instance_id, None)
            items = (
                ()
                if mailbox is None
                else tuple(
                    {
                        id(item): item
                        for item in (mailbox.current, mailbox.pending)
                        if item is not None
                    }.values()
                )
            )
            for item in items:
                item.invalidated.set()
                if not item.output_scheduled:
                    self._record_trace(
                        item,
                        status=TraceResponseStatus.CANCELLED,
                        accepted=False,
                        reason=reason,
                        validation_codes=("cancelled",),
                    )
                self._discard_item_locked(item)
                if not item.output_scheduled:
                    self._resolve(item, "cancelled")
            for output_state in self._output_states.values():
                for batch in tuple(output_state.queue):
                    if batch.item.request.viewer_instance_id != viewer_instance_id:
                        continue
                    output_state.queue.remove(batch)
                    self._cancel_queued_output_locked(batch, reason=reason)
                if (
                    output_state.active is not None
                    and output_state.active.item.request.viewer_instance_id
                    == viewer_instance_id
                ):
                    output_state.active.interruption_reason = reason
            task = None if mailbox is None else mailbox.task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def dispatch(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        pool: object,
        runtime: object,
    ) -> ViewerDispatchSummary:
        selected = len(decision.selected_viewer_ids)
        if not self._valid_dispatch(wave, decision):
            return ViewerDispatchSummary(selected=selected, stale=selected)
        if (
            wave.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES
            and wave.frame_bundle is None
        ):
            return ViewerDispatchSummary(selected=selected, rejected=selected)
        wave_generation = await self._advance_wave_fence(wave)
        if wave_generation is None:
            return ViewerDispatchSummary(selected=selected, superseded=selected)

        viewers = {
            item.viewer_instance_id: item
            for item in getattr(pool, "viewers", ())
            if isinstance(item, ViewerInstance)
        }
        available_viewer_ids = tuple(viewers)
        futures: list[asyncio.Future[_DispatchResult]] = []
        unmatched_stale = 0
        for viewer_id in decision.selected_viewer_ids:
            viewer = viewers.get(viewer_id)
            if viewer is None or not self._viewer_matches(viewer, wave):
                unmatched_stale += 1
                continue
            future = await self._enqueue(
                viewer=viewer,
                wave=wave,
                decision=decision,
                available_viewer_ids=available_viewer_ids,
                runtime=runtime,
                wave_generation=wave_generation,
            )
            futures.append(future)
        if not futures:
            return ViewerDispatchSummary(selected=selected, stale=unmatched_stale)
        results = await asyncio.shield(asyncio.gather(*futures))
        return ViewerDispatchSummary.combine(
            results,
            selected=selected,
            unmatched_stale=unmatched_stale,
        )

    async def dispatch_window_batch(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        pool: object,
        runtime: object,
    ) -> ViewerDispatchSummary:
        selected = len(decision.selected_viewer_ids)
        if not self._valid_dispatch(wave, decision):
            return ViewerDispatchSummary(selected=selected, stale=selected)
        if (
            wave.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES
            and wave.frame_bundle is None
        ):
            return ViewerDispatchSummary(selected=selected, rejected=selected)
        wave_generation = await self._advance_wave_fence(wave)
        if wave_generation is None:
            return ViewerDispatchSummary(selected=selected, superseded=selected)

        work = _WindowBatchWork(
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            priority=self._wave_priority(wave),
            wave_generation=wave_generation,
        )
        try:
            if not await self._admit_window_batch(work, runtime):
                unmatched_stale = await self._ensure_window_batch_trace_items(
                    work=work,
                    wave=wave,
                    decision=decision,
                    pool=pool,
                    runtime=runtime,
                )
                return self._window_batch_interrupted_summary(
                    work,
                    selected=selected,
                    unmatched_stale=unmatched_stale,
                )
            return await self._execute_window_batch(
                work=work,
                wave=wave,
                decision=decision,
                pool=pool,
                runtime=runtime,
            )
        except asyncio.CancelledError:
            if work.output_delivery_detached:
                raise
            async with self._lock:
                work.superseded_reason = (
                    work.superseded_reason or "superseded_by_scheduler"
                )
                work.cancelled.set()
                if work.provider_task is not None and not work.provider_task.done():
                    work.provider_task.cancel()
                    work.provider_task.add_done_callback(self._consume_task_result)
            unmatched_stale = await self._ensure_window_batch_trace_items(
                work=work,
                wave=wave,
                decision=decision,
                pool=pool,
                runtime=runtime,
            )
            return self._window_batch_interrupted_summary(
                work,
                selected=selected,
                unmatched_stale=unmatched_stale,
            )
        finally:
            await self._finish_window_batch(work)

    async def _execute_window_batch(
        self,
        *,
        work: _WindowBatchWork,
        wave: ObservationWave,
        decision: CrowdDecision,
        pool: object,
        runtime: object,
    ) -> ViewerDispatchSummary:
        selected = len(decision.selected_viewer_ids)
        if work.superseded_reason is not None:
            return self._window_batch_interrupted_summary(
                work,
                selected=selected,
            )
        viewers = {
            item.viewer_instance_id: item
            for item in getattr(pool, "viewers", ())
            if isinstance(item, ViewerInstance)
        }
        available_viewer_ids = tuple(viewers)
        items: list[_WorkItem] = []
        unmatched_stale = 0
        loop = asyncio.get_running_loop()
        async with self._lock:
            if not self._window_batch_is_current_locked(work):
                work.superseded_reason = (
                    work.superseded_reason or "window_batch_wave_superseded"
                )
                return self._window_batch_interrupted_summary(
                    work,
                    selected=selected,
                )
            for viewer_id in decision.selected_viewer_ids:
                viewer = viewers.get(viewer_id)
                if viewer is None or not self._viewer_matches(viewer, wave):
                    unmatched_stale += 1
                    continue
                previous_epoch = self._sequence_epochs.get(viewer.viewer_instance_id)
                previous_sequence = (
                    self._sequences.get(viewer.viewer_instance_id, viewer.viewer_sequence)
                    if previous_epoch == viewer.audience_epoch
                    else viewer.viewer_sequence
                )
                sequence = previous_sequence + 1
                items.append(
                    _WorkItem(
                        request=self._build_request(
                            viewer=viewer,
                            wave=wave,
                            decision=decision,
                            runtime=runtime,
                            sequence=sequence,
                            active_viewer_ids=available_viewer_ids,
                        ),
                        viewer=viewer,
                        wave=wave,
                        decision=decision,
                        available_viewer_ids=available_viewer_ids,
                        runtime=runtime,
                        generation=self._generation,
                        wave_generation=work.wave_generation,
                        priority=self._wave_priority(wave),
                        future=loop.create_future(),
                        queued_at_ms=self._clock.now_ms(),
                    )
                )
            work.items.extend(items)
            work.unmatched_stale = unmatched_stale
        if not items:
            return ViewerDispatchSummary(selected=selected, stale=unmatched_stale)

        claimed: list[_WorkItem] = []
        for item in items:
            if work.cancelled.is_set():
                return self._window_batch_interrupted_summary(
                    work,
                    selected=selected,
                    unmatched_stale=unmatched_stale,
                )
            if await self._claim_item_sequence(item):
                claimed.append(item)
            else:
                self._record_trace(
                    item,
                    status=TraceResponseStatus.STALE,
                    accepted=False,
                    reason="viewer_sequence_claim_rejected",
                    validation_codes=("viewer_sequence_claim_rejected",),
                )
                unmatched_stale += 1
                work.unmatched_stale = unmatched_stale
        if not claimed:
            return ViewerDispatchSummary(selected=selected, stale=unmatched_stale)

        batch_id = self._id_generator.new_id()
        batch_request = WindowBatchGenerationRequest(
            batch_generation_request_id=batch_id,
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            requests=[item.request for item in claimed],
            deadline_at_ms=min(item.request.deadline_at_ms for item in claimed),
        )
        try:
            timeout_seconds = max(
                0.0,
                (batch_request.deadline_at_ms - self._clock.now_ms()) / 1_000,
            )
            if timeout_seconds <= 0:
                raise _ViewerRequestExpired("Window batch request TTL expired")
            response = await self._generate_window_batch_with_retry(
                work,
                batch_request,
                timeout_seconds=timeout_seconds,
            )
            if (
                response.batch_generation_request_id
                != batch_request.batch_generation_request_id
            ):
                raise ViewerRuntimeProviderError(
                    "Window batch response request ID mismatch"
                )
        except _WindowBatchSuperseded:
            return self._window_batch_interrupted_summary(
                work,
                selected=selected,
                unmatched_stale=unmatched_stale,
            )
        except asyncio.CancelledError:
            if work.cancelled.is_set():
                return self._window_batch_interrupted_summary(
                    work,
                    selected=selected,
                    unmatched_stale=unmatched_stale,
                )
            raise
        except (TimeoutError, _ViewerRequestExpired):
            for item in claimed:
                item.completed_at_ms = self._clock.now_ms()
                self._record_trace(
                    item,
                    status=TraceResponseStatus.EXPIRED,
                    accepted=False,
                    reason="window_batch_provider_expired",
                    validation_codes=("expired",),
                )
            return ViewerDispatchSummary(
                selected=selected,
                queued=(len(claimed) if work.was_queued else 0),
                dispatched=len(claimed),
                completed=len(claimed),
                retry=sum(item.retry_count for item in claimed),
                expired=len(claimed),
                stale=unmatched_stale,
            )
        except Exception:
            logger.exception("Window batch Viewer provider failed")
            for item in claimed:
                item.completed_at_ms = self._clock.now_ms()
                self._record_trace(
                    item,
                    status=TraceResponseStatus.FAILED,
                    accepted=False,
                    reason="window_batch_provider_failed",
                    validation_codes=("provider_failed",),
                )
            return ViewerDispatchSummary(
                selected=selected,
                queued=(len(claimed) if work.was_queued else 0),
                dispatched=len(claimed),
                completed=len(claimed),
                retry=sum(item.retry_count for item in claimed),
                failed=len(claimed),
                stale=unmatched_stale,
            )

        by_viewer = {item.request.viewer_instance_id: item for item in claimed}
        candidates: dict[str, tuple[_WorkItem, ViewerGenerationResponse]] = {}
        seen_texts: set[str] = set()
        for candidate in response.candidates:
            item = by_viewer.get(candidate.viewer_instance_id)
            normalized_texts = tuple(
                text.strip()[:MAX_VIEWER_BARRAGE_TEXT_LENGTH].casefold()
                for text in candidate.texts or ()
            )
            if (
                item is None
                or candidate.viewer_instance_id in candidates
                or candidate.generation_request_id != item.request.generation_request_id
                or candidate.viewer_sequence != item.request.viewer_sequence
                or not normalized_texts
                or any(text in seen_texts for text in normalized_texts)
            ):
                continue
            seen_texts.update(normalized_texts)
            candidates[candidate.viewer_instance_id] = (item, candidate)

        outcomes: list[_DispatchResult] = []
        scheduled_items: list[_WorkItem] = []
        for item in claimed:
            item.completed_at_ms = self._clock.now_ms()
            candidate_entry = candidates.get(item.request.viewer_instance_id)
            if candidate_entry is None:
                if await self._final_fence_outcome(item) is not None:
                    outcome = "stale"
                elif await self._commit_silence(item):
                    self._record_trace(
                        item,
                        status=TraceResponseStatus.SILENCE,
                        accepted=True,
                    )
                    outcome = "silenced"
                else:
                    outcome = self._finalize_after_provider(item, phase="silence_commit")
            else:
                _, candidate = candidate_entry
                outcome = await self._accept_window_batch_candidate(item, candidate)
            if outcome == "scheduled":
                scheduled_items.append(item)
                continue
            outcomes.append(
                _DispatchResult(
                    outcome=outcome,
                    queued=int(work.was_queued),
                    dispatched=1,
                    completed=1,
                    retry=item.retry_count,
                )
            )
        if scheduled_items:
            work.output_delivery_detached = True
            await self._finish_window_batch(work)
            completed = await asyncio.shield(
                asyncio.gather(*(item.future for item in scheduled_items))
            )
            for item, result in zip(scheduled_items, completed, strict=True):
                outcomes.append(
                    _DispatchResult(
                        outcome=result.outcome,
                        queued=int(work.was_queued),
                        dispatched=1,
                        completed=1,
                        retry=item.retry_count,
                    )
                )
        return ViewerDispatchSummary.combine(
            outcomes,
            selected=selected,
            unmatched_stale=unmatched_stale,
        )

    async def _admit_window_batch(
        self,
        work: _WindowBatchWork,
        runtime: object,
    ) -> bool:
        lane_key, max_in_flight, queue_capacity = self._runtime_limits(
            runtime=runtime,
            session_id=work.session_id,
            audience_epoch=work.audience_epoch,
        )
        async with self._lock:
            if not self._window_batch_is_current_locked(work):
                work.superseded_reason = self._wave_fence_rejection_reason(
                    session_id=work.session_id,
                    audience_epoch=work.audience_epoch,
                    priority=work.priority,
                )
                return False
            work.generation = self._generation
            lane = self._lanes.setdefault(
                lane_key,
                _RuntimeLane(
                    max_in_flight=max_in_flight,
                    queue_capacity=queue_capacity,
                ),
            )
            work.lane = lane
            if lane.active < lane.max_in_flight:
                lane.active += 1
                work.slot_reserved = True
                work.ready.set()
            elif lane.queued >= lane.queue_capacity:
                work.superseded_reason = "window_batch_queue_capacity_exceeded"
                return False
            else:
                lane.queued += 1
                work.queued = True
                work.was_queued = True
                lane.eligible.append(work)
            work.admitted = True
            self._window_batches[id(work)] = work
        try:
            await work.ready.wait()
        except BaseException:
            await self._finish_window_batch(work)
            raise
        async with self._lock:
            if not self._window_batch_is_current_locked(work):
                work.superseded_reason = (
                    work.superseded_reason or "window_batch_wave_superseded"
                )
                return False
            return True

    async def _finish_window_batch(self, work: _WindowBatchWork) -> None:
        async with self._lock:
            if work.finished:
                return
            work.finished = True
            self._window_batches.pop(id(work), None)
            if work.queued:
                self._discard_item_locked(work)
            self._release_slot_locked(work)

    async def _ensure_window_batch_trace_items(
        self,
        *,
        work: _WindowBatchWork,
        wave: ObservationWave,
        decision: CrowdDecision,
        pool: object,
        runtime: object,
    ) -> int:
        if work.items:
            return work.unmatched_stale
        viewers = {
            item.viewer_instance_id: item
            for item in getattr(pool, "viewers", ())
            if isinstance(item, ViewerInstance)
        }
        available_viewer_ids = tuple(viewers)
        loop = asyncio.get_running_loop()
        async with self._lock:
            if work.items:
                return work.unmatched_stale
            unmatched_stale = 0
            for viewer_id in decision.selected_viewer_ids:
                viewer = viewers.get(viewer_id)
                if viewer is None or not self._viewer_matches(viewer, wave):
                    unmatched_stale += 1
                    continue
                previous_epoch = self._sequence_epochs.get(viewer.viewer_instance_id)
                previous_sequence = (
                    self._sequences.get(viewer.viewer_instance_id, viewer.viewer_sequence)
                    if previous_epoch == viewer.audience_epoch
                    else viewer.viewer_sequence
                )
                work.items.append(
                    _WorkItem(
                        request=self._build_request(
                            viewer=viewer,
                            wave=wave,
                            decision=decision,
                            runtime=runtime,
                            sequence=previous_sequence + 1,
                            active_viewer_ids=available_viewer_ids,
                        ),
                        viewer=viewer,
                        wave=wave,
                        decision=decision,
                        available_viewer_ids=available_viewer_ids,
                        runtime=runtime,
                        generation=self._generation,
                        wave_generation=work.wave_generation,
                        priority=work.priority,
                        future=loop.create_future(),
                        queued_at_ms=self._clock.now_ms(),
                    )
                )
            work.unmatched_stale = unmatched_stale
            return unmatched_stale

    def _window_batch_interrupted_summary(
        self,
        work: _WindowBatchWork,
        *,
        selected: int,
        unmatched_stale: int = 0,
    ) -> ViewerDispatchSummary:
        claimed_items = work.items
        interrupted_items = [item for item in claimed_items if not item.traced]
        reason = work.superseded_reason or "window_batch_superseded"
        cancelled = reason in {
            "session_stopped",
            "window_batch_queue_capacity_exceeded",
        }
        validation_codes = (
            ("queue_capacity_exceeded",)
            if reason == "window_batch_queue_capacity_exceeded"
            else (("cancelled",) if cancelled else ("superseded",))
        )
        now_ms = self._clock.now_ms()
        for item in interrupted_items:
            item.completed_at_ms = now_ms if item.dispatched_at_ms is not None else None
            self._record_trace(
                item,
                status=TraceResponseStatus.CANCELLED,
                accepted=False,
                reason=reason,
                validation_codes=validation_codes,
            )
        terminal = len(interrupted_items)
        return ViewerDispatchSummary(
            selected=selected,
            queued=(terminal if work.was_queued else 0),
            dispatched=sum(item.dispatched_at_ms is not None for item in claimed_items),
            completed=sum(item.completed_at_ms is not None for item in claimed_items),
            retry=sum(item.retry_count for item in claimed_items),
            stale=unmatched_stale,
            cancelled=(terminal if cancelled else 0),
            superseded=(0 if cancelled else terminal),
        )

    async def _generate_window_batch_with_retry(
        self,
        work: _WindowBatchWork,
        request: WindowBatchGenerationRequest,
        *,
        timeout_seconds: float,
    ) -> WindowBatchGenerationResponse:
        loop = asyncio.get_running_loop()
        monotonic_deadline = loop.time() + timeout_seconds
        try:
            return await self._window_batch_provider_attempt(
                work,
                request,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            if isinstance(error, (_ViewerRequestExpired, _WindowBatchSuperseded)):
                raise
            first_request = request.requests[0]
            if not self._is_transient(error) or self._expired(first_request):
                raise
            backoff_seconds = self._retry_delay_seconds(error)
            remaining_seconds = min(
                self._remaining_ttl_seconds(first_request),
                monotonic_deadline - loop.time(),
            )
            if remaining_seconds < backoff_seconds + 0.05:
                raise _ViewerRequestExpired("insufficient TTL for window batch retry")
            await asyncio.sleep(backoff_seconds)
            async with self._lock:
                if not self._window_batch_is_current_locked(work):
                    raise _WindowBatchSuperseded
            retry_budget = min(
                self._remaining_ttl_seconds(first_request),
                monotonic_deadline - loop.time(),
            )
            if retry_budget < 0.05:
                raise _ViewerRequestExpired("insufficient TTL for window batch retry")
            for item in work.items:
                item.retry_count = 1
            return await self._window_batch_provider_attempt(
                work,
                request,
                timeout_seconds=retry_budget,
            )

    async def _window_batch_provider_attempt(
        self,
        work: _WindowBatchWork,
        request: WindowBatchGenerationRequest,
        *,
        timeout_seconds: float,
    ) -> WindowBatchGenerationResponse:
        if timeout_seconds <= 0:
            raise _ViewerRequestExpired("Window batch request TTL expired")
        if not await self._wait_for_window_batch_request_turn(work):
            raise _WindowBatchSuperseded
        async with self._lock:
            if not self._window_batch_is_current_locked(work):
                raise _WindowBatchSuperseded
            attempt = asyncio.create_task(
                self._provider.generate_window_batch(request),
                name=f"viewer-window-batch:{request.batch_generation_request_id}",
            )
            dispatched_at_ms = self._clock.now_ms()
            if work.provider_dispatched_at_ms is None:
                work.provider_dispatched_at_ms = dispatched_at_ms
            request_ids = {
                item.generation_request_id
                for item in request.requests
            }
            for item in work.items:
                if (
                    item.request.generation_request_id in request_ids
                    and item.dispatched_at_ms is None
                ):
                    item.dispatched_at_ms = dispatched_at_ms
            work.provider_task = attempt
        cancelled = asyncio.create_task(work.cancelled.wait())
        try:
            done, _ = await asyncio.wait(
                {attempt, cancelled},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            attempt.cancel()
            attempt.add_done_callback(self._consume_task_result)
            cancelled.cancel()
            cancelled.add_done_callback(self._consume_task_result)
            raise
        finally:
            if work.provider_task is attempt:
                work.provider_task = None
        if cancelled in done or work.cancelled.is_set():
            attempt.cancel()
            attempt.add_done_callback(self._consume_task_result)
            if cancelled not in done:
                cancelled.cancel()
                cancelled.add_done_callback(self._consume_task_result)
            raise _WindowBatchSuperseded
        cancelled.cancel()
        cancelled.add_done_callback(self._consume_task_result)
        if attempt in done:
            return attempt.result()
        attempt.cancel()
        attempt.add_done_callback(self._consume_task_result)
        raise _ViewerRequestExpired("Window batch provider attempt exceeded remaining TTL")

    def _window_batch_is_current_locked(self, work: _WindowBatchWork) -> bool:
        return (
            not work.finished
            and work.superseded_reason is None
            and work.generation in (0, self._generation)
            and self._active_session_id == work.session_id
            and (
                self._wave_matches_current_fence(
                    session_id=work.session_id,
                    audience_epoch=work.audience_epoch,
                    wave_generation=work.wave_generation,
                    observation_id=work.observation_id,
                )
                or (
                    work.admitted
                    and work.provider_dispatched_at_ms is not None
                )
            )
        )

    async def _accept_window_batch_candidate(
        self,
        item: _WorkItem,
        response: ViewerGenerationResponse,
    ) -> str:
        validation = self._barrage_pipeline.validate(
            request=item.request,
            response=response,
        )
        if not validation.accepted:
            reason = str(validation.rejection_reason or "validation_rejected")
            self._record_trace(
                item,
                status=TraceResponseStatus.REJECTED,
                accepted=False,
                reason=reason,
                validation_codes=(reason,),
            )
            return "rejected"
        if not validation.events:
            fenced = await self._final_fence_outcome(item)
            if fenced is not None:
                return fenced
            self._record_trace(
                item,
                status=TraceResponseStatus.COMPLETED,
                accepted=True,
            )
            return "silenced"
        fenced = await self._final_fence_outcome(item)
        if fenced is not None:
            return fenced
        return await self._schedule_output_batch(item, validation.events)

    def record_observation_trace(
        self,
        *,
        wave: ObservationWave,
        runtime: object | None,
        status: ObservationWaveStatus,
        decision: CrowdDecision | None = None,
        failure_reason: str | None = None,
    ) -> None:
        if self._trace_recorder is None:
            return
        try:
            self._trace_recorder.record(
                build_observation_wave_trace(
                    wave=wave,
                    runtime=runtime,
                    status=status,
                    decision=decision,
                    failure_reason=failure_reason,
                )
            )
        except Exception as error:
            logger.warning(
                "observation wave trace recording failed",
                extra={"error_type": type(error).__name__},
            )

    async def _enqueue(
        self,
        *,
        viewer: ViewerInstance,
        wave: ObservationWave,
        decision: CrowdDecision,
        available_viewer_ids: tuple[str, ...],
        runtime: object,
        wave_generation: int,
    ) -> asyncio.Future[_DispatchResult]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[_DispatchResult] = loop.create_future()
        async with self._lock:
            previous_epoch = self._sequence_epochs.get(viewer.viewer_instance_id)
            previous_sequence = (
                self._sequences.get(viewer.viewer_instance_id, viewer.viewer_sequence)
                if previous_epoch == viewer.audience_epoch
                else viewer.viewer_sequence
            )
            sequence = previous_sequence + 1
            request = self._build_request(
                viewer=viewer,
                wave=wave,
                decision=decision,
                runtime=runtime,
                sequence=sequence,
                active_viewer_ids=available_viewer_ids,
            )
            item = _WorkItem(
                request=request,
                viewer=viewer,
                wave=wave,
                decision=decision,
                available_viewer_ids=available_viewer_ids,
                runtime=runtime,
                generation=self._generation,
                wave_generation=wave_generation,
                priority=self._wave_priority(wave),
                future=future,
                queued_at_ms=self._clock.now_ms(),
            )
            if not self._wave_matches_current_fence(
                session_id=wave.session_id,
                audience_epoch=wave.audience_epoch,
                wave_generation=wave_generation,
                observation_id=wave.observation_id,
            ):
                self._record_superseded(
                    item,
                    reason=self._wave_fence_rejection_reason(
                        session_id=wave.session_id,
                        audience_epoch=wave.audience_epoch,
                        priority=item.priority,
                    ),
                )
                return future
            lane_key, max_in_flight, queue_capacity = self._runtime_limits(
                runtime=runtime,
                session_id=wave.session_id,
                audience_epoch=wave.audience_epoch,
            )
            lane = self._lanes.setdefault(
                lane_key,
                _RuntimeLane(
                    max_in_flight=max_in_flight,
                    queue_capacity=queue_capacity,
                ),
            )
            item.lane = lane
            mailbox = self._mailboxes.setdefault(viewer.viewer_instance_id, _ViewerMailbox())
            mailbox_busy = mailbox.task is not None and not mailbox.task.done()
            if (
                mailbox.current is not None
                and mailbox.current.priority > item.priority
                and mailbox.current.superseded_reason is None
                and not mailbox.current.future.done()
            ):
                self._record_superseded(item, reason="lower_priority_than_current_request")
                return future
            if mailbox.pending is not None and mailbox.pending.priority > item.priority:
                self._record_superseded(item, reason="lower_priority_than_pending_request")
                return future
            needs_queue = mailbox_busy or lane.active >= lane.max_in_flight
            if needs_queue and lane.queued >= lane.queue_capacity:
                self._record_trace(
                    item,
                    status=TraceResponseStatus.CANCELLED,
                    accepted=False,
                    reason="viewer_queue_capacity_exceeded",
                    validation_codes=("queue_capacity_exceeded",),
                )
                self._resolve(item, "cancelled")
                return future
            if not mailbox_busy:
                if needs_queue:
                    lane.queued += 1
                    item.queued = True
                    item.was_queued = True
                    lane.eligible.append(item)
                else:
                    lane.active += 1
                    item.slot_reserved = True
                mailbox.current = item
                mailbox.task = asyncio.create_task(
                    self._run_mailbox(viewer.viewer_instance_id, item)
                )
                self._promote_locked(lane)
            else:
                previous_pending = mailbox.pending
                if previous_pending is not None:
                    self._record_superseded(
                        previous_pending,
                        reason=self._supersede_reason(item.priority, previous_pending.priority),
                    )
                    self._discard_item_locked(previous_pending)
                lane.queued += 1
                item.queued = True
                item.was_queued = True
                mailbox.pending = item
        return future

    async def _claim_sequence(self, request: ViewerGenerationRequest) -> bool:
        if self._sequence_claimer is None:
            return True
        return await self._sequence_claimer.claim_viewer_sequence(
            room_id=request.room_id,
            session_id=request.session_id,
            audience_epoch=request.audience_epoch,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
        )

    async def _claim_item_sequence(self, item: _WorkItem) -> bool:
        claim = asyncio.create_task(
            self._claim_item_sequence_to_completion(item),
            name=f"viewer-sequence:{item.request.generation_request_id}",
        )
        try:
            return await asyncio.shield(claim)
        except asyncio.CancelledError:
            await claim
            raise

    async def _claim_item_sequence_to_completion(self, item: _WorkItem) -> bool:
        viewer_id = item.request.viewer_instance_id
        async with self._lock:
            previous_epoch = self._sequence_epochs.get(viewer_id)
            previous_sequence = (
                self._sequences.get(viewer_id, item.viewer.viewer_sequence)
                if previous_epoch == item.request.audience_epoch
                else item.viewer.viewer_sequence
            )
            sequence = previous_sequence + 1
            if item.request.viewer_sequence != sequence:
                item.request = item.request.model_copy(
                    update={"viewer_sequence": sequence}
                )
            request = item.request

        if not await self._claim_sequence(request):
            return False

        async with self._lock:
            current_epoch = self._sequence_epochs.get(viewer_id)
            current_sequence = self._sequences.get(viewer_id, 0)
            if (
                current_epoch != request.audience_epoch
                or request.viewer_sequence > current_sequence
            ):
                self._sequences[viewer_id] = request.viewer_sequence
                self._sequence_epochs[viewer_id] = request.audience_epoch
        return True

    async def _advance_wave_fence(self, wave: ObservationWave) -> int | None:
        priority = self._wave_priority(wave)
        key = (wave.session_id, wave.audience_epoch)
        async with self._lock:
            current = self._wave_fences.get(key)
            if current is not None:
                generation, current_priority, observation_id = current
                if observation_id == wave.observation_id:
                    return generation
                if priority < current_priority and self._wave_has_live_work_locked(generation):
                    return None
            self._wave_generation += 1
            generation = self._wave_generation
            self._wave_fences[key] = (generation, priority, wave.observation_id)
            for mailbox in self._mailboxes.values():
                pending = mailbox.pending
                if (
                    pending is not None
                    and pending.request.session_id == wave.session_id
                    and pending.request.audience_epoch == wave.audience_epoch
                    and pending.priority <= priority
                    and pending.wave_generation != generation
                ):
                    mailbox.pending = None
                    self._record_superseded(
                        pending,
                        reason=self._supersede_reason(priority, pending.priority),
                    )
                    self._discard_item_locked(pending)
                active = mailbox.current
                if (
                    active is not None
                    and active.request.session_id == wave.session_id
                    and active.request.audience_epoch == wave.audience_epoch
                    and active.priority <= priority
                    and active.wave_generation != generation
                    and not active.output_scheduled
                ):
                    active.superseded_reason = self._supersede_reason(
                        priority,
                        active.priority,
                    )
                    active.invalidated.set()
                    if active.dispatched_at_ms is not None:
                        # Let the provider call finish, but prevent stale output from publishing.
                        continue
                    if active.queued:
                        self._discard_item_locked(active)
                        active.ready.set()
                    if active.provider_task is not None and not active.provider_task.done():
                        active.provider_task.cancel()
            for work in self._window_batches.values():
                if (
                    work.session_id == wave.session_id
                    and work.audience_epoch == wave.audience_epoch
                    and work.priority <= priority
                    and work.wave_generation != generation
                ):
                    reason = self._supersede_reason(
                        priority,
                        work.priority,
                    )
                    if work.provider_dispatched_at_ms is not None:
                        # Let the batch provider call finish, but fence every stale candidate.
                        for item in work.items:
                            if not item.output_scheduled:
                                item.superseded_reason = reason
                        continue
                    work.superseded_reason = reason
                    work.cancelled.set()
                    if work.queued:
                        self._discard_item_locked(work)
                        work.ready.set()
                    if work.provider_task is not None and not work.provider_task.done():
                        work.provider_task.cancel()
            return generation

    def _wave_has_live_work_locked(self, generation: int) -> bool:
        viewer_work = any(
            item is not None
            and item.wave_generation == generation
            and not item.future.done()
            for mailbox in self._mailboxes.values()
            for item in (mailbox.current, mailbox.pending)
        )
        return viewer_work or any(
            work.wave_generation == generation and not work.finished
            for work in self._window_batches.values()
        )

    async def _run_mailbox(self, viewer_id: str, item: _WorkItem) -> None:
        current: _WorkItem | None = item
        try:
            while current is not None:
                while True:
                    while current.replacement is not None:
                        current = current.replacement
                    if current.slot_reserved or current.superseded_reason is not None:
                        break
                    await current.ready.wait()
                try:
                    outcome = await self._execute(current)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.exception(
                        "Viewer mailbox item failed",
                        extra={
                            "generation_request_id": (
                                current.request.generation_request_id
                            ),
                            "error_type": type(error).__name__,
                        },
                    )
                    current.completed_at_ms = self._clock.now_ms()
                    self._record_trace(
                        current,
                        status=TraceResponseStatus.FAILED,
                        accepted=False,
                        reason="viewer_mailbox_failed",
                        validation_codes=("viewer_mailbox_failed",),
                    )
                    outcome = "failed"
                if outcome != "scheduled":
                    self._resolve(current, outcome)
                async with self._lock:
                    self._release_slot_locked(current)
                    mailbox = self._mailboxes.get(viewer_id)
                    if mailbox is None:
                        current = None
                    else:
                        current = mailbox.pending
                        mailbox.pending = None
                        mailbox.current = current
                        if current is None:
                            mailbox.task = None
                            self._mailboxes.pop(viewer_id, None)
                        else:
                            assert current.lane is not None
                            current.lane.eligible.append(current)
                            self._promote_locked(current.lane)
        except asyncio.CancelledError:
            if current is not None and not current.output_scheduled:
                self._record_trace(
                    current,
                    status=TraceResponseStatus.CANCELLED,
                    accepted=False,
                    reason="session_stopped",
                    validation_codes=("cancelled",),
                )
                self._resolve(current, "cancelled")
            async with self._lock:
                if current is not None:
                    self._discard_item_locked(current)
                mailbox = self._mailboxes.pop(viewer_id, None)
                if mailbox is not None:
                    if mailbox.pending is not None:
                        pending = mailbox.pending
                        mailbox.pending = None
                        if not pending.output_scheduled:
                            self._record_trace(
                                pending,
                                status=TraceResponseStatus.CANCELLED,
                                accepted=False,
                                reason="session_stopped",
                                validation_codes=("cancelled",),
                            )
                        self._discard_item_locked(pending)
                        self._resolve(pending, "cancelled")
            raise

    async def _execute(self, item: _WorkItem) -> str:
        request = item.request
        if item.superseded_reason is not None:
            return self._finalize_superseded(item)
        if not self._is_current(item) or self._expired(request):
            return self._finalize_pre_dispatch(item)
        try:
            if not self._is_current(item) or self._expired(request):
                return self._finalize_pre_dispatch(item)
            if not await self._claim_item_sequence(item):
                self._record_trace(
                    item,
                    status=TraceResponseStatus.STALE,
                    accepted=False,
                    reason="viewer_sequence_claim_rejected",
                    validation_codes=("viewer_sequence_claim_rejected",),
                )
                return "stale"
            request = item.request
            if not self._is_current(item) or self._expired(request):
                return self._finalize_pre_dispatch(item)
            response = await self._generate_with_retry(item)
            item.completed_at_ms = self._clock.now_ms()
        except asyncio.CancelledError:
            if item.superseded_reason is not None:
                item.completed_at_ms = self._clock.now_ms()
                return self._finalize_superseded(item)
            raise
        except _ViewerRequestSuperseded:
            return (
                self._finalize_superseded(item)
                if item.superseded_reason is not None
                else self._finalize_pre_dispatch(item)
            )
        except _ViewerRequestExpired:
            item.completed_at_ms = self._clock.now_ms()
            self._record_trace(
                item,
                status=TraceResponseStatus.EXPIRED,
                accepted=False,
                reason="provider_attempt_expired",
                validation_codes=("expired",),
            )
            return "expired"
        except ViewerRuntimeProviderBlockedError as error:
            logger.warning(
                "Viewer request %s was blocked: %s",
                request.generation_request_id,
                error,
            )
            item.completed_at_ms = self._clock.now_ms()
            self._record_trace(
                item,
                status=TraceResponseStatus.FAILED,
                accepted=False,
                reason="provider_blocked",
                validation_codes=("provider_blocked",),
            )
            return "failed"
        except Exception as error:
            if isinstance(error, ViewerRuntimeProviderError) and error.status_code == 429:
                logger.warning(
                    "Viewer provider rate limited for request %s after retry",
                    request.generation_request_id,
                )
            else:
                logger.exception(
                    "Viewer provider failed for request %s",
                    request.generation_request_id,
                )
            item.completed_at_ms = self._clock.now_ms()
            self._record_trace(
                item,
                status=TraceResponseStatus.FAILED,
                accepted=False,
                reason="provider_failed",
                validation_codes=("provider_failed",),
            )
            return "failed"

        if response.action is ViewerAction.SILENCE:
            fenced = await self._final_fence_outcome(item)
            if fenced is not None:
                return fenced
            if not await self._commit_silence(item):
                return self._finalize_after_provider(item, phase="silence_commit")
            self._record_trace(
                item,
                status=TraceResponseStatus.SILENCE,
                accepted=True,
            )
            return "silenced"
        validation = self._barrage_pipeline.validate(request=request, response=response)
        if not validation.accepted:
            reason = str(validation.rejection_reason or "")
            expired = reason.endswith("expired")
            self._record_trace(
                item,
                status=(
                    TraceResponseStatus.EXPIRED
                    if expired
                    else TraceResponseStatus.REJECTED
                ),
                accepted=False,
                reason=reason or ("validation_expired" if expired else "validation_rejected"),
                validation_codes=(
                    reason or ("validation_expired" if expired else "validation_rejected"),
                ),
            )
            return "expired" if expired else "rejected"
        if not validation.events:
            fenced = await self._final_fence_outcome(item)
            if fenced is not None:
                return fenced
            self._record_trace(
                item,
                status=TraceResponseStatus.COMPLETED,
                accepted=True,
            )
            return "silenced"
        fenced = await self._final_fence_outcome(item)
        if fenced is not None:
            return fenced
        return await self._schedule_output_batch(item, validation.events)

    async def _schedule_output_batch(
        self,
        item: _WorkItem,
        events: tuple[ViewerBarrageEvent, ...],
    ) -> str:
        ready_at_ms = item.completed_at_ms or self._clock.now_ms()
        batch = _OutputBatch(
            item=item,
            events=events,
            ready_at_ms=ready_at_ms,
            scheduled_at_ms=self._clock.now_ms(),
        )
        async with self._lock:
            if not self._output_item_is_current_locked(item):
                batch.interruption_reason = "output_fence_rejected"
                self._cancel_queued_output_locked(
                    batch,
                    reason=batch.interruption_reason,
                )
                return "cancelled"
            state = self._output_states.setdefault(
                item.request.session_id,
                _OutputPaceState(session_id=item.request.session_id),
            )
            if state.stopped:
                self._cancel_queued_output_locked(batch, reason="session_stopped")
                return "cancelled"
            item.output_scheduled = True
            state.queue.append(batch)
            if state.worker is None or state.worker.done():
                state.worker = asyncio.create_task(
                    self._run_output_worker(state),
                    name=f"viewer-output:{item.request.session_id}",
                )
            self._record_output_delivery(batch, stage="output_scheduled")
        return "scheduled"

    async def _run_output_worker(self, state: _OutputPaceState) -> None:
        worker = asyncio.current_task()
        try:
            while True:
                async with self._lock:
                    if state.stopped:
                        return
                    if not state.queue:
                        if state.worker is worker:
                            state.worker = None
                        return
                    batch = state.queue.popleft()
                    state.active = batch
                try:
                    outcome = await self._deliver_output_batch(state, batch)
                except asyncio.CancelledError:
                    batch.interruption_reason = (
                        batch.interruption_reason or "output_worker_cancelled"
                    )
                    await self._finish_output_batch(batch, outcome="cancelled")
                    raise
                except Exception:
                    logger.exception(
                        "Viewer output worker failed",
                        extra={
                            "session_id": batch.item.request.session_id,
                            "generation_request_id": (
                                batch.item.request.generation_request_id
                            ),
                        },
                    )
                    batch.interruption_reason = "output_worker_failed"
                    await self._finish_output_batch(batch, outcome="failed")
                else:
                    await self._finish_output_batch(batch, outcome=outcome)
                finally:
                    async with self._lock:
                        if state.active is batch:
                            state.active = None
        finally:
            async with self._lock:
                if state.worker is worker:
                    state.worker = None

    async def _deliver_output_batch(
        self,
        state: _OutputPaceState,
        batch: _OutputBatch,
    ) -> str:
        for event in batch.events:
            if not await self._wait_for_output_turn(state, batch):
                return "cancelled"
            published_event = event.model_copy(
                update={
                    "created_at_ms": self._clock.now_ms(),
                    "expires_at_ms": UNBOUNDED_DEADLINE_AT_MS,
                }
            )
            outcome, delivery_failed = await self._commit_output_event(
                state,
                batch,
                published_event,
            )
            if outcome != "published":
                batch.interruption_reason = (
                    batch.interruption_reason
                    or (
                        "publish_side_effect_failed"
                        if outcome == "failed"
                        else "session_fence_rejected"
                    )
                )
                return "failed" if outcome == "failed" else "cancelled"
            if batch.published_at_ms is None:
                batch.published_at_ms = published_event.created_at_ms
            batch.published_events.append(published_event)
            batch.realtime_delivery_failed = (
                batch.realtime_delivery_failed or delivery_failed
            )
            async with self._lock:
                state.next_release_at_ms = (
                    self._clock.now_ms()
                    + int(_BARRAGE_BATCH_INTERVAL_SECONDS * 1_000)
                )
            self._record_output_delivery(batch, stage="output_published")
        return "published"

    async def _wait_for_output_turn(
        self,
        state: _OutputPaceState,
        batch: _OutputBatch,
    ) -> bool:
        async with self._lock:
            if not self._output_batch_is_current_locked(state, batch):
                return False
            next_release_at_ms = state.next_release_at_ms
            delay_ms = (
                0
                if next_release_at_ms is None
                else max(0, next_release_at_ms - self._clock.now_ms())
            )
        if delay_ms:
            await self._sleep(delay_ms / 1_000)
        async with self._lock:
            return self._output_batch_is_current_locked(state, batch)

    async def _commit_output_event(
        self,
        state: _OutputPaceState,
        batch: _OutputBatch,
        event: ViewerBarrageEvent,
    ) -> tuple[str, bool]:
        if not await self._output_fence_accepts(state, batch):
            return "cancelled", False

        async def commit_once() -> str:
            async with self._lock:
                if not self._output_batch_is_current_locked(state, batch):
                    return "cancelled"
                try:
                    await self._room_service.append_published_barrage(event)
                except Exception:
                    return "failed"
                return "published"

        async def commit_with_fence() -> str:
            execute = getattr(self._session_fence, "execute_if_accepting", None)
            if not callable(execute):
                return await commit_once()
            accepted, result = await execute(
                room_id=batch.item.request.room_id,
                session_id=batch.item.request.session_id,
                audience_epoch=batch.item.request.audience_epoch,
                viewer_instance_id=batch.item.request.viewer_instance_id,
                viewer_sequence=batch.item.request.viewer_sequence,
                presence_revision=batch.item.request.presence_revision,
                moderation_revision=batch.item.request.moderation_revision,
                behavior_revision=None,
                operation=commit_once,
            )
            if not accepted or result is None:
                return "cancelled"
            return result

        commit = asyncio.create_task(
            commit_with_fence(),
            name=f"viewer-effect:{batch.item.request.generation_request_id}",
        )
        try:
            outcome = await asyncio.shield(commit)
        except asyncio.CancelledError:
            outcome = await commit
        if outcome != "published":
            return outcome, False
        async with self._lock:
            if not self._output_batch_is_current_locked(state, batch):
                return "published", True
        return "published", await self._deliver_realtime(batch.item, event)

    async def _output_fence_accepts(
        self,
        state: _OutputPaceState,
        batch: _OutputBatch,
    ) -> bool:
        async with self._lock:
            if not self._output_batch_is_current_locked(state, batch):
                return False
        request = batch.item.request
        accepted = await self._session_fence.accepts(
            room_id=request.room_id,
            session_id=request.session_id,
            audience_epoch=request.audience_epoch,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            presence_revision=request.presence_revision,
            moderation_revision=request.moderation_revision,
            behavior_revision=None,
            deadline_at_ms=UNBOUNDED_DEADLINE_AT_MS,
        )
        async with self._lock:
            if not accepted and batch.interruption_reason is None:
                batch.interruption_reason = "session_fence_rejected"
            return accepted and self._output_batch_is_current_locked(state, batch)

    async def _finish_output_batch(
        self,
        batch: _OutputBatch,
        *,
        outcome: str,
    ) -> None:
        if batch.finished:
            return
        published_events = tuple(batch.published_events)
        if published_events:
            if batch.interruption_reason != "session_stopped":
                await self._commit_published_behavior(batch.item, published_events)
            if batch.realtime_delivery_failed:
                logger.warning(
                    "durable Viewer barrage could not be delivered in realtime",
                    extra={
                        "session_id": batch.item.request.session_id,
                        "generation_request_id": (
                            batch.item.request.generation_request_id
                        ),
                    },
                )
            validation_codes = []
            if batch.interruption_reason is not None:
                validation_codes.append(batch.interruption_reason)
            if batch.realtime_delivery_failed:
                validation_codes.append("realtime_delivery_failed")
            self._record_trace(
                batch.item,
                status=TraceResponseStatus.PUBLISHED,
                accepted=True,
                reason=batch.interruption_reason,
                validation_codes=tuple(validation_codes),
                published_barrage_ids=self._published_barrage_ids(published_events),
                output_delivery=self._output_delivery(batch),
            )
            if batch.interruption_reason is not None:
                self._record_output_delivery(batch, stage="output_interrupted")
            self._resolve(batch.item, "published")
        elif outcome == "failed":
            self._record_trace(
                batch.item,
                status=TraceResponseStatus.FAILED,
                accepted=False,
                reason=batch.interruption_reason or "publish_side_effect_failed",
                validation_codes=("publish_side_effect_failed",),
                output_delivery=self._output_delivery(batch),
            )
            self._record_output_delivery(batch, stage="output_failed")
            self._resolve(batch.item, "failed")
        else:
            self._record_trace(
                batch.item,
                status=TraceResponseStatus.CANCELLED,
                accepted=False,
                reason=batch.interruption_reason or "output_cancelled",
                validation_codes=("cancelled",),
                output_delivery=self._output_delivery(batch),
            )
            self._record_output_delivery(batch, stage="output_cancelled")
            self._resolve(batch.item, "cancelled")
        batch.finished = True

    def _cancel_queued_output_locked(
        self,
        batch: _OutputBatch,
        *,
        reason: str,
    ) -> None:
        if batch.finished:
            return
        batch.interruption_reason = reason
        batch.finished = True
        self._record_trace(
            batch.item,
            status=TraceResponseStatus.CANCELLED,
            accepted=False,
            reason=reason,
            validation_codes=("cancelled",),
            output_delivery=self._output_delivery(batch),
        )
        self._record_output_delivery(batch, stage="output_cancelled")
        self._resolve(batch.item, "cancelled")

    def _output_batch_is_current_locked(
        self,
        state: _OutputPaceState,
        batch: _OutputBatch,
    ) -> bool:
        return (
            self._output_states.get(state.session_id) is state
            and not state.stopped
            and state.active is batch
            and not batch.finished
            and batch.interruption_reason is None
            and self._output_item_is_current_locked(batch.item)
        )

    def _output_item_is_current_locked(self, item: _WorkItem) -> bool:
        return (
            item.generation == self._generation
            and self._active_session_id == item.request.session_id
            and item.superseded_reason is None
            and item.viewer.lifecycle_state is ViewerLifecycleState.ACTIVE
            and item.viewer.room_id == item.request.room_id
            and item.viewer.session_id == item.request.session_id
            and item.viewer.audience_epoch == item.request.audience_epoch
        )

    def _output_delivery(self, batch: _OutputBatch) -> ViewerOutputDelivery:
        published_at_ms = batch.published_at_ms
        return ViewerOutputDelivery(
            ready_at_ms=batch.ready_at_ms,
            scheduled_at_ms=batch.scheduled_at_ms,
            published_at_ms=published_at_ms,
            queue_delay_ms=(
                None
                if published_at_ms is None
                else max(0, published_at_ms - batch.scheduled_at_ms)
            ),
            event_count=len(batch.events),
            published_event_count=len(batch.published_events),
            interruption_reason=batch.interruption_reason,
        )

    @staticmethod
    def _published_barrage_ids(
        events: tuple[ViewerBarrageEvent, ...],
    ) -> tuple[str, ...]:
        return tuple(event.barrage_id for event in events)

    def _record_output_delivery(
        self,
        batch: _OutputBatch,
        *,
        stage: str,
    ) -> None:
        recorder = self._trace_recorder
        record = getattr(recorder, "record_viewer_output", None)
        if not callable(record):
            return
        try:
            record(
                batch.item.request.generation_request_id,
                self._output_delivery(batch),
                stage=stage,
            )
        except Exception as error:
            logger.warning(
                "viewer output delivery recording failed",
                extra={"error_type": type(error).__name__},
            )

    async def _record_behavior_published(
        self,
        request: ViewerGenerationRequest,
        event: object,
    ) -> None:
        if self._behavior_state_sink is None:
            return
        try:
            await self._behavior_state_sink.record_published(request, event)
        except Exception as error:
            logger.warning(
                "Viewer behavior state update failed after publish",
                extra={"error_type": type(error).__name__},
            )

    async def _record_behavior_silence(self, request: ViewerGenerationRequest) -> None:
        if self._behavior_state_sink is None:
            return
        try:
            await self._behavior_state_sink.record_silence(request)
        except Exception as error:
            logger.warning(
                "Viewer behavior state update failed after silence",
                extra={"error_type": type(error).__name__},
            )

    async def _commit_published_behavior(
        self,
        item: _WorkItem,
        events: tuple[ViewerBarrageEvent, ...],
    ) -> None:
        if not events or self._behavior_state_sink is None:
            return

        async def commit_once() -> None:
            async with self._lock:
                await self._record_behavior_published(item.request, events)

        async def commit_with_fence() -> None:
            execute = getattr(self._session_fence, "execute_if_accepting", None)
            if not callable(execute):
                await commit_once()
                return
            await execute(
                room_id=item.request.room_id,
                session_id=item.request.session_id,
                audience_epoch=item.request.audience_epoch,
                viewer_instance_id=item.request.viewer_instance_id,
                viewer_sequence=item.request.viewer_sequence,
                presence_revision=item.request.presence_revision,
                moderation_revision=item.request.moderation_revision,
                behavior_revision=None,
                operation=commit_once,
            )

        behavior = asyncio.create_task(
            commit_with_fence(),
            name=f"viewer-behavior:{item.request.generation_request_id}",
        )
        try:
            await asyncio.shield(behavior)
        except asyncio.CancelledError:
            await behavior

    async def _commit_silence(self, item: _WorkItem) -> bool:
        async def commit_once() -> bool:
            async with self._lock:
                if self._expired(item.request) or not self._is_current(item):
                    return False
                await self._record_behavior_silence(item.request)
                return True

        async def commit_with_fence() -> bool:
            execute = getattr(self._session_fence, "execute_if_accepting", None)
            if not callable(execute):
                return await commit_once()
            accepted, result = await execute(
                room_id=item.request.room_id,
                session_id=item.request.session_id,
                audience_epoch=item.request.audience_epoch,
                viewer_instance_id=item.request.viewer_instance_id,
                viewer_sequence=item.request.viewer_sequence,
                presence_revision=item.request.presence_revision,
                moderation_revision=item.request.moderation_revision,
                behavior_revision=None,
                operation=commit_once,
            )
            return accepted and result is True

        behavior = asyncio.create_task(
            commit_with_fence(),
            name=f"viewer-silence:{item.request.generation_request_id}",
        )
        try:
            return await asyncio.shield(behavior)
        except asyncio.CancelledError:
            return await behavior

    async def _deliver_realtime(
        self,
        item: _WorkItem,
        event: object,
    ) -> bool:
        delivery_failed = False
        delivery = asyncio.create_task(
            self._publisher.publish(event),
            name=f"viewer-realtime:{item.request.generation_request_id}",
        )
        try:
            await asyncio.shield(delivery)
        except asyncio.CancelledError:
            try:
                await delivery
            except Exception:
                delivery_failed = True
        except Exception:
            delivery_failed = True
        return delivery_failed

    async def _final_fence_outcome(self, item: _WorkItem) -> str | None:
        request = item.request
        if self._expired(request) or not self._is_current(item):
            return self._finalize_after_provider(item, phase="after_provider_completion")
        if not await self._session_fence.accepts(
            room_id=request.room_id,
            session_id=request.session_id,
            audience_epoch=request.audience_epoch,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            presence_revision=request.presence_revision,
            moderation_revision=request.moderation_revision,
            behavior_revision=None,
            deadline_at_ms=request.deadline_at_ms,
        ):
            self._record_trace(
                item,
                status=TraceResponseStatus.STALE,
                accepted=False,
                reason="session_fence_rejected",
                validation_codes=("session_fence_rejected",),
            )
            return "stale"
        if self._expired(request) or not self._is_current(item):
            return self._finalize_after_provider(item, phase="before_publish")
        return None

    async def _generate_with_retry(
        self,
        item: _WorkItem,
    ) -> object:
        request = item.request
        loop = asyncio.get_running_loop()
        monotonic_deadline = loop.time() + self._remaining_ttl_seconds(request)
        try:
            return await self._provider_attempt(
                item,
                timeout_seconds=self._remaining_attempt_seconds(
                    request,
                    monotonic_deadline,
                ),
            )
        except Exception as error:
            if isinstance(error, (_ViewerRequestExpired, _ViewerRequestSuperseded)):
                raise
            if not self._is_transient(error) or self._expired(request):
                raise
            backoff_seconds = self._retry_delay_seconds(error)
            minimum_attempt_seconds = 0.05
            remaining_seconds = self._remaining_attempt_seconds(
                request,
                monotonic_deadline,
            )
            if remaining_seconds < backoff_seconds + minimum_attempt_seconds:
                raise _ViewerRequestExpired("insufficient TTL for Viewer retry")
            await asyncio.sleep(backoff_seconds)
            if self._expired(request) or not self._is_current(item):
                raise _ViewerRequestExpired("viewer retry deadline or sequence expired")
            retry_budget = self._remaining_attempt_seconds(
                request,
                monotonic_deadline,
            )
            if retry_budget < minimum_attempt_seconds:
                raise _ViewerRequestExpired("insufficient TTL for Viewer retry")
            item.retry_count = 1
            return await self._provider_attempt(
                item,
                timeout_seconds=retry_budget,
            )

    @staticmethod
    def _retry_delay_seconds(error: Exception) -> float:
        retry_after_seconds = getattr(error, "retry_after_seconds", None)
        if (
            isinstance(retry_after_seconds, (int, float))
            and not isinstance(retry_after_seconds, bool)
            and math.isfinite(retry_after_seconds)
            and retry_after_seconds >= 0
        ):
            return float(retry_after_seconds)
        return 0.5

    async def _provider_attempt(
        self,
        item: _WorkItem,
        *,
        timeout_seconds: float,
    ) -> object:
        if timeout_seconds <= 0:
            raise _ViewerRequestExpired("Viewer request TTL expired")
        if not await self._wait_for_request_turn(item):
            raise _ViewerRequestSuperseded
        attempt = asyncio.create_task(self._provider.generate(item.request))
        if item.dispatched_at_ms is None:
            item.dispatched_at_ms = self._clock.now_ms()
        item.provider_task = attempt
        try:
            done, _ = await asyncio.wait({attempt}, timeout=timeout_seconds)
        except BaseException:
            attempt.cancel()
            attempt.add_done_callback(self._consume_task_result)
            raise
        finally:
            if item.provider_task is attempt:
                item.provider_task = None
        if attempt in done:
            return attempt.result()
        attempt.cancel()
        attempt.add_done_callback(self._consume_task_result)
        raise _ViewerRequestExpired("Viewer provider attempt exceeded remaining TTL")

    async def _wait_for_request_turn(self, item: _WorkItem) -> bool:
        return await self._wait_for_request_pace(
            session_id=item.request.session_id,
            interval_ms=self._request_start_interval_ms(item.runtime),
            deadline_at_ms=item.request.deadline_at_ms,
            invalidated=item.invalidated,
            is_current=lambda: self._is_current(item),
        )

    async def _wait_for_window_batch_request_turn(self, work: _WindowBatchWork) -> bool:
        runtime = work.items[0].runtime if work.items else None
        deadline_at_ms = min(
            (item.request.deadline_at_ms for item in work.items),
            default=UNBOUNDED_DEADLINE_AT_MS,
        )
        return await self._wait_for_request_pace(
            session_id=work.session_id,
            interval_ms=self._request_start_interval_ms(runtime),
            deadline_at_ms=deadline_at_ms,
            invalidated=work.cancelled,
            is_current=lambda: self._window_batch_is_current_locked(work),
        )

    async def _wait_for_request_pace(
        self,
        *,
        session_id: str,
        interval_ms: int,
        deadline_at_ms: int,
        invalidated: asyncio.Event,
        is_current: Callable[[], bool],
    ) -> bool:
        async with self._lock:
            if not is_current() or self._clock.now_ms() >= deadline_at_ms:
                return False
            state = self._request_pace_states.setdefault(
                session_id,
                _RequestPaceState(),
            )
        async with state.turn:
            while True:
                async with self._lock:
                    now_ms = self._clock.now_ms()
                    if (
                        self._request_pace_states.get(session_id) is not state
                        or state.stopped.is_set()
                        or invalidated.is_set()
                        or not is_current()
                        or now_ms >= deadline_at_ms
                    ):
                        return False
                    delay_ms = max(0, (state.next_start_at_ms or now_ms) - now_ms)
                    delay_ms = min(delay_ms, deadline_at_ms - now_ms)
                    if delay_ms == 0:
                        state.next_start_at_ms = now_ms + interval_ms
                        return True
                if not await self._sleep_while_request_is_current(
                    delay_ms / 1_000,
                    invalidated,
                    state.stopped,
                ):
                    return False

    async def _sleep_while_request_is_current(
        self,
        delay_seconds: float,
        *invalidations: asyncio.Event,
    ) -> bool:
        sleep = asyncio.create_task(self._sleep(delay_seconds))
        watchers = [asyncio.create_task(event.wait()) for event in invalidations]
        tasks = [sleep, *watchers]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            return sleep in done and not any(event.is_set() for event in invalidations)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _request_start_interval_ms(runtime: object | None) -> int:
        spec = getattr(runtime, "canonical_runtime_spec", runtime)
        settings = getattr(spec, "settings", None)
        interval_ms = getattr(settings, "viewer_request_start_interval_ms", None)
        if isinstance(interval_ms, int) and interval_ms >= 0:
            return interval_ms
        return 200

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        if not task.cancelled():
            task.exception()

    def _remaining_ttl_seconds(self, request: ViewerGenerationRequest) -> float:
        remaining_ms = request.deadline_at_ms - self._clock.now_ms()
        if remaining_ms <= 0:
            raise _ViewerRequestExpired("Viewer request TTL expired")
        return remaining_ms / 1000

    def _remaining_attempt_seconds(
        self,
        request: ViewerGenerationRequest,
        monotonic_deadline: float,
    ) -> float:
        return min(
            self._remaining_ttl_seconds(request),
            monotonic_deadline - asyncio.get_running_loop().time(),
        )

    def _finalize_pre_dispatch(self, item: _WorkItem) -> str:
        expired = self._expired(item.request)
        self._record_trace(
            item,
            status=(
                TraceResponseStatus.EXPIRED if expired else TraceResponseStatus.STALE
            ),
            accepted=False,
            reason="expired_before_dispatch" if expired else "stale_before_dispatch",
            validation_codes=("expired" if expired else "stale",),
        )
        return "expired" if expired else "stale"

    def _finalize_superseded(self, item: _WorkItem) -> str:
        self._record_trace(
            item,
            status=TraceResponseStatus.CANCELLED,
            accepted=False,
            reason=item.superseded_reason or "superseded_by_newer_request",
            validation_codes=("superseded",),
        )
        return "superseded"

    def _record_superseded(self, item: _WorkItem, *, reason: str) -> None:
        item.superseded_reason = reason
        item.invalidated.set()
        self._record_trace(
            item,
            status=TraceResponseStatus.CANCELLED,
            accepted=False,
            reason=reason,
            validation_codes=("superseded",),
        )
        self._resolve(item, "superseded")

    @staticmethod
    def _supersede_reason(new_priority: int, old_priority: int) -> str:
        return (
            "superseded_by_higher_priority_wave"
            if new_priority > old_priority
            else "superseded_by_newer_equal_priority_wave"
        )

    @staticmethod
    def _wave_priority(wave: ObservationWave) -> int:
        if any(
            trigger in {ObservationTrigger.USER_TEXT, ObservationTrigger.FINAL_VOICE}
            for trigger in wave.triggers
        ):
            return 3
        if (
            ObservationTrigger.SYSTEM_AUDIO in wave.triggers
            or ObservationTrigger.SCREEN_CHANGE in wave.triggers
        ):
            return 2
        return 1

    def _finalize_after_provider(self, item: _WorkItem, *, phase: str) -> str:
        if item.superseded_reason is not None:
            if item.dispatched_at_ms is not None:
                self._record_trace(
                    item,
                    status=TraceResponseStatus.STALE,
                    accepted=False,
                    reason=f"{item.superseded_reason}_after_provider",
                    validation_codes=("superseded", "stale"),
                )
                return "stale"
            return self._finalize_superseded(item)
        expired = self._expired(item.request)
        self._record_trace(
            item,
            status=(
                TraceResponseStatus.EXPIRED if expired else TraceResponseStatus.STALE
            ),
            accepted=False,
            reason=f"{'expired' if expired else 'stale'}_{phase}",
            validation_codes=("expired" if expired else "stale",),
        )
        return "expired" if expired else "stale"

    def _record_trace(
        self,
        item: _WorkItem,
        *,
        status: TraceResponseStatus,
        accepted: bool,
        reason: str | None = None,
        validation_codes: tuple[str, ...] = (),
        published_barrage_id: str | None = None,
        published_barrage_ids: tuple[str, ...] = (),
        output_delivery: ViewerOutputDelivery | None = None,
    ) -> None:
        if item.traced:
            return
        item.traced = True
        if self._trace_recorder is None:
            return
        try:
            self._trace_recorder.record(
                build_viewer_request_trace(
                    request=item.request,
                    viewer=item.viewer,
                    wave=item.wave,
                    decision=item.decision,
                    available_viewer_ids=item.available_viewer_ids,
                    runtime=item.runtime,
                    queued_at_ms=item.queued_at_ms,
                    dispatched_at_ms=item.dispatched_at_ms,
                    completed_at_ms=item.completed_at_ms,
                    response_status=status,
                    retry_count=item.retry_count,
                    accepted=accepted,
                    validation_codes=validation_codes,
                    stale_or_cancel_reason=reason,
                    published_barrage_id=published_barrage_id,
                    published_barrage_ids=published_barrage_ids,
                    output_delivery=output_delivery,
                )
            )
        except Exception as error:
            logger.warning(
                "viewer trace recording failed",
                extra={"error_type": type(error).__name__},
            )

    def _build_request(
        self,
        *,
        viewer: ViewerInstance,
        wave: ObservationWave,
        decision: CrowdDecision,
        runtime: object,
        sequence: int,
        active_viewer_ids: tuple[str, ...],
    ) -> ViewerGenerationRequest:
        context = self._request_context(runtime, wave)
        frame_bundle = wave.frame_bundle
        visual_mode = wave.visual_input_mode
        shared_summary = wave.shared_visual_summary
        memory = context.room_memory_slice or RoomMemorySlice(
            room_id=wave.room_id,
            memory_revision=0,
        )
        mode_context = {
            **context.mode_context,
            "_viewer_persona_id": viewer.persona_id,
            "_viewer_display_name": viewer.display_name,
        }
        persona = self._resolved_persona(runtime, viewer)
        assessment = getattr(runtime, "scene_assessment", None)
        if assessment is None:
            raise ValueError("Viewer runtime requires a SceneAssessment")
        return ViewerGenerationRequest(
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            generation_request_id=self._id_generator.new_id(),
            viewer_instance_id=viewer.viewer_instance_id,
            viewer_sequence=sequence,
            username=viewer.username,
            display_name=viewer.display_name,
            persona=persona,
            persona_revision=viewer.persona_revision,
            presence_revision=viewer.presence_revision,
            moderation_revision=viewer.moderation_revision,
            behavior_revision=viewer.behavior_revision,
            scene_assessment=assessment,
            active_viewer_ids=list(active_viewer_ids),
            instance_variant=viewer.variant,
            mode_context=mode_context,
            visual_input_mode=visual_mode,
            frame_bundle=(
                frame_bundle
                if visual_mode is ViewerVisualInputMode.DIRECT_FRAMES
                else None
            ),
            shared_visual_summary=shared_summary,
            input_event_ids=wave.trigger_event_ids,
            public_context_event_ids=context.public_context_event_ids,
            public_context=context.public_context,
            reply_context_event_ids=context.reply_context_event_ids,
            reply_context=context.reply_context,
            conversation_history_summary=context.conversation_history_summary,
            viewer_private_state=viewer.private_state,
            room_memory_slice=memory,
            deadline_at_ms=self._deadline(wave, runtime),
            trigger_context=ViewerRequestTriggerContext(
                triggers=list(wave.triggers),
                trigger_event_ids=list(wave.trigger_event_ids[-128:]),
                trigger_frame_ids=list(wave.trigger_frame_ids[-32:]),
                screen_change_score=(
                    wave.trigger_screen_change_score
                    if ObservationTrigger.SCREEN_CHANGE in wave.triggers
                    else None
                ),
                target_viewer_id=wave.target_viewer_id,
                target_persona_id=wave.target_persona_id,
                target_ambiguous=wave.target_ambiguous,
                selection_reason_codes=list(decision.reason_codes[:32]),
            ),
        )

    @staticmethod
    def _request_context(runtime: object, wave: ObservationWave) -> _RequestContext:
        mode_context = getattr(runtime, "mode_context", {})
        if not isinstance(mode_context, dict):
            mode_context = {}
        spec = getattr(runtime, "canonical_runtime_spec", runtime)
        if not mode_context:
            active_mode_id = getattr(spec, "active_mode_id", None)
            modes = getattr(spec, "modes", ())
            active_mode = next(
                (
                    mode
                    for mode in modes
                    if getattr(mode, "mode_id", None) == active_mode_id
                ),
                None,
            )
            if active_mode is not None:
                mode_context = active_mode.model_dump(mode="json")
        public_ids = getattr(runtime, "public_context_event_ids", wave.event_ids)
        if not isinstance(public_ids, (list, tuple)):
            public_ids = wave.event_ids
        memory = getattr(runtime, "room_memory_slice", None)
        if not isinstance(memory, RoomMemorySlice) or memory.room_id != wave.room_id:
            memory = None
        def public_event(event: object) -> ViewerPublicEvent:
            payload = getattr(event, "payload", {})
            viewer_id = payload.get("viewer_instance_id")
            display_name = payload.get("display_name")
            target_viewer_id = payload.get("target_viewer_id")
            return ViewerPublicEvent(
                event_id=event.event_id,
                sequence=event.sequence,
                source_type=event.source_type.value,
                source_id=event.source_id,
                text=event.text,
                viewer_instance_id=(viewer_id if isinstance(viewer_id, str) else None),
                display_name=(display_name if isinstance(display_name, str) else None),
                target_viewer_id=(
                    target_viewer_id if isinstance(target_viewer_id, str) else None
                ),
                occurred_at_ms=event.created_at_ms,
            )
        public_events = [public_event(event) for event in getattr(runtime, "public_context", ())]
        reply_events = [
            public_event(event) for event in getattr(runtime, "reply_context", ())[-32:]
        ]
        reply_ids = getattr(runtime, "reply_context_event_ids", ())
        if not isinstance(reply_ids, (list, tuple)):
            reply_ids = ()
        return _RequestContext(
            mode_context=mode_context,
            public_context_event_ids=list(public_ids),
            public_context=public_events,
            reply_context_event_ids=list(reply_ids)[-32:],
            reply_context=reply_events,
            conversation_history_summary=(
                getattr(runtime, "conversation_history_summary", None)
                if isinstance(getattr(runtime, "conversation_history_summary", None), str)
                else None
            ),
            room_memory_slice=memory,
        )

    @staticmethod
    def _resolved_persona(runtime: object, viewer: ViewerInstance) -> PersonaTemplate:
        spec = getattr(runtime, "canonical_runtime_spec", runtime)
        persona = next(
            (
                item
                for item in getattr(spec, "personas", ())
                if item.persona_id == viewer.persona_id
            ),
            None,
        )
        if not isinstance(persona, PersonaTemplate):
            raise ValueError("Viewer references an unavailable PersonaTemplate")
        active_mode_id = getattr(spec, "active_mode_id", None)
        mode = next(
            (
                item
                for item in getattr(spec, "modes", ())
                if item.mode_id == active_mode_id
            ),
            None,
        )
        override = (
            None
            if mode is None
            else mode.persona_overrides.get(viewer.persona_id)
        )
        if override is None:
            return persona
        return persona.model_copy(update=override.model_dump(exclude_none=True))

    @staticmethod
    def _deadline(wave: ObservationWave, runtime: object) -> int:
        spec = getattr(runtime, "canonical_runtime_spec", runtime)
        settings = getattr(spec, "settings", None)
        ttl_ms = getattr(settings, "viewer_request_ttl_ms", None)
        if isinstance(ttl_ms, int) and ttl_ms > 0:
            return min(wave.deadline_at_ms, wave.created_at_ms + ttl_ms)
        return UNBOUNDED_DEADLINE_AT_MS

    def _runtime_limits(
        self,
        *,
        runtime: object,
        session_id: str,
        audience_epoch: int,
    ) -> tuple[tuple[object, ...], int, int]:
        spec = getattr(runtime, "canonical_runtime_spec", runtime)
        settings = getattr(spec, "settings", None)
        max_in_flight = getattr(settings, "max_in_flight_viewer_requests", None)
        if not isinstance(max_in_flight, int) or max_in_flight < 1:
            max_in_flight = self._default_max_in_flight
        queue_capacity = getattr(settings, "viewer_queue_capacity", None)
        if not isinstance(queue_capacity, int) or queue_capacity < 1:
            queue_capacity = self._default_queue_capacity
        revision = getattr(spec, "config_revision", None)
        return (
            (
                session_id,
                audience_epoch,
                revision,
                max_in_flight,
                queue_capacity,
            ),
            max_in_flight,
            queue_capacity,
        )

    def _release_slot_locked(
        self,
        item: _WorkItem | _WindowBatchWork,
    ) -> None:
        lane = item.lane
        if lane is None or not item.slot_reserved:
            return
        item.slot_reserved = False
        lane.active -= 1
        self._promote_locked(lane)
        self._prune_lane_locked(lane)

    def _discard_item_locked(
        self,
        item: _WorkItem | _WindowBatchWork,
    ) -> None:
        lane = item.lane
        if lane is None:
            return
        if item.slot_reserved:
            self._release_slot_locked(item)
            return
        if not item.queued:
            return
        item.queued = False
        lane.queued -= 1
        try:
            lane.eligible.remove(item)
        except ValueError:
            pass
        self._prune_lane_locked(lane)

    @staticmethod
    def _promote_locked(lane: _RuntimeLane) -> None:
        while lane.active < lane.max_in_flight and lane.eligible:
            item = lane.eligible.popleft()
            if not item.queued:
                continue
            item.queued = False
            item.slot_reserved = True
            lane.queued -= 1
            lane.active += 1
            item.ready.set()

    def _prune_lane_locked(self, lane: _RuntimeLane) -> None:
        if lane.active or lane.queued:
            return
        for key, candidate in tuple(self._lanes.items()):
            if candidate is lane:
                self._lanes.pop(key, None)
                return

    def _valid_dispatch(self, wave: ObservationWave, decision: CrowdDecision) -> bool:
        return (
            self._active_session_id == wave.session_id
            and decision.room_id == wave.room_id
            and decision.session_id == wave.session_id
            and decision.audience_epoch == wave.audience_epoch
            and decision.observation_id == wave.observation_id
            and self._clock.now_ms() < wave.deadline_at_ms
            and self._clock.now_ms() < decision.expires_at_ms
        )

    @staticmethod
    def _viewer_matches(viewer: ViewerInstance, wave: ObservationWave) -> bool:
        return (
            viewer.room_id == wave.room_id
            and viewer.session_id == wave.session_id
            and viewer.audience_epoch == wave.audience_epoch
            and viewer.lifecycle_state is ViewerLifecycleState.ACTIVE
        )

    def _is_current(self, item: _WorkItem) -> bool:
        return (
            item.generation == self._generation
            and self._active_session_id == item.request.session_id
            and item.superseded_reason is None
            and item.viewer.lifecycle_state is ViewerLifecycleState.ACTIVE
            and item.viewer.room_id == item.request.room_id
            and item.viewer.session_id == item.request.session_id
            and item.viewer.audience_epoch == item.request.audience_epoch
            and (
                item.dispatched_at_ms is not None
                or self._wave_matches_current_fence(
                    session_id=item.request.session_id,
                    audience_epoch=item.request.audience_epoch,
                    wave_generation=item.wave_generation,
                    observation_id=item.request.observation_id,
                )
            )
        )

    def _wave_matches_current_fence(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        wave_generation: int,
        observation_id: str,
    ) -> bool:
        wave_fence = self._wave_fences.get((session_id, audience_epoch))
        return (
            wave_fence is not None
            and wave_fence[0] == wave_generation
            and wave_fence[2] == observation_id
        )

    def _wave_fence_rejection_reason(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        priority: int,
    ) -> str:
        wave_fence = self._wave_fences.get((session_id, audience_epoch))
        if wave_fence is None:
            return "wave_fence_rejected"
        return self._supersede_reason(wave_fence[1], priority)

    def _expired(self, request: ViewerGenerationRequest) -> bool:
        return self._clock.now_ms() >= request.deadline_at_ms

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        if getattr(error, "retryable", False) is True:
            return True
        if isinstance(error, (ConnectionError, TimeoutError, OSError)):
            return True
        status_code = getattr(error, "status_code", None)
        return status_code == 429 or (
            isinstance(status_code, int) and 500 <= status_code <= 599
        )

    @staticmethod
    def _resolve(item: _WorkItem, outcome: str) -> None:
        if not item.future.done():
            item.future.set_result(
                _DispatchResult(
                    outcome=outcome,
                    queued=int(item.was_queued),
                    dispatched=int(item.dispatched_at_ms is not None),
                    completed=int(item.completed_at_ms is not None),
                    retry=item.retry_count,
                )
            )
