from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import Header, HTTPException

from advx_backend.application.memory_extractor import OpenAICompatibleMemoryExtractor
from advx_backend.application.ports.memory import MemoryEvidence
from advx_backend.application.recorded_scenario import build_recorded_runtime_fixture
from advx_backend.contracts.debug import AiCallQuery, AiCallRole
from advx_backend.contracts.protocol import PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from advx_backend.contracts.replay import (
    RecordedProviderOutput,
    ReplayBundle,
    ReplayEvent,
)
from advx_backend.contracts.session import SessionSnapshot
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
)

PROVIDER_PROFILE_ID = "runtime-smoke-recorded"
MODEL_BASE_URL = "https://recorded.invalid/v1"
VIEWER_MODEL = "recorded-viewer"
MEMORY_MODEL = "recorded-memory"
VISUAL_SUMMARY_MODEL = "recorded-visual"
MODEL_API_KEY = "recorded-no-network"
ASR_API_KEY = "recorded-no-network"
BARRAGE_TEXT = "deterministic runtime smoke barrage"
VIRTUAL_CLOCK_START_MS = 4_102_444_800_000


def _bundle() -> ReplayBundle:
    persona = PersonaTemplate(
        persona_id="runtime-smoke-persona",
        document_version=1,
        revision=1,
        content_hash="a" * 64,
        display_name="Runtime Smoke Viewer",
        role="deterministic integration proof",
        silence_bias=0,
        burst_bias=1,
        repetition_bias=0,
        cooldown_ms=1,
    )
    mode = ModeDefinition(
        mode_id="runtime-smoke-mode",
        namespace_id="runtime-smoke-memes",
        revision=1,
        persona_counts={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=1, maximum=1),
        highlight_response_range=ResponseRange(minimum=1, maximum=1),
    )
    spec = CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="runtime-smoke-room",
            display_name="Runtime Smoke Room",
            created_at_ms=1_000,
            updated_at_ms=1_000,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id=PROVIDER_PROFILE_ID,
            viewer_model=VIEWER_MODEL,
            memory_model=MEMORY_MODEL,
            visual_summary_model=VISUAL_SUMMARY_MODEL,
        ),
    )
    outputs = {
        "viewer": {
            "action": "barrage",
            "text": BARRAGE_TEXT,
            "reaction_type": "runtime_smoke",
        },
        "memory": {"candidates": []},
        "visual_summary": {"summary": "deterministic runtime smoke frame"},
        "asr": {
            "text": "deterministic runtime smoke transcript",
            "final": True,
            "started_at_ms": 1_010,
            "ended_at_ms": 1_020,
        },
    }
    roles = tuple(outputs)
    return ReplayBundle(
        bundle_id="desktop-runtime-smoke",
        created_at_ms=1_000,
        seed=7,
        virtual_clock_start_ms=VIRTUAL_CLOCK_START_MS,
        config_hash=spec.config_hash(),
        canonical_runtime_spec=spec,
        events=[
            ReplayEvent(
                sequence=index,
                event_type=f"{role}.completed",
                occurred_at_ms=1_000 + index,
                payload={"generation_request_id": f"runtime-smoke-{role}"},
            )
            for index, role in enumerate(roles, start=1)
        ],
        recorded_provider_outputs=[
            RecordedProviderOutput(
                generation_request_id=f"runtime-smoke-{role}",
                provider_role=role,
                output=outputs[role],
            )
            for role in roles
        ],
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic, no-external Electron runtime smoke backend."
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    fixture = build_recorded_runtime_fixture(
        data_directory=args.data_dir,
        local_token=args.token,
        bundle=_bundle(),
    )

    async def proof(
        authorization: str | None = Header(default=None),
        protocol_version: str | None = Header(
            default=None,
            alias=PROTOCOL_VERSION_HEADER,
        ),
    ) -> dict[str, object]:
        if authorization != f"Bearer {args.token}":
            raise HTTPException(status_code=401, detail="invalid smoke token")
        if protocol_version != str(PROTOCOL_VERSION):
            raise HTTPException(status_code=426, detail="invalid protocol version")
        session = await fixture.runtime.session_service.status()
        return {
            "proof_scope": "deterministic-no-external-electron-fastapi-overlay",
            "backend_pid": os.getpid(),
            "deterministic_adapters": True,
            "runtime_protocol": PROTOCOL_VERSION,
            "external_transport_call_count": fixture.external_transport_call_count,
            "sqlite_path": str(fixture.runtime.database.path),
            "sqlite_started": fixture.runtime.database.started,
            "capability_probe_calls": fixture.capability_probe.calls,
            "viewer_calls": fixture.viewer_provider.viewer_calls,
            "memory_extractor_calls": fixture.memory_extractor.calls,
            "session": SessionSnapshot.from_domain(session).model_dump(mode="json"),
        }

    async def seed_ai_call(
        payload: dict[str, str],
        authorization: str | None = Header(default=None),
        protocol_version: str | None = Header(
            default=None,
            alias=PROTOCOL_VERSION_HEADER,
        ),
    ) -> dict[str, str]:
        if authorization != f"Bearer {args.token}":
            raise HTTPException(status_code=401, detail="invalid smoke token")
        if protocol_version != str(PROTOCOL_VERSION):
            raise HTTPException(status_code=426, detail="invalid protocol version")
        session_id = payload.get("session_id")
        if not session_id:
            raise HTTPException(status_code=422, detail="session_id is required")
        session = await fixture.runtime.session_service.status()
        if session.session_id != session_id:
            raise HTTPException(status_code=409, detail="smoke session mismatch")
        committed = await fixture.runtime.runtime_state.snapshot(session_id)
        visible_text = "deterministic runtime smoke memory input"

        async def provider_response(request: httpx.Request) -> httpx.Response:
            if request.headers.get("Authorization") != f"Bearer {MODEL_API_KEY}":
                raise AssertionError("real memory adapter did not authenticate")
            return httpx.Response(
                200,
                request=request,
                headers={"x-request-id": "runtime-smoke-provider-request"},
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "memory_type": "shared_experience",
                                                "content": (
                                                    "runtime smoke candidate body "
                                                    "must stay out of debug logs"
                                                ),
                                                "evidence_event_ids": [
                                                    "runtime-smoke-event"
                                                ],
                                                "tags": ["runtime-smoke"],
                                                "importance": 0.8,
                                                "confidence": 0.9,
                                            }
                                        ]
                                    },
                                    separators=(",", ":"),
                                )
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 37,
                        "completion_tokens": 11,
                        "total_tokens": 48,
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(provider_response)
        ) as client:
            extractor = OpenAICompatibleMemoryExtractor(
                OpenAICompatibleViewerRuntimeConfig(
                    base_url=MODEL_BASE_URL,
                    provider=ProviderRuntimeSpec(
                        provider_profile_id=PROVIDER_PROFILE_ID,
                        viewer_model=VIEWER_MODEL,
                        memory_model=MEMORY_MODEL,
                        visual_summary_model=VISUAL_SUMMARY_MODEL,
                    ),
                    api_key=MODEL_API_KEY,
                ),
                client=client,
                ai_call_sink=fixture.runtime.debug_service,
            )
            try:
                await extractor.extract(
                    room_id=committed.spec.room.room_id,
                    session_id=session_id,
                    audience_epoch=committed.audience_epoch,
                    events=[
                        MemoryEvidence(
                            event_id="runtime-smoke-event",
                            room_id=committed.spec.room.room_id,
                            source_type="user_text",
                            occurred_at_ms=VIRTUAL_CLOCK_START_MS + 2,
                            summary=visible_text,
                        )
                    ],
                    current_revision=0,
                )
            finally:
                await extractor.aclose()

        traces = fixture.runtime.debug_service.query_ai_calls(
            AiCallQuery(
                session_id=session_id,
                role=AiCallRole.MEMORY,
                limit=1,
            )
        )
        if not traces.items:
            raise HTTPException(status_code=500, detail="real memory trace missing")
        trace = traces.items[0]
        return {
            "call_id": trace.call_id,
            "correlation_id": trace.correlation_id,
            "visible_text": visible_text,
        }

    fixture.app.add_api_route(
        "/__runtime-smoke/proof",
        proof,
        methods=["GET"],
        include_in_schema=False,
    )
    fixture.app.add_api_route(
        "/__runtime-smoke/ai-call",
        seed_ai_call,
        methods=["POST"],
        include_in_schema=False,
    )
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.write_text(
        json.dumps(
            {
                "backend_pid": os.getpid(),
                "runtime_protocol": PROTOCOL_VERSION,
                "provider": {
                    "baseUrl": MODEL_BASE_URL,
                    "providerProfileId": PROVIDER_PROFILE_ID,
                    "model": VIEWER_MODEL,
                    "viewerModel": VIEWER_MODEL,
                    "memoryModel": MEMORY_MODEL,
                    "visualSummaryModel": VISUAL_SUMMARY_MODEL,
                    "apiKey": MODEL_API_KEY,
                    "asrBaseUrl": "https://api.stepfun.com/v1",
                    "asrModel": "stepaudio-2.5-asr",
                    "asrApiKey": ASR_API_KEY,
                },
                "expected_barrage_text": BARRAGE_TEXT,
                "synthetic_frame_captured_at_ms": VIRTUAL_CLOCK_START_MS + 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    uvicorn.run(
        fixture.app,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
