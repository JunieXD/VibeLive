from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from advx_backend.domain.audience import AudienceOrigin, AudienceProfile


@dataclass(frozen=True, slots=True)
class BuiltinAudience:
    """Stable template for an audience profile owned by the application."""

    audience_id: str
    display_name: str
    personality: dict[str, Any]
    preferences: dict[str, Any]
    speaking_style: dict[str, Any]
    preset_id: str
    avatar_ref: str | None = None
    preset_version: int = 1

    def to_profile(self, *, now_ms: int) -> AudienceProfile:
        return AudienceProfile(
            audience_id=self.audience_id,
            display_name=self.display_name,
            avatar_ref=self.avatar_ref,
            personality=deepcopy(self.personality),
            preferences=deepcopy(self.preferences),
            speaking_style=deepcopy(self.speaking_style),
            origin=AudienceOrigin.PRESET,
            preset_id=self.preset_id,
            preset_version=self.preset_version,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )


BUILTIN_AUDIENCES: tuple[BuiltinAudience, ...] = (
    BuiltinAudience(
        audience_id="builtin-luna",
        display_name="Luna",
        personality={"temperament": "warm", "energy": "steady"},
        preferences={"topics": ["indie games", "creative play"]},
        speaking_style={"length": "short", "tone": "encouraging"},
        preset_id="advx.luna",
    ),
    BuiltinAudience(
        audience_id="builtin-max",
        display_name="Max",
        personality={"temperament": "curious", "energy": "lively"},
        preferences={"topics": ["strategy", "speedruns"]},
        speaking_style={"length": "concise", "tone": "playful"},
        preset_id="advx.max",
    ),
    BuiltinAudience(
        audience_id="builtin-nova",
        display_name="Nova",
        personality={"temperament": "thoughtful", "energy": "calm"},
        preferences={"topics": ["story", "music"]},
        speaking_style={"length": "medium", "tone": "observant"},
        preset_id="advx.nova",
    ),
)


def builtin_profiles(*, now_ms: int) -> tuple[AudienceProfile, ...]:
    """Create fresh profile instances so callers cannot mutate template data."""
    return tuple(template.to_profile(now_ms=now_ms) for template in BUILTIN_AUDIENCES)
