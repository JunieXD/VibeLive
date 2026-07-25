import base64
import binascii
import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

_SUPPORTED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_DEFAULT_MAX_ITEMS = 120
_DEFAULT_MAX_TOTAL_BYTES = 96 * 1024 * 1024
_DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AiCallImage:
    mime_type: str
    body: bytes


class AiCallImageStore:
    """Bounded in-memory image previews for local AI call inspection."""

    def __init__(
        self,
        *,
        max_items: int = _DEFAULT_MAX_ITEMS,
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
        max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be at least one")
        if max_total_bytes < 1:
            raise ValueError("max_total_bytes must be at least one")
        if max_image_bytes < 1:
            raise ValueError("max_image_bytes must be at least one")
        self._max_items = max_items
        self._max_total_bytes = max_total_bytes
        self._max_image_bytes = min(max_image_bytes, max_total_bytes)
        self._items: OrderedDict[str, AiCallImage] = OrderedDict()
        self._ids_by_digest: dict[str, str] = {}
        self._total_bytes = 0
        self._lock = RLock()

    def capture(self, data_url: str) -> str | None:
        parsed = self._parse_data_url(data_url)
        if parsed is None:
            return None
        mime_type, body = parsed
        digest = f"{mime_type}:{hashlib.sha256(body).hexdigest()}"
        with self._lock:
            existing_id = self._ids_by_digest.get(digest)
            if existing_id is not None and existing_id in self._items:
                self._items.move_to_end(existing_id)
                return existing_id

            preview_id = f"ai-image-{uuid4()}"
            image = AiCallImage(mime_type=mime_type, body=body)
            self._items[preview_id] = image
            self._ids_by_digest[digest] = preview_id
            self._total_bytes += len(body)
            self._trim()
            return preview_id

    def get(self, preview_id: str) -> AiCallImage | None:
        with self._lock:
            image = self._items.get(preview_id)
            if image is not None:
                self._items.move_to_end(preview_id)
            return image

    def _parse_data_url(self, value: str) -> tuple[str, bytes] | None:
        header, separator, encoded = value.partition(",")
        if not separator or not header.startswith("data:") or not header.endswith(";base64"):
            return None
        mime_type = header.removeprefix("data:").removesuffix(";base64")
        if mime_type not in _SUPPORTED_MIME_TYPES:
            return None
        max_encoded_chars = ((self._max_image_bytes + 2) // 3) * 4
        if not encoded or len(encoded) > max_encoded_chars:
            return None
        try:
            body = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not body or len(body) > self._max_image_bytes:
            return None
        return mime_type, body

    def _trim(self) -> None:
        while self._items and (
            len(self._items) > self._max_items or self._total_bytes > self._max_total_bytes
        ):
            preview_id, image = self._items.popitem(last=False)
            self._total_bytes -= len(image.body)
            digest = f"{image.mime_type}:{hashlib.sha256(image.body).hexdigest()}"
            self._ids_by_digest.pop(digest, None)
