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

`scripts/dev.mjs` creates one random token per development launch and injects it into the backend and Electron Main process without printing it. The production launcher must provide the same value through its protected bootstrap channel before building `BackendRuntime`; it must not be written to ordinary configuration or logs.
