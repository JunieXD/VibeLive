import hashlib
import struct

from advx_backend.application.frame_store import InMemoryFrameStore
from advx_backend.application.viewer_runtime_coordinator import FrameMetadata
from advx_backend.domain.observation import FrameRef


class StoredFrameMetadataResolver:
    def __init__(
        self,
        *,
        frame_store: InMemoryFrameStore,
    ) -> None:
        self._frame_store = frame_store

    async def resolve(
        self,
        *,
        session_id: str,
        frame: FrameRef,
    ) -> FrameMetadata | None:
        resolved = await self._frame_store.resolve(session_id=session_id, frame=frame)
        if resolved is None:
            return None
        dimensions = _image_metadata(resolved.body)
        if dimensions is None:
            return None
        width, height, encoding = dimensions
        if resolved.mime_type.casefold() != encoding:
            return None
        return FrameMetadata(
            width=width,
            height=height,
            encoding=encoding,
            content_hash=hashlib.sha256(resolved.body).hexdigest(),
            change_score=resolved.change_score,
        )

    async def retain(
        self,
        *,
        session_id: str,
        frames: tuple[FrameRef, ...],
    ) -> bool:
        return await self._frame_store.retain(session_id=session_id, frames=frames)

    async def release(
        self,
        *,
        session_id: str,
        frames: tuple[FrameRef, ...],
    ) -> None:
        await self._frame_store.release(session_id=session_id, frames=frames)


def _image_metadata(body: bytes) -> tuple[int, int, str] | None:
    if len(body) >= 24 and body.startswith(b"\x89PNG\r\n\x1a\n"):
        if body[12:16] != b"IHDR":
            return None
        width, height = struct.unpack(">II", body[16:24])
        return _valid_dimensions(width, height, "image/png")
    if body.startswith(b"\xff\xd8"):
        dimensions = _jpeg_dimensions(body)
        return (
            None
            if dimensions is None
            else _valid_dimensions(*dimensions, "image/jpeg")
        )
    if (
        len(body) >= 30
        and body.startswith(b"RIFF")
        and body[8:12] == b"WEBP"
    ):
        dimensions = _webp_dimensions(body)
        return (
            None
            if dimensions is None
            else _valid_dimensions(*dimensions, "image/webp")
        )
    return None


def _jpeg_dimensions(body: bytes) -> tuple[int, int] | None:
    offset = 2
    while offset + 4 <= len(body):
        if body[offset] != 0xFF:
            return None
        while offset < len(body) and body[offset] == 0xFF:
            offset += 1
        if offset >= len(body):
            return None
        marker = body[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            return None
        if offset + 2 > len(body):
            return None
        length = int.from_bytes(body[offset : offset + 2], "big")
        if length < 2 or offset + length > len(body):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if length < 7:
                return None
            return (
                int.from_bytes(body[offset + 5 : offset + 7], "big"),
                int.from_bytes(body[offset + 3 : offset + 5], "big"),
            )
        offset += length
    return None


def _webp_dimensions(body: bytes) -> tuple[int, int] | None:
    chunk = body[12:16]
    if chunk == b"VP8X" and len(body) >= 30:
        return (
            1 + int.from_bytes(body[24:27], "little"),
            1 + int.from_bytes(body[27:30], "little"),
        )
    if chunk == b"VP8L" and len(body) >= 25 and body[20] == 0x2F:
        bits = int.from_bytes(body[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8 " and len(body) >= 30 and body[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(body[26:28], "little") & 0x3FFF,
            int.from_bytes(body[28:30], "little") & 0x3FFF,
        )
    return None


def _valid_dimensions(
    width: int, height: int, encoding: str
) -> tuple[int, int, str] | None:
    if width < 1 or height < 1:
        return None
    return width, height, encoding
