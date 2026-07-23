# Desktop app

Electron owns operating-system capabilities and contains three isolated renderer entries:

- `control`: React control surface and user text input.
- `capture`: hidden screen and microphone capture context.
- `overlay`: transparent, click-through barrage renderer.

The Main Process owns windows, permissions, credentials and the FastAPI child-process lifecycle. Renderers must not call model providers or read stored secrets directly.

## MVP commands

Run from the repository root:

```bash
pnpm dev:desktop
pnpm --filter @advx/desktop typecheck
pnpm --filter @advx/desktop test
pnpm --filter @advx/desktop build
pnpm --filter @advx/desktop smoke
```

The current control surface includes desktop-source selection, microphone metering, a
click-through barrage overlay, audience controls and securely stored provider settings. AI
generation and ASR status remain in demo mode until the FastAPI realtime contracts are wired in.
