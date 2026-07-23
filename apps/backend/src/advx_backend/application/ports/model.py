from typing import Protocol

from advx_backend.contracts.generation import GenerationRequest, GenerationResult


class ModelProvider(Protocol):
    async def health(self) -> bool: ...

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    async def cancel(self, request_id: str) -> None: ...
