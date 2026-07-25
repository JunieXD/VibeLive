import asyncio
import logging
import math
from collections import deque
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
from advx_backend.contracts.debug import ObservationWaveStatus, TraceResponseStatus
from advx_backend.contracts.viewer_runtime import (
    ViewerAction,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
    ViewerPublicEvent,
    WindowBatchGenerationRequest,
    WindowBatchGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import (
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
    ready: asyncio.Event = field(default_factory=asyncio.Event)


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
    superseded_reason: str | None = None
    provider_task: asyncio.Task[object] | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    finished: bool = False


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
        self._lanes: dict[tuple[object, ...], _RuntimeLane] = {}
        self._mailboxes: dict[str, _ViewerMailbox] = {}
        self._window_batches: dict[int, _WindowBatchWork] = {}
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
            if mailbox is None:
                return
            items = tuple(
                {
                    id(item): item
                    for item in (mailbox.current, mailbox.pending)
                    if item is not None
                }.values()
            )
            for item in items:
                self._record_trace(
                    item,
                    status=TraceResponseStatus.CANCELLED,
                    accepted=False,
                    reason=reason,
                    validation_codes=("cancelled",),
                )
                self._discard_item_locked(item)
                self._resolve(item, "cancelled")
            task = mailbox.task
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
        results = await asyncio.gather(*futures)
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
            if await self._claim_sequence(item.request):
                async with self._lock:
                    viewer_id = item.request.viewer_instance_id
                    current_epoch = self._sequence_epochs.get(viewer_id)
                    current_sequence = self._sequences.get(viewer_id, 0)
                    if (
                        current_epoch != item.request.audience_epoch
                        or item.request.viewer_sequence > current_sequence
                    ):
                        self._sequences[viewer_id] = item.request.viewer_sequence
                        self._sequence_epochs[viewer_id] = item.request.audience_epoch
                item.dispatched_at_ms = self._clock.now_ms()
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
        seen_text: set[str] = set()
        for candidate in response.candidates:
            item = by_viewer.get(candidate.viewer_instance_id)
            normalized_text = (candidate.text or "").strip().casefold()
            if (
                item is None
                or candidate.viewer_instance_id in candidates
                or candidate.generation_request_id != item.request.generation_request_id
                or candidate.viewer_sequence != item.request.viewer_sequence
                or not normalized_text
                or normalized_text in seen_text
            ):
                continue
            seen_text.add(normalized_text)
            candidates[candidate.viewer_instance_id] = (item, candidate)

        outcomes: list[_DispatchResult] = []
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
            outcomes.append(
                _DispatchResult(
                    outcome=outcome,
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
                work.superseded_reason = "window_batch_wave_superseded"
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
        attempt = asyncio.create_task(
            self._provider.generate_window_batch(request),
            name=f"viewer-window-batch:{request.batch_generation_request_id}",
        )
        cancelled = asyncio.create_task(work.cancelled.wait())
        work.provider_task = attempt
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
        wave_fence = self._wave_fences.get((work.session_id, work.audience_epoch))
        return (
            not work.finished
            and work.superseded_reason is None
            and work.generation in (0, self._generation)
            and self._active_session_id == work.session_id
            and wave_fence is not None
            and wave_fence[0] == work.wave_generation
            and wave_fence[2] == work.observation_id
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
        if validation.event is None:
            return "silenced"
        fenced = await self._final_fence_outcome(item)
        if fenced is not None:
            return fenced
        outcome, delivery_failed = await self._commit_published_event(
            item,
            validation.event,
        )
        if outcome != "published":
            return outcome
        self._record_trace(
            item,
            status=TraceResponseStatus.PUBLISHED,
            accepted=True,
            validation_codes=(("realtime_delivery_failed",) if delivery_failed else ()),
            published_barrage_id=getattr(validation.event, "barrage_id", None),
        )
        return "published"

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
            self._sequences[viewer.viewer_instance_id] = sequence
            self._sequence_epochs[viewer.viewer_instance_id] = viewer.audience_epoch
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
                ):
                    active.superseded_reason = self._supersede_reason(
                        priority,
                        active.priority,
                    )
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
                    work.superseded_reason = self._supersede_reason(
                        priority,
                        work.priority,
                    )
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
                outcome = await self._execute(current)
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
            if current is not None:
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
            if not await self._claim_sequence(request):
                self._record_trace(
                    item,
                    status=TraceResponseStatus.STALE,
                    accepted=False,
                    reason="viewer_sequence_claim_rejected",
                    validation_codes=("viewer_sequence_claim_rejected",),
                )
                return "stale"
            item.dispatched_at_ms = self._clock.now_ms()
            response = await self._generate_with_retry(item)
            item.completed_at_ms = self._clock.now_ms()
        except asyncio.CancelledError:
            if item.superseded_reason is not None:
                item.completed_at_ms = self._clock.now_ms()
                return self._finalize_superseded(item)
            raise
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
        if validation.event is None:
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

        outcome, delivery_failed = await self._commit_published_event(
            item,
            validation.event,
        )
        if outcome == "stale":
            return self._finalize_after_provider(item, phase="publish_commit")
        if outcome == "failed":
            self._record_trace(
                item,
                status=TraceResponseStatus.FAILED,
                accepted=False,
                reason="publish_side_effect_failed",
                validation_codes=("publish_side_effect_failed",),
                published_barrage_id=getattr(validation.event, "barrage_id", None),
            )
            return "failed"
        if delivery_failed:
            logger.warning(
                "durable Viewer barrage could not be delivered in realtime",
                extra={
                    "session_id": request.session_id,
                    "generation_request_id": request.generation_request_id,
                },
            )
        self._record_trace(
            item,
            status=TraceResponseStatus.PUBLISHED,
            accepted=True,
            validation_codes=(
                ("realtime_delivery_failed",)
                if delivery_failed
                else ()
            ),
            published_barrage_id=getattr(validation.event, "barrage_id", None),
        )
        return "published"

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

    async def _commit_published_event(
        self,
        item: _WorkItem,
        event: object,
    ) -> tuple[str, bool]:
        async def commit_once() -> str:
            async with self._lock:
                if self._expired(item.request) or not self._is_current(item):
                    return "stale"
                try:
                    await self._room_service.append_published_barrage(event)
                except Exception:
                    return "failed"
                await self._record_behavior_published(item.request, event)
                return "published"

        async def commit_with_fence() -> str:
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
            if not accepted or result is None:
                return "stale"
            return result

        # The task that owns the session fence must perform the behavior update.
        # Otherwise a child task can wait forever to re-enter its parent's fence.
        commit = asyncio.create_task(
            commit_with_fence(),
            name=f"viewer-effect:{item.request.generation_request_id}",
        )
        try:
            outcome = await asyncio.shield(commit)
        except asyncio.CancelledError:
            outcome = await commit
        if outcome != "published":
            return outcome, False
        return "published", await self._deliver_realtime(item, event)

    async def _commit_silence(self, item: _WorkItem) -> bool:
        async with self._lock:
            if self._expired(item.request) or not self._is_current(item):
                return False
            await self._record_behavior_silence(item.request)
            return True

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
            if isinstance(error, _ViewerRequestExpired):
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
        attempt = asyncio.create_task(self._provider.generate(item.request))
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
        return wave.deadline_at_ms

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
        wave_fence = self._wave_fences.get(
            (item.request.session_id, item.request.audience_epoch)
        )
        return (
            item.generation == self._generation
            and self._active_session_id == item.request.session_id
            and wave_fence is not None
            and wave_fence[0] == item.wave_generation
            and wave_fence[2] == item.request.observation_id
            and item.viewer.lifecycle_state is ViewerLifecycleState.ACTIVE
            and item.viewer.room_id == item.request.room_id
            and item.viewer.session_id == item.request.session_id
            and item.viewer.audience_epoch == item.request.audience_epoch
        )

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
