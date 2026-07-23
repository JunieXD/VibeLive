from dataclasses import dataclass

import pytest

from advx_backend.application.barrage_pipeline import BarragePipeline
from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.generation import (
    AudienceContext,
    BarrageCandidate,
    GenerationRequest,
    GenerationResult,
    Observation,
)
from advx_backend.domain.barrage import (
    BarragePolicy,
    BarrageRejectionReason,
    BarrageValidationScope,
)


@dataclass
class ManualClock:
    value: int = 10_000

    def now_ms(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class SequenceIdGenerator:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"barrage-{self.value}"


def policy(**overrides: object) -> BarragePolicy:
    values: dict[str, object] = {
        "max_text_length": 20,
        "ttl_ms": 1_000,
        "blocked_words": frozenset(),
        "duplicate_window_ms": 100,
        "max_duplicate_entries_per_session": 10,
        "density_window_ms": 100,
        "max_outputs_per_density_window": 10,
        "max_tracked_sessions": 4,
    }
    values.update(overrides)
    return BarragePolicy(**values)  # type: ignore[arg-type]


def make_pipeline(
    *,
    clock: ManualClock | None = None,
    barrage_policy: BarragePolicy | None = None,
) -> tuple[BarragePipeline, ManualClock]:
    clock = clock or ManualClock()
    return (
        BarragePipeline(
            policy=barrage_policy or policy(),
            clock=clock,
            id_generator=SequenceIdGenerator(),
        ),
        clock,
    )


def make_request(
    *,
    session_id: str = "session-1",
    observation_id: str = "observation-1",
    request_id: str = "request-1",
    created_at_ms: int = 9_500,
    audience_ids: tuple[str, ...] = ("audience-1",),
) -> GenerationRequest:
    return GenerationRequest(
        request_id=request_id,
        observation=Observation(
            session_id=session_id,
            observation_id=observation_id,
            created_at_ms=created_at_ms,
        ),
        audiences=[
            AudienceContext(
                member=AudienceMember(
                    audience_id=audience_id,
                    display_name=audience_id,
                )
            )
            for audience_id in audience_ids
        ],
    )


def matching_scope(request: GenerationRequest) -> BarrageValidationScope:
    return BarrageValidationScope(
        session_id=request.observation.session_id,
        observation_id=request.observation.observation_id,
        request_id=request.request_id,
    )


def make_result(
    *texts: str,
    request_id: str = "request-1",
    audience_id: str = "audience-1",
) -> GenerationResult:
    return GenerationResult(
        request_id=request_id,
        candidates=[BarrageCandidate(audience_id=audience_id, text=text) for text in texts],
    )


def test_rejects_forged_audience_without_retaining_rejected_text() -> None:
    pipeline, _ = make_pipeline()
    request = make_request()
    result = GenerationResult(
        request_id=request.request_id,
        candidates=[
            BarrageCandidate(audience_id="audience-1", text="known"),
            BarrageCandidate(audience_id="forged", text="private rejected body"),
        ],
    )

    validated = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=result,
    )

    assert [event.audience_id for event in validated.events] == ["audience-1"]
    assert [rejection.reason for rejection in validated.rejections] == [
        BarrageRejectionReason.AUDIENCE_NOT_IN_REQUEST
    ]
    assert "private rejected body" not in repr(validated.rejections)


@pytest.mark.parametrize(
    ("request_session", "request_observation", "result_request", "reason"),
    [
        (
            "wrong-session",
            "observation-1",
            "request-1",
            BarrageRejectionReason.SESSION_MISMATCH,
        ),
        (
            "session-1",
            "wrong-observation",
            "request-1",
            BarrageRejectionReason.OBSERVATION_MISMATCH,
        ),
        (
            "session-1",
            "observation-1",
            "wrong-request",
            BarrageRejectionReason.REQUEST_MISMATCH,
        ),
    ],
)
def test_rejects_wrong_session_observation_or_request_ownership(
    request_session: str,
    request_observation: str,
    result_request: str,
    reason: BarrageRejectionReason,
) -> None:
    pipeline, _ = make_pipeline()
    request = make_request(
        session_id=request_session,
        observation_id=request_observation,
    )
    scope = BarrageValidationScope(
        session_id="session-1",
        observation_id="observation-1",
        request_id="request-1",
    )

    validated = pipeline.process(
        scope=scope,
        request=request,
        result=make_result("text", request_id=result_request),
    )

    assert validated.events == ()
    assert validated.batch_rejection_reason is reason
    assert [rejection.reason for rejection in validated.rejections] == [reason]


def test_rejects_expired_candidate() -> None:
    pipeline, clock = make_pipeline(barrage_policy=policy(ttl_ms=500))
    request = make_request(created_at_ms=clock.now_ms() - 500)

    validated = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("late"),
    )

    assert validated.events == ()
    assert validated.batch_rejection_reason is BarrageRejectionReason.EXPIRED


def test_trims_text_and_rejects_empty_or_overlong_text() -> None:
    pipeline, _ = make_pipeline(barrage_policy=policy(max_text_length=5))
    request = make_request()
    result = make_result("  ok  ", " \t\n ", "123456")

    validated = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=result,
    )

    assert [event.text for event in validated.events] == ["ok"]
    assert [rejection.reason for rejection in validated.rejections] == [
        BarrageRejectionReason.EMPTY_TEXT,
        BarrageRejectionReason.TEXT_TOO_LONG,
    ]


def test_rejects_blocked_word_case_insensitively() -> None:
    pipeline, _ = make_pipeline(barrage_policy=policy(blocked_words=frozenset({"blocked"})))
    request = make_request()

    validated = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("is BLOCKED"),
    )

    assert validated.events == ()
    assert validated.rejections[0].reason is BarrageRejectionReason.BLOCKED_WORD


def test_rejects_duplicate_within_window_and_allows_it_at_boundary() -> None:
    pipeline, clock = make_pipeline(barrage_policy=policy(duplicate_window_ms=100))
    request = make_request()

    first = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("  repeated  "),
    )
    clock.advance(99)
    duplicate = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("repeated"),
    )
    clock.advance(1)
    after_window = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("repeated"),
    )

    assert [event.text for event in first.events] == ["repeated"]
    assert duplicate.rejections[0].reason is BarrageRejectionReason.DUPLICATE
    assert [event.text for event in after_window.events] == ["repeated"]


def test_limits_output_density_with_a_sliding_window() -> None:
    pipeline, clock = make_pipeline(
        barrage_policy=policy(
            density_window_ms=100,
            max_outputs_per_density_window=2,
        )
    )
    request = make_request()

    limited = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("one", "two", "three"),
    )
    clock.advance(100)
    after_window = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("three"),
    )

    assert [event.text for event in limited.events] == ["one", "two"]
    assert limited.rejections[0].reason is BarrageRejectionReason.DENSITY_LIMIT_EXCEEDED
    assert [event.text for event in after_window.events] == ["three"]


def test_duplicate_and_density_state_is_isolated_between_sessions() -> None:
    pipeline, _ = make_pipeline(barrage_policy=policy(max_outputs_per_density_window=1))
    first_request = make_request(session_id="session-1", request_id="request-1")
    second_request = make_request(session_id="session-2", request_id="request-2")

    first = pipeline.process(
        scope=matching_scope(first_request),
        request=first_request,
        result=make_result("same", request_id="request-1"),
    )
    second = pipeline.process(
        scope=matching_scope(second_request),
        request=second_request,
        result=make_result("same", request_id="request-2"),
    )
    repeated_first = pipeline.process(
        scope=matching_scope(first_request),
        request=first_request,
        result=make_result("different", request_id="request-1"),
    )

    assert len(first.events) == 1
    assert len(second.events) == 1
    assert repeated_first.rejections[0].reason is BarrageRejectionReason.DENSITY_LIMIT_EXCEEDED


def test_clear_session_resets_duplicate_and_density_state() -> None:
    pipeline, _ = make_pipeline(barrage_policy=policy(max_outputs_per_density_window=2))
    request = make_request()

    initial = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("one", "two"),
    )
    pipeline.clear_session(request.observation.session_id)
    after_cleanup = pipeline.process(
        scope=matching_scope(request),
        request=request,
        result=make_result("one"),
    )

    assert len(initial.events) == 2
    assert [event.text for event in after_cleanup.events] == ["one"]


def test_duplicate_and_session_state_caps_evict_oldest_entries() -> None:
    pipeline, _ = make_pipeline(
        barrage_policy=policy(
            max_duplicate_entries_per_session=2,
            max_tracked_sessions=2,
        )
    )
    first_request = make_request(session_id="session-1", request_id="request-1")

    initial = pipeline.process(
        scope=matching_scope(first_request),
        request=first_request,
        result=make_result("one", "two", "three", request_id="request-1"),
    )
    evicted_text = pipeline.process(
        scope=matching_scope(first_request),
        request=first_request,
        result=make_result("one", request_id="request-1"),
    )

    for number in (2, 3):
        request = make_request(
            session_id=f"session-{number}",
            request_id=f"request-{number}",
        )
        pipeline.process(
            scope=matching_scope(request),
            request=request,
            result=make_result("session text", request_id=request.request_id),
        )

    evicted_session = pipeline.process(
        scope=matching_scope(first_request),
        request=first_request,
        result=make_result("three", request_id="request-1"),
    )

    assert len(initial.events) == 3
    assert [event.text for event in evicted_text.events] == ["one"]
    assert [event.text for event in evicted_session.events] == ["three"]
