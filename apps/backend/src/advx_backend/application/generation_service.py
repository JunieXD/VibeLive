import asyncio
import logging
from collections.abc import Iterable, Sequence

from advx_backend.application.ports.generation import (
    AudienceBatch,
    AudienceSelector,
    AudienceSnapshot,
    AudienceSnapshotProvider,
    GenerationInvocationPlanner,
    GenerationTrigger,
    GenerationWorkItem,
    SessionTaskScope,
)
from advx_backend.application.ports.model import ModelProvider
from advx_backend.application.ports.session import IdGenerator
from advx_backend.contracts.generation import (
    AudienceContext,
    GenerationRequest,
    GenerationResult,
    Observation,
)
from advx_backend.services.audience_engine import keep_known_audiences

logger = logging.getLogger(__name__)


class GenerationService:
    def __init__(
        self,
        *,
        snapshots: AudienceSnapshotProvider,
        trigger: GenerationTrigger,
        selector: AudienceSelector,
        invocation_planner: GenerationInvocationPlanner,
        model_provider: ModelProvider,
        session_tasks: SessionTaskScope,
        id_generator: IdGenerator,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")

        self._snapshots = snapshots
        self._trigger = trigger
        self._selector = selector
        self._invocation_planner = invocation_planner
        self._model_provider = model_provider
        self._session_tasks = session_tasks
        self._id_generator = id_generator
        self._max_concurrency = max_concurrency
        self._model_slots = asyncio.BoundedSemaphore(max_concurrency)

    async def generate(self, observation: Observation) -> tuple[GenerationResult, ...]:
        task = await self._session_tasks.start_task(
            observation.session_id,
            lambda: self._orchestrate(observation),
            name=(f"generation:{observation.session_id}:{observation.observation_id}"),
        )
        return await task

    async def _orchestrate(self, observation: Observation) -> tuple[GenerationResult, ...]:
        if not await self._session_tasks.accepts_results(observation.session_id):
            return ()
        if not await self._trigger.should_generate(observation=observation):
            return ()

        snapshot = await self._snapshots.get_snapshot(observation=observation)
        if not self._snapshot_matches(observation, snapshot):
            logger.warning(
                "discarding mismatched audience snapshot",
                extra={
                    "session_id": observation.session_id,
                    "observation_id": observation.observation_id,
                },
            )
            return ()
        if not await self._session_tasks.accepts_results(observation.session_id):
            return ()

        selected_ids = await self._selector.select_candidates(
            observation=observation,
            snapshot=snapshot,
        )
        candidates = self._known_contexts(selected_ids, snapshot.audiences)
        if not candidates:
            return ()

        batches = await self._invocation_planner.plan_invocations(
            observation=observation,
            candidates=candidates,
        )
        work_items = self._build_work_items(observation, candidates, batches)
        if not work_items:
            return ()

        return await self._run_work_items(work_items)

    def _build_work_items(
        self,
        observation: Observation,
        candidates: Sequence[AudienceContext],
        batches: Sequence[AudienceBatch],
    ) -> tuple[GenerationWorkItem, ...]:
        work_items: list[GenerationWorkItem] = []
        for batch in batches:
            audiences = self._known_contexts(batch.audience_ids, candidates)
            if not audiences:
                continue

            request_id = self._id_generator.new_id()
            request = GenerationRequest(
                request_id=request_id,
                observation=observation,
                audiences=list(audiences),
            )
            work_items.append(
                GenerationWorkItem(
                    session_id=observation.session_id,
                    observation_id=observation.observation_id,
                    request_id=request_id,
                    request=request,
                )
            )
        return tuple(work_items)

    async def _run_work_items(
        self,
        work_items: Sequence[GenerationWorkItem],
    ) -> tuple[GenerationResult, ...]:
        indexed_items = iter(enumerate(work_items))
        pending: set[asyncio.Task[tuple[int, GenerationResult | None]]] = set()
        accepted: list[GenerationResult | None] = [None] * len(work_items)
        exhausted = False

        try:
            while pending or not exhausted:
                while len(pending) < self._max_concurrency and not exhausted:
                    try:
                        index, item = next(indexed_items)
                    except StopIteration:
                        exhausted = True
                        break

                    await self._model_slots.acquire()
                    task = asyncio.create_task(
                        self._run_work_item(index, item),
                        name=(
                            f"generation-model:{item.session_id}:"
                            f"{item.observation_id}:{item.request_id}"
                        ),
                    )
                    pending.add(task)

                if not pending:
                    continue

                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    index, result = task.result()
                    accepted[index] = result
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        return tuple(result for result in accepted if result is not None)

    async def _run_work_item(
        self,
        index: int,
        item: GenerationWorkItem,
    ) -> tuple[int, GenerationResult | None]:
        try:
            return index, await self._call_model(item)
        finally:
            self._model_slots.release()

    async def _call_model(self, item: GenerationWorkItem) -> GenerationResult | None:
        if not await self._session_tasks.accepts_results(item.session_id):
            return None

        try:
            result = await self._model_provider.generate(item.request)
        except asyncio.CancelledError:
            await self._cancel_model_request(item)
            raise
        except Exception as error:
            logger.warning(
                "model generation failed",
                extra={
                    "session_id": item.session_id,
                    "observation_id": item.observation_id,
                    "request_id": item.request_id,
                    "error_type": type(error).__name__,
                },
            )
            return None

        if result.request_id != item.request_id:
            logger.warning(
                "discarding generation result with mismatched request id",
                extra={
                    "session_id": item.session_id,
                    "observation_id": item.observation_id,
                    "request_id": item.request_id,
                    "result_request_id": result.request_id,
                },
            )
            return None
        if not await self._session_tasks.accepts_results(item.session_id):
            return None

        return keep_known_audiences(item.request, result)

    async def _cancel_model_request(self, item: GenerationWorkItem) -> None:
        try:
            await asyncio.shield(self._model_provider.cancel(item.request_id))
        except Exception as error:
            logger.warning(
                "model request cancellation failed",
                extra={
                    "session_id": item.session_id,
                    "observation_id": item.observation_id,
                    "request_id": item.request_id,
                    "error_type": type(error).__name__,
                },
            )

    @staticmethod
    def _snapshot_matches(observation: Observation, snapshot: AudienceSnapshot) -> bool:
        return (
            snapshot.session_id == observation.session_id
            and snapshot.observation_id == observation.observation_id
        )

    @staticmethod
    def _known_contexts(
        audience_ids: Iterable[str],
        contexts: Sequence[AudienceContext],
    ) -> tuple[AudienceContext, ...]:
        known = {context.member.audience_id: context for context in contexts}
        selected: list[AudienceContext] = []
        seen: set[str] = set()
        for audience_id in audience_ids:
            if audience_id in seen:
                continue
            context = known.get(audience_id)
            if context is not None:
                selected.append(context)
                seen.add(audience_id)
        return tuple(selected)
