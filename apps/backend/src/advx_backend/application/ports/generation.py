import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from advx_backend.contracts.generation import AudienceContext, GenerationRequest, Observation

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AudienceSnapshot:
    session_id: str
    observation_id: str
    audiences: tuple[AudienceContext, ...]

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if not self.observation_id:
            raise ValueError("observation_id must not be empty")


@dataclass(frozen=True, slots=True)
class AudienceBatch:
    """Audience identities assigned to one provider invocation."""

    audience_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenerationWorkItem:
    session_id: str
    observation_id: str
    request_id: str
    request: GenerationRequest

    def __post_init__(self) -> None:
        observation = self.request.observation
        if self.session_id != observation.session_id:
            raise ValueError("work item session_id does not match its request")
        if self.observation_id != observation.observation_id:
            raise ValueError("work item observation_id does not match its request")
        if self.request_id != self.request.request_id:
            raise ValueError("work item request_id does not match its request")


class AudienceSnapshotProvider(Protocol):
    async def get_snapshot(self, *, observation: Observation) -> AudienceSnapshot: ...


class GenerationTrigger(Protocol):
    async def should_generate(self, *, observation: Observation) -> bool: ...


class AudienceSelector(Protocol):
    async def select_candidates(
        self,
        *,
        observation: Observation,
        snapshot: AudienceSnapshot,
    ) -> Sequence[str]: ...


class GenerationInvocationPlanner(Protocol):
    async def plan_invocations(
        self,
        *,
        observation: Observation,
        candidates: Sequence[AudienceContext],
    ) -> Sequence[AudienceBatch]: ...


class SessionTaskScope(Protocol):
    async def start_task(
        self,
        session_id: str,
        factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]: ...

    async def accepts_results(self, session_id: str) -> bool: ...
