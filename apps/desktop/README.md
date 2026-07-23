# Desktop app

Electron owns operating-system capabilities and contains three isolated renderer entries:

- `control`: React control surface and user text input.
- `capture`: hidden screen and microphone capture context.
- `overlay`: transparent, click-through barrage renderer.

The Main Process owns windows, permissions, credentials and the FastAPI child-process lifecycle. Renderers must not call model providers or read stored secrets directly.
