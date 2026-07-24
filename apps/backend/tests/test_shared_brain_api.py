from functools import partial
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from advx_backend.api.http.shared_brain import create_shared_brain_router
from advx_backend.application.runtime_session_service import NoOpRuntimeCapabilityProbe
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.debug import (
    MemoryReferenceTrace,
    ObservationWaveStatus,
    ObservationWaveTrace,
)
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.meme import ModeMeme
from advx_backend.domain.memory import RoomLongTermMemory, RoomMemoryType
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.room import RoomEventSource
from advx_backend.infrastructure.persistence.sqlite.models import (
    ModeMemeCandidateRow,
    ModeMemeRow,
    RoomEventRow,
)
from advx_backend.main import create_app

LOCAL_TOKEN = "test-token"


def headers(version: str = "3") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        "X-ADVX-Protocol-Version": version,
    }


def app(service: object | None = None) -> FastAPI:
    instance = FastAPI()
    instance.include_router(create_shared_brain_router(local_token=LOCAL_TOKEN))
    if service is not None:
        instance.state.shared_brain_service = service
    return instance


def canonical_spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash=f"{1:064x}",
        display_name="Persona",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-a",
        revision=1,
        viewer_count=1,
        persona_ids=[persona.persona_id],
        persona_weights={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


@pytest.fixture
def live_runtime(tmp_path):
    runtime = build_runtime(
        local_token=LOCAL_TOKEN,
        data_directory=tmp_path,
        runtime_capability_probe=NoOpRuntimeCapabilityProbe(),
    )
    spec = canonical_spec()
    runtime.provider_controller.install_initial(
        ProviderConfigurationRequest(
            provider_profile_id=spec.provider.provider_profile_id,
            model_base_url="https://models.example/v1",
            model_name=spec.provider.viewer_model,
            director_model=spec.provider.director_model,
            viewer_model=spec.provider.viewer_model,
            memory_model=spec.provider.memory_model,
            visual_summary_model=spec.provider.visual_summary_model,
            model_api_key="test-model-key",
            asr_api_key="test-asr-key",
        )
    )
    with TestClient(create_app(runtime=runtime)) as client:
        started = client.post(
            "/runtime/sessions",
            headers=headers(),
            json=RuntimeSessionStartRequest(
                client_request_id="shared-brain-start",
                canonical_runtime_spec=spec,
                client_config_hash=spec.config_hash(),
            ).model_dump(mode="json"),
        )
        assert started.status_code == 201
        yield client, runtime, started.json()


class Service:
    async def list_memories(self, room_id: str) -> tuple[object, ...]:
        del room_id
        return ()

    async def get_memory_head(self, room_id: str) -> int:
        del room_id
        return 7

    async def get_auto_ingest(self, namespace_id: str) -> object:
        return SimpleNamespace(
            namespace_id=namespace_id,
            enabled=True,
            revision=0,
        )

    async def pin_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return ModeMeme(
            meme_id=meme_id,
            room_id="room-1",
            namespace_id=namespace_id,
            text="meme",
            source_candidate_id="candidate-1",
            pinned=True,
            revision=expected_revision + 1,
            created_at_ms=1,
            updated_at_ms=2,
        )

    async def edit_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
        text: str,
        intensity: float | None,
    ) -> ModeMeme:
        return ModeMeme(
            meme_id=meme_id,
            room_id="room-1",
            namespace_id=namespace_id,
            text=text,
            intensity=0.5 if intensity is None else intensity,
            source_candidate_id="candidate-1",
            revision=expected_revision + 1,
            created_at_ms=1,
            updated_at_ms=2,
        )

    async def edit_memory(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        content: str,
        confidence: float,
        evidence_event_ids: tuple[str, ...] | None,
    ) -> RoomLongTermMemory:
        return RoomLongTermMemory(
            memory_id=memory_id,
            room_id=room_id,
            memory_type=RoomMemoryType.USER_PREFERENCE,
            content=content,
            evidence_event_ids=list(evidence_event_ids or ("event-1",)),
            confidence=confidence,
            revision=expected_revision + 1,
            created_at_ms=1,
            updated_at_ms=2,
        )


def test_shared_brain_requires_v2_and_configured_service() -> None:
    with TestClient(app()) as client:
        legacy = client.get(
            "/shared-brain/rooms/room-1/memories",
            headers=headers("1"),
        )
        unavailable = client.get(
            "/shared-brain/rooms/room-1/memories",
            headers=headers(),
        )

    assert legacy.status_code == 409
    assert legacy.json()["detail"]["code"] == "protocol_version_conflict"
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "shared_brain_service_unavailable"


def test_shared_brain_exposes_settings_and_cas_meme_actions() -> None:
    with TestClient(app(Service())) as client:
        setting = client.get(
            "/shared-brain/modes/mode-a/auto-ingest",
            headers=headers(),
        )
        pinned = client.post(
            "/shared-brain/modes/mode-a/memes/meme-1/pin",
            headers=headers(),
            json={"expected_revision": 3},
        )

    assert setting.status_code == 200
    assert setting.json() == {
        "namespace_id": "mode-a",
        "enabled": True,
        "revision": 0,
    }
    assert pinned.status_code == 200
    assert pinned.json()["revision"] == 4
    assert pinned.json()["pinned"] is True


def test_shared_brain_exposes_authoritative_memory_and_meme_edit_routes() -> None:
    with TestClient(app(Service())) as client:
        memory = client.put(
            "/shared-brain/rooms/room-1/memories/memory-1",
            headers=headers(),
            json={
                "expected_revision": 2,
                "content": "edited memory",
                "confidence": 0.8,
            },
        )
        meme = client.put(
            "/shared-brain/modes/mode-a/memes/meme-1",
            headers=headers(),
            json={"expected_revision": 3, "text": "edited meme"},
        )
        head = client.get(
            "/shared-brain/rooms/room-1/memory-head",
            headers=headers(),
        )

    assert memory.status_code == 200
    assert memory.json()["revision"] == 3
    assert memory.json()["content"] == "edited memory"
    assert meme.status_code == 200
    assert meme.json()["revision"] == 4
    assert meme.json()["intensity"] == 0.5
    assert head.json() == {"room_id": "room-1", "revision": 7}


def test_direct_candidate_rejects_untrusted_observation_event_and_frame_provenance(
    live_runtime,
) -> None:
    client, runtime, started = live_runtime
    base = {
        "room_id": "room-1",
        "session_id": started["session_id"],
        "audience_epoch": started["audience_epoch"],
        "namespace_id": "mode-a",
        "text": "candidate",
        "outcome": "pending",
        "created_at_ms": 100,
    }
    missing_observation = client.post(
        "/shared-brain/meme-candidates",
        headers=headers(),
        json={
            **base,
            "candidate_id": "missing-observation",
            "observation_id": "missing",
            "evidence_event_ids": ["missing-event"],
            "evidence_frame_indexes": [],
        },
    )

    event = client.portal.call(
        partial(
            runtime.room_service.append_event,
            started["session_id"],
            source_type=RoomEventSource.USER_TEXT,
            source_id="host",
            text="trusted",
            payload={"input_id": "text-1"},
        )
    )
    runtime.debug_service.record(
        ObservationWaveTrace(
            trace_id="wave-trusted",
            room_id="room-1",
            session_id=started["session_id"],
            audience_epoch=started["audience_epoch"],
            config_hash=canonical_spec().config_hash(),
            observation_id="trusted-observation",
            created_at_ms=100,
            deadline_at_ms=1_000,
            triggers=["user_text"],
            event_ids=[event.event_id],
            trigger_event_ids=[event.event_id],
            frame_hashes=[],
            memory=MemoryReferenceTrace(
                room_id="room-1",
                memory_revision=0,
                memory_ids=[],
            ),
            director_status=ObservationWaveStatus.COMPLETED,
        )
    )
    missing_event = client.post(
        "/shared-brain/meme-candidates",
        headers=headers(),
        json={
            **base,
            "candidate_id": "missing-event",
            "observation_id": "trusted-observation",
            "evidence_event_ids": ["not-in-wave"],
            "evidence_frame_indexes": [],
        },
    )
    missing_frame = client.post(
        "/shared-brain/meme-candidates",
        headers=headers(),
        json={
            **base,
            "candidate_id": "missing-frame",
            "observation_id": "trusted-observation",
            "evidence_event_ids": [event.event_id],
            "evidence_frame_indexes": [0],
        },
    )

    assert [
        missing_observation.status_code,
        missing_event.status_code,
        missing_frame.status_code,
    ] == [422, 422, 422]
    assert client.get(
        "/shared-brain/modes/mode-a/meme-candidates/pending",
        headers=headers(),
    ).json() == []
    assert client.get(
        "/shared-brain/modes/mode-a/memes",
        headers=headers(),
    ).json() == []


def test_legacy_import_creates_real_provenance_and_is_idempotent_across_sessions(
    live_runtime,
) -> None:
    client, runtime, started = live_runtime
    first_body = {
        "room_id": "room-1",
        "session_id": started["session_id"],
        "audience_epoch": started["audience_epoch"],
        "legacy_meme_id": "legacy-1",
        "text": "legacy meme",
        "legacy_created_at_ms": 50,
    }
    first = client.post(
        "/shared-brain/modes/mode-a/legacy-memes/import",
        headers=headers(),
        json=first_body,
    )
    repeated = client.post(
        "/shared-brain/modes/mode-a/legacy-memes/import",
        headers=headers(),
        json=first_body,
    )

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert repeated.status_code == 200
    assert repeated.json() == {**first.json(), "created": False}

    stopped = client.post(
        f"/sessions/{started['session_id']}/stop",
        headers=headers(),
    )
    assert stopped.status_code == 200
    spec = canonical_spec()
    restarted = client.post(
        "/runtime/sessions",
        headers=headers(),
        json=RuntimeSessionStartRequest(
            client_request_id="shared-brain-restart",
            canonical_runtime_spec=spec,
            client_config_hash=spec.config_hash(),
        ).model_dump(mode="json"),
    )
    assert restarted.status_code == 201
    next_body = {
        **first_body,
        "session_id": restarted.json()["session_id"],
        "audience_epoch": restarted.json()["audience_epoch"],
    }
    cross_session = client.post(
        "/shared-brain/modes/mode-a/legacy-memes/import",
        headers=headers(),
        json=next_body,
    )
    assert cross_session.status_code == 200
    assert cross_session.json() == {**first.json(), "created": False}

    async def counts() -> tuple[int, int, int]:
        async with runtime.database.session_factory() as session:
            candidates = await session.scalar(
                select(func.count()).select_from(ModeMemeCandidateRow)
            )
            memes = await session.scalar(select(func.count()).select_from(ModeMemeRow))
            events = await session.scalar(
                select(func.count())
                .select_from(RoomEventRow)
                .where(
                    RoomEventRow.event_id
                    == first.json()["provenance_event_id"]
                )
            )
        return int(candidates or 0), int(memes or 0), int(events or 0)

    assert client.portal.call(counts) == (1, 1, 1)
