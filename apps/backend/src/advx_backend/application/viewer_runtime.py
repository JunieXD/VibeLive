import asyncio
import logging
import math
import unicodedata
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
    ViewerPublicEvent,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import ObservationWave, ViewerVisualInputMode
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
    ready: asyncio.Event = field(default_factory=asyncio.Event)


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
    eligible: deque[_WorkItem] = field(default_factory=deque)


@dataclass(slots=True)
class _RequestContext:
    mode_context: dict[str, Any] = field(default_factory=dict)
    public_context_event_ids: list[str] = field(default_factory=list)
    public_context: list[ViewerPublicEvent] = field(default_factory=list)
    room_memory_slice: RoomMemorySlice | None = None


class _ViewerRequestExpired(TimeoutError):
    pass


class ViewerBehaviorStateSink(Protocol):
    async def record_published(self, request: ViewerGenerationRequest, event: object) -> None: ...

    async def record_silence(self, request: ViewerGenerationRequest) -> None: ...


class ViewerRuntime:
    """Dispatch independent Viewer requests through bounded, latest-wins mailboxes."""

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
        self._sequences: dict[str, int] = {}
        self._sequence_epochs: dict[str, int] = {}
        self._semantic_outputs: dict[
            tuple[str, int, str],
            dict[str, tuple[int, str]],
        ] = {}
        self._lock = asyncio.Lock()
        self._semantic_lock = asyncio.Lock()
        self._active_session_id: str | None = None
        self._generation = 0

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
            self._lanes.clear()
            self._sequences.clear()
            self._sequence_epochs.clear()
        async with self._semantic_lock:
            for scope in tuple(self._semantic_outputs):
                if scope[0] == session_id:
                    self._semantic_outputs.pop(scope, None)
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
            queued_current = (
                mailbox.current
                if mailbox_busy
                and mailbox.current is not None
                and mailbox.current.queued
                else None
            )
            replacement = mailbox.pending or queued_current
            replacing_pending = mailbox_busy and replacement is not None
            inherits_queue_slot = (
                replacing_pending
                and replacement is not None
                and replacement.lane is lane
                and replacement.queued
            )
            needs_queue = mailbox_busy or lane.active >= lane.max_in_flight
            if (
                needs_queue
                and not inherits_queue_slot
                and lane.queued >= lane.queue_capacity
            ):
                self._record_trace(
                    item,
                    status=TraceResponseStatus.CANCELLED,
                    accepted=False,
                    reason="viewer_queue_capacity_exceeded",
                    validation_codes=("queue_capacity_exceeded",),
                )
                self._resolve(item, "cancelled")
                return future
            if not await self._claim_sequence(request):
                self._record_trace(
                    item,
                    status=TraceResponseStatus.STALE,
                    accepted=False,
                    reason="viewer_sequence_claim_rejected",
                    validation_codes=("viewer_sequence_claim_rejected",),
                )
                self._resolve(item, "stale")
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
                if replacement is not None:
                    previous_pending = replacement
                    self._record_trace(
                        previous_pending,
                        status=TraceResponseStatus.CANCELLED,
                        accepted=False,
                        reason="superseded_by_newer_request",
                        validation_codes=("superseded",),
                    )
                    self._resolve(previous_pending, "superseded")
                    if queued_current is previous_pending:
                        if inherits_queue_slot:
                            position = lane.eligible.index(previous_pending)
                            lane.eligible[position] = item
                        else:
                            self._discard_item_locked(previous_pending)
                            lane.queued += 1
                            lane.eligible.append(item)
                        previous_pending.queued = False
                        previous_pending.replacement = item
                        previous_pending.ready.set()
                        item.queued = True
                        item.was_queued = True
                        mailbox.current = item
                        self._promote_locked(lane)
                        return future
                    if inherits_queue_slot:
                        previous_pending.queued = False
                    else:
                        self._discard_item_locked(previous_pending)
                if not inherits_queue_slot:
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

    async def _run_mailbox(self, viewer_id: str, item: _WorkItem) -> None:
        current: _WorkItem | None = item
        try:
            while current is not None:
                while True:
                    while current.replacement is not None:
                        current = current.replacement
                    if current.slot_reserved:
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
                if mailbox is not None and mailbox.pending is not None:
                    self._record_trace(
                        mailbox.pending,
                        status=TraceResponseStatus.CANCELLED,
                        accepted=False,
                        reason="session_stopped",
                        validation_codes=("cancelled",),
                    )
                    self._discard_item_locked(mailbox.pending)
                    self._resolve(mailbox.pending, "cancelled")
            raise

    async def _execute(self, item: _WorkItem) -> str:
        request = item.request
        if not self._is_current(item) or self._expired(request):
            return self._finalize_pre_dispatch(item)
        try:
            if not self._is_current(item) or self._expired(request):
                return self._finalize_pre_dispatch(item)
            item.dispatched_at_ms = self._clock.now_ms()
            response = await self._generate_with_retry(item)
            item.completed_at_ms = self._clock.now_ms()
        except asyncio.CancelledError:
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
            self._record_trace(
                item,
                status=TraceResponseStatus.SILENCE,
                accepted=True,
            )
            await self._record_behavior_silence(request)
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
            semantic_text=response.text or "",
        )
        if outcome == "duplicate":
            self._record_trace(
                item,
                status=TraceResponseStatus.REJECTED,
                accepted=False,
                reason="semantic_duplicate",
                validation_codes=("semantic_duplicate",),
            )
            return "rejected"
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
        await self._record_behavior_published(request, validation.event)
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
        *,
        semantic_text: str,
    ) -> tuple[str, bool]:
        async def commit_once() -> tuple[str, bool]:
            if self._expired(item.request) or not self._is_current(item):
                return "stale", False
            if not await self._claim_semantic_output(item, semantic_text):
                return "duplicate", False
            try:
                await self._room_service.append_published_barrage(event)
            except Exception:
                await self._release_semantic_output(item, semantic_text)
                return "failed", False
            return "published", await self._deliver_realtime(item, event)

        async def commit_to_completion() -> tuple[str, bool]:
            commit = asyncio.create_task(
                commit_once(),
                name=f"viewer-effect:{item.request.generation_request_id}",
            )
            try:
                return await asyncio.shield(commit)
            except asyncio.CancelledError:
                return await commit

        execute = getattr(self._session_fence, "execute_if_accepting", None)
        if not callable(execute):
            return await commit_to_completion()
        accepted, result = await execute(
            room_id=item.request.room_id,
            session_id=item.request.session_id,
            audience_epoch=item.request.audience_epoch,
            viewer_instance_id=item.request.viewer_instance_id,
            viewer_sequence=item.request.viewer_sequence,
            presence_revision=item.request.presence_revision,
            moderation_revision=item.request.moderation_revision,
            behavior_revision=item.request.behavior_revision,
            operation=commit_to_completion,
        )
        if not accepted or result is None:
            return "stale", False
        return result

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
            behavior_revision=request.behavior_revision,
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
                request,
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
                request,
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
        request: ViewerGenerationRequest,
        *,
        timeout_seconds: float,
    ) -> object:
        if timeout_seconds <= 0:
            raise _ViewerRequestExpired("Viewer request TTL expired")
        attempt = asyncio.create_task(self._provider.generate(request))
        try:
            done, _ = await asyncio.wait({attempt}, timeout=timeout_seconds)
        except BaseException:
            attempt.cancel()
            attempt.add_done_callback(self._consume_task_result)
            raise
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

    async def _claim_semantic_output(
        self,
        item: _WorkItem,
        text: str,
    ) -> bool:
        semantic_key = self._semantic_key(text)
        scope = (
            item.request.session_id,
            item.request.audience_epoch,
            item.request.observation_id,
        )
        now_ms = self._clock.now_ms()
        async with self._semantic_lock:
            for candidate_scope, entries in tuple(self._semantic_outputs.items()):
                for key, (expires_at_ms, _) in tuple(entries.items()):
                    if expires_at_ms <= now_ms:
                        entries.pop(key, None)
                if not entries:
                    self._semantic_outputs.pop(candidate_scope, None)
            entries = self._semantic_outputs.setdefault(scope, {})
            if semantic_key in entries:
                return False
            entries[semantic_key] = (
                item.request.deadline_at_ms,
                item.request.generation_request_id,
            )
            return True

    async def _release_semantic_output(
        self,
        item: _WorkItem,
        text: str,
    ) -> None:
        scope = (
            item.request.session_id,
            item.request.audience_epoch,
            item.request.observation_id,
        )
        semantic_key = self._semantic_key(text)
        async with self._semantic_lock:
            entries = self._semantic_outputs.get(scope)
            if (
                entries is not None
                and entries.get(semantic_key, (None, None))[1]
                == item.request.generation_request_id
            ):
                entries.pop(semantic_key, None)
                if not entries:
                    self._semantic_outputs.pop(scope, None)

    @staticmethod
    def _semantic_key(text: str) -> str:
        folded = text.casefold()
        semantic = "".join(
            character
            for character in folded
            if unicodedata.category(character)[0] in {"L", "N"}
        )
        return semantic or " ".join(folded.split())

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

    def _finalize_after_provider(self, item: _WorkItem, *, phase: str) -> str:
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
        if self._trace_recorder is None or item.traced:
            return
        item.traced = True
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
        public_events: list[ViewerPublicEvent] = []
        for event in getattr(runtime, "public_context", ()):
            payload = getattr(event, "payload", {})
            viewer_id = payload.get("viewer_instance_id")
            display_name = payload.get("display_name")
            target_viewer_id = payload.get("target_viewer_id")
            public_events.append(
                ViewerPublicEvent(
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
            )
        return _RequestContext(
            mode_context=mode_context,
            public_context_event_ids=list(public_ids),
            public_context=public_events,
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

    def _release_slot_locked(self, item: _WorkItem) -> None:
        lane = item.lane
        if lane is None or not item.slot_reserved:
            return
        item.slot_reserved = False
        lane.active -= 1
        self._promote_locked(lane)
        self._prune_lane_locked(lane)

    def _discard_item_locked(self, item: _WorkItem) -> None:
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
            and self._sequences.get(item.request.viewer_instance_id)
            == item.request.viewer_sequence
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
