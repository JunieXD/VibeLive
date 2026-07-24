from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER

LOCAL_TOKEN = "test-local-token"


def persona(persona_id: str, *, revision: int = 1) -> dict[str, object]:
    return {
        "persona_id": persona_id,
        "document_version": 1,
        "revision": revision,
        "content_hash": f"{revision:064x}",
        "display_name": persona_id,
        "role": "viewer",
        "traits": [],
        "speech_style": {},
        "behavior": {},
        "trigger_preferences": [],
        "avoid_patterns": [],
        "silence_bias": 0.2,
        "burst_bias": 0.2,
        "repetition_bias": 0.2,
        "cooldown_ms": 0,
        "content_flags": [],
        "enabled": True,
    }


def runtime_spec(*, revision: int = 1, persona_revision: int = 1) -> dict[str, object]:
    return {
        "protocol_version": 3,
        "audience_contract_version": 2,
        "config_revision": revision,
        "room": {
            "room_id": "room-1",
            "display_name": "Room",
            "revision": 1,
            "created_at_ms": 1,
            "updated_at_ms": 1,
        },
        "active_mode_id": "mode-1",
        "personas": [persona("persona-1", revision=persona_revision)],
        "modes": [
            {
                "mode_id": "mode-1",
                "namespace_id": "mode-1",
                "revision": revision,
                "target_concurrent_viewers": 1,
                "persona_ids": ["persona-1"],
                "persona_weights": {"persona-1": 1},
                "persona_overrides": {},
                "normal_response_range": {"minimum": 0, "maximum": 1},
                "highlight_response_range": {"minimum": 0, "maximum": 1},
                "ambience": "natural",
            }
        ],
        "provider": {
            "provider_profile_id": "provider-1",
            "director_model": "director",
            "viewer_model": "viewer",
            "memory_model": "memory",
            "visual_summary_model": "visual",
        },
        "settings": {},
    }


def start_body(
    *,
    client_request_id: str = "start-1",
    spec: dict[str, object] | None = None,
) -> dict[str, object]:
    from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec

    canonical = CanonicalRuntimeSpec.model_validate(spec or runtime_spec())
    return {
        "client_request_id": client_request_id,
        "canonical_runtime_spec": canonical.model_dump(mode="json"),
        "client_config_hash": canonical.config_hash(),
    }


def headers(*, version: str = "3") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: version,
    }


class FakeRuntimeSessionService:
    def __init__(self) -> None:
        self.starts: dict[str, tuple[str, dict[str, object]]] = {}
        self.current_by_session: dict[str, dict[str, object]] = {}
        self.apply_error: Exception | None = None
        self.applies: list[str] = []
        self.rollbacks: list[str] = []
        self.recoveries: list[str] = []

    async def start(self, request: object) -> dict[str, object]:
        request_id = request.client_request_id
        config_hash = request.client_config_hash
        existing = self.starts.get(request_id)
        if existing is not None:
            previous_hash, response = existing
            if previous_hash != config_hash:
                from advx_backend.application.runtime_session_service import (
                    RuntimeSessionConflictError,
                )

                raise RuntimeSessionConflictError(
                    "client_request_id was already used with a different canonical hash"
                )
            return response
        response = {
            "session_id": "session-1",
            "room_id": request.canonical_runtime_spec.room.room_id,
            "audience_epoch": 1,
            "config_revision": request.canonical_runtime_spec.config_revision,
            "config_hash": config_hash,
            "canonical_runtime_spec": request.canonical_runtime_spec.model_dump(mode="json"),
            "viewers": [],
            "apply_id": f"start:{request_id}",
            "diff": {
                "changed_paths": [],
                "added_viewer_ids": [],
                "retained_viewer_ids": [],
                "reset_viewer_ids": [],
                "removed_viewer_ids": [],
            },
            "recovered": False,
        }
        self.starts[request_id] = (config_hash, response)
        self.current_by_session["session-1"] = deepcopy(response)
        return response

    async def current(self, session_id: str) -> dict[str, object]:
        return self.current_by_session[session_id]

    async def apply(self, session_id: str, request: object) -> dict[str, object]:
        if self.apply_error is not None:
            raise self.apply_error
        self.applies.append(session_id)
        current = self.current_by_session[session_id]
        current.update(
            {
                "audience_epoch": int(current["audience_epoch"]) + 1,
                "config_revision": request.canonical_runtime_spec.config_revision,
                "config_hash": request.client_config_hash,
                "canonical_runtime_spec": request.canonical_runtime_spec.model_dump(mode="json"),
                "apply_id": request.apply_id,
                "diff": {
                    "changed_paths": ["config_revision"],
                    "added_viewer_ids": [],
                    "retained_viewer_ids": [],
                    "reset_viewer_ids": [],
                    "removed_viewer_ids": [],
                },
            }
        )
        return current

    async def rollback(self, session_id: str, request: object) -> dict[str, object]:
        self.rollbacks.append(session_id)
        current = self.current_by_session[session_id]
        current.update(
            {
                "audience_epoch": int(current["audience_epoch"]) + 1,
                "config_revision": request.target_revision,
                "apply_id": request.apply_id,
            }
        )
        return current

    async def recover(self, session_id: str) -> dict[str, object]:
        self.recoveries.append(session_id)
        current = self.current_by_session[session_id]
        current["audience_epoch"] = int(current["audience_epoch"]) + 1
        current["apply_id"] = "recover:recovery-1"
        current["diff"] = {
            "changed_paths": [],
            "added_viewer_ids": [],
            "retained_viewer_ids": ["viewer-1"],
            "reset_viewer_ids": [],
            "removed_viewer_ids": [],
        }
        current["recovered"] = True
        return current


def app_with(service: FakeRuntimeSessionService | None) -> FastAPI:
    from advx_backend.api.http.runtime import create_runtime_router

    app = FastAPI()
    if service is not None:
        app.state.runtime_session_service = service
    app.include_router(create_runtime_router(local_token=LOCAL_TOKEN))
    return app


def test_runtime_start_is_idempotent_for_same_request_id_and_canonical_hash() -> None:
    service = FakeRuntimeSessionService()
    body = start_body()

    with TestClient(app_with(service)) as client:
        first = client.post("/runtime/sessions", headers=headers(), json=body)
        repeated = client.post("/runtime/sessions", headers=headers(), json=body)

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json() == first.json()
    assert len(service.starts) == 1


def test_runtime_start_rejects_request_id_reuse_with_different_hash() -> None:
    service = FakeRuntimeSessionService()
    first = start_body()
    changed = runtime_spec(revision=2, persona_revision=2)
    conflicting = start_body(client_request_id="start-1", spec=changed)

    with TestClient(app_with(service)) as client:
        assert client.post("/runtime/sessions", headers=headers(), json=first).status_code == 201
        response = client.post(
            "/runtime/sessions",
            headers=headers(),
            json=conflicting,
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "client_request_conflict"


def test_runtime_current_apply_and_rollback_are_explicit_session_operations() -> None:
    service = FakeRuntimeSessionService()
    started = start_body()
    next_spec = runtime_spec(revision=2, persona_revision=2)
    apply_request = {
        "apply_id": "apply-2",
        "base_revision": 1,
        "audience_contract_version": 2,
        "canonical_runtime_spec": next_spec,
        "client_config_hash": start_body(spec=next_spec)["client_config_hash"],
    }

    with TestClient(app_with(service)) as client:
        client.post("/runtime/sessions", headers=headers(), json=started)
        current = client.get("/runtime/sessions/session-1", headers=headers())
        applied = client.post(
            "/runtime/sessions/session-1/apply",
            headers=headers(),
            json=apply_request,
        )
        rolled_back = client.post(
            "/runtime/sessions/session-1/rollback",
            headers=headers(),
            json={
                "apply_id": "rollback-1",
                "base_revision": 2,
                "target_revision": 1,
                "audience_contract_version": 2,
            },
        )

    assert current.json()["config_revision"] == 1
    assert applied.json()["config_revision"] == 2
    assert applied.json()["audience_epoch"] == 2
    assert applied.json()["apply_id"] == "apply-2"
    assert applied.json()["diff"]["changed_paths"] == ["config_revision"]
    assert rolled_back.json()["config_revision"] == 1
    assert rolled_back.json()["audience_epoch"] == 3
    assert rolled_back.json()["apply_id"] == "rollback-1"


def test_failed_apply_preserves_previous_committed_runtime() -> None:
    service = FakeRuntimeSessionService()
    started = start_body()
    next_spec = runtime_spec(revision=2, persona_revision=2)
    service.apply_error = ValueError("provider capability probe failed")

    with TestClient(app_with(service)) as client:
        client.post("/runtime/sessions", headers=headers(), json=started)
        rejected = client.post(
            "/runtime/sessions/session-1/apply",
            headers=headers(),
            json={
                "apply_id": "apply-2",
                "base_revision": 1,
                "audience_contract_version": 2,
                "canonical_runtime_spec": next_spec,
                "client_config_hash": start_body(spec=next_spec)["client_config_hash"],
            },
        )
        current = client.get("/runtime/sessions/session-1", headers=headers())

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "runtime_apply_rejected"
    assert current.json()["config_revision"] == 1
    assert current.json()["audience_epoch"] == 1


def test_explicit_recovery_keeps_session_id_and_advances_epoch() -> None:
    service = FakeRuntimeSessionService()

    with TestClient(app_with(service)) as client:
        started = client.post(
            "/runtime/sessions",
            headers=headers(),
            json=start_body(),
        ).json()
        recovered = client.post(
            "/runtime/sessions/session-1/recover",
            headers=headers(),
        )

    assert recovered.status_code == 200
    assert recovered.json()["session_id"] == started["session_id"]
    assert recovered.json()["audience_epoch"] == started["audience_epoch"] + 1
    assert recovered.json()["apply_id"] == "recover:recovery-1"
    assert recovered.json()["diff"]["retained_viewer_ids"] == ["viewer-1"]
    assert recovered.json()["recovered"] is True
