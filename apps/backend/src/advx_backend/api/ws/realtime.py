from fastapi import APIRouter, WebSocket

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def realtime(websocket: WebSocket) -> None:
    """Handshake-only endpoint until the versioned event protocol is generated."""
    await websocket.accept()
    await websocket.send_json({"type": "backend.ready", "protocol_version": 1})
    await websocket.close()
