import base64

from advx_backend.infrastructure.logging.ai_call_image_store import AiCallImageStore


def _data_url(body: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(body).decode('ascii')}"


def test_captures_only_supported_bounded_images_and_deduplicates() -> None:
    store = AiCallImageStore(max_items=2, max_total_bytes=8, max_image_bytes=4)
    first = store.capture(_data_url(b"one"))

    assert first is not None
    assert store.capture(_data_url(b"one")) == first
    image = store.get(first)
    assert image is not None
    assert image.mime_type == "image/png"
    assert image.body == b"one"
    assert store.capture("data:image/gif;base64,b25l") is None
    assert store.capture("data:image/png;base64,not-base64") is None
    assert store.capture(_data_url(b"too-long")) is None


def test_evicts_oldest_preview_when_its_memory_budget_is_exceeded() -> None:
    store = AiCallImageStore(max_items=3, max_total_bytes=4, max_image_bytes=4)
    first = store.capture(_data_url(b"one"))
    second = store.capture(_data_url(b"two"))

    assert first is not None
    assert second is not None
    assert store.get(first) is None
    assert store.get(second) is not None
