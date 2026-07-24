from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Protocol

from advx_backend.application.ports.asr import (
    TranscriptSegment,
    TranscriptTargetResolution,
)


class TranscriptRuntimeState(Protocol):
    async def snapshot(self, session_id: str) -> object: ...


class RuntimeTranscriptTargetResolver:
    """Resolve exact spoken mentions against the committed runtime roster."""

    resolver_id = "runtime-roster-exact-v1"

    def __init__(self, runtime_state: TranscriptRuntimeState) -> None:
        self._runtime_state = runtime_state

    async def resolve(
        self,
        segment: TranscriptSegment,
    ) -> TranscriptTargetResolution:
        try:
            committed = await self._runtime_state.snapshot(segment.session_id)
        except KeyError:
            return TranscriptTargetResolution(resolver_id=self.resolver_id)

        text = self._normalize(segment.text)
        viewers = tuple(getattr(committed.pool, "viewers", ()))
        personas = tuple(getattr(committed.spec, "personas", ()))

        viewer_id_matches = self._matches(
            text,
            ((viewer.viewer_instance_id, viewer.viewer_instance_id) for viewer in viewers),
        )
        persona_id_matches = self._matches(
            text,
            ((persona.persona_id, persona.persona_id) for persona in personas),
        )
        exact_ids = {
            *(("viewer", value) for value in viewer_id_matches),
            *(("persona", value) for value in persona_id_matches),
        }
        if exact_ids:
            return self._resolution(exact_ids)

        viewer_name_matches = self._matches(
            text,
            ((viewer.display_name, viewer.viewer_instance_id) for viewer in viewers),
        )
        if viewer_name_matches:
            return self._resolution(
                {("viewer", value) for value in viewer_name_matches}
            )

        persona_name_matches = self._matches(
            text,
            ((persona.display_name, persona.persona_id) for persona in personas),
        )
        return self._resolution(
            {("persona", value) for value in persona_name_matches}
        )

    def _resolution(
        self,
        matches: set[tuple[str, str]],
    ) -> TranscriptTargetResolution:
        if not matches:
            return TranscriptTargetResolution(resolver_id=self.resolver_id)
        if len(matches) != 1:
            return TranscriptTargetResolution(
                resolver_id=self.resolver_id,
                ambiguous=True,
            )
        kind, target = next(iter(matches))
        if kind == "viewer":
            return TranscriptTargetResolution(
                resolver_id=self.resolver_id,
                target_viewer_id=target,
            )
        return TranscriptTargetResolution(
            resolver_id=self.resolver_id,
            target_persona_id=target,
        )

    @classmethod
    def _matches(
        cls,
        text: str,
        candidates: Iterable[tuple[str, str]],
    ) -> set[str]:
        matches: set[str] = set()
        for label, target in candidates:
            normalized = cls._normalize(label)
            if normalized and cls._contains_exact(text, normalized):
                matches.add(target)
        return matches

    @staticmethod
    def _contains_exact(text: str, candidate: str) -> bool:
        left = r"(?<![\w])" if candidate[0].isascii() and candidate[0].isalnum() else ""
        right = r"(?![\w])" if candidate[-1].isascii() and candidate[-1].isalnum() else ""
        pattern = rf"{left}{re.escape(candidate)}{right}"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    @staticmethod
    def _normalize(value: str) -> str:
        return unicodedata.normalize("NFKC", value).casefold().strip()
