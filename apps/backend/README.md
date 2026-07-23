# Local backend

The FastAPI backend follows this dependency direction:

```text
api -> application -> domain
                \--> application ports

providers -------> application ports
infrastructure ---> application ports
```

`bootstrap` creates concrete adapters and injects them into the application layer. Keep provider wire formats and SQLAlchemy models inside their adapters. Audience selection, speaking timing, model-call grouping and memory extraction remain replaceable algorithms and must not be embedded in transport handlers.

See [Backend design](../../docs/BACKEND_DESIGN.md) for module responsibilities, the realtime data lifecycle, SQLite schema, transaction boundaries and migration rules.

## Implemented control protocol

- `GET /health` is public so Electron Main can supervise the process.
- Session control uses `POST /sessions`, `GET /sessions/current`, and the `pause`, `resume`, and `stop` commands under `/sessions/{session_id}`.
- Session HTTP requests require `Authorization: Bearer <local-token>` and `X-ADVX-Protocol-Version: 1`.
- `/ws` accepts JSON text messages. The first message must be `client.hello` with the same local token and protocol version; the connection then receives ordered session status events.

The additive realtime ingest contract, including binary audio/image envelopes and privacy boundaries, is defined in [Ingest protocol](../../docs/INGEST_PROTOCOL.md). Its Handler and service integration are intentionally deferred.

`scripts/dev.mjs` creates one random token per development launch and injects it into the backend and Electron Main process without printing it. The production launcher must provide the same value through its protected bootstrap channel before building `BackendRuntime`; it must not be written to ordinary configuration or logs.

## Local persistence

The backend applies Alembic migrations before accepting a session and owns the SQLite connection lifecycle. The launcher supplies `ADVX_DATA_DIR`; the database is created as `advx.sqlite3` inside that directory. Production should pass Electron's `userData/data` directory. Development uses the ignored repository-local `.advx-data` directory.

SQLite runs with foreign keys, WAL and a bounded busy timeout enabled. Repositories and the Unit of Work live under `infrastructure/persistence/sqlite`; application code depends only on the persistence Ports. Audio, frames, full room events, prompts, credentials and raw provider responses are never stored in this schema.

## Bounded reaction pipeline

`BackendRuntime` owns the active Room buffer, Context Builder and Barrage Pipeline so their bounded state starts and stops with the session. A configured caller supplies the audience snapshot, trigger, selection, invocation-planning and model-provider Ports to `build_reaction_service`. The resulting path maps immutable Domain observations into provider contracts, preserves request ownership through generation, validates barrage candidates locally, writes accepted output back to Room and publishes `barrage.event` over the realtime connection.
