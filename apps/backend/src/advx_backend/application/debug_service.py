import base64
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from advx_backend.contracts.debug import (
    AiCallImagePreview,
    AiCallQuery,
    AiCallQueryResponse,
    AiCallTrace,
    DebugContextReferences,
    DebugMemeSnapshot,
    DebugMemorySnapshot,
    DebugQueueSnapshot,
    DebugRuntimeSnapshot,
    DebugTrace,
    RuntimeAgentDebugSnapshot,
    TraceQuery,
    TraceQueryResponse,
)
from advx_backend.contracts.protocol import TRACE_SCHEMA_VERSION
from advx_backend.contracts.viewer_runtime import ViewerRuntimeTelemetry
from advx_backend.infrastructure.logging.ai_call_image_store import AiCallImageStore
from advx_backend.infrastructure.logging.ai_call_store import AiCallStore
from advx_backend.infrastructure.logging.trace_store import (
    TraceStore,
    assert_redacted_artifact,
)


class RuntimeDebugState(Protocol):
    async def debug_snapshot(self, session_id: str) -> object: ...


RuntimeAgentDebugProvider = Callable[
    [str],
    RuntimeAgentDebugSnapshot
    | ViewerRuntimeTelemetry
    | Awaitable[RuntimeAgentDebugSnapshot | ViewerRuntimeTelemetry],
]


class DebugService:
    def __init__(
        self,
        trace_store: TraceStore,
        *,
        runtime_state: RuntimeDebugState | None = None,
        runtime_agent: RuntimeAgentDebugProvider | object | None = None,
        ai_call_store: AiCallStore | None = None,
        ai_call_image_store: AiCallImageStore | None = None,
    ) -> None:
        self._trace_store = trace_store
        self._runtime_state = runtime_state
        self._runtime_agent = runtime_agent
        self._ai_call_store = ai_call_store or AiCallStore()
        self._ai_call_image_store = ai_call_image_store or AiCallImageStore()

    def bind_runtime_state(self, runtime_state: RuntimeDebugState) -> None:
        self._runtime_state = runtime_state

    def bind_runtime_agent(self, runtime_agent: RuntimeAgentDebugProvider | object) -> None:
        self._runtime_agent = runtime_agent

    def record(self, trace: DebugTrace) -> None:
        self._trace_store.append(trace)

    def query(self, query: TraceQuery | None = None) -> TraceQueryResponse:
        return self._trace_store.query(query)

    def record_ai_call(self, trace: AiCallTrace) -> None:
        self._ai_call_store.upsert(trace)

    def query_ai_calls(
        self,
        query: AiCallQuery | None = None,
    ) -> AiCallQueryResponse:
        return self._ai_call_store.query(query)

    def capture_ai_call_image(self, data_url: str) -> str | None:
        return self._ai_call_image_store.capture(data_url)

    def query_ai_call_image(self, preview_id: str) -> AiCallImagePreview | None:
        image = self._ai_call_image_store.get(preview_id)
        if image is None:
            return None
        return AiCallImagePreview(
            mime_type=image.mime_type,
            data_url=(
                f"data:{image.mime_type};base64,"
                f"{base64.b64encode(image.body).decode('ascii')}"
            ),
        )

    def observation_wave(
        self,
        observation_id: str,
    ) -> object | None:
        result = self.query(TraceQuery(observation_id=observation_id, limit=1_000))
        matches = [
            wave for wave in result.waves if wave.observation_id == observation_id
        ]
        return matches[-1] if matches else None

    def export(self, destination: Path, query: TraceQuery | None = None) -> int:
        return self._trace_store.export(destination, query)

    def export_artifact(self, query: TraceQuery | None = None) -> dict[str, object]:
        response = self.query(query)
        artifact: dict[str, object] = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "redacted": True,
            "items": [
                item.model_dump(mode="json")
                for item in response.items
            ],
            "waves": [
                wave.model_dump(mode="json")
                for wave in response.waves
            ],
            "next_cursor": response.next_cursor,
            "metadata": response.metadata,
        }
        assert_redacted_artifact(artifact)
        return artifact

    async def runtime_snapshot(self, session_id: str) -> DebugRuntimeSnapshot:
        if self._runtime_state is None:
            raise RuntimeError("runtime debug state is unavailable")
        state = await self._runtime_state.debug_snapshot(session_id)
        spec = getattr(state, "spec")
        pool = getattr(state, "pool")
        traces = self.query(TraceQuery(session_id=session_id, limit=1_000))
        extension = await self._runtime_agent_snapshot(session_id)

        event_ids = {
            event_id
            for item in traces.items
            for event_id in (
                *item.public_context_event_ids,
                *item.private_state_event_ids,
            )
        }
        event_ids.update(
            event_id for wave in traces.waves for event_id in wave.event_ids
        )
        frame_hashes = {
            frame_hash
            for item in (*traces.items, *traces.waves)
            for frame_hash in item.frame_hashes
        }
        memory_ids = {
            memory_id
            for item in (*traces.items, *traces.waves)
            for memory_id in item.memory.memory_ids
        }
        candidate_ids = {
            candidate_id
            for item in traces.items
            for candidate_id in item.side_effects.memory_candidate_ids
        }
        traced_context = DebugContextReferences(
            event_ids=sorted(event_ids),
            frame_hashes=sorted(frame_hashes),
            memory_ids=sorted(memory_ids),
        )
        context_refs = _merge_context_refs(traced_context, extension.context_refs)
        memory = extension.memory or DebugMemorySnapshot(
            revision=max(
                (
                    item.memory.memory_revision
                    for item in (*traces.items, *traces.waves)
                ),
                default=0,
            ),
            ids=context_refs.memory_ids,
        )
        memes = extension.memes or DebugMemeSnapshot(
            candidate_ids=sorted(candidate_ids)
        )
        history = extension.history or [
            {
                "trace_id": item.trace_id,
                "observation_id": item.observation_id,
                "viewer_instance_id": item.viewer_instance_id,
                "response_status": item.response_status.value,
            }
            for item in traces.items
        ]
        queue = extension.queue or DebugQueueSnapshot(
            capacity=spec.settings.viewer_queue_capacity
        )
        unavailable = []
        if queue.depth is None:
            unavailable.append("queue.depth")
        if extension.telemetry is None:
            unavailable.append("telemetry")
        snapshot = DebugRuntimeSnapshot(
            session_id=session_id,
            room_id=spec.room.room_id,
            audience_epoch=getattr(state, "audience_epoch"),
            accepting_results=getattr(state, "accepting_results"),
            config=spec,
            pool=pool,
            waves=traces.waves,
            queue=queue,
            telemetry=extension.telemetry,
            context_refs=context_refs,
            memory=memory,
            memes=memes,
            history=history,
            unavailable=unavailable,
        )
        assert_redacted_artifact(snapshot.model_dump(mode="json"))
        return snapshot

    async def _runtime_agent_snapshot(
        self,
        session_id: str,
    ) -> RuntimeAgentDebugSnapshot:
        provider = self._runtime_agent
        if provider is None:
            return RuntimeAgentDebugSnapshot()
        if callable(provider):
            outcome = provider(session_id)
        else:
            method = getattr(provider, "debug_snapshot", None)
            if method is None:
                method = getattr(provider, "telemetry_snapshot", None)
            if method is None:
                return RuntimeAgentDebugSnapshot()
            outcome = method(session_id)
        value = await outcome if inspect.isawaitable(outcome) else outcome
        if isinstance(value, RuntimeAgentDebugSnapshot):
            return value
        if isinstance(value, ViewerRuntimeTelemetry):
            return RuntimeAgentDebugSnapshot(telemetry=value)
        return RuntimeAgentDebugSnapshot.model_validate(value)


def _merge_context_refs(
    traced: DebugContextReferences,
    supplied: DebugContextReferences | None,
) -> DebugContextReferences:
    if supplied is None:
        return traced
    return DebugContextReferences(
        event_ids=sorted({*traced.event_ids, *supplied.event_ids}),
        frame_hashes=sorted({*traced.frame_hashes, *supplied.frame_hashes}),
        memory_ids=sorted({*traced.memory_ids, *supplied.memory_ids}),
    )
