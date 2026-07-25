import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel

from advx_backend.contracts.debug import (
    AiCallError,
    AiCallRequestSummary,
    AiCallResponseSummary,
    AiCallRole,
    AiCallStatus,
    AiCallTimelineEvent,
    AiCallTrace,
)
from advx_backend.contracts.viewer_runtime import ViewerRequestTriggerContext

logger = logging.getLogger(__name__)

_MAX_PREVIEW_STRING = 1_000
_MAX_PREVIEW_ITEMS = 64
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "audio",
    "base64",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "data_ref",
    "frame",
    "image",
    "image_url",
    "input_text",
    "instructions",
    "messages",
    "password",
    "prompt",
    "prompt_text",
    "provider_response",
    "provider_raw_response",
    "raw_input",
    "raw_media",
    "raw_prompt",
    "raw_response",
    "refresh_token",
    "response_body",
    "secret",
    "system_prompt",
    "token",
}
_INLINE_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_SECRET]"),
    (
        re.compile(
            r"(?i)\b[a-z0-9._-]*(?:api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret|password|credential|secret)"
            r"[a-z0-9._-]*\s*[:=]\s*[^\s,;}\]]+"
        ),
        "[REDACTED_SECRET]",
    ),
)
_SENSITIVE_KEY_MARKERS = {
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "refreshtoken",
    "secret",
}


class AiCallSink(Protocol):
    def record_ai_call(self, trace: AiCallTrace) -> None: ...


class AiCallImageCapture(Protocol):
    def capture_ai_call_image(self, data_url: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class AiCallScope:
    room_id: str | None = None
    session_id: str | None = None
    audience_epoch: int | None = None
    observation_id: str | None = None
    generation_request_id: str | None = None
    viewer_instance_id: str | None = None
    trigger_context: ViewerRequestTriggerContext | None = None
    utterance_id: str | None = None


class AiCallLifecycle:
    """Best-effort lifecycle recorder. Logging failures never break Provider calls."""

    def __init__(
        self,
        *,
        sink: AiCallSink | None,
        role: AiCallRole,
        correlation_id: str,
        provider: str,
        model_id: str,
        endpoint: str,
        scope: AiCallScope,
    ) -> None:
        self._sink = sink
        started_at_ms = _now_ms()
        self._trace = AiCallTrace(
            call_id=f"ai-call-{uuid4()}",
            correlation_id=correlation_id,
            role=role,
            status=AiCallStatus.PREPARING,
            provider=provider,
            model_id=model_id,
            endpoint=sanitize_endpoint(endpoint),
            room_id=scope.room_id,
            session_id=scope.session_id,
            audience_epoch=scope.audience_epoch,
            observation_id=scope.observation_id,
            generation_request_id=scope.generation_request_id,
            viewer_instance_id=scope.viewer_instance_id,
            trigger_context=scope.trigger_context,
            utterance_id=scope.utterance_id,
            started_at_ms=started_at_ms,
            updated_at_ms=started_at_ms,
            timeline=[AiCallTimelineEvent(stage="preparing", at_ms=started_at_ms)],
        )
        self._emit()

    @property
    def call_id(self) -> str:
        return self._trace.call_id

    def sent(self, request: AiCallRequestSummary) -> None:
        self._transition(
            AiCallStatus.SENT,
            "sent",
            request=request,
            detail={
                "wire_bytes": request.wire_bytes,
                "wire_sha256": request.wire_sha256,
            },
        )

    def capture_ai_call_image(self, data_url: str) -> str | None:
        sink = self._sink
        capture = getattr(sink, "capture_ai_call_image", None)
        if not callable(capture):
            return None
        try:
            preview_id = capture(data_url)
        except Exception:
            logger.exception("ai_call.image_capture_failed", extra={"call_id": self.call_id})
            return None
        return preview_id if isinstance(preview_id, str) and preview_id else None

    def received(self, response: AiCallResponseSummary) -> None:
        self._transition(
            AiCallStatus.RECEIVED,
            "received",
            response=response,
            detail={
                "http_status": response.http_status,
                "provider_request_id": response.provider_request_id,
            },
        )

    def streaming(
        self,
        parsed_output: object,
        *,
        detail: object | None = None,
    ) -> None:
        response = self._with_parsed_output(parsed_output)
        self._transition(
            AiCallStatus.STREAMING,
            "streaming",
            response=response,
            detail=detail,
        )

    def succeeded(self, parsed_output: object) -> None:
        now_ms = _now_ms()
        response = self._with_parsed_output(parsed_output)
        timeline = [
            *self._trace.timeline,
            AiCallTimelineEvent(stage="parsed", at_ms=now_ms),
            AiCallTimelineEvent(stage="completed", at_ms=now_ms),
        ]
        self._trace = self._trace.model_copy(
            update={
                "status": AiCallStatus.SUCCEEDED,
                "updated_at_ms": now_ms,
                "completed_at_ms": now_ms,
                "duration_ms": max(0, now_ms - self._trace.started_at_ms),
                "response": response,
                "error": None,
                "timeline": timeline,
            }
        )
        self._emit()

    def failed(self, error: BaseException, *, blocked: bool | None = None) -> None:
        now_ms = _now_ms()
        status_code = getattr(error, "status_code", None)
        retryable = bool(getattr(error, "retryable", False))
        message = _redact_text(str(error)).strip()[:1024] or type(error).__name__
        error_code = type(error).__name__[:128]
        if blocked is None:
            is_blocked = (
                "blocked" in type(error).__name__.casefold()
                or "credentials are not configured" in message.casefold()
            )
        else:
            is_blocked = blocked
        status = AiCallStatus.BLOCKED if is_blocked else AiCallStatus.FAILED
        self._trace = self._trace.model_copy(
            update={
                "status": status,
                "updated_at_ms": now_ms,
                "completed_at_ms": now_ms,
                "duration_ms": max(0, now_ms - self._trace.started_at_ms),
                "error": AiCallError(
                    code=error_code,
                    message=message,
                    http_status=(
                        status_code
                        if isinstance(status_code, int) and 100 <= status_code <= 599
                        else None
                    ),
                    retryable=retryable,
                ),
                "timeline": [
                    *self._trace.timeline,
                    AiCallTimelineEvent(
                        stage=status.value,
                        at_ms=now_ms,
                        detail={
                            "code": error_code,
                            "http_status": status_code,
                            "retryable": retryable,
                        },
                    ),
                ],
            }
        )
        self._emit()

    def cancelled(self) -> None:
        now_ms = _now_ms()
        self._trace = self._trace.model_copy(
            update={
                "status": AiCallStatus.CANCELLED,
                "updated_at_ms": now_ms,
                "completed_at_ms": now_ms,
                "duration_ms": max(0, now_ms - self._trace.started_at_ms),
                "timeline": [
                    *self._trace.timeline,
                    AiCallTimelineEvent(stage="cancelled", at_ms=now_ms),
                ],
            }
        )
        self._emit()

    def _with_parsed_output(self, parsed_output: object) -> AiCallResponseSummary:
        safe_output, _ = safe_debug_value(parsed_output)
        response = self._trace.response or AiCallResponseSummary()
        return response.model_copy(update={"parsed_output": safe_output})

    def _transition(
        self,
        status: AiCallStatus,
        stage: str,
        *,
        request: AiCallRequestSummary | None = None,
        response: AiCallResponseSummary | None = None,
        detail: object | None = None,
    ) -> None:
        now_ms = _now_ms()
        safe_detail, _ = safe_debug_value(detail)
        updates: dict[str, object] = {
            "status": status,
            "updated_at_ms": now_ms,
            "timeline": [
                *self._trace.timeline,
                AiCallTimelineEvent(
                    stage=stage,
                    at_ms=now_ms,
                    detail=safe_detail,
                ),
            ],
        }
        if request is not None:
            updates["request"] = request
        if response is not None:
            updates["response"] = response
        self._trace = self._trace.model_copy(update=updates)
        self._emit()

    def _emit(self) -> None:
        if self._sink is None:
            return
        try:
            self._sink.record_ai_call(self._trace)
        except Exception:
            logger.exception(
                "ai_call.record_failed",
                extra={"call_id": self._trace.call_id, "role": self._trace.role.value},
            )


def build_openai_request_summary(
    payload: dict[str, object],
    *,
    image_capture: AiCallImageCapture | None = None,
) -> AiCallRequestSummary:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    messages = payload.get("messages")
    system_content: object = None
    user_content: object = None
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            if item.get("role") == "system" and system_content is None:
                system_content = item.get("content")
            elif item.get("role") == "user" and user_content is None:
                user_content = item.get("content")

    redacted_fields: set[str] = set()
    instruction = _instruction_reference(system_content)
    input_context = _content_preview(
        user_content,
        path="input",
        redacted_fields=redacted_fields,
        image_capture=image_capture,
    )
    schema_name = None
    response_format = payload.get("response_format")
    if isinstance(response_format, dict):
        schema = response_format.get("json_schema")
        if isinstance(schema, dict) and isinstance(schema.get("name"), str):
            schema_name = schema["name"]
    max_tokens = payload.get("max_tokens")
    return AiCallRequestSummary(
        wire_sha256=hashlib.sha256(canonical).hexdigest(),
        wire_bytes=len(canonical),
        schema_name=schema_name,
        max_output_tokens=max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else None,
        input_preview={
            "instruction": instruction,
            "input": input_context,
        },
        redacted_fields=sorted(redacted_fields),
    )


def build_audio_request_summary(
    *,
    pcm: bytes,
    wire_body: bytes,
    started_at_ms: int,
    ended_at_ms: int,
    sample_rate: int,
    channels: int,
    sample_width_bits: int,
    language: str,
) -> AiCallRequestSummary:
    return AiCallRequestSummary(
        wire_sha256=hashlib.sha256(wire_body).hexdigest(),
        wire_bytes=len(wire_body),
        input_preview={
            "audio_ref": {
                "started_at_ms": started_at_ms,
                "ended_at_ms": ended_at_ms,
                "duration_ms": max(0, ended_at_ms - started_at_ms),
                "pcm_bytes": len(pcm),
                "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_width_bits": sample_width_bits,
                "language": language,
            }
        },
        redacted_fields=["audio_ref.pcm_body"],
    )


def build_http_response_summary(
    response: httpx.Response,
    *,
    include_body_digest: bool = True,
    include_model_output: bool = False,
) -> AiCallResponseSummary:
    body = response.content if include_body_digest else b""
    payload: object = None
    if include_body_digest:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            payload = None
    provider_request_id = _provider_request_id(response, payload)
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    model_output: str | None = None
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            raw_finish_reason = choices[0].get("finish_reason")
            if isinstance(raw_finish_reason, str) and raw_finish_reason:
                finish_reason = raw_finish_reason[:128]
            if include_model_output:
                message = choices[0].get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str):
                    model_output = content
        usage = payload.get("usage")
        if isinstance(usage, dict):
            input_tokens = _non_negative_int(usage.get("prompt_tokens", usage.get("input_tokens")))
            output_tokens = _non_negative_int(
                usage.get("completion_tokens", usage.get("output_tokens"))
            )
            total_tokens = _non_negative_int(usage.get("total_tokens"))
    return AiCallResponseSummary(
        http_status=response.status_code,
        provider_request_id=provider_request_id,
        body_sha256=hashlib.sha256(body).hexdigest() if include_body_digest else None,
        body_bytes=len(body) if include_body_digest else None,
        finish_reason=finish_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        model_output=model_output,
    )


def safe_debug_value(value: object) -> tuple[object, list[str]]:
    redacted_fields: set[str] = set()
    safe = _safe_value(
        value,
        path="$",
        redacted_fields=redacted_fields,
        depth=0,
    )
    return safe, sorted(redacted_fields)


def sanitize_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))[:2048]


def _content_preview(
    content: object,
    *,
    path: str,
    redacted_fields: set[str],
    image_capture: AiCallImageCapture | None,
) -> object:
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            if path == "input":
                encoded = content.encode("utf-8")
                return {
                    "kind": "input_text_ref",
                    "chars": len(content),
                    "bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
            return _text_preview(content, path=path, redacted_fields=redacted_fields)
        return _safe_value(
            decoded,
            path=path,
            redacted_fields=redacted_fields,
            depth=0,
        )
    if isinstance(content, list):
        parts: list[object] = []
        for index, item in enumerate(content[:_MAX_PREVIEW_ITEMS]):
            item_path = f"{path}[{index}]"
            if not isinstance(item, dict):
                parts.append(
                    _safe_value(
                        item,
                        path=item_path,
                        redacted_fields=redacted_fields,
                        depth=1,
                    )
                )
                continue
            if item.get("type") == "text":
                parts.append(
                    {
                        "type": "text_ref",
                        "value": _content_preview(
                            item.get("text"),
                            path=f"{item_path}.text",
                            redacted_fields=redacted_fields,
                            image_capture=image_capture,
                        ),
                    }
                )
                continue
            if item.get("type") == "image_url":
                media = item.get("image_url")
                url = media.get("url") if isinstance(media, dict) else None
                parts.append(_media_reference(url, image_capture=image_capture))
                redacted_fields.add(f"{item_path}.image_url")
                continue
            parts.append(
                _safe_value(
                    item,
                    path=item_path,
                    redacted_fields=redacted_fields,
                    depth=1,
                )
            )
        if len(content) > _MAX_PREVIEW_ITEMS:
            parts.append({"omitted_items": len(content) - _MAX_PREVIEW_ITEMS})
        return parts
    return _safe_value(
        content,
        path=path,
        redacted_fields=redacted_fields,
        depth=0,
    )


def _safe_value(
    value: object,
    *,
    path: str,
    redacted_fields: set[str],
    depth: int,
) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        return _text_preview(value, path=path, redacted_fields=redacted_fields)
    if isinstance(value, (bytes, bytearray, memoryview)):
        body = bytes(value)
        redacted_fields.add(path)
        return {
            "binary_ref": {
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        }
    if depth >= 8:
        redacted_fields.add(path)
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        if path.endswith(".room_memory_slice") or path == "input.room_memory_slice":
            redacted_fields.add(f"{path}.items")
            return {
                "room_id": value.get("room_id"),
                "memory_revision": value.get("memory_revision"),
                "memory_ids": value.get("memory_ids", []),
            }
        if path.endswith((".viewer_private_state", ".private_state")):
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            redacted_fields.add(path)
            return {
                "revision": value.get("revision"),
                "published_event_ids": value.get("published_event_ids", []),
                "direct_interaction_event_ids": value.get(
                    "direct_interaction_event_ids",
                    [],
                ),
                "state_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        result: dict[str, object] = {}
        items = list(value.items())
        for raw_key, child in items[:_MAX_PREVIEW_ITEMS]:
            key = str(raw_key)
            normalized = key.strip().casefold().replace("-", "_")
            child_path = f"{path}.{key}"
            if normalized == "room_memory_slice" and isinstance(child, dict):
                result["room_memory_refs"] = _safe_value(
                    child,
                    path=child_path,
                    redacted_fields=redacted_fields,
                    depth=depth + 1,
                )
                continue
            if _is_sensitive_key(key):
                redacted_fields.add(child_path)
                result[f"redacted_field_{len(redacted_fields)}"] = "[REDACTED]"
                continue
            safe_key = {
                "frames": "frame_refs",
            }.get(normalized, key)
            result[safe_key] = _safe_value(
                child,
                path=child_path,
                redacted_fields=redacted_fields,
                depth=depth + 1,
            )
        if len(items) > _MAX_PREVIEW_ITEMS:
            result["omitted_fields"] = len(items) - _MAX_PREVIEW_ITEMS
        return result
    if isinstance(value, (list, tuple)):
        items = [
            _safe_value(
                item,
                path=f"{path}[{index}]",
                redacted_fields=redacted_fields,
                depth=depth + 1,
            )
            for index, item in enumerate(value[:_MAX_PREVIEW_ITEMS])
        ]
        if len(value) > _MAX_PREVIEW_ITEMS:
            items.append({"omitted_items": len(value) - _MAX_PREVIEW_ITEMS})
        return items
    redacted_fields.add(path)
    return f"[UNSUPPORTED {type(value).__name__}]"


def _text_preview(
    value: str,
    *,
    path: str,
    redacted_fields: set[str],
) -> object:
    redacted = _redact_text(value)
    if redacted != value:
        redacted_fields.add(path)
    preview = redacted[:_MAX_PREVIEW_STRING]
    if len(redacted) <= _MAX_PREVIEW_STRING:
        return preview
    redacted_fields.add(f"{path}.truncated")
    return {
        "text_preview": preview,
        "text_chars": len(redacted),
        "text_sha256": hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
        "truncated": True,
    }


def _instruction_reference(content: object) -> object:
    if content is None:
        return None
    if not isinstance(content, str):
        encoded = json.dumps(
            content,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return {
            "kind": "structured_instruction_ref",
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    encoded = content.encode("utf-8")
    return {
        "kind": "instruction_ref",
        "chars": len(content),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold().replace("-", "_")
    if normalized in _SENSITIVE_KEYS:
        return True
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    return any(marker in compact for marker in _SENSITIVE_KEY_MARKERS)


def _media_reference(
    value: object,
    *,
    image_capture: AiCallImageCapture | None,
) -> dict[str, object]:
    if not isinstance(value, str):
        return {"type": "media_ref", "available": False}
    header, separator, encoded = value.partition(",")
    mime_type = None
    if header.startswith("data:"):
        mime_type = header.removeprefix("data:").split(";", 1)[0]
    reference: dict[str, object] = {
        "type": "media_ref",
        "mime_type": mime_type,
        "encoded_chars": len(encoded) if separator else len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }
    if image_capture is not None:
        preview_id = image_capture.capture_ai_call_image(value)
        if preview_id is not None:
            reference["preview_id"] = preview_id
    return reference


def _provider_request_id(response: httpx.Response, payload: object) -> str | None:
    for key in ("x-request-id", "request-id", "x-step-request-id"):
        value = response.headers.get(key)
        if value:
            return value[:256]
    if isinstance(payload, dict):
        value = payload.get("id")
        if isinstance(value, str) and value:
            return value[:256]
    return None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _INLINE_SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
