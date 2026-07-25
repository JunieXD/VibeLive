import math
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
ContentHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ViewerCount = Annotated[int, Field(ge=0, le=32)]


class AudienceDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonaTemplate(AudienceDomainModel):
    persona_id: Identifier
    document_version: int = Field(ge=1)
    revision: int = Field(ge=1)
    content_hash: ContentHash
    display_name: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=256)
    traits: list[str] = Field(default_factory=list, max_length=64)
    speech_style: dict[str, JsonValue] = Field(default_factory=dict)
    behavior: dict[str, JsonValue] = Field(default_factory=dict)
    trigger_preferences: list[str] = Field(default_factory=list, max_length=64)
    avoid_patterns: list[str] = Field(default_factory=list, max_length=64)
    silence_bias: float = Field(ge=0, le=1)
    burst_bias: float = Field(ge=0, le=1)
    repetition_bias: float = Field(ge=0, le=1)
    cooldown_ms: int = Field(ge=0)
    content_flags: list[str] = Field(default_factory=list, max_length=64)
    enabled: bool = True


class ResponseRange(AudienceDomainModel):
    minimum: int = Field(ge=0, le=32)
    maximum: int = Field(ge=0, le=32)

    @model_validator(mode="after")
    def validate_order(self) -> "ResponseRange":
        if self.maximum < self.minimum:
            raise ValueError("maximum must not be less than minimum")
        return self


class AmbienceMode(StrEnum):
    NATURAL = "natural"
    CONTINUOUS = "continuous"


class PersonaOverride(AudienceDomainModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    traits: list[str] | None = Field(default=None, max_length=64)
    speech_style: dict[str, JsonValue] | None = None
    behavior: dict[str, JsonValue] | None = None
    trigger_preferences: list[str] | None = Field(default=None, max_length=64)
    avoid_patterns: list[str] | None = Field(default=None, max_length=64)
    silence_bias: float | None = Field(default=None, ge=0, le=1)
    burst_bias: float | None = Field(default=None, ge=0, le=1)
    repetition_bias: float | None = Field(default=None, ge=0, le=1)
    cooldown_ms: int | None = Field(default=None, ge=0)
    content_flags: list[str] | None = Field(default=None, max_length=64)


class ModeDefinition(AudienceDomainModel):
    mode_id: Identifier
    namespace_id: Identifier
    revision: int = Field(ge=1)
    persona_counts: dict[Identifier, ViewerCount] = Field(min_length=1, max_length=32)
    persona_overrides: dict[str, PersonaOverride] = Field(default_factory=dict)
    normal_response_range: ResponseRange
    highlight_response_range: ResponseRange
    ambience: AmbienceMode = AmbienceMode.NATURAL

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_weighted_configuration(cls, value: Any) -> Any:
        """Accept persisted v2 weighted mode payloads while runtime records migrate."""

        if not isinstance(value, dict) or "persona_counts" in value:
            return value

        persona_ids = value.get("persona_ids")
        persona_weights = value.get("persona_weights")
        target = value.get("target_concurrent_viewers", value.get("viewer_count"))
        if (
            not isinstance(persona_ids, list)
            or not all(isinstance(persona_id, str) for persona_id in persona_ids)
            or len(set(persona_ids)) != len(persona_ids)
            or not isinstance(persona_weights, dict)
            or not isinstance(target, int)
            or isinstance(target, bool)
            or not 1 <= target <= 32
        ):
            return value

        weights: list[tuple[str, float, int]] = []
        for index, persona_id in enumerate(persona_ids):
            weight = persona_weights.get(persona_id)
            if (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not math.isfinite(weight)
                or weight < 0
            ):
                return value
            weights.append((persona_id, float(weight), index))
        if set(persona_weights) != set(persona_ids):
            return value

        positive = [item for item in weights if item[1] > 0]
        total_weight = sum(weight for _, weight, _ in positive)
        if total_weight <= 0:
            return value
        quotas = {
            persona_id: target * weight / total_weight
            for persona_id, weight, _ in positive
        }
        counts = {persona_id: math.floor(quota) for persona_id, quota in quotas.items()}
        remaining = target - sum(counts.values())
        positions = {persona_id: index for persona_id, _, index in positive}
        for persona_id in sorted(
            quotas,
            key=lambda item: (
                -(quotas[item] - counts[item]),
                positions[item],
                item,
            ),
        )[:remaining]:
            counts[persona_id] += 1

        migrated = dict(value)
        migrated["persona_counts"] = {
            persona_id: counts.get(persona_id, 0) for persona_id in persona_ids
        }
        migrated.pop("target_concurrent_viewers", None)
        migrated.pop("viewer_count", None)
        migrated.pop("persona_ids", None)
        migrated.pop("persona_weights", None)
        return migrated

    @model_validator(mode="after")
    def validate_persona_configuration(self) -> "ModeDefinition":
        if self.viewer_count == 0:
            raise ValueError("at least one persona count must be greater than zero")
        if self.viewer_count > 32:
            raise ValueError("the total persona count must not exceed 32")
        known = set(self.persona_counts)
        if not set(self.persona_overrides).issubset(known):
            raise ValueError("persona_overrides cannot reference an unknown persona")
        if self.normal_response_range.maximum > self.viewer_count:
            raise ValueError(
                "normal response range cannot exceed the total persona count"
            )
        if self.highlight_response_range.maximum > self.viewer_count:
            raise ValueError(
                "highlight response range cannot exceed the total persona count"
            )
        return self

    @property
    def viewer_count(self) -> int:
        return sum(self.persona_counts.values())

    @property
    def target_concurrent_viewers(self) -> int:
        """Derived runtime compatibility accessor; counts remain the source of truth."""

        return self.viewer_count
