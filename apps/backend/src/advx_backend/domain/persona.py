from enum import StrEnum
from typing import Annotated

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
ContentHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


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
    target_concurrent_viewers: int = Field(
        ge=1,
        le=32,
        validation_alias=AliasChoices("target_concurrent_viewers", "viewer_count"),
    )
    persona_ids: list[Identifier] = Field(min_length=1, max_length=32)
    persona_weights: dict[str, float] = Field(min_length=1, max_length=32)
    persona_overrides: dict[str, PersonaOverride] = Field(default_factory=dict)
    normal_response_range: ResponseRange
    highlight_response_range: ResponseRange
    ambience: AmbienceMode = AmbienceMode.NATURAL

    @model_validator(mode="after")
    def validate_persona_configuration(self) -> "ModeDefinition":
        if len(set(self.persona_ids)) != len(self.persona_ids):
            raise ValueError("persona_ids must be unique")
        known = set(self.persona_ids)
        if set(self.persona_weights) != known:
            raise ValueError("persona_weights must contain exactly the configured persona_ids")
        if not any(weight > 0 for weight in self.persona_weights.values()):
            raise ValueError("at least one persona weight must be greater than zero")
        if any(weight < 0 for weight in self.persona_weights.values()):
            raise ValueError("persona weights must not be negative")
        if not set(self.persona_overrides).issubset(known):
            raise ValueError("persona_overrides cannot reference an unknown persona")
        if self.normal_response_range.maximum > self.target_concurrent_viewers:
            raise ValueError(
                "normal response range cannot exceed target_concurrent_viewers"
            )
        if self.highlight_response_range.maximum > self.target_concurrent_viewers:
            raise ValueError(
                "highlight response range cannot exceed target_concurrent_viewers"
            )
        return self

    @property
    def viewer_count(self) -> int:
        """Compatibility accessor for runtime v1 callers during contract migration."""

        return self.target_concurrent_viewers
