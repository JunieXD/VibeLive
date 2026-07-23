"""Deterministic default policies used by :class:`GenerationService`.

The policies in this module deliberately make no provider, transport, or
session-lifecycle decisions.  They only decide whether a bounded observation
is worth reacting to, which known audience members may participate, and how
those members are grouped for provider calls.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from advx_backend.application.ports.generation import AudienceBatch, AudienceSnapshot
from advx_backend.application.ports.session import Clock
from advx_backend.contracts.events import RoomEvent, RoomEventSource
from advx_backend.contracts.generation import AudienceContext, Observation

__all__ = [
    "AudienceSelectorConfig",
    "DefaultAudienceSelector",
    "DefaultGenerationInvocationPlanner",
    "DefaultGenerationTrigger",
    "GenerationInvocationPlannerConfig",
    "GenerationTriggerConfig",
]


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_bool(name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class GenerationTriggerConfig:
    """Bounds and rules for :class:`DefaultGenerationTrigger`.

    ``user_voice`` events are final transcripts by the existing Room event
    contract.  ``require_final_voice_marker`` can be enabled for integrations
    which also place partial transcripts in an observation; an explicit
    ``final=False`` is always treated as partial.
    """

    observation_ttl_ms: int = 30_000
    user_input_cooldown_ms: int = 0
    screen_cooldown_ms: int = 5_000
    audience_barrage_cooldown_ms: int = 30_000
    max_consecutive_audience_barrage_triggers: int = 0
    screen_triggers_enabled: bool = True
    trigger_on_new_frames: bool = True
    trigger_on_screen_events: bool = True
    minimum_new_frames: int = 1
    require_final_voice_marker: bool = False
    final_voice_payload_key: str = "final"
    max_tracked_sessions: int = 32
    trigger_state_ttl_ms: int = 120_000

    def __post_init__(self) -> None:
        _require_positive_int("observation_ttl_ms", self.observation_ttl_ms)
        _require_non_negative_int("user_input_cooldown_ms", self.user_input_cooldown_ms)
        _require_non_negative_int("screen_cooldown_ms", self.screen_cooldown_ms)
        _require_non_negative_int(
            "audience_barrage_cooldown_ms",
            self.audience_barrage_cooldown_ms,
        )
        _require_non_negative_int(
            "max_consecutive_audience_barrage_triggers",
            self.max_consecutive_audience_barrage_triggers,
        )
        _require_bool("screen_triggers_enabled", self.screen_triggers_enabled)
        _require_bool("trigger_on_new_frames", self.trigger_on_new_frames)
        _require_bool("trigger_on_screen_events", self.trigger_on_screen_events)
        _require_positive_int("minimum_new_frames", self.minimum_new_frames)
        _require_bool("require_final_voice_marker", self.require_final_voice_marker)
        if not self.final_voice_payload_key:
            raise ValueError("final_voice_payload_key must not be empty")
        _require_positive_int("max_tracked_sessions", self.max_tracked_sessions)
        _require_positive_int("trigger_state_ttl_ms", self.trigger_state_ttl_ms)


@dataclass(frozen=True, slots=True)
class AudienceSelectorConfig:
    """Upper bound for a deterministic audience selection."""

    max_candidates: int = 4

    def __post_init__(self) -> None:
        _require_positive_int("max_candidates", self.max_candidates)


@dataclass(frozen=True, slots=True)
class GenerationInvocationPlannerConfig:
    """Maximum number of audience members in one provider invocation."""

    batch_size: int = 2

    def __post_init__(self) -> None:
        _require_positive_int("batch_size", self.batch_size)


@dataclass(slots=True)
class _TriggerState:
    last_accessed_at_ms: int
    last_user_event_id: str | None = None
    last_screen_event_id: str | None = None
    last_barrage_event_id: str | None = None
    last_frame_id: str | None = None
    last_user_trigger_at_ms: int | None = None
    last_screen_trigger_at_ms: int | None = None
    last_barrage_trigger_at_ms: int | None = None
    consecutive_barrage_triggers: int = 0


class DefaultGenerationTrigger:
    """Trigger user input first, then configured screen signals.

    The trigger keeps only a small, TTL-bound watermark per session.  A room
    event or frame retained in later observations therefore cannot repeatedly
    trigger generation.  Audience barrage events are disabled by default; if
    explicitly enabled, their consecutive-trigger cap prevents a model output
    from sustaining its own reaction loop.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        config: GenerationTriggerConfig | None = None,
    ) -> None:
        self._clock = clock
        self._config = GenerationTriggerConfig() if config is None else config
        self._states: OrderedDict[str, _TriggerState] = OrderedDict()

    async def should_generate(self, *, observation: Observation) -> bool:
        now_ms = self._clock.now_ms()
        self._discard_expired_states(now_ms)

        if self._is_expired(observation, now_ms):
            return False
        if not observation.frames and not observation.room_events:
            return False

        state = self._state_for(observation.session_id, now_ms)
        if state is None:
            return False

        user_event = self._latest_event(observation.room_events, self._is_user_input)
        screen_event = self._latest_event(
            observation.room_events,
            lambda event: event.source_type == RoomEventSource.SCREEN_OBSERVATION,
        )
        barrage_event = self._latest_event(
            observation.room_events,
            lambda event: (
                event.source_type == RoomEventSource.AUDIENCE_BARRAGE and self._has_text(event)
            ),
        )
        new_frame_count, latest_frame_id = self._new_frame_count(
            observation,
            state.last_frame_id,
        )

        has_new_user_input = self._is_new_event(user_event, state.last_user_event_id)
        has_new_screen_event = self._is_new_event(screen_event, state.last_screen_event_id)
        has_new_barrage = self._is_new_event(barrage_event, state.last_barrage_event_id)

        if user_event is not None:
            state.last_user_event_id = user_event.event_id
        if screen_event is not None:
            state.last_screen_event_id = screen_event.event_id
        if barrage_event is not None:
            state.last_barrage_event_id = barrage_event.event_id
        if latest_frame_id is not None:
            state.last_frame_id = latest_frame_id

        if has_new_user_input and self._cooldown_elapsed(
            state.last_user_trigger_at_ms,
            observation.created_at_ms,
            self._config.user_input_cooldown_ms,
        ):
            state.last_user_trigger_at_ms = observation.created_at_ms
            state.consecutive_barrage_triggers = 0
            return True

        has_screen_signal = self._has_screen_signal(
            has_new_screen_event=has_new_screen_event,
            new_frame_count=new_frame_count,
        )
        if has_screen_signal and self._cooldown_elapsed(
            state.last_screen_trigger_at_ms,
            observation.created_at_ms,
            self._config.screen_cooldown_ms,
        ):
            state.last_screen_trigger_at_ms = observation.created_at_ms
            state.consecutive_barrage_triggers = 0
            return True

        if self._should_trigger_from_barrage(
            has_new_barrage=has_new_barrage,
            state=state,
            observation_created_at_ms=observation.created_at_ms,
        ):
            return True

        return False

    def _is_expired(self, observation: Observation, now_ms: int) -> bool:
        return now_ms >= observation.created_at_ms + self._config.observation_ttl_ms

    def _discard_expired_states(self, now_ms: int) -> None:
        expired_session_ids = [
            session_id
            for session_id, state in self._states.items()
            if now_ms >= state.last_accessed_at_ms + self._config.trigger_state_ttl_ms
        ]
        for session_id in expired_session_ids:
            self._states.pop(session_id, None)

    def _state_for(self, session_id: str, now_ms: int) -> _TriggerState | None:
        state = self._states.get(session_id)
        if state is None:
            if len(self._states) >= self._config.max_tracked_sessions:
                return None
            state = _TriggerState(last_accessed_at_ms=now_ms)
            self._states[session_id] = state
        else:
            state.last_accessed_at_ms = now_ms
            self._states.move_to_end(session_id)
        return state

    @staticmethod
    def _has_text(event: RoomEvent) -> bool:
        return event.text is not None and bool(event.text.strip())

    def _is_user_input(self, event: RoomEvent) -> bool:
        if not self._has_text(event):
            return False
        if event.source_type == RoomEventSource.USER_TEXT:
            return True
        if event.source_type != RoomEventSource.USER_VOICE:
            return False

        final_marker = event.payload.get(self._config.final_voice_payload_key)
        if final_marker is False:
            return False
        if self._config.require_final_voice_marker:
            return final_marker is True
        return True

    @staticmethod
    def _latest_event(
        events: Sequence[RoomEvent],
        matches: Callable[[RoomEvent], bool],
    ) -> RoomEvent | None:
        for event in reversed(events):
            if matches(event):
                return event
        return None

    @staticmethod
    def _is_new_event(event: RoomEvent | None, previous_event_id: str | None) -> bool:
        return event is not None and event.event_id != previous_event_id

    @staticmethod
    def _new_frame_count(
        observation: Observation,
        previous_frame_id: str | None,
    ) -> tuple[int, str | None]:
        frame_ids = tuple(frame.frame_id for frame in observation.frames)
        if not frame_ids:
            return 0, None

        latest_frame_id = frame_ids[-1]
        if previous_frame_id is None:
            return len(set(frame_ids)), latest_frame_id

        previous_index = next(
            (
                index
                for index in range(len(frame_ids) - 1, -1, -1)
                if frame_ids[index] == previous_frame_id
            ),
            None,
        )
        if previous_index is None:
            return len(set(frame_ids)), latest_frame_id
        return len(set(frame_ids[previous_index + 1 :])), latest_frame_id

    def _has_screen_signal(
        self,
        *,
        has_new_screen_event: bool,
        new_frame_count: int,
    ) -> bool:
        if not self._config.screen_triggers_enabled:
            return False
        return (self._config.trigger_on_screen_events and has_new_screen_event) or (
            self._config.trigger_on_new_frames
            and new_frame_count >= self._config.minimum_new_frames
        )

    @staticmethod
    def _cooldown_elapsed(
        last_trigger_at_ms: int | None,
        observation_created_at_ms: int,
        cooldown_ms: int,
    ) -> bool:
        return (
            last_trigger_at_ms is None
            or observation_created_at_ms >= last_trigger_at_ms + cooldown_ms
        )

    def _should_trigger_from_barrage(
        self,
        *,
        has_new_barrage: bool,
        state: _TriggerState,
        observation_created_at_ms: int,
    ) -> bool:
        if not has_new_barrage:
            return False
        if self._config.max_consecutive_audience_barrage_triggers == 0:
            return False
        if (
            state.consecutive_barrage_triggers
            >= self._config.max_consecutive_audience_barrage_triggers
        ):
            return False
        if not self._cooldown_elapsed(
            state.last_barrage_trigger_at_ms,
            observation_created_at_ms,
            self._config.audience_barrage_cooldown_ms,
        ):
            return False

        state.last_barrage_trigger_at_ms = observation_created_at_ms
        state.consecutive_barrage_triggers += 1
        return True


class DefaultAudienceSelector:
    """Choose enabled, known audiences in their snapshot order."""

    def __init__(self, *, config: AudienceSelectorConfig | None = None) -> None:
        self._config = AudienceSelectorConfig() if config is None else config

    async def select_candidates(
        self,
        *,
        observation: Observation,
        snapshot: AudienceSnapshot,
    ) -> tuple[str, ...]:
        del observation

        selected: list[str] = []
        seen: set[str] = set()
        for context in snapshot.audiences:
            audience_id = context.member.audience_id
            if not context.member.enabled or audience_id in seen:
                continue
            selected.append(audience_id)
            seen.add(audience_id)
            if len(selected) == self._config.max_candidates:
                break
        return tuple(selected)


class DefaultGenerationInvocationPlanner:
    """Split stable, de-duplicated audience order into bounded batches."""

    def __init__(
        self,
        *,
        config: GenerationInvocationPlannerConfig | None = None,
    ) -> None:
        self._config = GenerationInvocationPlannerConfig() if config is None else config

    async def plan_invocations(
        self,
        *,
        observation: Observation,
        candidates: Sequence[AudienceContext],
    ) -> tuple[AudienceBatch, ...]:
        del observation

        audience_ids: list[str] = []
        seen: set[str] = set()
        for context in candidates:
            audience_id = context.member.audience_id
            if audience_id in seen:
                continue
            audience_ids.append(audience_id)
            seen.add(audience_id)

        batch_size = self._config.batch_size
        return tuple(
            AudienceBatch(tuple(audience_ids[index : index + batch_size]))
            for index in range(0, len(audience_ids), batch_size)
        )
