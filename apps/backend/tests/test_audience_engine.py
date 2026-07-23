from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.generation import (
    AudienceContext,
    BarrageCandidate,
    GenerationRequest,
    GenerationResult,
    Observation,
)
from advx_backend.services.audience_engine import keep_known_audiences


def test_unknown_audience_is_dropped() -> None:
    request = GenerationRequest(
        request_id="request-1",
        observation=Observation(
            session_id="session-1",
            observation_id="observation-1",
            created_at_ms=1,
        ),
        audiences=[
            AudienceContext(member=AudienceMember(audience_id="known", display_name="Known"))
        ],
    )
    result = GenerationResult(
        request_id="request-1",
        candidates=[
            BarrageCandidate(audience_id="known", text="hello"),
            BarrageCandidate(audience_id="unknown", text="spoofed"),
        ],
    )

    validated = keep_known_audiences(request, result)

    assert [candidate.audience_id for candidate in validated.candidates] == ["known"]
