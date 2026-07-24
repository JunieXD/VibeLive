from types import SimpleNamespace

import pytest

from advx_backend.application.ports.asr import TranscriptSegment
from advx_backend.application.transcript_target_resolver import (
    RuntimeTranscriptTargetResolver,
)


class State:
    def __init__(self) -> None:
        self.value = SimpleNamespace(
            pool=SimpleNamespace(
                viewers=(
                    SimpleNamespace(
                        viewer_instance_id="viewer-alpha-1",
                        display_name="Alpha One",
                    ),
                    SimpleNamespace(
                        viewer_instance_id="viewer-beta-1",
                        display_name="Beta One",
                    ),
                )
            ),
            spec=SimpleNamespace(
                personas=(
                    SimpleNamespace(persona_id="persona-alpha", display_name="Alpha"),
                    SimpleNamespace(persona_id="persona-beta", display_name="Beta"),
                )
            ),
        )

    async def snapshot(self, session_id: str) -> object:
        assert session_id == "session-1"
        return self.value


def segment(text: str) -> TranscriptSegment:
    return TranscriptSegment(
        session_id="session-1",
        text=text,
        started_at_ms=0,
        ended_at_ms=1,
        final=True,
        utterance_id="utterance-1",
    )


@pytest.mark.asyncio
async def test_resolver_prefers_exact_viewer_id_or_unique_display_name() -> None:
    resolver = RuntimeTranscriptTargetResolver(State())

    by_id = await resolver.resolve(segment("viewer-alpha-1 please answer"))
    by_name = await resolver.resolve(segment("Alpha One please answer"))

    assert by_id.target_viewer_id == "viewer-alpha-1"
    assert by_name.target_viewer_id == "viewer-alpha-1"
    assert by_name.ambiguous is False


@pytest.mark.asyncio
async def test_resolver_returns_persona_and_broadcasts_conflicting_mentions() -> None:
    resolver = RuntimeTranscriptTargetResolver(State())

    persona = await resolver.resolve(segment("persona-beta please answer"))
    ambiguous = await resolver.resolve(
        segment("viewer-alpha-1 and viewer-beta-1 please answer")
    )

    assert persona.target_persona_id == "persona-beta"
    assert ambiguous.ambiguous is True
    assert ambiguous.target_viewer_id is None
    assert ambiguous.target_persona_id is None
