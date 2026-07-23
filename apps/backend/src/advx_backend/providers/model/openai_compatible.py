from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str


class OpenAICompatibleProvider:
    """Protocol adapter boundary for user-configured multimodal endpoints."""

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config
