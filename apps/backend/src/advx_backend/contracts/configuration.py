from pydantic import BaseModel, ConfigDict, Field


class ProviderConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_base_url: str = Field(min_length=1, max_length=2_048)
    model_name: str = Field(min_length=1, max_length=256)
    model_api_key: str = Field(min_length=1, max_length=4_096, repr=False)
    asr_api_key: str = Field(min_length=1, max_length=4_096, repr=False)


class ProviderConfigurationStatus(BaseModel):
    configured: bool
    model_base_url: str | None = None
    model_name: str | None = None
    asr_model: str | None = None
