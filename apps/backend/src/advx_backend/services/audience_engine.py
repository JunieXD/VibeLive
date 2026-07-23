from advx_backend.contracts.generation import GenerationRequest, GenerationResult


def keep_known_audiences(
    request: GenerationRequest,
    result: GenerationResult,
) -> GenerationResult:
    """Drop candidates whose audience identity was not part of this request."""
    allowed_ids = {context.member.audience_id for context in request.audiences}
    candidates = [
        candidate for candidate in result.candidates if candidate.audience_id in allowed_ids
    ]
    return result.model_copy(update={"candidates": candidates})
