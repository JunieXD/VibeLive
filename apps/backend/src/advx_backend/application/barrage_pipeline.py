from collections import OrderedDict, deque
from dataclasses import dataclass, field

from advx_backend.application.ports.session import Clock, IdGenerator
from advx_backend.contracts.generation import GenerationRequest, GenerationResult
from advx_backend.domain.barrage import (
    BarrageEvent,
    BarragePolicy,
    BarrageRejection,
    BarrageRejectionReason,
    BarrageValidationResult,
    BarrageValidationScope,
)


@dataclass(slots=True)
class _SessionState:
    recent_texts: OrderedDict[str, int] = field(default_factory=OrderedDict)
    output_times: deque[int] = field(default_factory=deque)


class BarragePipeline:
    """Turn a model result into trusted barrage events using bounded local state."""

    def __init__(
        self,
        *,
        policy: BarragePolicy,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._id_generator = id_generator
        self._session_states: OrderedDict[str, _SessionState] = OrderedDict()

    def process(
        self,
        *,
        scope: BarrageValidationScope,
        request: GenerationRequest,
        result: GenerationResult,
    ) -> BarrageValidationResult:
        now_ms = self._clock.now_ms()
        batch_rejection = self._validate_batch(scope, request, result, now_ms)
        if batch_rejection is not None:
            return self._reject_batch(result, batch_rejection)

        allowed_audience_ids = {context.member.audience_id for context in request.audiences}
        expires_at_ms = request.observation.created_at_ms + self._policy.ttl_ms
        events: list[BarrageEvent] = []
        rejections: list[BarrageRejection] = []

        for index, candidate in enumerate(result.candidates):
            audience_id = candidate.audience_id
            safe_audience_id = audience_id if isinstance(audience_id, str) else None
            if safe_audience_id is None or safe_audience_id not in allowed_audience_ids:
                rejections.append(
                    self._rejection(
                        index,
                        safe_audience_id,
                        BarrageRejectionReason.AUDIENCE_NOT_IN_REQUEST,
                    )
                )
                continue

            if not isinstance(candidate.text, str):
                rejections.append(
                    self._rejection(
                        index,
                        safe_audience_id,
                        BarrageRejectionReason.INVALID_TEXT,
                    )
                )
                continue

            text = candidate.text.strip()
            content_rejection = self._validate_text(text)
            if content_rejection is not None:
                rejections.append(self._rejection(index, safe_audience_id, content_rejection))
                continue

            state = self._session_states.get(scope.session_id)
            if state is not None:
                self._prune_state(state, now_ms)
                if text in state.recent_texts:
                    rejections.append(
                        self._rejection(
                            index,
                            safe_audience_id,
                            BarrageRejectionReason.DUPLICATE,
                        )
                    )
                    continue
                if len(state.output_times) >= self._policy.max_outputs_per_density_window:
                    rejections.append(
                        self._rejection(
                            index,
                            safe_audience_id,
                            BarrageRejectionReason.DENSITY_LIMIT_EXCEEDED,
                        )
                    )
                    continue
            elif self._policy.max_outputs_per_density_window == 0:
                rejections.append(
                    self._rejection(
                        index,
                        safe_audience_id,
                        BarrageRejectionReason.DENSITY_LIMIT_EXCEEDED,
                    )
                )
                continue

            event = BarrageEvent(
                barrage_id=self._id_generator.new_id(),
                session_id=scope.session_id,
                observation_id=scope.observation_id,
                request_id=scope.request_id,
                audience_id=safe_audience_id,
                text=text,
                created_at_ms=now_ms,
                expires_at_ms=expires_at_ms,
            )
            state = self._state_for_acceptance(scope.session_id)
            self._record_acceptance(state, text, now_ms)
            events.append(event)

        return BarrageValidationResult(
            events=tuple(events),
            rejections=tuple(rejections),
        )

    def clear_session(self, session_id: str) -> None:
        """Release duplicate and density history when a session stops."""
        self._session_states.pop(session_id, None)

    def _validate_batch(
        self,
        scope: BarrageValidationScope,
        request: GenerationRequest,
        result: GenerationResult,
        now_ms: int,
    ) -> BarrageRejectionReason | None:
        if request.observation.session_id != scope.session_id:
            return BarrageRejectionReason.SESSION_MISMATCH
        if request.observation.observation_id != scope.observation_id:
            return BarrageRejectionReason.OBSERVATION_MISMATCH
        if request.request_id != scope.request_id or result.request_id != scope.request_id:
            return BarrageRejectionReason.REQUEST_MISMATCH
        if request.observation.created_at_ms > now_ms:
            return BarrageRejectionReason.OBSERVATION_IN_FUTURE
        if now_ms >= request.observation.created_at_ms + self._policy.ttl_ms:
            return BarrageRejectionReason.EXPIRED
        return None

    def _validate_text(self, text: str) -> BarrageRejectionReason | None:
        if not text:
            return BarrageRejectionReason.EMPTY_TEXT
        if len(text) > self._policy.max_text_length:
            return BarrageRejectionReason.TEXT_TOO_LONG

        folded_text = text.casefold()
        if any(word in folded_text for word in self._policy.blocked_words):
            return BarrageRejectionReason.BLOCKED_WORD
        return None

    def _prune_state(self, state: _SessionState, now_ms: int) -> None:
        duplicate_cutoff = now_ms - self._policy.duplicate_window_ms
        while state.recent_texts:
            _, accepted_at_ms = next(iter(state.recent_texts.items()))
            if accepted_at_ms > duplicate_cutoff:
                break
            state.recent_texts.popitem(last=False)

        density_cutoff = now_ms - self._policy.density_window_ms
        while state.output_times and state.output_times[0] <= density_cutoff:
            state.output_times.popleft()

    def _state_for_acceptance(self, session_id: str) -> _SessionState:
        state = self._session_states.pop(session_id, None)
        if state is None:
            if len(self._session_states) >= self._policy.max_tracked_sessions:
                self._session_states.popitem(last=False)
            state = _SessionState()
        self._session_states[session_id] = state
        return state

    def _record_acceptance(self, state: _SessionState, text: str, now_ms: int) -> None:
        state.recent_texts[text] = now_ms
        while len(state.recent_texts) > self._policy.max_duplicate_entries_per_session:
            state.recent_texts.popitem(last=False)
        state.output_times.append(now_ms)

    @staticmethod
    def _rejection(
        candidate_index: int,
        audience_id: str | None,
        reason: BarrageRejectionReason,
    ) -> BarrageRejection:
        return BarrageRejection(
            reason=reason,
            candidate_index=candidate_index,
            audience_id=audience_id,
        )

    def _reject_batch(
        self,
        result: GenerationResult,
        reason: BarrageRejectionReason,
    ) -> BarrageValidationResult:
        rejections = tuple(
            self._rejection(
                index,
                candidate.audience_id if isinstance(candidate.audience_id, str) else None,
                reason,
            )
            for index, candidate in enumerate(result.candidates)
        )
        return BarrageValidationResult(
            events=(),
            rejections=rejections,
            batch_rejection_reason=reason,
        )
