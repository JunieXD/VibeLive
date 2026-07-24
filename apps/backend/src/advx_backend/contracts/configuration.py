from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderProbeStatus = Literal["passed", "failed", "blocked", "skipped"]


class ProviderConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_profile_id: str = Field(default="default", min_length=1, max_length=128)
    model_base_url: str = Field(min_length=1, max_length=2_048)
    model_name: str = Field(min_length=1, max_length=256)
    viewer_model: str | None = Field(default=None, min_length=1, max_length=256)
    memory_model: str | None = Field(default=None, min_length=1, max_length=256)
    visual_summary_model: str | None = Field(default=None, min_length=1, max_length=256)
    model_api_key: str = Field(min_length=1, max_length=4_096, repr=False)
    asr_base_url: str = Field(
        default="https://api.stepfun.com/v1",
        min_length=1,
        max_length=2_048,
    )
    asr_model: str = Field(default="stepaudio-2.5-asr", min_length=1, max_length=256)
    asr_api_key: str = Field(min_length=1, max_length=4_096, repr=False)

    @field_validator(
        "provider_profile_id",
        "model_base_url",
        "model_name",
        "viewer_model",
        "memory_model",
        "visual_summary_model",
        "model_api_key",
        "asr_base_url",
        "asr_model",
        "asr_api_key",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    def role_models(self) -> dict[str, str]:
        return {
            "viewer": self.viewer_model or self.model_name,
            "memory": self.memory_model or self.model_name,
            "visual_summary": self.visual_summary_model or self.model_name,
        }


class RuntimeModelProviderCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_profile_id: str = Field(default="default", min_length=1, max_length=128)
    model_base_url: str = Field(min_length=1, max_length=2_048)
    model_name: str = Field(min_length=1, max_length=256)
    viewer_model: str | None = Field(default=None, min_length=1, max_length=256)
    memory_model: str | None = Field(default=None, min_length=1, max_length=256)
    visual_summary_model: str | None = Field(default=None, min_length=1, max_length=256)
    model_api_key: str = Field(min_length=1, max_length=4_096, repr=False)

    @field_validator(
        "provider_profile_id",
        "model_base_url",
        "model_name",
        "viewer_model",
        "memory_model",
        "visual_summary_model",
        "model_api_key",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    def role_models(self) -> dict[str, str]:
        return {
            "viewer": self.viewer_model or self.model_name,
            "memory": self.memory_model or self.model_name,
            "visual_summary": self.visual_summary_model or self.model_name,
        }


class ProviderConfigurationStatus(BaseModel):
    configured: bool
    provider_profile_id: str | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    viewer_model: str | None = None
    memory_model: str | None = None
    visual_summary_model: str | None = None
    asr_base_url: str | None = None
    asr_model: str | None = None


class ProviderModelDiscovery(BaseModel):
    provider_profile_id: str
    model_ids: list[str]


class ProviderCapabilityCheck(BaseModel):
    capability: str
    status: ProviderProbeStatus
    model_id: str | None = None
    error_code: str | None = None
    http_status: int | None = None


class ProviderCapabilityProbeResult(BaseModel):
    provider_profile_id: str
    status: ProviderProbeStatus
    discovered_model_ids: list[str]
    checks: list[ProviderCapabilityCheck]
